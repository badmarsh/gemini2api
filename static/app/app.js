/**
 * Gemini2API Admin Panel - Main Application Entry
 */

import { initializeComponents } from './component-loader.js';
import { initThemeSwitcher } from './theme-switcher.js';
import { initAuth, apiCall, logout } from './auth.js';
import { showToast, formatNumber, getStatusBadge, maskString, copyToClipboard, showConfirm, formatDate } from './utils.js';
import { initUsageStats, loadUsageStats } from './usage-chart.js';
import { initLogs, loadLogs } from './logs.js';
import { initSettings, loadSettings } from './settings.js';
import { initApiKeys, loadApiKeys } from './api-keys.js';
import { initGems, loadAccountsThenGems } from './gems.js';
import { initI18n, t } from './i18n.js';
import { initLanguageSwitcher } from './language-switcher.js';
import { APP_CONSTANTS } from './config/constants.js';
import { safeStorage } from './services/storageService.js';
import { validateAccountPayload } from './services/validationService.js';
import { accountService } from './services/accountService.js';
import { telemetryService } from './services/telemetryService.js';

let isAppInitialized = false;

const elements = {
    navItems: null,
    sections: null
};

function initElements() {
    elements.navItems = document.querySelectorAll('.nav-item');
    elements.sections = document.querySelectorAll('.section');
}

function initNavigation() {
    if (!elements.navItems || !elements.sections) return;

    elements.navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const sectionId = item.dataset.section;
            activateSection(sectionId);
        });
    });

    window.addEventListener('hashchange', () => {
        const sectionId = window.location.hash.slice(1) || 'dashboard';
        activateSection(sectionId, false);
    });

    const initialSectionId = window.location.hash.slice(1) || 'dashboard';
    activateSection(initialSectionId, false);
}

function activateSection(sectionId, updateHash = true) {
    elements.navItems.forEach(nav => {
        nav.classList.remove('active');
        if (nav.dataset.section === sectionId) {
            nav.classList.add('active');
        }
    });

    elements.sections.forEach(section => {
        section.classList.remove('active');
        if (section.id === sectionId) {
            section.classList.add('active');
        }
    });

    if (updateHash) {
        window.location.hash = sectionId;
    }

    loadSectionData(sectionId);
    window.scrollTo(0, 0);
}

function renderSectionErrorBoundary(sectionId, error) {
    const sectionEl = document.getElementById(sectionId);
    if (!sectionEl) return;
    sectionEl.querySelectorAll('.section-error-boundary-banner').forEach(el => el.remove());

    const banner = document.createElement('div');
    banner.className = 'section-error-boundary-banner';
    banner.style.cssText = 'padding: 2rem; margin: 1.5rem 0; border-radius: 12px; background: rgba(239, 68, 68, 0.08); border: 1px solid var(--danger-color, #ef4444); color: var(--text-primary); text-align: center;';
    banner.innerHTML = `
        <i class="fas fa-exclamation-triangle" style="color: var(--danger-color, #ef4444); font-size: 2rem; margin-bottom: 0.75rem;" aria-hidden="true"></i>
        <h3 style="margin: 0.25rem 0 0.5rem; font-size: 1.125rem;">Section Failed to Load (${escapeHtml(sectionId)})</h3>
        <p style="font-size: 0.875rem; color: var(--text-secondary); margin: 0 0 1.25rem; max-width: 450px; display: inline-block;">${escapeHtml(error.message || 'An unexpected error occurred while loading this section. Other sections remain fully operational.')}</p>
        <div>
            <button class="btn btn-sm btn-primary retry-btn">
                <i class="fas fa-redo"></i> Retry Section
            </button>
        </div>
    `;
    banner.querySelector('.retry-btn')?.addEventListener('click', () => {
        banner.remove();
        loadSectionData(sectionId);
    });
    sectionEl.prepend(banner);
}

async function loadSectionData(sectionId) {
    const sectionEl = document.getElementById(sectionId);
    if (sectionEl) {
        sectionEl.querySelectorAll('.section-error-boundary-banner').forEach(el => el.remove());
    }
    try {
        switch (sectionId) {
            case 'dashboard':
                await loadDashboard();
                break;
            case 'accounts':
                await loadAccounts();
                break;
            case 'usage-stats':
                await loadUsageStats();
                break;
            case 'logs':
                await loadLogs();
                break;
            case 'settings':
                await loadSettings();
                break;
            case 'api-keys':
                await loadApiKeys();
                break;
            case 'gems':
                await loadAccountsThenGems();
                break;
        }
    } catch (error) {
        renderSectionErrorBoundary(sectionId, error);
    }
}

// ============================================================================
// Dashboard
// ============================================================================

// Pure: reduce the two settled dashboard requests to a plain view-model (never throws).
function settleDashboardResults(statusRes, sysRes) {
    const errorOf = (r) => (r.status === 'rejected')
        ? ((r.reason && r.reason.message) ? String(r.reason.message) : 'Unknown error')
        : '';
    return {
        status: statusRes.status === 'fulfilled' ? statusRes.value : null,
        statusError: errorOf(statusRes),
        sys: sysRes.status === 'fulfilled' ? sysRes.value : null,
        sysError: errorOf(sysRes),
    };
}

// Pure: should the periodic dashboard refresh run right now?
function shouldAutoRefresh(s) {
    if (!s) return false;
    return s.activeSection === 'dashboard' && !s.hidden && !s.inFlight
        && (s.now - s.lastLoadAt) >= s.intervalMs;
}

// Per-card error notice: shown when only one of the dashboard requests failed, so the other
// card group keeps working. An empty message removes the notice.
function setDashboardCardError(key, message) {
    const section = document.getElementById('dashboard');
    if (!section) return;
    const id = 'dashboard-card-error-' + key;
    const old = document.getElementById(id);
    if (old) old.remove();
    if (!message) return;
    const el = document.createElement('div');
    el.id = id;
    el.className = 'dashboard-card-error';
    el.setAttribute('role', 'alert');
    const text = document.createElement('span');
    text.textContent = t('dashboard.error.' + key) + ': ' + message;
    const btn = document.createElement('button');
    btn.className = 'btn btn-sm';
    btn.textContent = t('dashboard.retry');
    btn.addEventListener('click', () => loadSectionData('dashboard'));
    el.append(text, btn);
    section.prepend(el);
}

let _dashboardLoading = false;
let _dashboardLastLoadAt = 0;

async function loadDashboard(opts) {
    const background = !!(opts && opts.background === true);
    _dashboardLoading = true;
    try {
        // The requests are independent: run them in parallel, and let one failing leave the
        // other card group intact. QR cards are static, so a background refresh skips them.
        const qrPromise = background ? Promise.resolve() : loadQrCards();
        const [statusRes, sysRes] = await Promise.allSettled([
            telemetryService.getAdminStatus(),
            telemetryService.getSystemInfo(),
        ]);
        const view = settleDashboardResults(statusRes, sysRes);
        if (!view.status && !view.sys && !background) {
            // Nothing could be loaded: fall through to the section error boundary (with retry).
            throw new Error(view.statusError);
        }
        setDashboardCardError('status', view.statusError);
        setDashboardCardError('system', view.sysError);

        if (view.status) {
            const data = view.status;
            const accounts = data.accounts || [];

            const activeCount = accounts.filter(a => a.status === 'active').length;
            const totalRequests = accounts.reduce((sum, a) => sum + (a.request_count || 0), 0);
            const modelsSet = new Set();
            accounts.forEach(a => {
                if (a.models && Array.isArray(a.models)) {
                    a.models.forEach(m => modelsSet.add(m));
                }
            });

            setText('stat-total', formatNumber(accounts.length));
            setText('stat-active', formatNumber(activeCount));
            setText('stat-requests', formatNumber(totalRequests));
            setText('stat-models', formatNumber(modelsSet.size || accounts.reduce((s, a) => s + (a.models_count || 0), 0)));

            const strategyMap = { 'round-robin': t('settings.strategy.roundRobin'), 'least-used': t('settings.strategy.leastUsed'), 'failover': t('settings.strategy.failover') };
            setText('info-strategy', strategyMap[data.strategy] || data.strategy);
            setText('info-concurrent', data.max_concurrent_per_account || '-');
            setText('info-total-accounts', accounts.length);
            setText('info-active-accounts', activeCount);

            renderAccountStatusGrid(accounts);
            renderModelsList(modelsSet);
            updatePlaygroundModels(modelsSet);
        }
        if (view.sys) applySystemInfo(view.sys);
        await qrPromise;

        // Bind and auto-check update button (must be after DOM is loaded)
        const updateBtn = document.getElementById('checkUpdateBtn');
        if (updateBtn && !updateBtn._bound) {
            updateBtn._bound = true;
            updateBtn.addEventListener('click', handleCheckUpdate);
            checkForUpdate();
        }
    } finally {
        _dashboardLoading = false;
        _dashboardLastLoadAt = Date.now();
    }
}

const DASHBOARD_REFRESH_MS = 30000;
const DASHBOARD_REFRESH_CHECK_MS = 5000;
let _dashboardRefreshTimer = null;

// Background refresh: runs only while the dashboard is the active section and the browser tab is
// visible. A tab that was hidden catches up within one check interval after it becomes visible.
function startDashboardAutoRefresh() {
    if (_dashboardRefreshTimer) return;
    _dashboardRefreshTimer = setInterval(() => {
        const active = document.querySelector('.section.active');
        if (shouldAutoRefresh({
            activeSection: active ? active.id : '',
            hidden: document.hidden,
            inFlight: _dashboardLoading,
            lastLoadAt: _dashboardLastLoadAt,
            now: Date.now(),
            intervalMs: DASHBOARD_REFRESH_MS,
        })) {
            loadDashboard({ background: true }).catch(() => {});
        }
    }, DASHBOARD_REFRESH_CHECK_MS);
}

let uptimeInterval = null;

function startUptimeTimer(initialSeconds) {
    if (uptimeInterval) clearInterval(uptimeInterval);
    let seconds = initialSeconds;
    function update() {
        const d = Math.floor(seconds / 86400);
        const h = Math.floor((seconds % 86400) / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = seconds % 60;
        setText('uptime-display', d + t('time.days') + ' ' + h + t('time.hours') + ' ' + m + t('time.minutes') + ' ' + s + t('time.seconds'));
        seconds++;
    }
    update();
    uptimeInterval = setInterval(update, 1000);
}

// Restart helpers. Kept small and dependency-free so the node tests can run them as-is.
// Hide the button only when the backend says restart is unsupported (older backends keep it).
function restartButtonVisible(sysInfo) {
    return !(sysInfo && sysInfo.restart_supported === false);
}

// Run the restart request; never throws. On failure carries the real error message.
async function requestRestart(call) {
    try {
        await call();
        return { ok: true, message: '' };
    } catch (e) {
        return { ok: false, message: (e && e.message) ? String(e.message) : '' };
    }
}

function applySystemInfo(data) {
    setText('sys-version', 'v' + data.version);
    setText('sys-ver-detail', 'v' + data.version);
    setText('sys-python', data.python_version);
    setText('sys-time', data.server_time);
    setText('sys-os', data.os);
    setText('sys-memory', data.memory_usage + ' MB / ' + data.memory_total + ' MB');
    setText('sys-cpu', data.cpu_percent + '%');
    setText('sys-mode', data.run_mode);
    setText('sys-pid', data.pid);
    const restartBtnEl = document.getElementById('restartBtn');
    if (restartBtnEl) restartBtnEl.style.display = restartButtonVisible(data) ? '' : 'none';
    if (data.uptime_seconds !== undefined) {
        startUptimeTimer(data.uptime_seconds);
    }
}

const QR_REMOTE_BASE = '/api-assets';

async function loadQrCards() {
    const container = document.getElementById('qrCardsContainer');
    if (!container) return;
    try {
        const resp = await fetch(`${QR_REMOTE_BASE}/qr-config.json`);
        if (!resp.ok) return;
        const config = await resp.json();
        // Security: qr-config.json may come from external sources; escape title/description/image
        container.innerHTML = (config.cards || []).map(card => `
            <div class="qr-card">
                <p class="qr-title">${escapeHtml(card.title)}</p>
                <img src="${QR_REMOTE_BASE}/${encodeURIComponent(String(card.image ?? ''))}" alt="${escapeAttr(card.title)}" class="qr-img" onclick="window.app.openLightbox(this.src)">
                <p class="qr-desc">${escapeHtml(card.description)}</p>
            </div>
        `).join('');
    } catch (e) {
        console.error('Failed to load QR configuration:', e);
    }
}

function openLightbox(src) {
    const overlay = document.getElementById('lightboxOverlay');
    const img = document.getElementById('lightboxImage');
    if (overlay && img) {
        img.src = src;
        overlay.classList.add('active');
    }
}

function closeLightbox() {
    const overlay = document.getElementById('lightboxOverlay');
    if (overlay) overlay.classList.remove('active');
}

function initLightbox() {
    const overlay = document.getElementById('lightboxOverlay');
    const closeBtn = document.getElementById('lightboxClose');
    if (overlay) overlay.addEventListener('click', (e) => { if (e.target === overlay) closeLightbox(); });
    if (closeBtn) closeBtn.addEventListener('click', closeLightbox);
}

// Health badge on account card (pure function).
// Exposes real session health and consecutive failures to make degraded accounts visible.
// Returns null when healthy with no failures to avoid cluttering cards.
function _accountHealthNote(account) {
    if (!account) return null;
    if (account.is_healthy === false) {
        // Session unhealthy while status still marked active
        return {
            cls: account.status === 'active' ? 'text-danger' : 'text-muted',
            text: account.status === 'active'
                ? t('accounts.healthMismatch')
                : t('accounts.sessionUnhealthy')
        };
    }
    const streak = Number(account.consecutive_failures) || 0;
    if (streak > 0) {
        return { cls: 'text-warning', text: `${t('accounts.consecutiveFailures')}: ${streak}` };
    }
    return null;
}

// Aligned with app/core/account_pool.EMPTY_STREAK_THRESHOLD: streak required before marking broken
const EMPTY_STREAK_THRESHOLD = 3;

// Empty response indicator (pure function).
// Tracks upstream HTTP 200 responses with empty bodies that would otherwise pass unnoticed.
// Returns null if account has never emitted an empty response.
function _accountEmptyNote(account) {
    const total = Number(account?.empty_count) || 0;
    if (total <= 0) return null;
    const streak = Number(account.consecutive_empty) || 0;
    return {
        cls: streak >= EMPTY_STREAK_THRESHOLD ? 'text-danger' : 'text-warning',
        text: streak > 0 ? `${total} (${t('accounts.consecutiveNow')} ${streak})` : String(total)
    };
}

// Render empty response detail row; returns empty string if no warning.
function _accountEmptyRow(account) {
    const note = _accountEmptyNote(account);
    if (!note) return '';
    return `
            <div class="account-detail">
                <span class="label">${t('accounts.emptyResponses')}</span>
                <span class="value ${note.cls}">${escapeHtml(note.text)}</span>
            </div>`;
}

// Display text for recent error: error summary + timestamp.
// Renders last_error_at from get_status() to differentiate stale errors from current issues.
function _accountLastErrorText(account) {
    const msg = escapeHtml(String(account?.last_error ?? ''));
    if (!account?.last_error_at) return msg;
    return `${msg} (${escapeHtml(formatDate(account.last_error_at))})`;
}

// Render health note row; returns empty string if healthy. Values are escaped via escapeHtml.
function _accountHealthRow(account) {
    const note = _accountHealthNote(account);
    if (!note) return '';
    return `
            <div class="account-detail">
                <span class="label">${t('accounts.health')}</span>
                <span class="value ${note.cls}">${escapeHtml(note.text)}</span>
            </div>`;
}

function renderAccountStatusGrid(accounts) {
    const container = document.getElementById('accountStatusGrid');
    if (!container) return;

    if (accounts.length === 0) {
        container.innerHTML = '<div class="empty-state"><i class="fas fa-server"></></div>';
        return;
    }

    // Security: escape account.label before rendering to prevent stored XSS
    container.innerHTML = accounts.map(account => `
        <div class="provider-card">
            <div class="provider-card-header">
                <h4>${escapeHtml(account.label || account.id)}</h4>
                ${getStatusBadge(account.status)}
            </div>
            <div class="account-detail">
                <span class="label">${t('accounts.requests')}</span>
                <span class="value">${formatNumber(account.request_count || 0)}</span>
            </div>
            <div class="account-detail">
                <span class="label">${t('accounts.errors')}</span>
                <span class="value">${formatNumber(account.error_count || 0)}</span>
            </div>
            <div class="account-detail">
                <span class="label">${t('accounts.concurrency')}</span>
                <span class="value">${account.active_requests || 0}</span>
            </div>
            <div class="account-detail">
                <span class="label">${t('accounts.models')}</span>
                <span class="value">${account.models_count || 0}</span>
            </div>
            <div class="account-detail">
                <span class="label">${t('accounts.lastSuccess')}</span>
                <span class="value">${escapeHtml(formatDate(account.last_success_at))}</span>
            </div>${_accountHealthRow(account)}${_accountEmptyRow(account)}
        </div>
    `).join('');
}

function renderModelsList(modelsSet) {
    const container = document.getElementById('modelsList');
    if (!container) return;

    const models = Array.from(modelsSet).sort();
    if (models.length === 0) {
        container.innerHTML = `<span class="text-muted">${t('accounts.noModels')}</span>`;
        return;
    }

    // Security: use data-model attribute + addEventListener instead of inline onclick JS strings;
    // model is escaped via escapeAttr/escapeHtml
    container.innerHTML = models.map(model => `
        <span class="model-tag" data-model="${escapeAttr(model)}">
            <i class="fas fa-cube"></i> ${escapeHtml(model)}
        </span>
    `).join('');
    container.querySelectorAll('.model-tag').forEach(tag => {
        tag.addEventListener('click', () => copyModel(tag.dataset.model));
    });
}

function updatePlaygroundModels(modelsSet) {
    const select = document.getElementById('pg-model');
    if (!select) return;

    const models = Array.from(modelsSet).sort();
    if (models.length === 0) {
        select.innerHTML = `<option value="">${t('accounts.noModels')}</option>`;
        return;
    }

    // Security: escape model before inserting into option value and text content
    select.innerHTML = models.map(model =>
        `<option value="${escapeAttr(model)}">${escapeHtml(model)}</option>`
    ).join('');
}

// ============================================================================
// Accounts
// ============================================================================

async function loadAccounts() {
    try {
        const data = (typeof accountService !== 'undefined' && accountService.listAccounts)
            ? await accountService.listAccounts()
            : await apiCall('GET', '/admin/accounts');
        const accounts = data.accounts || [];

        const container = document.getElementById('accountsList');
        if (!container) return;

        if (accounts.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-users"></i>
                    <p>${t('accounts.noAccounts')}</p>
                    <button class="btn btn-primary" onclick="window.app.openAddAccountModal()">
                        <i class="fas fa-plus"></i> ${t('accounts.noAccounts')}
                    </button>
                </div>`;
            return;
        }

        // Security: account.label is user-controlled; id/label are escaped for text (escapeHtml)
        // and attributes (escapeAttr). Uses data-* attributes + addEventListener to avoid inline JS strings.
        container.innerHTML = accounts.map(account => {
            const idEsc = escapeAttr(account.id);
            const labelEsc = escapeAttr(account.label || '');
            return `
            <div class="account-card">
                <div class="account-card-header">
                    <div>
                        <h4>${escapeHtml(account.label || t('accounts.unnamed'))}</h4>
                        <span class="text-muted">ID: ${escapeHtml(String(account.id ?? ''))}</span>
                    </div>
                    ${getStatusBadge(account.status)}
                </div>
                <div class="account-detail">
                    <span class="label">PSID</span>
                    <span class="value">${escapeHtml(maskString(account.psid || '', 6, 4))}</span>
                </div>
                <div class="account-detail">
                    <span class="label">${t('accounts.requests')}</span>
                    <span class="value">${formatNumber(account.request_count || 0)}</span>
                </div>
                <div class="account-detail">
                    <span class="label">${t('accounts.errors')}</span>
                    <span class="value">${formatNumber(account.error_count || 0)}</span>
                </div>
                <div class="account-detail">
                    <span class="label">${t('accounts.models')}</span>
                    <span class="value">${(account.models || []).length || account.models_count || 0}</span>
                </div>
                <div class="account-detail">
                    <span class="label">${t('accounts.lastSuccess')}</span>
                    <span class="value">${escapeHtml(formatDate(account.last_success_at))}</span>
                </div>
${_accountHealthRow(account)}${_accountEmptyRow(account)}
                ${account.last_error ? `
                <div class="account-detail">
                    <span class="label">${t('accounts.lastError')}</span>
                    <span class="value text-muted">${_accountLastErrorText(account)}</span>
                </div>` : ''}
                <div class="account-actions">
                    <button class="btn btn-sm btn-outline acc-check-btn" data-account-id="${idEsc}">
                        <i class="fas fa-heartbeat"></i> ${t('accounts.check')}
                    </button>
                    <button class="btn btn-sm btn-outline acc-cookie-btn" data-account-id="${idEsc}" data-account-label="${labelEsc}">
                        <i class="fas fa-cookie-bite"></i> ${t('accounts.updateCookie')}
                    </button>
                    <button class="btn btn-sm btn-danger acc-remove-btn" data-account-id="${idEsc}">
                        <i class="fas fa-trash"></i> ${t('accounts.delete')}
                    </button>
                </div>
            </div>
        `;
        }).join('');

        // Bind events via dataset to prevent user data in inline JS strings
        container.querySelectorAll('.acc-check-btn').forEach(btn => {
            btn.addEventListener('click', () => checkAccount(btn.dataset.accountId));
        });
        container.querySelectorAll('.acc-cookie-btn').forEach(btn => {
            btn.addEventListener('click', () => openUpdateCookieModal(btn.dataset.accountId, btn.dataset.accountLabel || ''));
        });
        container.querySelectorAll('.acc-remove-btn').forEach(btn => {
            btn.addEventListener('click', () => removeAccount(btn.dataset.accountId));
        });
    } catch (error) {
        console.error('Failed to load accounts:', error);
    }
}

async function checkAccount(accountId) {
    try {
        showToast(t('accounts.checking') || 'Checking account...', 'info');
        const result = await accountService.checkAccount(accountId);
        const msg = result.valid ? (t('accounts.valid') || 'Account valid') : (t('accounts.invalid') || 'Account invalid');
        showToast(`${msg} | Models: ${result.models_count || 0}`, result.valid ? 'success' : 'error');
        await loadAccounts();
        await loadDashboard();
    } catch (error) {
        showToast(`${t('accounts.checkFailed')}: ${error.message}`, 'error');
    }
}

async function removeAccount(accountId) {
    const confirmed = await showConfirm({
        title: t('confirm.delete.title'),
        message: t('confirm.delete.message'),
        confirmText: t('confirm.delete.btn'),
        cancelText: t('confirm.cancel'),
        type: 'danger'
    });
    if (!confirmed) return;

    try {
        showToast(t('accounts.deleting'), 'info');
        await accountService.removeAccount(accountId);
        showToast(t('accounts.deleted'), 'success');
        await loadAccounts();
        await loadDashboard();
    } catch (error) {
        showToast(`${t('accounts.deleteFailed')}: ${error.message}`, 'error');
    }
}

function openAddAccountModal() {
    const modal = document.getElementById('addAccountModal');
    if (modal) modal.classList.add('active');
}

function closeAddAccountModal() {
    const modal = document.getElementById('addAccountModal');
    if (modal) {
        modal.classList.remove('active');
        const inputs = modal.querySelectorAll('input');
        inputs.forEach(input => { input.value = ''; });
    }
}

async function submitAddAccount() {
    const psid = document.getElementById('add-psid')?.value.trim();
    const psidts = document.getElementById('add-psidts')?.value.trim() || '';
    const label = document.getElementById('add-label')?.value.trim() || '';

    try {
        showToast(t('accounts.adding') || 'Adding account...', 'info');
        await accountService.addAccount({ psid, psidts, label });
        showToast(t('accounts.added') || 'Account added', 'success');
        closeAddAccountModal();
        await loadAccounts();
        await loadDashboard();
    } catch (error) {
        showToast(`${t('accounts.addFailed') || 'Failed to add account'}: ${error.message}`, 'error');
    }
}

// ============================================================================
// Update Cookie Modal
// ============================================================================

let updateCookieAccountId = null;

function openUpdateCookieModal(accountId, label) {
    updateCookieAccountId = accountId;
    const modal = document.getElementById('updateCookieModal');
    const title = document.getElementById('updateCookieTitle');
    if (title) title.textContent = `Update Cookie - ${label || accountId}`;
    if (modal) modal.classList.add('active');
}

function closeUpdateCookieModal() {
    const modal = document.getElementById('updateCookieModal');
    if (modal) {
        modal.classList.remove('active');
        const inputs = modal.querySelectorAll('input');
        inputs.forEach(input => { input.value = ''; });
    }
    updateCookieAccountId = null;
}

async function submitUpdateCookie() {
    const psid = document.getElementById('update-psid')?.value.trim();
    const psidts = document.getElementById('update-psidts')?.value.trim() || '';

    if (!updateCookieAccountId) {
        showToast('No account selected', 'error');
        return;
    }

    try {
        showToast('Updating Cookie...', 'info');
        await accountService.updateAccountCookies(updateCookieAccountId, { psid, psidts });
        showToast('Cookie updated successfully', 'success');
        closeUpdateCookieModal();
        await loadAccounts();
        await loadDashboard();
    } catch (error) {
        showToast(`Update failed: ${error.message}`, 'error');
    }
}

// ============================================================================
// Playground
// ============================================================================

let _pgConversationId = '';
let _pgMessages = [];
let _pgImages = [];  // Images pending upload [{dataUrl, name}]

// Aligned with backend app/utils/tools.py, used for image generation waiting state UI
const _PG_IMAGE_INTENT_PATTERNS = [
    '画一', '画个', '画张', '画幅', '画副', '帮我画', '给我画', '画图', '画一张', '画一幅',
    '画个图', '画张图', '画幅图', '画一只', '画只', '画头', '画条',
    '生成图片', '生成一张图', '生成图像', '生成一幅', '生成一张照片', '生成张图', '生成个图',
    '生成海报', '生成插画', '生成一张海报',
    '做一张图', '做个图', '做张图', '做一张海报', '做个海报', '做张海报', '做一张照片',
    '设计一张海报', '设计海报', '设计一张图', '设计张海报', '设计个海报', '设计一幅',
    '画一张海报', '画张海报', '画个海报', '画海报',
    '绘制', '绘一', '画出', '出一张图', '出张图', 'p一张', 'p个图', '做幅',
    '一张海报', '张海报', '幅海报', '来张图', '来一张图', '来个图',
    'draw a', 'draw an', 'draw me', 'generate an image', 'generate a picture',
    'generate an picture', 'generate a poster', 'create an image', 'create a picture',
    'create a photo', 'create a poster', 'design a poster', 'make an image',
    'make a picture', 'make a poster', 'an image of', 'a picture of', 'a photo of',
    'a poster of', 'image of a', 'picture of a', 'poster of',
];
const _PG_IMG_NOUNS = ['图', '图片', '图像', '海报', '插画', '照片', '壁纸', 'logo', '头像', '封面',
    'image', 'picture', 'poster', 'photo', 'drawing', 'illustration', 'wallpaper', 'avatar'];
const _PG_IMG_VERBS = ['画', '生成', '绘', '做', '设计', '出', '整', '来', '搞', '弄', '制作', '帮我', '给我',
    '想要', '要', '想', '需要', '求', '来一', '来个', '来张',
    'draw', 'generate', 'create', 'make', 'design', 'render', 'want', 'need'];

function _pgIsImageGenerationIntent(text) {
    if (!text) return false;
    const low = text.toLowerCase();
    return _PG_IMAGE_INTENT_PATTERNS.some((p) => low.includes(p));
}

function _pgMaybeImageGenerationIntent(text) {
    if (!text) return false;
    if (_pgIsImageGenerationIntent(text)) return true;
    const low = text.toLowerCase();
    return _PG_IMG_NOUNS.some((n) => low.includes(n)) && _PG_IMG_VERBS.some((v) => low.includes(v));
}

function _pgShowGeneratingWait(aiMsg, aiBubble, chatContainer) {
    aiMsg.classList.add('pg-generating');
    aiBubble.setAttribute('role', 'status');
    aiBubble.setAttribute('aria-live', 'polite');
    aiBubble.setAttribute('aria-busy', 'true');
    aiBubble.innerHTML = `
        <span class="pg-wait-text">${escapeHtml(t('playground.generatingImage'))}</span>
        <span class="pg-wait-hint">${escapeHtml(t('playground.generatingHint'))}</span>
        <span class="pg-wait-pulse" aria-hidden="true"></span>
    `;
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

function _pgClearGeneratingState(aiMsg, aiBubble) {
    aiMsg.classList.remove('pg-generating');
    aiBubble.removeAttribute('aria-busy');
    aiBubble.removeAttribute('aria-live');
    aiBubble.removeAttribute('role');
}

function _pgShowStillWorking(aiBubble) {
    const textEl = aiBubble.querySelector('.pg-wait-text');
    if (textEl) textEl.textContent = t('playground.stillWorking');
}

function _pgFormatPlaygroundError(error, status) {
    const raw = (error?.message || '').toLowerCase();
    const statusHint = status ? String(status) : '';
    if (raw.includes('network') || raw.includes('failed to fetch') ||
        raw.includes('520') || raw.includes('502') || raw.includes('504') ||
        statusHint === '520' || statusHint === '502' || statusHint === '504') {
        return t('playground.networkError');
    }
    if (error?.message) {
        return `${t('playground.requestFailed')}: ${error.message}`;
    }
    return `${t('playground.requestFailed')} (${status || 'unknown'})`;
}

// Classify SSE frame (pure function).
// Handles top-level {"error":{message,type,code}} frames from backend without choices.
function _pgClassifyChunk(chunk) {
    if (chunk && typeof chunk === 'object' && chunk.error && typeof chunk.error === 'object') {
        return {
            kind: 'error',
            message: chunk.error.message == null ? '' : String(chunk.error.message),
            // Error frames on responses/gemini/claude routes may omit code; tolerate undefined/null
            code: chunk.error.code == null ? null : chunk.error.code
        };
    }
    const delta = chunk?.choices?.[0]?.delta;
    return {
        kind: 'delta',
        role: delta?.role || '',
        content: delta?.content || '',
        reasoning: delta?.reasoning_content || '',
        toolCalls: delta?.tool_calls || null
    };
}

// Extract display text from error frame (pure function).
// Clamps length and collapses whitespace; callers must render via textContent.
const _PG_ERR_MAX = 500;
function _pgFormatStreamError(err) {
    const raw = String(err?.message ?? '').replace(/\s+/g, ' ').trim();
    const msg = raw.length > _PG_ERR_MAX ? `${raw.slice(0, _PG_ERR_MAX)}…` : raw;
    const head = err?.code == null
        ? t('playground.gatewayError')
        : `${t('playground.gatewayError')} (${err.code})`;
    return msg ? `${head}: ${msg}` : head;
}

// Finalize streaming bubble display (pure function):
//   "error"         - Error before any content streamed: replace entire bubble with error
//   "partial-error" - Error after partial content streamed: preserve content and append error
//   "empty"         - No content and no error: valid empty response
//   "content"       - Normal completed stream
function _pgStreamOutcome(content, streamError) {
    if (streamError) {
        return {
            mode: content ? 'partial-error' : 'error',
            content: content || '',
            errorText: _pgFormatStreamError(streamError)
        };
    }
    if (!content) return { mode: 'empty', content: '', errorText: '' };
    return { mode: 'content', content, errorText: '' };
}

// Construct error DOM via textContent to prevent HTML injection.
function _pgErrorSpan(text) {
    const span = document.createElement('span');
    span.className = 'text-danger';
    span.textContent = text;
    return span;
}

function _pgSetSendBusy(busy) {
    const pgSend = document.getElementById('pg-send');
    if (!pgSend) return;
    pgSend.disabled = busy;
    if (busy) pgSend.setAttribute('aria-busy', 'true');
    else pgSend.removeAttribute('aria-busy');
}

let _pgAbortController = null;

async function sendPlaygroundRequest() {
    const pgSend = document.getElementById('pg-send');
    if (pgSend?.disabled) return;

    const message = document.getElementById('pg-message')?.value.trim();
    const model = document.getElementById('pg-model')?.value;
    const chatContainer = document.getElementById('pg-chat');

    if (!message && _pgImages.length === 0) {
        showToast(t('playground.emptyMessage'), 'warning');
        return;
    }

    // Cancel any previous stream
    if (_pgAbortController) {
        _pgAbortController.abort();
        _pgAbortController = null;
    }
    _pgAbortController = new AbortController();

    const placeholder = chatContainer?.querySelector('.chat-placeholder');
    if (placeholder) placeholder.remove();

    const isImageGen = _pgMaybeImageGenerationIntent(message);

    // Assemble multimodal content: use array format when images are present, string for plain text
    let userContent;
    if (_pgImages.length > 0) {
        userContent = [];
        if (message) userContent.push({ type: 'text', text: message });
        for (const img of _pgImages) {
            userContent.push({ type: 'image_url', image_url: { url: img.dataUrl } });
        }
    } else {
        userContent = message;
    }
    _pgMessages.push({ role: 'user', content: userContent });
    // Enforce ring-buffer bounds (FIFO eviction on client heap)
    while (_pgMessages.length > APP_CONSTANTS.LIMITS.MAX_RING_BUFFER_MESSAGES) {
        _pgMessages.shift();
    }

    const imgHtml = _pgImages.map(img => `<img src="${img.dataUrl}" class="chat-img" alt="${escapeHtml(img.name)}">`).join('');
    const userMsg = document.createElement('div');
    userMsg.className = 'chat-message user';
    userMsg.innerHTML = `
        <div class="chat-avatar"><i class="fas fa-user"></i></div>
        <div class="chat-bubble">${escapeHtml(message)}${imgHtml}</div>
    `;
    chatContainer.appendChild(userMsg);

    const aiMsg = document.createElement('div');
    aiMsg.className = 'chat-message assistant';
    aiMsg.innerHTML = `
        <div class="chat-avatar"><i class="fas fa-microchip"></i></div>
        <div class="chat-content"><div class="chat-bubble"><span class="typing-cursor"></span></div></div>
    `;
    chatContainer.appendChild(aiMsg);
    const aiBubble = aiMsg.querySelector('.chat-bubble');

    // Enforce DOM node bounds (FIFO eviction)
    while (chatContainer.children.length > APP_CONSTANTS.LIMITS.MAX_RING_BUFFER_MESSAGES) {
        chatContainer.removeChild(chatContainer.firstElementChild);
    }
    chatContainer.scrollTop = chatContainer.scrollHeight;

    if (isImageGen) {
        _pgShowGeneratingWait(aiMsg, aiBubble, chatContainer);
    }

    document.getElementById('pg-message').value = '';
    _pgImages = [];
    _renderPgImagePreview();
    _pgSetSendBusy(true);

    const token = safeStorage.getSecureItem(APP_CONSTANTS.STORAGE_KEYS.TOKEN);
    const reqBody = {
        model: model || APP_CONSTANTS.DEFAULT_MODELS.FALLBACK_MODEL,
        messages: [..._pgMessages],
        stream: true
    };
    if (_pgConversationId) {
        reqBody.conversation_id = _pgConversationId;
    }
    if (document.getElementById('pg-thinking')?.checked) {
        reqBody.reasoning_effort = 'high';
    }

    const streamTimeoutId = setTimeout(() => {
        if (_pgAbortController) {
            _pgAbortController.abort(new Error(`Stream timed out after ${APP_CONSTANTS.TIMEOUTS.STREAM_DEFAULT / 1000}s`));
        }
    }, APP_CONSTANTS.TIMEOUTS.STREAM_DEFAULT);

    try {
        const resp = await fetch(APP_CONSTANTS.ROUTES.CHAT_COMPLETIONS, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify(reqBody),
            signal: _pgAbortController.signal
        });

        clearTimeout(streamTimeoutId);

        if (!resp.ok) {
            _pgMessages.pop();
            const err = await resp.json().catch(() => null);
            const msg = err?.error?.message || _pgFormatPlaygroundError(null, resp.status);
            _pgClearGeneratingState(aiMsg, aiBubble);
            aiBubble.innerHTML = `<span class="text-danger">${escapeHtml(msg)}</span>`;
            return;
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let content = '';
        let gotContent = false;
        let streamError = null;
        let reasoningBody = null;
        const ensureReasoningBlock = () => {
            if (reasoningBody) return reasoningBody;
            const det = document.createElement('details');
            det.className = 'pg-reasoning';
            det.open = true;
            const summary = document.createElement('summary');
            summary.textContent = t('playground.reasoningTitle');
            const body = document.createElement('div');
            body.className = 'pg-reasoning-body';
            det.appendChild(summary);
            det.appendChild(body);
            aiBubble.parentNode.insertBefore(det, aiBubble);   // .chat-content column: reasoning ABOVE the answer bubble
            reasoningBody = body;
            return reasoningBody;
        };

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();

            for (const rawLine of lines) {
                const line = rawLine.replace(/\r$/, '');
                if (line.startsWith(':')) {
                    if (isImageGen && !gotContent) _pgShowStillWorking(aiBubble);
                    continue;
                }
                if (!line.startsWith('data: ')) continue;
                const data = line.slice(6).trim();
                if (data === '[DONE]') break;
                try {
                    const chunk = JSON.parse(data);
                    const info = _pgClassifyChunk(chunk);
                    if (info.kind === 'error') {
                        // Record error without breaking loop: error frames are followed by [DONE], finalized on completion
                        streamError = info;
                        continue;
                    }
                    if (info.role && !info.content && isImageGen && !gotContent) {
                        _pgShowStillWorking(aiBubble);
                        continue;
                    }
                    const piece = info.content;
                    if (piece) {
                        gotContent = true;
                        _pgClearGeneratingState(aiMsg, aiBubble);
                        content += piece;
                        aiBubble.textContent = content;
                        chatContainer.scrollTop = chatContainer.scrollHeight;
                    }
                    const rPiece = info.reasoning;
                    if (rPiece) {
                        const rb = ensureReasoningBlock();
                        rb.textContent += rPiece;
                        chatContainer.scrollTop = chatContainer.scrollHeight;
                    }
                } catch {}
            }
        }

        const outcome = _pgStreamOutcome(content, streamError);
        _pgClearGeneratingState(aiMsg, aiBubble);
        if (outcome.mode === 'error') {
            _pgMessages.pop();
            aiBubble.textContent = '';
            aiBubble.appendChild(_pgErrorSpan(outcome.errorText));
        } else if (outcome.mode === 'partial-error') {
            aiBubble.innerHTML = _pgRenderContent(outcome.content);
            aiBubble.appendChild(document.createElement('br'));
            aiBubble.appendChild(_pgErrorSpan(outcome.errorText));
            chatContainer.scrollTop = chatContainer.scrollHeight;
            _pgMessages.push({ role: 'assistant', content: outcome.content });
            while (_pgMessages.length > (typeof APP_CONSTANTS !== 'undefined' ? APP_CONSTANTS.LIMITS.MAX_RING_BUFFER_MESSAGES : 200)) {
                _pgMessages.shift();
            }
        } else if (outcome.mode === 'empty') {
            aiBubble.textContent = t('playground.noContent');
        } else {
            aiBubble.innerHTML = _pgRenderContent(outcome.content);
            chatContainer.scrollTop = chatContainer.scrollHeight;
            _pgMessages.push({ role: 'assistant', content: outcome.content });
            while (_pgMessages.length > (typeof APP_CONSTANTS !== 'undefined' ? APP_CONSTANTS.LIMITS.MAX_RING_BUFFER_MESSAGES : 200)) {
                _pgMessages.shift();
            }
        }
    } catch (error) {
        clearTimeout(streamTimeoutId);
        _pgMessages.pop();
        _pgClearGeneratingState(aiMsg, aiBubble);
        aiBubble.innerHTML = `<span class="text-danger">${escapeHtml(_pgFormatPlaygroundError(error))}</span>`;
    } finally {
        clearTimeout(streamTimeoutId);
        _pgAbortController = null;
        _pgSetSendBusy(false);
    }
}

function escapeHtml(text) {
    return String(text == null ? '' : text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Double-quote attribute escaping: escapeHtml does not escape ASCII double quotes;
// escape for src="..." and alt="..." attributes to prevent event handler injection.
function escapeAttr(text) {
    return escapeHtml(text).replace(/"/g, '&quot;');
}

// Render AI response as HTML: convert markdown images and data URIs to <img>, escaping remaining text
function _pgRenderContent(text) {
    if (!text) return '';
    const imgs = [];
    let s = text;
    // 1) Markdown images: ![alt](url)
    s = s.replace(/!\[[^\]]*\]\((data:image\/[^)]+|https?:\/\/[^)\s]+)\)/g, (m, url) => {
        const i = imgs.push(url) - 1;
        return `\u0000IMG${i}\u0000`;
    });
    // 2) Standalone data:image or /images/ URL
    s = s.replace(/(data:image\/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+)/g, (m, url) => {
        const i = imgs.push(url) - 1;
        return `\u0000IMG${i}\u0000`;
    });
    s = s.replace(/(https?:\/\/[^\s)]+\/images\/[^\s)]+\.(?:png|jpg|jpeg|webp|gif))/gi, (m, url) => {
        const i = imgs.push(url) - 1;
        return `\u0000IMG${i}\u0000`;
    });
    // Escape remaining text and restore <img> tags
    let html = escapeHtml(s);
    imgs.forEach((url, i) => {
// Security: URL comes from model output (untrusted). Validate protocol http(s)/data:image
// and escape attributes using escapeAttr to prevent XSS.
        const safeUrl = /^(https?:|data:image\/)/i.test(url) ? escapeAttr(url) : '';
        html = html.replace(`\u0000IMG${i}\u0000`,
            `<img src="${safeUrl}" class="chat-img" alt="generated image">`);
    });
    return html;
}

function clearPlayground() {
    if (_pgAbortController) {
        _pgAbortController.abort();
        _pgAbortController = null;
    }
    const chatContainer = document.getElementById('pg-chat');
    if (chatContainer) {
        chatContainer.innerHTML = `<div class="chat-placeholder"><i class="fas fa-comment-dots"></i><p>${escapeHtml(t('playground.startChat'))}</p></div>`;
    }
    const message = document.getElementById('pg-message');
    if (message) message.value = '';
    _pgConversationId = '';
    _pgMessages = [];
    _pgImages = [];
    _renderPgImagePreview();
}

function _renderPgImagePreview() {
    const box = document.getElementById('pg-image-preview');
    if (!box) return;
    box.innerHTML = _pgImages.map((img, i) =>
        `<span class="pg-img-thumb"><img src="${img.dataUrl}" alt="${escapeHtml(img.name)}"><button type="button" class="pg-img-del" data-idx="${i}">&times;</button></span>`
    ).join('');
    box.querySelectorAll('.pg-img-del').forEach(btn => {
        btn.addEventListener('click', () => {
            _pgImages.splice(parseInt(btn.dataset.idx), 1);
            _renderPgImagePreview();
        });
    });
}

function _handlePgImageSelect(files) {
    for (const file of files) {
        if (!file.type.startsWith('image/')) continue;
        const reader = new FileReader();
        reader.onload = (e) => {
            _pgImages.push({ dataUrl: e.target.result, name: file.name });
            _renderPgImagePreview();
        };
        reader.readAsDataURL(file);
    }
}

// ============================================================================
// Utilities
// ====================================================================================

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

async function copyModel(model) {
    const ok = await copyToClipboard(model);
    showToast(ok ? `${t('toast.copied') || 'Copied'}: ${model}` : (t('toast.error') || 'Failed to copy'), ok ? 'success' : 'error');
}

// ============================================================================
// Update Check
// ============================================================================

let updateInfo = null;

async function checkForUpdate() {
    try {
        const data = await apiCall('GET', '/admin/check-update');
        updateInfo = data;
        updateCheckButton(data);
    } catch (error) {
        console.error('Failed to check for updates:', error);
    }
}

function updateCheckButton(data) {
    const btn = document.getElementById('checkUpdateBtn');
    if (!btn) return;

    const text = btn.querySelector('span');

    if (data.has_update) {
        btn.classList.remove('btn-outline');
        btn.classList.add('btn-success');
        if (text) text.textContent = t('dashboard.updateAvailable') + ' v' + data.latest;
    } else {
        if (text) text.textContent = t('dashboard.upToDate');
    }
}

function extractLocalizedNotes(body) {
    if (!body) return '';
    const lang = localStorage.getItem('gemini2api_lang') || 'zh-CN';
    const marker = `<!-- ${lang} -->`;
    const idx = body.indexOf(marker);
    if (idx === -1) {
        const enIdx = body.indexOf('<!-- en-US -->');
        if (enIdx !== -1) {
            const start = enIdx + '<!-- en-US -->'.length;
            const nextMarker = body.indexOf('<!--', start);
            return (nextMarker === -1 ? body.slice(start) : body.slice(start, nextMarker)).trim();
        }
        return body.replace(/<!--.*?-->/g, '').trim().split('\n').slice(0, 10).join('\n');
    }
    const start = idx + marker.length;
    const nextMarker = body.indexOf('<!--', start);
    return (nextMarker === -1 ? body.slice(start) : body.slice(start, nextMarker)).trim();
}

async function handleCheckUpdate() {
    const btn = document.getElementById('checkUpdateBtn');
    if (!btn) return;

    if (updateInfo && updateInfo.has_update) {
        const updateCmd = 'docker compose pull && docker compose up -d';
        const releaseNotes = extractLocalizedNotes(updateInfo.release_notes || '');

        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay active';
        overlay.style.bottom = '0';
        overlay.innerHTML = `
            <div style="background:var(--bg-primary);border-radius:var(--radius-xl,16px);width:90%;max-width:460px;box-shadow:0 20px 60px rgba(0,0,0,0.3);animation:modalIn 0.2s ease">
                <div style="padding:1.5rem 2rem;text-align:center">
                    <div style="width:56px;height:56px;border-radius:50%;background:var(--primary-color)15;display:inline-flex;align-items:center;justify-content:center;margin-bottom:1rem">
                        <i class="fas fa-cloud-download-alt" style="font-size:1.5rem;color:var(--primary-color)"></i>
                    </div>
                    <h3 style="margin:0 0 0.5rem;font-size:1.125rem;color:var(--text-primary)">${t('confirm.update.title')} v${updateInfo.latest}</h3>
                    ${releaseNotes ? `<div style="max-height:120px;overflow-y:auto;text-align:left;padding:0.75rem;margin:0.75rem 0;background:var(--bg-tertiary);border-radius:var(--radius-lg,10px);border:1px solid var(--border-color);font-size:0.8rem;color:var(--text-secondary);line-height:1.5;white-space:pre-wrap">${escapeHtml(releaseNotes)}</div>` : ''}
                    <p style="margin:0.75rem 0 0.5rem;color:var(--text-secondary);font-size:0.85rem">${t('confirm.update.message')}</p>
                    <div style="display:flex;align-items:center;background:var(--bg-tertiary);border-radius:var(--radius-lg,10px);padding:0.6rem 1rem;margin:0.5rem 0;border:1px solid var(--border-color)">
                        <code style="flex:1;font-size:0.8rem;color:var(--text-primary);font-family:monospace;word-break:break-all">${updateCmd}</code>
                        <button class="update-copy-btn" style="flex-shrink:0;margin-left:0.5rem;background:none;border:none;color:var(--text-secondary);cursor:pointer;padding:0.25rem;border-radius:4px;transition:all 0.2s" title="${t('toast.copied')}">
                            <i class="fas fa-copy" style="font-size:0.9rem"></i>
                        </button>
                    </div>
                </div>
                <div style="display:flex;gap:0.75rem;padding:0 2rem 1.5rem;justify-content:center">
                    <button class="update-close-btn" style="flex:1;padding:0.625rem 1.25rem;border-radius:var(--radius-lg,10px);border:1px solid var(--border-color);background:var(--bg-tertiary);color:var(--text-primary);cursor:pointer;font-size:0.875rem;font-weight:500;transition:all 0.2s">${t('confirm.cancel')}</button>
                </div>
            </div>
        `;

        const close = () => {
            overlay.style.opacity = '0';
            setTimeout(() => overlay.remove(), 200);
        };

        overlay.querySelector('.update-copy-btn').onclick = async () => {
            try {
                await navigator.clipboard.writeText(updateCmd);
            } catch (e) {
                const ta = document.createElement('textarea');
                ta.value = updateCmd;
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                document.body.removeChild(ta);
            }
            const icon = overlay.querySelector('.update-copy-btn i');
            icon.className = 'fas fa-check';
            setTimeout(() => { icon.className = 'fas fa-copy'; }, 1500);
            showToast(t('toast.copied'), 'success');
        };

        overlay.querySelector('.update-close-btn').onclick = close;
        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

        document.body.appendChild(overlay);
    } else {
        // Check for update
        btn.disabled = true;
        await checkForUpdate();
        btn.disabled = false;
    }
}

// ============================================================================
// Event Listeners
// ============================================================================

function initEventListeners() {
    // Add account button
    const addAccountBtn = document.getElementById('addAccountBtn');
    if (addAccountBtn) {
        addAccountBtn.addEventListener('click', openAddAccountModal);
    }

    // Confirm add account
    const confirmBtn = document.getElementById('confirmAddAccount');
    if (confirmBtn) {
        confirmBtn.addEventListener('click', submitAddAccount);
    }

    // Add account modal close buttons
    const addModal = document.getElementById('addAccountModal');
    if (addModal) {
        addModal.querySelectorAll('.modal-close, .modal-cancel').forEach(btn => {
            btn.addEventListener('click', closeAddAccountModal);
        });
        addModal.addEventListener('click', (e) => {
            if (e.target === addModal) closeAddAccountModal();
        });
    }

    // Update cookie modal
    const updateModal = document.getElementById('updateCookieModal');
    if (updateModal) {
        updateModal.querySelectorAll('.modal-close, .modal-cancel').forEach(btn => {
            btn.addEventListener('click', closeUpdateCookieModal);
        });
        updateModal.addEventListener('click', (e) => {
            if (e.target === updateModal) closeUpdateCookieModal();
        });
        const confirmUpdateBtn = document.getElementById('confirmUpdateCookie');
        if (confirmUpdateBtn) {
            confirmUpdateBtn.addEventListener('click', submitUpdateCookie);
        }
    }

    // Playground send
    const pgSend = document.getElementById('pg-send');
    if (pgSend) {
        pgSend.addEventListener('click', sendPlaygroundRequest);
    }

    // Playground textarea: Enter to send, Shift+Enter for newline
    const pgMessage = document.getElementById('pg-message');
    if (pgMessage) {
        pgMessage.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendPlaygroundRequest();
            }
        });
    }

    // Playground clear
    const pgClear = document.getElementById('pg-clear-btn');
    if (pgClear) {
        pgClear.addEventListener('click', clearPlayground);
    }

    // Playground image upload
    const pgImageBtn = document.getElementById('pg-image-btn');
    const pgImageInput = document.getElementById('pg-image-input');
    if (pgImageBtn && pgImageInput) {
        pgImageBtn.addEventListener('click', () => pgImageInput.click());
        pgImageInput.addEventListener('change', (e) => {
            _handlePgImageSelect(e.target.files);
            e.target.value = '';
        });
    }

    // Restart
    const restartBtn = document.getElementById('restartBtn');
    if (restartBtn) {
        restartBtn.addEventListener('click', async () => {
            const confirmed = await showConfirm({
                title: t('confirm.restart.title'),
                message: t('confirm.restart.message'),
                confirmText: t('confirm.restart.btn'),
                cancelText: t('confirm.cancel'),
                type: 'warning'
            });
            if (!confirmed) return;
            restartBtn.classList.add('restarting');
            const result = await requestRestart(() => apiCall('POST', '/admin/restart?confirm=true'));
            if (!result.ok) {
                restartBtn.classList.remove('restarting');
                showToast(result.message || t('toast.error'), 'error');
                return;
            }
            showToast(t('toast.restarting'), 'success');
            setTimeout(() => {
                const check = setInterval(async () => {
                    try {
                        const resp = await fetch('/health');
                        if (resp.ok) {
                            clearInterval(check);
                            window.location.reload();
                        }
                    } catch (e) {}
                }, 1500);
                setTimeout(() => {
                    clearInterval(check);
                    window.location.reload();
                }, 15000);
            }, 2000);
        });
    }

    // Logout
    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', async () => {
            const confirmed = await showConfirm({
                title: t('confirm.logout.title'),
                message: t('confirm.logout.message'),
                confirmText: t('confirm.logout.btn'),
                cancelText: t('confirm.cancel'),
                type: 'info'
            });
            if (confirmed) logout();
        });
    }

    // Refresh account status
    const refreshBtn = document.getElementById('refreshAccountStatusBtn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', loadDashboard);
    }

    // Check update button
    const checkUpdateBtn = document.getElementById('checkUpdateBtn');
    if (checkUpdateBtn) {
        checkUpdateBtn.addEventListener('click', handleCheckUpdate);
    }

    // Mobile menu toggle
    const mobileToggle = document.getElementById('mobileMenuToggle');
    if (mobileToggle) {
        mobileToggle.addEventListener('click', () => {
            const sidebar = document.querySelector('.sidebar');
            if (sidebar) sidebar.classList.toggle('active');
        });
    }
}

// ============================================================================
// Initialization
// ============================================================================

async function initApp() {
    if (isAppInitialized) return;
    isAppInitialized = true;

    initElements();
    initThemeSwitcher();
    initI18n();
    initLanguageSwitcher();
    initNavigation();
    initEventListeners();
    initLogs();
    initUsageStats();
    initSettings();
    initApiKeys();
    initGems();
    initLightbox();
    startDashboardAutoRefresh();
}

// Expose functions for onclick handlers
window.app = {
    checkAccount,
    removeAccount,
    openAddAccountModal,
    closeAddAccountModal,
    openUpdateCookieModal,
    closeUpdateCookieModal,
    sendPlaygroundRequest,
    clearPlayground,
    copyModel,
    openLightbox,
    reloadCurrentSection() {
        const activeSection = document.querySelector('.section.active');
        if (activeSection) {
            loadSectionData(activeSection.id);
        }
    },
    retrySectionLoad(sectionId) {
        // A section whose template failed to load is only an error placeholder; reloading its data
        // cannot help. Re-fetch the whole shell so the template and its event bindings come back.
        const el = document.getElementById(sectionId);
        if (el && el.classList.contains('error-boundary-section')) {
            window.location.reload();
            return;
        }
        loadSectionData(sectionId);
    }
};

// Wait for components to load, then initialize
window.addEventListener('componentsLoaded', async () => {
    const authSuccess = await initAuth();
    if (authSuccess) {
        await initApp();
    }
});

// Fallback: if components are already in DOM (static HTML)
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(async () => {
        if (!isAppInitialized) {
            const sidebar = document.querySelector('.sidebar');
            if (sidebar) {
                const authSuccess = await initAuth();
                if (authSuccess) await initApp();
            }
        }
    }, 500);
});
