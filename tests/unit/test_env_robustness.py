"""Settings must survive what Windows editors and a shared .env do to the file."""

import logging

import pytest

import app.config as config
from app.config import Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("PORT", "LOG_LEVEL", "API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_settings_load_an_env_file_that_starts_with_a_utf8_bom(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(b"\xef\xbb\xbfPORT=6001\nLOG_LEVEL=debug\n")

    s = Settings(_env_file=env)

    assert s.port == 6001
    assert s.log_level == "debug"


def test_settings_ignore_unknown_keys_in_the_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(b"PORT=6002\nSINGLE_RUN=true\nSOMETHING_ELSE=1\n")

    s = Settings(_env_file=env)  # the refresher shares this file; it must not crash the gateway

    assert s.port == 6002


def test_unknown_keys_produce_exactly_one_warning_listing_names_only(tmp_path, caplog):
    env = tmp_path / ".env"
    env.write_bytes(
        b"# comment\nPORT=6003\nport=1\nSINGLE_RUN=top-secret-value\nexport REFRESH_TOKEN_X=abc\nNOT A KEY\n"
    )

    with caplog.at_level(logging.WARNING, logger=config.__name__):
        unknown = config.warn_unknown_env_keys(env)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert unknown == ["REFRESH_TOKEN_X", "SINGLE_RUN"]
    assert "SINGLE_RUN" in message and "REFRESH_TOKEN_X" in message
    assert "PORT" not in message  # known field names (any case) are not reported
    assert "top-secret-value" not in message and "abc" not in message


def test_no_warning_when_every_key_is_a_known_field(tmp_path, caplog):
    env = tmp_path / ".env"
    env.write_bytes(b"\xef\xbb\xbfPORT=6004\nAPI_KEY=sk-x\n")

    with caplog.at_level(logging.WARNING, logger=config.__name__):
        assert config.warn_unknown_env_keys(env) == []

    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_missing_or_undecodable_env_file_is_not_an_error(tmp_path, caplog):
    assert config.warn_unknown_env_keys(tmp_path / "absent.env") == []
    bad = tmp_path / "bad.env"
    bad.write_bytes(b"\xff\xfe\x00 not utf-8")
    assert config.warn_unknown_env_keys(bad) == []
