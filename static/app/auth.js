/**
 * Authentication module - Secure token management and timeout-guarded API call wrappers
 */

import { APP_CONSTANTS } from './config/constants.js';
import { safeStorage } from './services/storageService.js';

const TOKEN_KEY = APP_CONSTANTS.STORAGE_KEYS.TOKEN;
const SESSION_TIMEOUT = APP_CONSTANTS.TIMEOUTS.SESSION_IDLE;
let _sessionTimer = null;

function _resetSessionTimer() {
    if (_sessionTimer) clearTimeout(_sessionTimer);
    _sessionTimer = setTimeout(() => {
        if (isAuthenticated()) {
            clearToken();
            window.location.href = '/login.html';
        }
    }, SESSION_TIMEOUT);
}

function _initSessionWatcher() {
    const events = ['mousedown', 'mousemove', 'keydown', 'scroll', 'touchstart', 'click'];
    events.forEach(evt => {
        document.addEventListener(evt, _resetSessionTimer, { passive: true });
    });
    _resetSessionTimer();
}

/**
 * Get stored token from safe storage
 * @returns {string|null}
 */
function getToken() {
    return safeStorage.getSecureItem(TOKEN_KEY);
}

/**
 * Check if authenticated
 * @returns {boolean}
 */
function isAuthenticated() {
    return !!getToken();
}

/**
 * Save token to safe storage with boundary encoding
 * @param {string} token
 */
function saveToken(token) {
    if (token) {
        safeStorage.setSecureItem(TOKEN_KEY, token);
    } else {
        clearToken();
    }
}

/**
 * Clear token
 */
function clearToken() {
    safeStorage.removeItem(TOKEN_KEY);
}

/**
 * Logout
 */
function logout() {
    clearToken();
    window.location.href = '/login.html';
}

/**
 * Sanitizes an error message by stripping potential secrets or auth headers
 */
function sanitizeErrorMessage(msg) {
    if (!msg) return 'Unknown error';
    return String(msg)
        .replace(/Bearer\s+[A-Za-z0-9_\-\.]+/gi, 'Bearer [REDACTED]')
        .replace(/(__Secure-1PSID[A-Za-z0-9_\-]*=)[^;]+/gi, '$1[REDACTED]')
        .replace(/(sk-[A-Za-z0-9]{6})[A-Za-z0-9]+/gi, '$1****');
}

/**
 * Common API request method with AbortController, timeout bounds, and credential protection
 * @param {string} method - HTTP method
 * @param {string} path - API endpoint path
 * @param {Object} body - Request body
 * @param {Object} options - { timeout, signal }
 * @returns {Promise<any>}
 */
async function apiCall(method, path, body = null, options = {}) {
    const token = getToken();
    const headers = {
        'Content-Type': 'application/json'
    };

    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    const timeout = options.timeout || APP_CONSTANTS.TIMEOUTS.API_DEFAULT;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => {
        controller.abort(new Error(`API request timed out after ${timeout / 1000}s`));
    }, timeout);

    // If caller provided an external abort signal, chain it
    if (options.signal) {
        if (options.signal.aborted) {
            clearTimeout(timeoutId);
            throw new Error('Request was aborted');
        }
        options.signal.addEventListener('abort', () => controller.abort(options.signal.reason), { once: true });
    }

    const config = {
        method,
        headers,
        signal: controller.signal
    };

    if (body && (method === 'POST' || method === 'PUT' || method === 'PATCH')) {
        config.body = JSON.stringify(body);
    }

    try {
        const response = await fetch(path, config);
        clearTimeout(timeoutId);

        // Redirect to login if 401 Unauthorized
        if (response.status === 401) {
            clearToken();
            window.location.href = '/login.html';
            throw new Error('Unauthorized');
        }

        const contentType = response.headers.get('content-type');
        let data;

        if (contentType && contentType.includes('application/json')) {
            data = await response.json();
        } else {
            data = await response.text();
        }

        if (!response.ok) {
            const rawMsg = (data && typeof data === 'object' && data.error && data.error.message)
                || (data && typeof data === 'object' && data.detail)
                || (data && typeof data === 'object' && data.message)
                || `Request failed (status: ${response.status})`;
            throw new Error(sanitizeErrorMessage(rawMsg));
        }

        return data;
    } catch (error) {
        clearTimeout(timeoutId);
        const cleanMessage = sanitizeErrorMessage(error.message);
        const sanitizedErr = new Error(cleanMessage);
        sanitizedErr.name = error.name;
        throw sanitizedErr;
    }
}

/**
 * Initialize auth verification
 * @returns {Promise<boolean>}
 */
async function initAuth() {
    if (!isAuthenticated()) {
        window.location.href = '/login.html';
        return false;
    }

    try {
        await apiCall('GET', APP_CONSTANTS.ROUTES.ADMIN_VERIFY, null, { timeout: APP_CONSTANTS.TIMEOUTS.HEALTH_CHECK });
        _initSessionWatcher();
        return true;
    } catch (error) {
        clearToken();
        window.location.href = '/login.html';
        return false;
    }
}

/**
 * Login function (used by login.html)
 * @param {string} apiKey - API key
 * @returns {Promise<{success: boolean, message?: string}>}
 */
async function login(apiKey) {
    try {
        saveToken(apiKey);
        // /health is public and accepts any key; verify against an authenticated admin route instead.
        await apiCall('GET', APP_CONSTANTS.ROUTES.ADMIN_VERIFY, null, { timeout: APP_CONSTANTS.TIMEOUTS.HEALTH_CHECK });
        return { success: true };
    } catch (error) {
        clearToken();
        return { success: false, message: sanitizeErrorMessage(error.message) || 'Login failed, please check your API key' };
    }
}

// Export functions
export {
    TOKEN_KEY,
    getToken,
    isAuthenticated,
    saveToken,
    clearToken,
    logout,
    apiCall,
    initAuth,
    login,
    sanitizeErrorMessage
};
