"""The refresher must not report a signed-out Gemini page as a successful cookie refresh."""

from pathlib import Path

_SRC = (Path(__file__).resolve().parents[2] / "refresher" / "refresher.py").read_text(encoding="utf-8")


def test_refresh_account_checks_for_the_signed_in_marker_before_reporting_success():
    body = _SRC[_SRC.index("def refresh_account("):_SRC.index("def notify_gemini2api(")]
    marker = body.index('"SNlM0e"')
    success = body.index('"status": "active"')
    assert marker < success, "signed-in check must run before the success result"
    assert '"status": "signed_out"' in body


def test_only_active_results_are_pushed_to_the_server():
    body = _SRC[_SRC.index("def refresh_all("):]
    assert 'r.get("status") == "active"' in body
    assert "notify_gemini2api(acc[" in body
