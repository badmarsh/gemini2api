import { apiCall } from './auth.js';
import { showToast, escapeHtml, maskString } from './utils.js';
import { t } from './i18n.js';
import { validateApiKeyPayload } from './services/validationService.js';

// Double-quote attribute escaping: escapeHtml (via textContent) does not escape ASCII double quotes,
// so directly concatenating into value="..." could allow `"` injection.
function escapeAttr(str) {
    return escapeHtml(String(str ?? '')).replace(/"/g, '&quot;');
}

let keysData = [];
let catalogData = {};
let selectedIds = new Set();

const PROVIDER_COLORS = {
    openai: '#10a37f',
    anthropic: '#cc785c',
    gemini: '#4285f4',
    openrouter: '#6366f1',
    custom: '#6b7280'
};

export function initApiKeys() {
    document.getElementById('ak-add-btn')?.addEventListener('click', toggleAddPanel);
    document.getElementById('ak-cancel-add')?.addEventListener('click', hideAddPanel);
    document.getElementById('ak-confirm-add')?.addEventListener('click', handleAddKey);
    document.getElementById('ak-provider')?.addEventListener('change', handleProviderChange);
    document.getElementById('ak-fetch-models-btn')?.addEventListener('click', handleFetchModels);
    document.getElementById('ak-batch-delete')?.addEventListener('click', handleBatchDelete);
    document.getElementById('ak-export-btn')?.addEventListener('click', handleExport);
    document.getElementById('ak-import-btn')?.addEventListener('click', handleImportClick);
    document.getElementById('ak-fallback-switch')?.addEventListener('change', handleFallbackToggle);

    document.getElementById('ak-custom-model')?.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            const val = this.value.trim();
            if (val) {
                addModelChip(val);
                this.value = '';
            }
        }
    });
}

export async function loadApiKeys() {
    try {
        const data = await apiCall('GET', '/admin/api-keys');
        keysData = data.keys || [];
        renderKeysList();
    } catch (error) {
        showToast('Failed to load API keys: ' + error.message, 'error');
    }
    await loadFallbackState();
}

async function loadFallbackState() {
    try {
        const data = await apiCall('GET', '/admin/api-keys/fallback');
        const sw = document.getElementById('ak-fallback-switch');
        if (sw) sw.checked = !!data.enabled;
    } catch (error) {
        console.error('Failed to load fallback state:', error);
    }
}

async function handleFallbackToggle(e) {
    const enabled = e.currentTarget.checked;
    try {
        await apiCall('PATCH', '/admin/api-keys/fallback', { enabled });
        showToast(enabled ? (t('apiKeys.fallback.on') || 'Fallback enabled') : (t('apiKeys.fallback.off') || 'Fallback disabled'), 'success');
    } catch (error) {
        e.currentTarget.checked = !enabled;
        showToast((t('apiKeys.fallback.failed') || 'Fallback update failed') + ': ' + error.message, 'error');
    }
}

function toggleAddPanel() {
    const panel = document.getElementById('ak-add-panel');
    if (panel.style.display === 'none') {
        panel.style.display = '';
        loadCatalog();
    } else {
        panel.style.display = 'none';
    }
}

function hideAddPanel() {
    document.getElementById('ak-add-panel').style.display = 'none';
    resetAddForm();
}

async function loadCatalog() {
    if (Object.keys(catalogData).length > 0) return;
    try {
        const data = await apiCall('GET', '/admin/api-keys/catalog');
        catalogData = data.catalog || {};
        handleProviderChange();
    } catch (error) {
        showToast('Failed to load model catalog: ' + error.message, 'error');
    }
}

function handleProviderChange() {
    const provider = document.getElementById('ak-provider').value;
    const baseUrlInput = document.getElementById('ak-baseurl');
    const modelsList = document.getElementById('ak-models-list');
    const customInput = document.getElementById('ak-custom-model');
    const fetchBtn = document.getElementById('ak-fetch-models-btn');

    // All providers use the same flow: base_url + api_key -> fetch models
    baseUrlInput.disabled = false;
    customInput.style.display = '';
    fetchBtn.style.display = '';

    const info = catalogData[provider];
    if (info && info.default_base_url) {
        baseUrlInput.value = info.default_base_url;
    } else {
        baseUrlInput.value = '';
    }

    modelsList.innerHTML = `<span style="color:var(--text-secondary);font-size:0.8rem">${t('apiKeys.modelsHint') || 'Select or enter models'}</span>`;
}

function renderModelCheckboxes(models) {
    const container = document.getElementById('ak-models-list');
    if (models.length === 0) {
        container.innerHTML = '<span style="color:var(--text-secondary);font-size:0.8rem">No models available</span>';
        return;
    }
    // Security: model id may come from custom provider /models endpoint; escape before inserting.
    container.innerHTML = models.map(m =>
        '<label style="display:inline-flex;align-items:center;gap:0.3rem;padding:0.2rem 0.5rem;cursor:pointer;font-size:0.85rem">' +
        '<input type="checkbox" value="' + escapeAttr(m) + '"> ' + escapeHtml(String(m ?? '')) + '</label>'
    ).join('');
}

function addModelChip(model) {
    const container = document.getElementById('ak-models-list');
    // Compare dataset instead of interpolating into CSS selector to prevent syntax/injection errors
    const existing = Array.from(container.querySelectorAll('.ak-model-chip'))
        .some(el => el.dataset.model === model);
    if (existing) return;

    const placeholder = container.querySelector('span');
    if (placeholder) placeholder.remove();

    const chip = document.createElement('span');
    chip.className = 'ak-model-chip';
    chip.dataset.model = model;
    // Security: write model name as text node (textContent) to prevent XSS
    chip.textContent = model + ' ';
    const icon = document.createElement('i');
    icon.className = 'fas fa-times';
    icon.style.cursor = 'pointer';
    icon.style.fontSize = '0.7rem';
    icon.addEventListener('click', () => chip.remove());
    chip.appendChild(icon);
    container.appendChild(chip);
}

async function handleFetchModels() {
    const apiKey = document.getElementById('ak-apikey').value.trim();
    const baseUrl = document.getElementById('ak-baseurl').value.trim();
    const provider = document.getElementById('ak-provider').value;

    if (!apiKey) { showToast('Please enter API Key first', 'warning'); return; }
    if (!baseUrl) { showToast('Please enter Base URL first', 'warning'); return; }

    try {
        const data = await apiCall('POST', '/admin/api-keys/models', { provider, api_key: apiKey, base_url: baseUrl });
        const models = (data.models || []).map(m => m.id || m.display_name || m);
        if (models.length > 0) {
            renderModelCheckboxes(models);
            showToast('Found ' + models.length + ' models', 'success');
        } else {
            showToast('No models found', 'warning');
        }
    } catch (error) {
        showToast('Failed to fetch models: ' + error.message, 'error');
    }
}

async function handleAddKey() {
    const provider = document.getElementById('ak-provider').value;
    const apiKey = document.getElementById('ak-apikey').value.trim();
    const baseUrl = document.getElementById('ak-baseurl').value.trim();
    const label = document.getElementById('ak-label').value.trim();
    const reasoningEffort = document.getElementById('ak-effort').value.trim();

    if (!apiKey) { showToast('Please enter API Key', 'warning'); return; }

    let models = [];
    const chips = document.querySelectorAll('#ak-models-list .ak-model-chip');
    const checkboxes = document.querySelectorAll('#ak-models-list input[type="checkbox"]:checked');
    chips.forEach(c => models.push(c.dataset.model));
    checkboxes.forEach(c => models.push(c.value));
    if (models.length === 0) {
        const customVal = document.getElementById('ak-custom-model').value.trim();
        if (customVal) models = [customVal];
    }

    if (models.length === 0) { showToast('Please select at least one model', 'warning'); return; }

    try {
        const payload = validateApiKeyPayload({ provider, models, api_key: apiKey, base_url: baseUrl, label, reasoning_effort: reasoningEffort || null });
        await apiCall('POST', '/admin/api-keys', payload);
        showToast('Key added successfully', 'success');
        hideAddPanel();
        await loadApiKeys();
    } catch (error) {
        showToast('Failed to add key: ' + error.message, 'error');
    }
}

function resetAddForm() {
    document.getElementById('ak-provider').value = 'openai';
    document.getElementById('ak-apikey').value = '';
    document.getElementById('ak-baseurl').value = '';
    document.getElementById('ak-baseurl').disabled = false;
    document.getElementById('ak-label').value = '';
    document.getElementById('ak-custom-model').value = '';
    document.getElementById('ak-models-list').innerHTML = '';
    document.getElementById('ak-effort').value = '';
}

function renderKeysList() {
    const container = document.getElementById('ak-list-container');
    selectedIds.clear();
    updateBatchBar();

    if (keysData.length === 0) {
        container.innerHTML = `<div class="ak-empty"><i class="fas fa-key"></i><p>${t('apiKeys.noKeys') || 'No API Keys'}</p>` +
            `<button class="btn btn-primary btn-sm" onclick="document.getElementById('ak-add-btn').click()"><i class="fas fa-plus"></i> ${t('apiKeys.addFirst') || 'Add Key'}</button></div>`;
        return;
    }

    let html = '<table><thead><tr>';
    html += '<th><input type="checkbox" id="ak-select-all"></th>';
    html += '<th>Provider</th><th>Model</th><th>API Key</th><th>Label</th><th>Status</th><th>Thinking</th><th>Actions</th>';
    html += '</tr></thead><tbody>';

    keysData.forEach(key => {
        const color = PROVIDER_COLORS[key.provider] || PROVIDER_COLORS.custom;
        const statusClass = key.status === 'active' ? 'ak-status-active' : 'ak-status-disabled';
        const statusText = key.status === 'active' ? 'Active' : 'Disabled';
        const toggleText = key.status === 'active' ? 'Disable' : 'Enable';

        const idEsc = escapeAttr(key.id);
        html += '<tr>';
        html += '<td><input type="checkbox" class="ak-cb" data-id="' + idEsc + '"></td>';
        html += '<td><span class="ak-provider-badge" style="background:' + color + '20;color:' + color + '">' + escapeHtml(String(key.provider ?? '')) + '</span></td>';
        html += '<td>' + escapeHtml(String(key.model ?? '-')) + '</td>';
        html += '<td><span class="ak-key-masked">' + escapeHtml(maskString(String(key.api_key ?? '-'), 6, 4)) + '</span></td>';
        html += '<td>' + escapeHtml(String(key.label ?? '-')) + '</td>';
        html += '<td><span class="' + statusClass + '">' + escapeHtml(statusText) + '</span></td>';
        html += '<td><input type="text" class="ak-effort-input" data-id="' + idEsc +
                '" list="ak-effort-presets" value="' + escapeAttr(String(key.reasoning_effort ?? '')) +
                '" placeholder="Default" style="width:90px"></td>';
        html += '<td class="ak-actions">';
        html += '<button class="btn btn-xs btn-outline ak-toggle-btn" data-id="' + idEsc + '" data-status="' + escapeAttr(key.status) + '">' + escapeHtml(toggleText) + '</button>';
        html += '<button class="btn btn-xs btn-danger ak-del-btn" data-id="' + idEsc + '"><i class="fas fa-trash"></i></button>';
        html += '</td></tr>';
    });

    html += '</tbody></table>';
    container.innerHTML = html;

    document.getElementById('ak-select-all')?.addEventListener('change', handleSelectAll);
    container.querySelectorAll('.ak-cb').forEach(cb => cb.addEventListener('change', handleCheckboxChange));
    container.querySelectorAll('.ak-toggle-btn').forEach(btn => btn.addEventListener('click', handleStatusToggle));
    container.querySelectorAll('.ak-del-btn').forEach(btn => btn.addEventListener('click', handleDelete));
    container.querySelectorAll('.ak-effort-input').forEach(inp => inp.addEventListener('change', handleEffortChange));
}

function handleSelectAll(e) {
    const checked = e.target.checked;
    document.querySelectorAll('.ak-cb').forEach(cb => {
        cb.checked = checked;
        if (checked) selectedIds.add(cb.dataset.id);
        else selectedIds.delete(cb.dataset.id);
    });
    updateBatchBar();
}

function handleCheckboxChange(e) {
    if (e.target.checked) selectedIds.add(e.target.dataset.id);
    else selectedIds.delete(e.target.dataset.id);
    updateBatchBar();
}

function updateBatchBar() {
    const bar = document.getElementById('ak-batch-bar');
    const countEl = document.getElementById('ak-selected-count');
    if (selectedIds.size > 0) {
        bar.style.display = '';
        countEl.textContent = selectedIds.size + ' selected';
    } else {
        bar.style.display = 'none';
    }
}

async function handleStatusToggle(e) {
    const btn = e.currentTarget;
    const id = btn.dataset.id;
    const newStatus = btn.dataset.status === 'active' ? 'disabled' : 'active';
    try {
        await apiCall('PATCH', '/admin/api-keys/' + id + '/status', { status: newStatus });
        showToast('Status updated', 'success');
        await loadApiKeys();
    } catch (error) {
        showToast('Update failed: ' + error.message, 'error');
    }
}

async function handleEffortChange(e) {
    const inp = e.currentTarget;
    const id = inp.dataset.id;
    const value = inp.value.trim();
    try {
        await apiCall('PATCH', '/admin/api-keys/' + id + '/reasoning-effort', { reasoning_effort: value || null });
        showToast('Reasoning effort updated', 'success');
        await loadApiKeys();
    } catch (error) {
        showToast('Update failed: ' + error.message, 'error');
    }
}

async function handleDelete(e) {
    const id = e.currentTarget.dataset.id;
    if (!confirm('Are you sure you want to delete this API Key?')) return;
    try {
        await apiCall('DELETE', '/admin/api-keys/' + id);
        showToast('Deleted successfully', 'success');
        await loadApiKeys();
    } catch (error) {
        showToast('Delete failed: ' + error.message, 'error');
    }
}

async function handleBatchDelete() {
    if (selectedIds.size === 0) return;
    if (!confirm('Are you sure you want to delete ' + selectedIds.size + ' selected key(s)?')) return;
    try {
        await apiCall('POST', '/admin/api-keys/batch-delete', { ids: Array.from(selectedIds) });
        showToast('Batch delete successful', 'success');
        await loadApiKeys();
    } catch (error) {
        showToast('Batch delete failed: ' + error.message, 'error');
    }
}

async function handleExport() {
    try {
        const data = await apiCall('GET', '/admin/api-keys/export?reveal=true');
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'api-keys-' + new Date().toISOString().split('T')[0] + '.json';
        a.click();
        URL.revokeObjectURL(url);
        showToast('Export successful', 'success');
    } catch (error) {
        showToast('Export failed: ' + error.message, 'error');
    }
}

function handleImportClick() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = handleImport;
    input.click();
}

async function handleImport(e) {
    const file = e.target.files[0];
    if (!file) return;
    try {
        const text = await file.text();
        const data = JSON.parse(text);
        const keys = data.keys || data;
        await apiCall('POST', '/admin/api-keys/import', { keys: Array.isArray(keys) ? keys : [] });
        showToast('Import successful', 'success');
        await loadApiKeys();
    } catch (error) {
        showToast('Import failed: ' + error.message, 'error');
    }
}
