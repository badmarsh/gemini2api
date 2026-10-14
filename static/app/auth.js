/**
 * Authentication module - Token management and API call wrappers
 */

const TOKEN_KEY = 'gemini2api_token';
const SESSION_TIMEOUT = 5 * 60 * 1000; // 5 minutes
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
 * Get stored token
 * @returns {string|null}
 */
function getToken() {
    return localStorage.getItem(TOKEN_KEY);
}

/**
 * Check if authenticated
 * @returns {boolean}
 */
function isAuthenticated() {
    return !!getToken();
}

/**
 * Save token to local storage
 * @param {string} token
 */
function saveToken(token) {
    localStorage.setItem(TOKEN_KEY, token);
}

/**
 * Clear token
 */
function clearToken() {
    localStorage.removeItem(TOKEN_KEY);
}

/**
 * Logout
 */
function logout() {
    clearToken();
    window.location.href = '/login.html';
}

/**
 * Common API request method
 * @param {string} method - HTTP method
 * @param {string} path - API endpoint path
 * @param {Object} body - Request body
 * @returns {Promise<any>}
 */
async function apiCall(method, path, body = null) {
    const token = getToken();
    const headers = {
        'Content-Type': 'application/json'
    };

    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    const config = {
        method,
        headers
    };

    if (body && (method === 'POST' || method === 'PUT' || method === 'PATCH')) {
        config.body = JSON.stringify(body);
    }

    try {
        const response = await fetch(path, config);

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
            const errorMessage = (data && typeof data === 'object' && data.error && data.error.message)
                || (data && typeof data === 'object' && data.detail)
                || (data && typeof data === 'object' && data.message)
                || `Request failed (status: ${response.status})`;
            throw new Error(errorMessage);
        }

        return data;
    } catch (error) {
        console.error('API request error:', error);
        throw error;
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
        await apiCall('GET', '/admin/verify');
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
        await apiCall('GET', '/health');
        return { success: true };
    } catch (error) {
        clearToken();
        console.error('Login error:', error);
        return { success: false, message: error.message || 'Login failed, please check your API key' };
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
    login
};

console.log('Auth module loaded');
