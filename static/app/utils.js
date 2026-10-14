/**
 * Utility functions module
 */

import { getCurrentLanguage, t } from './i18n.js';

/**
 * Cryptographic boundary masking: Strictly exposes first 6 and last 4 characters.
 * Short keys (<= 8 chars) are fully masked (******) to prevent boundary recovery.
 * @param {string} str - Original credential string
 * @param {number} prefixLen - Leading characters (default: 6)
 * @param {number} suffixLen - Trailing characters (default: 4)
 * @returns {string}
 */
function maskString(str, prefixLen = 6, suffixLen = 4) {
    if (!str) return '';
    const s = String(str).trim();
    if (s.length <= 8) return '******';
    if (s.length <= prefixLen + suffixLen) {
        return `${s.substring(0, 2)}...${s.substring(s.length - 2)}`;
    }
    return `${s.substring(0, prefixLen)}...${s.substring(s.length - suffixLen)}`;
}


/**
 * Format number with locale
 * @param {number} num - Number
 * @returns {string}
 */
function formatNumber(num) {
    if (num === null || num === undefined) return '0';
    return num.toLocaleString(getCurrentLanguage());
}

/**
 * Get status badge HTML
 * @param {string} status - Status
 * @returns {string}
 */
function getStatusBadge(status) {
    const statusMap = {
        'active': { key: 'status.active', class: 'success' },
        'inactive': { key: 'status.inactive', class: 'secondary' },
        'error': { key: 'status.error', class: 'danger' },
        'disabled': { key: 'status.disabled', class: 'secondary' }
    };

    const info = statusMap[status] || { key: null, class: 'secondary' };
    const text = info.key ? t(info.key) : String(status ?? '');
    return `<span class="badge badge-${info.class}">${escapeHtml(text)}</span>`;
}

/**
 * Format date
 * @param {string} isoString - ISO date string
 * @returns {string}
 */
function formatDate(isoString) {
    if (!isoString) return '-';
    try {
        const date = new Date(isoString);
        return date.toLocaleString(getCurrentLanguage(), {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
    } catch (error) {
        return isoString;
    }
}

/**
 * Copy text to clipboard
 * @param {string} text - Text to copy
 * @returns {Promise<boolean>}
 */
async function copyToClipboard(text) {
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
        } else {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.position = 'fixed';
            textarea.style.left = '-9999px';
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            document.body.removeChild(textarea);
        }
        return true;
    } catch (error) {
        console.error('Failed to copy:', error);
        return false;
    }
}

/**
 * Display toast notification
 * @param {string} message - Message content
 * @param {string} type - Message type: 'success', 'error', 'warning', 'info'
 */
function showToast(message, type = 'info') {
    // Reuse existing container (#toastContainer in index.html) or create as needed; aria-live for screen readers
    let toastContainer = document.getElementById('toastContainer') || document.getElementById('toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toast-container';
        toastContainer.className = 'toast-container';
        document.body.appendChild(toastContainer);
    }
    toastContainer.setAttribute('aria-live', 'polite');
    toastContainer.setAttribute('aria-atomic', 'false');

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.setAttribute('role', 'status');

    const iconMap = {
        success: 'fa-check-circle',
        error: 'fa-exclamation-circle',
        warning: 'fa-exclamation-triangle',
        info: 'fa-info-circle'
    };

    const icon = iconMap[type] || iconMap.info;

    toast.innerHTML = `
        <i class="fas ${icon}" aria-hidden="true"></i>
        <span>${escapeHtml(String(message ?? ''))}</span>
        <button class="toast-close" aria-label="${t('common.close')}" style="margin-left:auto;background:none;border:none;color:inherit;cursor:pointer;font-size:1rem;line-height:1;opacity:0.6;padding:0 0 0 0.5rem">&times;</button>
    `;

    const dismiss = () => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    };
    toast.querySelector('.toast-close')?.addEventListener('click', dismiss);

    toastContainer.appendChild(toast);

    // Trigger animation
    setTimeout(() => {
        toast.classList.add('show');
    }, 10);

    // Auto dismiss (pause on hover)
    let timer = setTimeout(dismiss, 4000);
    toast.addEventListener('mouseenter', () => clearTimeout(timer));
    toast.addEventListener('mouseleave', () => { timer = setTimeout(dismiss, 2000); });
}

/**
 * Escape HTML special characters
 * @param {string} str - Raw string
 * @returns {string}
 */
function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

/**
 * Format file size
 * @param {number} bytes - Size in bytes
 * @returns {string}
 */
function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

/**
 * Debounce function
 * @param {Function} func - Function to debounce
 * @param {number} wait - Wait time in milliseconds
 * @returns {Function}
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * Throttle function
 * @param {Function} func - Function to throttle
 * @param {number} limit - Time limit in milliseconds
 * @returns {Function}
 */
function throttle(func, limit) {
    let inThrottle;
    return function(...args) {
        if (!inThrottle) {
            func.apply(this, args);
            inThrottle = true;
            setTimeout(() => inThrottle = false, limit);
        }
    };
}

function showConfirm({ title = 'Confirm Action', message = '', confirmText = 'Confirm', cancelText = 'Cancel', type = 'warning' }) {
    // Note: title/message/confirmText/cancelText are escaped via escapeHtml in the template below (XSS defense)
    return new Promise((resolve) => {
        const iconMap = { warning: 'fa-exclamation-triangle', danger: 'fa-trash-alt', info: 'fa-info-circle' };
        const colorMap = { warning: 'var(--warning-color, #f59e0b)', danger: 'var(--danger-color)', info: 'var(--primary-color)' };
        const icon = iconMap[type] || iconMap.warning;
        const color = colorMap[type] || colorMap.warning;
        const prevFocus = document.activeElement;
        const uid = 'cf-' + Date.now();

        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay active';
        overlay.style.bottom = '0';
        // a11y: dialog semantics and labelling; XSS: escaped text
        overlay.innerHTML = `
            <div role="dialog" aria-modal="true" aria-labelledby="${uid}-t" aria-describedby="${uid}-m" style="background:var(--bg-primary);border-radius:var(--radius-xl,16px);width:90%;max-width:400px;box-shadow:0 20px 60px rgba(0,0,0,0.3);animation:modalIn 0.2s ease">
                <div style="padding:2rem;text-align:center">
                    <div style="width:56px;height:56px;border-radius:50%;background:${color}15;display:inline-flex;align-items:center;justify-content:center;margin-bottom:1rem">
                        <i class="fas ${icon}" style="font-size:1.5rem;color:${color}" aria-hidden="true"></i>
                    </div>
                    <h3 id="${uid}-t" style="margin:0 0 0.5rem;font-size:1.125rem;color:var(--text-primary)">${escapeHtml(title)}</h3>
                    <p id="${uid}-m" style="margin:0;color:var(--text-secondary);font-size:0.875rem;line-height:1.5;white-space:pre-line">${escapeHtml(message)}</p>
                </div>
                <div style="display:flex;gap:0.75rem;padding:0 2rem 2rem;justify-content:center">
                    <button class="confirm-cancel-btn" style="flex:1;padding:0.625rem 1.25rem;border-radius:var(--radius-lg,10px);border:1px solid var(--border-color);background:var(--bg-tertiary);color:var(--text-primary);cursor:pointer;font-size:0.875rem;font-weight:500;transition:all 0.2s">${escapeHtml(cancelText)}</button>
                    <button class="confirm-ok-btn" style="flex:1;padding:0.625rem 1.25rem;border-radius:var(--radius-lg,10px);border:none;background:${color};color:#fff;cursor:pointer;font-size:0.875rem;font-weight:600;transition:all 0.2s">${escapeHtml(confirmText)}</button>
                </div>
            </div>
        `;

        const cancelBtn = overlay.querySelector('.confirm-cancel-btn');
        const okBtn = overlay.querySelector('.confirm-ok-btn');

        const onKey = (e) => {
            if (e.key === 'Escape') {
                e.preventDefault();
                close(false);
            } else if (e.key === 'Tab') {
                // Focus trap between Cancel and Confirm buttons
                e.preventDefault();
                const order = [cancelBtn, okBtn];
                const idx = order.indexOf(document.activeElement);
                const next = e.shiftKey
                    ? (idx <= 0 ? order.length - 1 : idx - 1)
                    : (idx >= order.length - 1 ? 0 : idx + 1);
                order[next].focus();
            }
        };

        const close = (result) => {
            document.removeEventListener('keydown', onKey, true);
            overlay.style.opacity = '0';
            setTimeout(() => overlay.remove(), 200);
            if (prevFocus && typeof prevFocus.focus === 'function') {
                try { prevFocus.focus(); } catch (_) {}
            }
            resolve(result);
        };

        cancelBtn.onclick = () => close(false);
        okBtn.onclick = () => close(true);
        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(false); });
        document.addEventListener('keydown', onKey, true);

        document.body.appendChild(overlay);
        okBtn.focus();
    });
}

// Export functions
export {
    maskString,
    maskString as maskCredential,
    formatNumber,
    getStatusBadge,
    formatDate,
    copyToClipboard,
    showToast,
    escapeHtml,
    formatFileSize,
    debounce,
    throttle,
    showConfirm
};
