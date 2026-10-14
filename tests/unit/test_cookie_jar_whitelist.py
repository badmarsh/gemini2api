"""PERSISTED_COOKIE_NAMES: with the whitelist enabled, telemetry cookies are discarded.

Replaying volatile session cookies (NID, AEC, SIDCC, OTZ ...) across processes makes
Google see an inconsistent session context (StreamGenerate stalls after 168 bytes), so
the jar must keep only the two auth cookies. Every other cookie-jar test disables the
whitelist (PERSISTED_COOKIE_NAMES = None), which left the enabled path unexercised.
"""
import hashlib
import json

import pytest

from app.core.fingerprint import cookie_jar as cj_mod
from app.core.fingerprint.cookie_jar import PersistentCookieJar

curl_cookies = pytest.importorskip(
    "curl_cffi.requests.cookies", reason="curl_cffi not installed (runtime dep)"
)

AUTH = {"__Secure-1PSID", "__Secure-1PSIDTS"}
TELEMETRY = ("NID", "AEC", "SIDCC", "OTZ", "__Secure-ENID")


class _FakeResponse:
    def __init__(self, cookies, headers=None):
        self.cookies = cookies
        self.headers = headers or {}
        self.text = ""
        self.status_code = 200


class _HeadersWithList(dict):
    def __init__(self, set_cookie_values):
        super().__init__({"set-cookie": set_cookie_values[0]})
        self._sc = list(set_cookie_values)

    def get_list(self, key):
        return list(self._sc) if key.lower() == "set-cookie" else []


def _curl_cookies(entries):
    c = curl_cookies.Cookies()
    for name, value in entries:
        c.set(name, value, ".google.com", secure=name.startswith("__Secure"))
    return c


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Isolated cookie dir; the whitelist is left at the module's real default."""
    monkeypatch.setattr(cj_mod, "COOKIE_STORE_DIR", tmp_path / "cookies")
    return tmp_path / "cookies"


def _on_disk_names(jar):
    return {c["name"] for c in json.loads(jar._store_path().read_text(encoding="utf-8"))}


def test_default_whitelist_is_exactly_the_two_auth_cookies():
    assert cj_mod.PERSISTED_COOKIE_NAMES == AUTH


def test_response_cookies_telemetry_is_discarded(store):
    jar = PersistentCookieJar("acct-whitelist")
    resp = _FakeResponse(_curl_cookies(
        [("__Secure-1PSID", "psid-v"), ("__Secure-1PSIDTS", "ts-v")]
        + [(n, "telemetry-v") for n in TELEMETRY]
    ))

    jar.update_from_response(resp)

    assert set(jar.cookie_names()) == AUTH
    assert set(jar.get_all()) == AUTH
    for name in TELEMETRY:
        assert jar.get(name) is None
    assert _on_disk_names(jar) == AUTH


def test_set_cookie_header_telemetry_is_discarded(store):
    jar = PersistentCookieJar("acct-whitelist-hdr")
    headers = _HeadersWithList([
        "NID=511=abc; Domain=.google.com; Path=/; HttpOnly",
        "AEC=xyz; Domain=.google.com; Path=/; Secure; HttpOnly",
        "__Secure-1PSIDTS=rotated-ts; Domain=.google.com; Path=/; Secure; HttpOnly",
    ])

    jar.update_from_response(_FakeResponse({}, headers))

    assert jar.cookie_names() == ["__Secure-1PSIDTS"]
    assert jar.get("__Secure-1PSIDTS") == "rotated-ts", "PSIDTS rotation must still persist"
    assert _on_disk_names(jar) == {"__Secure-1PSIDTS"}


def test_legacy_jar_on_disk_drops_telemetry_on_load(store):
    account = "acct-legacy"
    store.mkdir(parents=True)
    path = store / f"{hashlib.sha256(account.encode()).hexdigest()[:16]}.json"
    path.write_text(json.dumps([
        {"name": "__Secure-1PSID", "value": "psid-v", "domain": ".google.com"},
        {"name": "NID", "value": "old-nid", "domain": ".google.com"},
        {"name": "SIDCC", "value": "old-sidcc", "domain": ".google.com"},
    ]), encoding="utf-8")

    jar = PersistentCookieJar(account)

    assert jar.cookie_names() == ["__Secure-1PSID"]
    assert jar.get("NID") is None


def test_control_telemetry_is_stored_when_whitelist_disabled(store, monkeypatch):
    """Guards the tests above against passing vacuously."""
    monkeypatch.setattr(cj_mod, "PERSISTED_COOKIE_NAMES", None)
    jar = PersistentCookieJar("acct-control")

    jar.update_from_response(_FakeResponse(_curl_cookies([("NID", "nid-v"), ("AEC", "aec-v")])))

    assert {"NID", "AEC"} <= set(jar.cookie_names())
