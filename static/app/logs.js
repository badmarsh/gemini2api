/**
 * Structured Logs - REST polling, table rendering, filtering, pagination
 * Hardened with FIFO ring buffer guardrail (max 200 items), circuit breaker, and AbortControllers.
 */

import { apiCall } from './auth.js';
import { escapeHtml } from './utils.js';
import { APP_CONSTANTS } from './config/constants.js';
import { RingBuffer } from './services/telemetryService.js';

let pollTimer = null;
let currentDirection = 'all';
let currentSearch = '';
let currentOffset = 0;
const PAGE_SIZE = APP_CONSTANTS.LIMITS.PAGE_SIZE_LOGS;
const POLL_INTERVAL = APP_CONSTANTS.TIMEOUTS.POLL_LOGS;
let isPaused = false;
let selectedRecordId = null;

// Ring buffer guardrail: strictly bounds client-side heap to max 200 records
const clientLogBuffer = new RingBuffer(APP_CONSTANTS.LIMITS.MAX_RING_BUFFER_LOGS);
let inFlightController = null;
let consecutiveErrors = 0;

export function initLogs() {
    initLogControls();
    loadLogs();
    startPolling();
}

function initLogControls() {
    const dirBtns = document.querySelectorAll('#log-direction .btn');
    dirBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            dirBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentDirection = btn.dataset.value;
            currentOffset = 0;
            loadLogs();
        });
    });

    const searchInput = document.getElementById('log-search');
    if (searchInput) {
        let debounce = null;
        searchInput.addEventListener('input', () => {
            clearTimeout(debounce);
            debounce = setTimeout(() => {
                currentSearch = searchInput.value.trim();
                currentOffset = 0;
                loadLogs();
            }, 300);
        });
    }

    const toggleBtn = document.getElementById('log-toggle-btn');
    if (toggleBtn) {
        toggleBtn.addEventListener('click', togglePause);
    }

    const clearBtn = document.getElementById('log-clear-btn');
    if (clearBtn) {
        clearBtn.addEventListener('click', clearLogs);
    }

    const prevBtn = document.getElementById('log-prev-btn');
    if (prevBtn) {
        prevBtn.addEventListener('click', () => {
            if (currentOffset >= PAGE_SIZE) {
                currentOffset -= PAGE_SIZE;
                loadLogs();
            }
        });
    }

    const nextBtn = document.getElementById('log-next-btn');
    if (nextBtn) {
        nextBtn.addEventListener('click', () => {
            currentOffset += PAGE_SIZE;
            loadLogs();
        });
    }

    const detailClose = document.getElementById('log-detail-close');
    if (detailClose) {
        detailClose.addEventListener('click', closeDetail);
    }
}

function startPolling() {
    stopPolling();
    pollTimer = setInterval(() => {
        const logsSection = document.getElementById('logs');
        const isSectionActive = logsSection ? logsSection.classList.contains('active') : true;
        if (!isPaused && document.visibilityState === 'visible' && isSectionActive && consecutiveErrors < 5) {
            loadLogs();
        }
    }, POLL_INTERVAL);
}

function stopPolling() {
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
    if (inFlightController) {
        inFlightController.abort();
        inFlightController = null;
    }
}

function togglePause() {
    isPaused = !isPaused;
    if (!isPaused) {
        consecutiveErrors = 0; // Reset circuit breaker
    }
    const btn = document.getElementById('log-toggle-btn');
    if (btn) {
        const icon = btn.querySelector('i');
        if (icon) {
            icon.className = isPaused ? 'fas fa-play' : 'fas fa-pause';
        }
    }
}

async function clearLogs() {
    try {
        await apiCall('POST', '/admin/logs/clear');
        clientLogBuffer.clear();
        loadLogs();
    } catch (_) {
        // Silently handled without leaking raw traces
    }
}

async function loadLogs() {
    // Abort any prior in-flight fetch
    if (inFlightController) {
        inFlightController.abort();
    }
    inFlightController = new AbortController();

    try {
        let url = `${APP_CONSTANTS.ROUTES.ADMIN_LOGS}?limit=${PAGE_SIZE}&offset=${currentOffset}`;
        if (currentDirection !== 'all') {
            url += '&direction=' + encodeURIComponent(currentDirection);
        }
        if (currentSearch) {
            url += '&search=' + encodeURIComponent(currentSearch);
        }

        const data = await apiCall('GET', url, null, {
            signal: inFlightController.signal,
            timeout: APP_CONSTANTS.TIMEOUTS.API_DEFAULT
        });

        consecutiveErrors = 0;

        const records = data.records || [];
        clientLogBuffer.pushMany(records);

        renderTable(records);
        renderPagination(data.total || 0, data.offset || 0);
    } catch (e) {
        if (e.name !== 'AbortError') {
            consecutiveErrors++;
        }
    } finally {
        inFlightController = null;
    }
}

function renderTable(records) {
    const tbody = document.getElementById('logs-tbody');
    const empty = document.getElementById('logs-empty');
    if (!tbody) return;

    if (records.length === 0) {
        tbody.innerHTML = '';
        if (empty) empty.style.display = 'flex';
        return;
    }

    if (empty) empty.style.display = 'none';

    tbody.innerHTML = records.map(r => {
        const t = new Date(r.ts);
        const time = t.toLocaleTimeString();
        const dirLabel = r.direction === 'ingress' ? (t('logs.ingress') || 'Ingress') : (t('logs.egress') || 'Egress');
        const dirClass = 'badge-' + r.direction;
        const statusClass = getStatusClass(r.status);
        const latency = r.latency_ms ? Math.round(r.latency_ms) + 'ms' : '-';
        const selected = r.id === selectedRecordId ? ' selected' : '';

        return '<tr class="log-row' + selected + '" data-id="' + escapeHtml(String(r.id ?? '')) + '">'
            + '<td>' + escapeHtml(time) + '</td>'
            + '<td><span class="badge ' + dirClass + '">' + escapeHtml(dirLabel) + '</span></td>'
            + '<td>' + escapeHtml(String(r.method ?? '')) + '</td>'
            + '<td>' + escapeHtml(truncatePath(r.path)) + '</td>'
            + '<td><span class="status-code ' + statusClass + '">' + escapeHtml(String(r.status ?? '-')) + '</span></td>'
            + '<td>' + escapeHtml(latency) + '</td>'
            + '<td>' + escapeHtml(String(r.model ?? '-')) + '</td>'
            + '</tr>';
    }).join('');

    tbody.querySelectorAll('.log-row').forEach(row => {
        row.addEventListener('click', () => {
            const id = row.dataset.id;
            selectRecord(id);
        });
    });
}

function getStatusClass(status) {
    if (!status) return '';
    if (status < 300) return 'status-2xx';
    if (status < 400) return 'status-3xx';
    if (status < 500) return 'status-4xx';
    return 'status-5xx';
}

function truncatePath(path) {
    if (!path) return '-';
    if (path.length > 40) return path.substring(0, 37) + '...';
    return path;
}

function renderPagination(total, offset) {
    const info = document.getElementById('log-page-info');
    if (info) {
        const start = total > 0 ? offset + 1 : 0;
        const end = Math.min(offset + PAGE_SIZE, total);
        info.textContent = start + '-' + end + ' of ' + total;
    }

    const prevBtn = document.getElementById('log-prev-btn');
    const nextBtn = document.getElementById('log-next-btn');
    if (prevBtn) prevBtn.disabled = offset === 0;
    if (nextBtn) nextBtn.disabled = offset + PAGE_SIZE >= total;
}

async function selectRecord(id) {
    selectedRecordId = id;
    const rows = document.querySelectorAll('.log-row');
    rows.forEach(r => {
        r.classList.toggle('selected', r.dataset.id === id);
    });

    try {
        const record = await apiCall('GET', '/admin/logs/' + encodeURIComponent(id), null, {
            timeout: APP_CONSTANTS.TIMEOUTS.API_DEFAULT
        });
        showDetail(record);
    } catch (_) {
        // Handled silently
    }
}

/**
 * Sanitize sensitive fields before rendering raw JSON in detail pane
 */
function sanitizeRecordForDisplay(rec) {
    if (!rec || typeof rec !== 'object') return rec;
    const clone = JSON.parse(JSON.stringify(rec));
    const maskKey = (obj, key) => {
        if (obj && typeof obj[key] === 'string' && obj[key].length > 10) {
            obj[key] = obj[key].slice(0, 6) + '...' + obj[key].slice(-4);
        }
    };

    if (clone.headers) {
        ['authorization', 'cookie', 'x-api-key'].forEach(k => {
            if (clone.headers[k]) clone.headers[k] = '[REDACTED_HEADER]';
        });
    }
    maskKey(clone, 'api_key');
    maskKey(clone, 'psid');
    maskKey(clone, 'psidts');
    return clone;
}

function showDetail(record) {
    const panel = document.getElementById('logs-detail');
    const json = document.getElementById('log-detail-json');
    if (panel && json) {
        const safeRecord = sanitizeRecordForDisplay(record);
        json.textContent = JSON.stringify(safeRecord, null, 2);
        panel.classList.add('visible');
    }
}

function closeDetail() {
    const panel = document.getElementById('logs-detail');
    if (panel) panel.classList.remove('visible');
    selectedRecordId = null;
    const rows = document.querySelectorAll('.log-row');
    rows.forEach(r => r.classList.remove('selected'));
}

export function destroyLogs() {
    stopPolling();
}
