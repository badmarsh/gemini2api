"""issue #10-A：同名 Cookie 跨域并存不得再打断 token 获取。

复现的真实报错（报告人日志逐字）：
    [ERROR] Token extraction failed: Multiple cookies exist with name=NID on
            www.google.com and .google.com.hk, add domain parameter to suppress this error.
    [ERROR] Auto-refresh: token fetch failed, client unhealthy

根因是 ``PersistentCookieJar.update_from_response`` 里的 ``response.cookies.items()``：
curl_cffi 的 ``Cookies`` 经 MutableMapping 走 ``get(name)``，同名 Cookie 跨域并存时抛
``CookieConflict``。Google 把用户跳到国家域名（.google.com.hk/.com.tw/.co.jp …）时必现，
异常一路冒到 ``_obtain_session_token`` → token 置空 → 账号 unhealthy。
"""

import asyncio

import pytest

from app.core.fingerprint import cookie_jar as cj_mod
from app.core.fingerprint.cookie_jar import PersistentCookieJar

curl_cookies = pytest.importorskip(
    "curl_cffi.requests.cookies", reason="curl_cffi not installed (runtime dep)"
)


class _FakeResponse:
    """最小响应替身：只暴露 update_from_response 真正读的 cookies / headers。"""

    def __init__(self, cookies, headers=None, text="", status_code=200):
        self.cookies = cookies
        self.headers = headers or {}
        self.text = text
        self.status_code = status_code


class _HeadersWithList(dict):
    """模拟 curl_cffi/httpx 的多值头容器（Set-Cookie 可重复）。"""

    def __init__(self, set_cookie_values):
        super().__init__({"set-cookie": set_cookie_values[0] if set_cookie_values else ""})
        self._sc = list(set_cookie_values)

    def get_list(self, key):
        return list(self._sc) if key.lower() == "set-cookie" else []


def _cookies(*entries):
    """entries: (name, value, domain) —— 按给定顺序塞进真实 curl_cffi Cookies。"""
    c = curl_cookies.Cookies()
    for name, value, domain in entries:
        c.set(name, value, domain, secure=name.startswith("__Secure"))
    return c


@pytest.fixture
def jar(tmp_path, monkeypatch):
    monkeypatch.setattr(cj_mod, "COOKIE_STORE_DIR", tmp_path / "cookies")
    monkeypatch.setattr(cj_mod, "PERSISTED_COOKIE_NAMES", None)
    return PersistentCookieJar("acct-issue10")


# ---------------------------------------------------------------------------
# 1. 冲突场景：不抛异常，且取到规范域的值
# ---------------------------------------------------------------------------

def test_curl_cffi_items_really_raises_on_cross_domain_duplicate():
    """先钉死前提：这不是臆想的 bug，curl_cffi 的 items() 确实会炸。"""
    c = _cookies(("NID", "v-www", "www.google.com"), ("NID", "v-hk", ".google.com.hk"))
    with pytest.raises(Exception) as exc:
        list(c.items())
    assert "Multiple cookies exist with name=NID" in str(exc.value)


def test_cross_domain_duplicate_does_not_raise_and_picks_canonical_domain(jar):
    resp = _FakeResponse(_cookies(
        ("NID", "v-www", "www.google.com"),
        ("NID", "v-hk", ".google.com.hk"),
        ("__Secure-1PSIDTS", "ts-new", ".google.com"),
    ))

    jar.update_from_response(resp)  # 修复前：CookieConflict 冒出去

    assert jar.get("NID") == "v-www", "必须取 domain 以 google.com 结尾的规范域"
    assert jar.get("__Secure-1PSIDTS") == "ts-new"


def test_tie_break_is_deterministic_regardless_of_iteration_order(jar, tmp_path, monkeypatch):
    """取舍只由域名决定：把两条 Cookie 的插入顺序对调，结果必须完全一致。
    如果实现是"最后一个赢"，这个用例会红。"""
    resp_a = _FakeResponse(_cookies(
        ("NID", "v-www", "www.google.com"), ("NID", "v-hk", ".google.com.hk")))
    resp_b = _FakeResponse(_cookies(
        ("NID", "v-hk", ".google.com.hk"), ("NID", "v-www", "www.google.com")))

    jar.update_from_response(resp_a)
    first = jar.get("NID")

    monkeypatch.setattr(cj_mod, "COOKIE_STORE_DIR", tmp_path / "cookies2")
    jar2 = PersistentCookieJar("acct-issue10-b")
    jar2.update_from_response(resp_b)
    second = jar2.get("NID")

    assert first == second == "v-www"


def test_three_country_domains_still_pick_the_canonical_one(jar):
    resp = _FakeResponse(_cookies(
        ("NID", "v-tw", ".google.com.tw"),
        ("NID", "v-jp", ".google.co.jp"),
        ("NID", "v-canonical", ".google.com"),
        ("NID", "v-hk", ".google.com.hk"),
    ))
    jar.update_from_response(resp)
    assert jar.get("NID") == "v-canonical"


def test_no_canonical_domain_falls_back_to_last(jar):
    resp = _FakeResponse(_cookies(
        ("NID", "v-tw", ".google.com.tw"), ("NID", "v-jp", ".google.co.jp")))
    jar.update_from_response(resp)
    assert jar.get("NID") == "v-jp"


# ---------------------------------------------------------------------------
# 2. 零回归：单域名 / 空值删除 / Set-Cookie 头解析
# ---------------------------------------------------------------------------

def test_single_domain_behaviour_unchanged(jar):
    resp = _FakeResponse(_cookies(
        ("__Secure-1PSID", "psid-v", ".google.com"),
        ("SIDCC", "sidcc-v", ".google.com"),
    ))
    jar.update_from_response(resp)
    assert jar.get("__Secure-1PSID") == "psid-v"
    assert jar.get("SIDCC") == "sidcc-v"
    assert jar.cookie_names() == ["SIDCC", "__Secure-1PSID"]


def test_empty_value_still_means_delete(jar):
    jar.set("SIDCC", "old-value")
    assert jar.get("SIDCC") == "old-value"

    jar.update_from_response(_FakeResponse(_cookies(("SIDCC", "", ".google.com"))))
    assert jar.get("SIDCC") is None, "空值仍须视为服务端删除指令"


def test_empty_value_delete_semantics_survive_a_conflict(jar):
    """冲突场景下删除语义也不能变形。"""
    jar.set("SIDCC", "old-value")
    jar.update_from_response(_FakeResponse(_cookies(
        ("NID", "v-www", "www.google.com"),
        ("NID", "v-hk", ".google.com.hk"),
        ("SIDCC", "", ".google.com"),
    )))
    assert jar.get("SIDCC") is None
    assert jar.get("NID") == "v-www"


def test_set_cookie_header_parsing_still_runs_under_conflict(jar):
    """异常曾经中断整个方法，后半段的 Set-Cookie 补充解析根本不执行。
    修复后：即使 cookies 容器里有跨域同名冲突，头解析仍必须生效。"""
    headers = _HeadersWithList([
        "__Secure-1PSIDTS=ts-from-header; Path=/; Secure; HttpOnly",
        "EXTRA=extra-v; Path=/",
    ])
    resp = _FakeResponse(
        _cookies(("NID", "v-www", "www.google.com"), ("NID", "v-hk", ".google.com.hk")),
        headers=headers,
    )

    jar.update_from_response(resp)

    assert jar.get("__Secure-1PSIDTS") == "ts-from-header"
    assert jar.get("EXTRA") == "extra-v"
    assert jar.get("NID") == "v-www"


def test_plain_dict_cookies_container_still_supported(jar):
    """非 curl_cffi 容器（普通 dict）走原 items() 路径，行为不变。"""
    jar.update_from_response(_FakeResponse({"SIDCC": "d1", "NID": "d2"}))
    assert jar.get("SIDCC") == "d1"
    assert jar.get("NID") == "d2"


# ---------------------------------------------------------------------------
# 3. 纵深防御：cookie 层任何异常都不得冒到调用方
# ---------------------------------------------------------------------------

def test_cookie_layer_exception_is_swallowed(jar, caplog):
    class _ExplodingJar:
        def __iter__(self):
            raise RuntimeError("boom from the cookie layer")

    class _ExplodingCookies:
        jar = _ExplodingJar()

    with caplog.at_level("WARNING"):
        jar.update_from_response(_FakeResponse(_ExplodingCookies()))  # 不得抛

    assert any("boom from the cookie layer" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 4. 端到端：_obtain_session_token / check_account 在冲突下仍拿到 token
# ---------------------------------------------------------------------------

_BODY = '{"SNlM0e":"TEST-TOKEN"}' + "x" * 2000


def _client_with_conflicting_cookies(monkeypatch, tmp_path):
    from app.core import gemini_client as gc

    monkeypatch.setattr(cj_mod, "COOKIE_STORE_DIR", tmp_path / "cookies")

    async def _no_jitter(*_a, **_k):
        return None

    monkeypatch.setattr(gc, "apply_jitter", _no_jitter)

    class _Session:
        def __init__(self, *a, **k):
            self.cookies = _cookies()

        async def get(self, *a, **k):
            return _FakeResponse(
                _cookies(("NID", "v-www", "www.google.com"),
                         ("NID", "v-hk", ".google.com.hk")),
                text=_BODY,
                status_code=200,
            )

        async def close(self):
            return None

    monkeypatch.setattr(gc, "AsyncSession", _Session)
    client = gc.GeminiWebClient(psid="psid-x", psidts="ts-x")
    client._cookie_jar = PersistentCookieJar("acct-e2e")
    client._http = _Session()
    client._current_target = ""

    async def _ensure(*_a, **_k):
        return None

    async def _heartbeat(*_a, **_k):
        return None

    monkeypatch.setattr(client, "_ensure_session_current", _ensure)
    monkeypatch.setattr(client, "_send_heartbeat", _heartbeat)
    return client


def test_obtain_session_token_survives_cross_domain_cookie_conflict(monkeypatch, tmp_path):
    client = _client_with_conflicting_cookies(monkeypatch, tmp_path)

    asyncio.run(client._obtain_session_token())

    assert client._session_token == "TEST-TOKEN", (
        "修复前这里是空串，_last_reload_error 为 'Token extraction failed: Multiple cookies exist...'"
    )
    assert client._last_reload_error == ""


def test_check_account_stays_healthy_under_cross_domain_cookie_conflict(monkeypatch, tmp_path):
    client = _client_with_conflicting_cookies(monkeypatch, tmp_path)

    result = asyncio.run(client.check_account())

    assert result["valid"] is True
    assert result.get("error") is None
    assert client.is_healthy is True
