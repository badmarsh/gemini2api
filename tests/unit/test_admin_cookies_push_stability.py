"""A cookie push from the refresher must never destabilise live chat traffic.

Observed live (2026-10-17, proxy log): every refresher cycle PUT /admin/accounts/{id}/cookies for
all four accounts, even though the cookie pair had not changed. Each PUT ran ``reload_cookies``
without taking the client's ``_heal_lock``, so a request that reached ``generate()`` while the
reload had the client marked unhealthy started a *second*, concurrent ``reload_cookies`` (the
hazard the pool's M3 note describes) and the chat came back 502 a few milliseconds after the PUT
finished.

Two guarantees pinned here:
1. a push that carries the pair the client already uses is a no-op (no reload, no unhealthy window);
2. a real reload is serialised with the request-triggered self-heal, so a concurrent request waits
   for it instead of racing it.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.account_pool import Account
from app.core.gemini_client import GeminiWebClient
from app.routers.admin import UpdateCookiesRequest, update_account_cookies

PSID = "p" * 24
TS_OLD = "t" * 24
TS_NEW = "n" * 24


@pytest.fixture
def client(monkeypatch, tmp_path):
    # The cookie jar persists under the relative path data/cookies; keep it in tmp.
    monkeypatch.chdir(tmp_path)
    c = GeminiWebClient(psid=PSID, psidts=TS_OLD)
    c._healthy = True
    return c


def _pool_with(account):
    pool = MagicMock()
    pool.accounts = [account]
    pool._save_to_file = MagicMock()
    return pool


def _account(client, account_id="acc-stable"):
    account = Account(id=account_id, psid=PSID, psidts=TS_OLD)
    account.client = client
    return account


@pytest.mark.asyncio
async def test_unchanged_pair_on_a_healthy_client_does_not_reload(client, monkeypatch):
    reload = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(client, "reload_cookies", reload)
    account = _account(client)

    with patch("app.routers.admin.account_pool", _pool_with(account)) as pool:
        resp = await update_account_cookies(
            "acc-stable", UpdateCookiesRequest(psid=PSID, psidts=TS_OLD)
        )

    assert resp["status"] == "ok"
    assert resp["changed"] is False
    reload.assert_not_called()
    pool._save_to_file.assert_not_called()


@pytest.mark.asyncio
async def test_pair_already_rotated_into_the_jar_is_not_pushed_again(client, monkeypatch):
    """The pool's own jar tracks Set-Cookie rotation and can be newer than accounts.json."""
    client._cookie_jar.set("__Secure-1PSID", PSID)
    client._cookie_jar.set("__Secure-1PSIDTS", TS_NEW)
    reload = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(client, "reload_cookies", reload)
    account = _account(client)

    with patch("app.routers.admin.account_pool", _pool_with(account)):
        resp = await update_account_cookies(
            "acc-stable", UpdateCookiesRequest(psid=PSID, psidts=TS_NEW)
        )

    assert resp["changed"] is False
    reload.assert_not_called()


@pytest.mark.asyncio
async def test_a_changed_pair_still_reloads(client, monkeypatch):
    reload = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(client, "reload_cookies", reload)
    account = _account(client)

    with patch("app.routers.admin.account_pool", _pool_with(account)) as pool:
        resp = await update_account_cookies(
            "acc-stable", UpdateCookiesRequest(psid=PSID, psidts=TS_NEW)
        )

    assert resp == {"status": "ok", "message": "Account acc-stable cookies updated"}
    reload.assert_awaited_once()
    assert account.psidts == TS_NEW
    pool._save_to_file.assert_called_once()


@pytest.mark.asyncio
async def test_an_unhealthy_client_is_reloaded_even_with_the_same_pair(client, monkeypatch):
    client._healthy = False
    reload = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(client, "reload_cookies", reload)
    account = _account(client)

    with patch("app.routers.admin.account_pool", _pool_with(account)):
        await update_account_cookies("acc-stable", UpdateCookiesRequest(psid=PSID, psidts=TS_OLD))

    reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_push_is_serialised_with_the_request_self_heal(client, monkeypatch):
    """RED before the fix: the request's own self-heal started a second concurrent reload."""
    running = 0
    peak = 0
    reloads = 0
    gate = asyncio.Event()

    async def slow_reload(psid=None, psidts=None):
        nonlocal running, peak, reloads
        reloads += 1
        running += 1
        peak = max(peak, running)
        client._healthy = False  # what the real reload does before it touches the network
        await gate.wait()
        client._healthy = True
        running -= 1
        return {"success": True}

    async def fake_send_request(*args, **kwargs):
        return {"text": "pong"}

    monkeypatch.setattr(client, "reload_cookies", slow_reload)
    monkeypatch.setattr(client, "_send_request", fake_send_request)
    account = _account(client)

    with patch("app.routers.admin.account_pool", _pool_with(account)):
        push = asyncio.create_task(
            update_account_cookies("acc-stable", UpdateCookiesRequest(psid=PSID, psidts=TS_NEW))
        )
        for _ in range(5):
            await asyncio.sleep(0)  # the push is now mid-reload with the client unhealthy
        assert client._healthy is False
        request = asyncio.create_task(client.generate("hi", "gemini-flash"))
        for _ in range(5):
            await asyncio.sleep(0)
        gate.set()
        push_result, request_result = await asyncio.gather(push, request)

    assert push_result["status"] == "ok"
    assert request_result == {"text": "pong"}
    assert peak == 1, "two reloads ran concurrently on one client"
    assert reloads == 1, "the request must wait for the push's reload, not start its own"
