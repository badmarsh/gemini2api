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
