import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.routers.admin import update_account_cookies, UpdateCookiesRequest
from app.core.account_pool import Account, AccountStatus


@pytest.mark.asyncio
async def test_update_account_cookies_success():
    account = Account(id="acc-test", psid="old-psid", psidts="old-psidts")
    account.client = MagicMock()
    account.client.reload_cookies = AsyncMock(return_value={"success": True})

    with patch("app.routers.admin.account_pool") as mock_pool:
        mock_pool.accounts = [account]
        mock_pool._save_to_file = MagicMock()

        req = UpdateCookiesRequest(psid="new-psid", psidts="new-psidts")
        resp = await update_account_cookies("acc-test", req)

        assert resp == {"status": "ok", "message": "Account acc-test cookies updated"}
        assert account.psid == "new-psid"
        assert account.psidts == "new-psidts"
        assert account.status == AccountStatus.ACTIVE
        mock_pool._save_to_file.assert_called_once()


@pytest.mark.asyncio
async def test_update_account_cookies_failure_persists_when_persist_true():
    account = Account(id="acc-test-fail", psid="old-psid", psidts="old-psidts")
    account.client = MagicMock()
    account.client.reload_cookies = AsyncMock(return_value={"success": False, "error": "rate limit"})
    account.client.is_healthy = False  # nothing working to protect: keep what the caller submitted

    with patch("app.routers.admin.account_pool") as mock_pool:
        mock_pool.accounts = [account]
        mock_pool._save_to_file = MagicMock()

        req = UpdateCookiesRequest(psid="new-psid", psidts="new-psidts", persist=True)
        resp = await update_account_cookies("acc-test-fail", req)

        assert resp.status_code == 503
        assert account.psid == "new-psid"
        assert account.psidts == "new-psidts"
        assert account.last_error == "rate limit"
        mock_pool._save_to_file.assert_called_once()


@pytest.mark.asyncio
async def test_update_account_cookies_failure_does_not_persist_when_persist_false():
    account = Account(id="acc-test-no-persist", psid="old-psid", psidts="old-psidts")
    account.client = MagicMock()
    account.client.reload_cookies = AsyncMock(return_value={"success": False, "error": "invalid cookie"})

    with patch("app.routers.admin.account_pool") as mock_pool:
        mock_pool.accounts = [account]
        mock_pool._save_to_file = MagicMock()

        req = UpdateCookiesRequest(psid="new-psid", psidts="new-psidts", persist=False)
        resp = await update_account_cookies("acc-test-no-persist", req)

        assert resp.status_code == 503
        assert account.psid == "old-psid"
        assert account.psidts == "old-psidts"
        mock_pool._save_to_file.assert_not_called()


@pytest.mark.asyncio
async def test_failed_reload_keeps_stored_cookies_when_the_old_session_still_works():
    """reload_cookies rolls back to the previous working session on failure; the router must not
    then overwrite the stored (working) cookies or last_error with the rejected pair."""
    account = Account(id="acc-healthy", psid="old-psid", psidts="old-psidts")
    account.client = MagicMock()
    account.client.is_healthy = True
    account.client.reload_cookies = AsyncMock(return_value={"success": False, "error": "SNlM0e token not found"})

    with patch("app.routers.admin.account_pool") as mock_pool:
        mock_pool.accounts = [account]
        mock_pool._save_to_file = MagicMock()

        req = UpdateCookiesRequest(psid="new-psid", psidts="new-psidts", persist=True)
        resp = await update_account_cookies("acc-healthy", req)

        assert resp.status_code == 503
        assert b"working session was kept" in resp.body
        assert account.psid == "old-psid" and account.psidts == "old-psidts"
        assert account.last_error == ""
        mock_pool._save_to_file.assert_not_called()


class _FakeClient:
    """Stand-in for GeminiWebClient that records how the router built it."""

    created: list["_FakeClient"] = []

    def __init__(self, psid=None, psidts=None):
        self.psid = psid
        self.psidts = psidts
        _FakeClient.created.append(self)

    async def reload_cookies(self, psid=None, psidts=None):
        return {"success": True}


@pytest.mark.asyncio
async def test_update_account_cookies_creates_client_when_missing(monkeypatch):
    """Regression: GeminiWebClient was never imported in admin.py (NameError -> HTTP 500)."""
    from app.core.account_pool import account_pool

    _FakeClient.created.clear()
    account = Account(id="acc-no-client", psid="old-psid", psidts="old-psidts")
    assert account.client is None

    monkeypatch.setattr("app.routers.admin.GeminiWebClient", _FakeClient)
    # `accounts` is a read-only property; replace the backing list instead.
    monkeypatch.setattr(account_pool, "_accounts", [account])
    save = MagicMock()
    monkeypatch.setattr(account_pool, "_save_to_file", save)

    req = UpdateCookiesRequest(psid="new-psid", psidts="new-psidts")
    resp = await update_account_cookies("acc-no-client", req)

    assert resp == {"status": "ok", "message": "Account acc-no-client cookies updated"}
    assert len(_FakeClient.created) == 1
    assert _FakeClient.created[0].psid == "new-psid"
    assert _FakeClient.created[0].psidts == "new-psidts"
    assert account.client is _FakeClient.created[0]
    assert account.status == AccountStatus.ACTIVE
    save.assert_called_once()


@pytest.mark.asyncio
async def test_reload_cookies_works_on_client_never_initialized(monkeypatch, tmp_path):
    """The router builds a bare client and calls reload_cookies() without initialize()."""
    from app.core.gemini_client import GeminiWebClient

    # The cookie jar stores files under the relative path data/cookies; keep it in tmp.
    monkeypatch.chdir(tmp_path)
    client = GeminiWebClient(psid="p" * 24, psidts="t" * 24)
    assert client._http is None

    async def fake_obtain():
        client._session_token = "token"

    async def fake_heartbeat():
        return None

    monkeypatch.setattr(client, "_obtain_session_token", fake_obtain)
    monkeypatch.setattr(client, "_send_heartbeat", fake_heartbeat)
    monkeypatch.setattr(client, "_ensure_refresh_task", lambda: None)

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def close(self):
            return None

    # Avoid creating a real curl_cffi session (it spawns a selector thread on Windows).
    monkeypatch.setattr("app.core.gemini_client.AsyncSession", _FakeSession)

    result = await client.reload_cookies(psid="n" * 24, psidts="m" * 24)

    assert result == {"success": True}
    assert client.is_healthy
    assert isinstance(client._http, _FakeSession)
