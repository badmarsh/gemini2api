"""uvicorn.access must not log ?token= / sk- secrets (audit item 7a).

uvicorn formats its access line from ``record.args`` =
(client_addr, method, full_path, http_version, status_code), so the request path (including
the query string) lands in the log verbatim unless a filter scrubs it.
"""

import logging

ACCESS_FMT = '%s - "%s %s HTTP/%s" %d'
SECRET_TOKEN = "supersecrettokenvalue123"
SECRET_SK = "sk-abcdef1234567890"


def _record(path, status=200):
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg=ACCESS_FMT,
        args=("127.0.0.1:51234", "GET", path, "1.1", status),
        exc_info=None,
    )


def _apply_logger_filters(record):
    return logging.getLogger("uvicorn.access").filter(record)


def test_filter_is_installed_on_uvicorn_access(app_main):
    flt = [f for f in logging.getLogger("uvicorn.access").filters
           if isinstance(f, app_main.AccessLogScrubFilter)]
    assert len(flt) == 1


def test_install_is_idempotent(app_main):
    app_main._install_access_log_scrub()
    app_main._install_access_log_scrub()
    flt = [f for f in logging.getLogger("uvicorn.access").filters
           if isinstance(f, app_main.AccessLogScrubFilter)]
    assert len(flt) == 1


def test_query_token_is_scrubbed_from_message(app_main):
    rec = _record(f"/v1/models?token={SECRET_TOKEN}&x=1")
    assert _apply_logger_filters(rec)
    msg = rec.getMessage()
    assert SECRET_TOKEN not in msg
    assert "token=****" in msg


def test_sk_key_in_path_is_scrubbed(app_main):
    rec = _record(f"/admin/{SECRET_SK}/status")
    assert _apply_logger_filters(rec)
    assert SECRET_SK not in rec.getMessage()


def test_int_status_is_preserved_so_percent_d_still_formats(app_main):
    rec = _record(f"/v1/models?token={SECRET_TOKEN}", status=401)
    assert _apply_logger_filters(rec)
    assert rec.args[-1] == 401 and isinstance(rec.args[-1], int)
    assert rec.getMessage().endswith(" 401")


def test_clean_path_is_untouched(app_main):
    rec = _record("/v1/models")
    assert _apply_logger_filters(rec)
    assert rec.getMessage() == '127.0.0.1:51234 - "GET /v1/models HTTP/1.1" 200'


def test_record_without_args_does_not_crash(app_main):
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0, "plain line", None, None)
    assert _apply_logger_filters(rec)
    assert rec.getMessage() == "plain line"
