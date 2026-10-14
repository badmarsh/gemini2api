/**
 * Input Validation & Schema Guarding Service
 * Enterprise schema validator protecting against prototype pollution, injection, and invalid payloads.
 */

import { APP_CONSTANTS } from '../config/constants.js';

export class ValidationError extends Error {
    constructor(message, field = null) {
        super(message);
        this.name = 'ValidationError';
        this.field = field;
    }
}

/**
 * Checks an object recursively for prototype pollution keys (__proto__, constructor, prototype).
 * Throws ValidationError if detected.
 */
export function assertNoPrototypePollution(data) {
    if (!data || typeof data !== 'object') return;
    const forbidden = ['__proto__', 'constructor', 'prototype'];
    for (const key of Object.getOwnPropertyNames(data)) {
        if (forbidden.includes(key)) {
            throw new ValidationError(`Prototype pollution key detected: ${key}`);
        }
        if (typeof data[key] === 'object' && data[key] !== null) {
            assertNoPrototypePollution(data[key]);
        }
    }
}

/**
 * Safely strips null bytes and dangerous control characters while preserving valid formatting.
 */
export function sanitizeString(val, maxLen = APP_CONSTANTS.LIMITS.MAX_MESSAGE_LENGTH) {
    if (val === null || val === undefined) return '';
    let str = String(val);
    // Strip null bytes and non-printable control chars except \r, \n, \t
    str = str.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, '');
    if (str.length > maxLen) {
        str = str.slice(0, maxLen);
    }
    return str;
}

/**
 * Validates inbound playground prompt payload
 */
export function validatePlaygroundPayload(payload) {
    assertNoPrototypePollution(payload);
    if (!payload || typeof payload !== 'object') {
        throw new ValidationError('Payload must be an object');
    }

    const { model, messages } = payload;
    if (!model || typeof model !== 'string') {
        throw new ValidationError('Model identifier is required and must be a string', 'model');
    }

    if (!Array.isArray(messages) || messages.length === 0) {
        throw new ValidationError('Messages must be a non-empty array', 'messages');
    }

    // Validate each message structure
    messages.forEach((msg, idx) => {
        if (!msg || typeof msg !== 'object') {
            throw new ValidationError(`Message at index ${idx} must be an object`);
        }
        const allowedRoles = ['user', 'assistant', 'system', 'developer'];
        if (!allowedRoles.includes(msg.role)) {
            throw new ValidationError(`Invalid role "${msg.role}" at index ${idx}`);
        }
        if (typeof msg.content !== 'string' && !Array.isArray(msg.content)) {
            throw new ValidationError(`Message content at index ${idx} must be a string or multimodal array`);
        }
    });

    return true;
}

/**
 * Validates Account creation / update payload
 */
export function validateAccountPayload(payload) {
    assertNoPrototypePollution(payload);
    if (!payload || typeof payload !== 'object') {
        throw new ValidationError('Payload must be an object');
    }

    const psid = String(payload.psid || '').trim();
    if (!psid) {
        throw new ValidationError('PSID cookie is required', 'psid');
    }
    if (psid.length > 4096) {
        throw new ValidationError('PSID cookie exceeds maximum allowable length', 'psid');
    }

    const psidts = String(payload.psidts || '').trim();
    if (psidts.length > 4096) {
        throw new ValidationError('PSIDTS cookie exceeds maximum allowable length', 'psidts');
    }

    const label = String(payload.label || '').trim();
    if (label.length > APP_CONSTANTS.LIMITS.MAX_LABEL_LENGTH) {
        throw new ValidationError(`Label exceeds ${APP_CONSTANTS.LIMITS.MAX_LABEL_LENGTH} characters`, 'label');
    }

    return {
        psid: sanitizeString(psid, 4096),
        psidts: sanitizeString(psidts, 4096),
        label: sanitizeString(label, APP_CONSTANTS.LIMITS.MAX_LABEL_LENGTH)
    };
}

/**
 * Validates API key creation / import payload
 */
export function validateApiKeyPayload(payload) {
    assertNoPrototypePollution(payload);
    if (!payload || typeof payload !== 'object') {
        throw new ValidationError('Payload must be an object');
    }

    const provider = String(payload.provider || '').trim();
    if (!provider || !/^[a-zA-Z0-9_\-]+$/.test(provider)) {
        throw new ValidationError('Valid provider identifier is required', 'provider');
    }

    const apiKey = String(payload.api_key || '').trim();
    if (!apiKey) {
        throw new ValidationError('API Key is required', 'api_key');
    }

    if (!Array.isArray(payload.models) || payload.models.length === 0) {
        throw new ValidationError('At least one model must be selected', 'models');
    }

    const sanitizedModels = payload.models.map(m => sanitizeString(String(m).trim(), 128)).filter(Boolean);
    if (sanitizedModels.length === 0) {
        throw new ValidationError('Models list cannot be empty', 'models');
    }

    let baseUrl = payload.base_url ? String(payload.base_url).trim() : '';
    if (baseUrl) {
        if (!baseUrl.startsWith('http://') && !baseUrl.startsWith('https://')) {
            throw new ValidationError('Base URL must begin with http:// or https://', 'base_url');
        }
    }

    const effort = payload.reasoning_effort ? String(payload.reasoning_effort).trim() : null;
    if (effort) {
        if (effort.length > APP_CONSTANTS.LIMITS.MAX_EFFORT_LENGTH || !/^[\x20-\x7E]+$/.test(effort)) {
            throw new ValidationError('Reasoning effort must be ASCII and <= 32 characters', 'reasoning_effort');
        }
    }

    return {
        provider,
        api_key: apiKey,
        models: sanitizedModels,
        base_url: baseUrl || null,
        label: sanitizeString(String(payload.label || '').trim(), APP_CONSTANTS.LIMITS.MAX_LABEL_LENGTH),
        reasoning_effort: effort
    };
}

/**
 * Validates Settings overrides dictionary
 */
export function validateSettingsPayload(payload) {
    assertNoPrototypePollution(payload);
    if (!payload || typeof payload !== 'object' || !payload.settings || typeof payload.settings !== 'object') {
        throw new ValidationError('Settings payload must contain a settings dictionary');
    }
    assertNoPrototypePollution(payload.settings);
    return true;
}

/**
 * Sanitizes model markdown/HTML output against XSS
 */
export function sanitizeRenderHtml(rawHtml) {
    if (!rawHtml) return '';
    // Strip script tags, javascript: urls, inline handlers (onerror, onload, etc.)
    return String(rawHtml)
        .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '')
        .replace(/href\s*=\s*(['"])javascript:[^'"]*\1/gi, 'href="#"')
        .replace(/\bon\w+\s*=\s*(['"][^'"]*['"]|[^\s>]+)/gi, '');
}
