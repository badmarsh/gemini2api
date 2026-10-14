/**
 * Account Service
 * Domain service for managing Gemini web accounts, cookie updates, and health status.
 */

import { apiCall } from '../auth.js';
import { APP_CONSTANTS } from '../config/constants.js';
import { validateAccountPayload } from './validationService.js';

export class AccountService {
    /**
     * Fetch list of all managed accounts (credentials masked at boundary)
     */
    async listAccounts(signal = null) {
        return await apiCall('GET', APP_CONSTANTS.ROUTES.ADMIN_ACCOUNTS, null, {
            signal,
            timeout: APP_CONSTANTS.TIMEOUTS.API_DEFAULT
        });
    }

    /**
     * Add a new account to the pool
     */
    async addAccount({ psid, psidts, label }, signal = null) {
        const validated = validateAccountPayload({ psid, psidts, label });
        return await apiCall('POST', APP_CONSTANTS.ROUTES.ADMIN_ACCOUNTS, validated, {
            signal,
            timeout: APP_CONSTANTS.TIMEOUTS.API_DEFAULT
        });
    }

    /**
     * Remove an account by ID
     */
    async removeAccount(accountId, signal = null) {
        if (!accountId) throw new Error('Account ID is required');
        return await apiCall('DELETE', `${APP_CONSTANTS.ROUTES.ADMIN_ACCOUNTS}/${encodeURIComponent(accountId)}`, null, {
            signal,
            timeout: APP_CONSTANTS.TIMEOUTS.API_DEFAULT
        });
    }

    /**
     * Run a live health check on a specific account
     */
    async checkAccount(accountId, signal = null) {
        if (!accountId) throw new Error('Account ID is required');
        return await apiCall('GET', `${APP_CONSTANTS.ROUTES.ADMIN_ACCOUNTS}/${encodeURIComponent(accountId)}/check`, null, {
            signal,
            timeout: APP_CONSTANTS.TIMEOUTS.HEALTH_CHECK
        });
    }

    /**
     * Run health check on all accounts in parallel
     */
    async checkAllAccounts(signal = null) {
        return await apiCall('GET', '/admin/check-account', null, {
            signal,
            timeout: APP_CONSTANTS.TIMEOUTS.HEALTH_CHECK * 2
        });
    }

    /**
     * Update cookies for an existing account
     */
    async updateAccountCookies(accountId, { psid, psidts }, signal = null) {
        if (!accountId) throw new Error('Account ID is required');
        const validated = validateAccountPayload({ psid, psidts, label: 'cookie-update' });
        return await apiCall('PUT', `${APP_CONSTANTS.ROUTES.ADMIN_ACCOUNTS}/${encodeURIComponent(accountId)}/cookies`, {
            psid: validated.psid,
            psidts: validated.psidts
        }, {
            signal,
            timeout: APP_CONSTANTS.TIMEOUTS.API_DEFAULT
        });
    }

    /**
     * Trigger cookie reload from environment or payload
     */
    async reloadCookies(cookies = null, signal = null) {
        const payload = cookies ? {
            psid: cookies.psid ? String(cookies.psid).trim() : null,
            psidts: cookies.psidts ? String(cookies.psidts).trim() : null
        } : null;

        return await apiCall('POST', '/admin/reload-cookies', payload, {
            signal,
            timeout: APP_CONSTANTS.TIMEOUTS.API_DEFAULT
        });
    }
}

export const accountService = new AccountService();
