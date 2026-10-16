/**
 * Telemetry Service
 * Domain service for system metrics, usage statistics, and FIFO ring-buffered log ingestion.
 */

import { apiCall } from '../auth.js';
import { APP_CONSTANTS } from '../config/constants.js';

export class RingBuffer {
    constructor(maxSize = APP_CONSTANTS.LIMITS.MAX_RING_BUFFER_LOGS) {
        this.maxSize = maxSize;
        this.buffer = [];
    }

    push(item) {
        if (this.buffer.length >= this.maxSize) {
            this.buffer.shift(); // FIFO eviction
        }
        this.buffer.push(item);
    }

    pushMany(items) {
        if (!Array.isArray(items)) return;
        items.forEach(item => this.push(item));
    }

    getItems() {
        return [...this.buffer];
    }

    clear() {
        this.buffer = [];
    }

    get size() {
        return this.buffer.length;
    }
}

export class TelemetryService {
    constructor() {
        this.logRingBuffer = new RingBuffer(APP_CONSTANTS.LIMITS.MAX_RING_BUFFER_LOGS);
        this._failureCount = 0;
        this._circuitBreakerOpen = false;
    }

    /**
     * Get system runtime and hardware statistics
     */
    async getSystemInfo(signal = null) {
        return await apiCall('GET', APP_CONSTANTS.ROUTES.ADMIN_SYSTEM_INFO, null, {
            signal,
            timeout: APP_CONSTANTS.TIMEOUTS.API_DEFAULT
        });
    }

    /**
     * Get high-level admin dashboard status
     */
    async getAdminStatus(signal = null) {
        return await apiCall('GET', APP_CONSTANTS.ROUTES.ADMIN_STATUS, null, {
            signal,
            timeout: APP_CONSTANTS.TIMEOUTS.API_DEFAULT
        });
    }

    /**
     * Fetch structured logs with circuit breaker protection and ring-buffer caching
     */
    async getLogs({ direction = 'all', search = '', limit = 15, offset = 0 } = {}, signal = null) {
        if (this._circuitBreakerOpen) {
            // Cool off after repeated consecutive failures
            if (this._failureCount > 5) {
                this._failureCount = 0;
                this._circuitBreakerOpen = false;
            } else {
                throw new Error('Circuit breaker active for log telemetry; cooling off');
            }
        }

        try {
            const params = new URLSearchParams({
                direction,
                search,
                limit: String(limit),
                offset: String(offset)
            });
            const url = `${APP_CONSTANTS.ROUTES.ADMIN_LOGS}?${params.toString()}`;
            const data = await apiCall('GET', url, null, {
                signal,
                timeout: APP_CONSTANTS.TIMEOUTS.API_DEFAULT
            });

            this._failureCount = 0;
            this._circuitBreakerOpen = false;

            if (data && Array.isArray(data.records)) {
                this.logRingBuffer.pushMany(data.records);
            }

            return data;
        } catch (err) {
            this._failureCount++;
            if (this._failureCount >= 3) {
                this._circuitBreakerOpen = true;
            }
            throw err;
        }
    }

    /**
     * Get specific log record details
     */
    async getLogDetail(recordId, signal = null) {
        if (!recordId) throw new Error('Record ID is required');
        return await apiCall('GET', `${APP_CONSTANTS.ROUTES.ADMIN_LOG_RECORD}/${encodeURIComponent(recordId)}`, null, {
            signal,
            timeout: APP_CONSTANTS.TIMEOUTS.API_DEFAULT
        });
    }

    /**
     * Get bounded redacted request diagnostics for the dashboard debug panel.
     */
    async getDebugMetrics(signal = null) {
        return await apiCall('GET', APP_CONSTANTS.ROUTES.ADMIN_DEBUG_METRICS, null, {
            signal,
            timeout: APP_CONSTANTS.TIMEOUTS.API_DEFAULT
        });
    }

    /**
     * Validate a tool-call fixture using the backend parser without triggering regeneration.
     */
    async validateToolJson(text, signal = null) {
        return await apiCall('POST', APP_CONSTANTS.ROUTES.ADMIN_TOOL_JSON_TEST, { text }, {
            signal,
            timeout: APP_CONSTANTS.TIMEOUTS.API_DEFAULT
        });
    }

    /**
     * Clear server logs and reset local ring buffer
     */
    async clearLogs(signal = null) {
        const result = await apiCall('POST', `${APP_CONSTANTS.ROUTES.ADMIN_LOGS}/clear`, null, {
            signal,
            timeout: APP_CONSTANTS.TIMEOUTS.API_DEFAULT
        });
        this.logRingBuffer.clear();
        return result;
    }

    /**
     * Get token usage aggregate summary
     */
    async getUsageSummary(days = 30, signal = null) {
        return await apiCall('GET', `${APP_CONSTANTS.ROUTES.ADMIN_USAGE_SUMMARY}?days=${days}`, null, {
            signal,
            timeout: APP_CONSTANTS.TIMEOUTS.API_DEFAULT
        });
    }

    /**
     * Get hourly usage statistics for charts
     */
    async getUsageHourly(hours = 24, signal = null) {
        return await apiCall('GET', `${APP_CONSTANTS.ROUTES.ADMIN_USAGE_HOURLY}?hours=${hours}`, null, {
            signal,
            timeout: APP_CONSTANTS.TIMEOUTS.API_DEFAULT
        });
    }

    /**
     * Check for upstream version updates
     */
    async checkUpdate(signal = null) {
        return await apiCall('GET', APP_CONSTANTS.ROUTES.ADMIN_CHECK_UPDATE, null, {
            signal,
            timeout: APP_CONSTANTS.TIMEOUTS.API_DEFAULT
        });
    }
}

export const telemetryService = new TelemetryService();
