/**
 * Chat Service
 * Domain service for Playground streaming completions, reasoning extraction, and message ring-buffering.
 */

import { APP_CONSTANTS } from '../config/constants.js';
import { safeStorage } from './storageService.js';
import { validatePlaygroundPayload } from './validationService.js';
import { RingBuffer } from './telemetryService.js';

export class ChatService {
    constructor() {
        this.messageBuffer = new RingBuffer(APP_CONSTANTS.LIMITS.MAX_RING_BUFFER_MESSAGES);
        this._currentAbortController = null;
    }

    /**
     * Abort any ongoing stream completion
     */
    abortCurrentStream() {
        if (this._currentAbortController) {
            this._currentAbortController.abort();
            this._currentAbortController = null;
        }
    }

    /**
     * Push message into ring-buffered history (FIFO max 200 items)
     */
    addMessage(role, content) {
        const msg = { role, content, timestamp: Date.now() };
        this.messageBuffer.push(msg);
        return msg;
    }

    getHistory() {
        return this.messageBuffer.getItems().map(m => ({ role: m.role, content: m.content }));
    }

    clearHistory() {
        this.abortCurrentStream();
        this.messageBuffer.clear();
    }

    /**
     * Send streaming completion request with AbortController, timeout, and thinking extraction
     */
    async streamCompletion({
        model = APP_CONSTANTS.DEFAULT_MODELS.FALLBACK_MODEL,
        messages = [],
        thinking = false,
        conversationId = null,
        onChunk = () => {},
        onReasoning = () => {},
        onDone = () => {},
        onError = () => {}
    }) {
        this.abortCurrentStream();
        // Keep a local reference: a late `finally` of an aborted older call must not wipe the
        // controller of the newer stream (that made Stop/clear unable to cancel it).
        const controller = new AbortController();
        this._currentAbortController = controller;
        const signal = controller.signal;
        let idleTimer = null;

        const payload = {
            model,
            messages,
            stream: true
        };
        if (conversationId) {
            payload.conversation_id = conversationId;
        }
        if (thinking) {
            payload.reasoning_effort = 'high';
        }

        validatePlaygroundPayload(payload);

        const token = safeStorage.getSecureItem(APP_CONSTANTS.STORAGE_KEYS.TOKEN);
        const headers = {
            'Content-Type': 'application/json'
        };
        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }

        const timeoutTimer = setTimeout(() => {
            controller.abort(new Error(`Stream request timed out after ${APP_CONSTANTS.TIMEOUTS.STREAM_DEFAULT / 1000}s`));
        }, APP_CONSTANTS.TIMEOUTS.STREAM_DEFAULT);
        const armIdle = () => {
            clearTimeout(idleTimer);
            idleTimer = setTimeout(() => {
                controller.abort(new Error(`Stream stalled: no data for ${APP_CONSTANTS.TIMEOUTS.STREAM_IDLE / 1000}s`));
            }, APP_CONSTANTS.TIMEOUTS.STREAM_IDLE);
        };

        try {
            const resp = await fetch(APP_CONSTANTS.ROUTES.CHAT_COMPLETIONS, {
                method: 'POST',
                headers,
                body: JSON.stringify(payload),
                signal
            });

            clearTimeout(timeoutTimer);

            if (!resp.ok) {
                const errData = await resp.json().catch(() => null);
                const errMsg = errData?.error?.message || `Request failed with status ${resp.status}`;
                throw new Error(errMsg);
            }

            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            let fullText = '';
            let fullReasoning = '';

            armIdle();
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                armIdle();

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop(); // Keep partial line

                for (const line of lines) {
                    const trimmed = line.trim();
                    if (!trimmed || trimmed.startsWith(':')) continue; // Skip keep-alives and empty lines

                    if (trimmed === 'data: [DONE]') {
                        onDone({ content: fullText, reasoning: fullReasoning });
                        return { content: fullText, reasoning: fullReasoning };
                    }

                    if (trimmed.startsWith('data:')) {
                        const jsonStr = trimmed.slice(5).trimStart();
                        let streamError = null;
                        try {
                            const parsed = JSON.parse(jsonStr);
                            if (parsed && parsed.error) {
                                streamError = new Error(parsed.error.message || 'Upstream error during stream');
                            }
                            const delta = parsed.choices?.[0]?.delta;
                            if (delta) {
                                // Extract reasoning / thinking block
                                const reasoningText = delta.reasoning_content || delta.reasoning || '';
                                if (reasoningText) {
                                    fullReasoning += reasoningText;
                                    onReasoning(reasoningText, fullReasoning);
                                }

                                // Extract visible answer content
                                const contentText = delta.content || '';
                                if (contentText) {
                                    fullText += contentText;
                                    onChunk(contentText, fullText);
                                }
                            }
                        } catch (_) {
                            // Non-fatal parse warning during partial streaming
                        }
                        if (streamError) throw streamError; // surface mid-stream errors instead of an empty answer
                    }
                }
            }

            onDone({ content: fullText, reasoning: fullReasoning });
            return { content: fullText, reasoning: fullReasoning };
        } catch (err) {
            clearTimeout(timeoutTimer);
            if (err.name === 'AbortError') {
                onError(new Error('Request cancelled by user or timeout'));
            } else {
                onError(err);
            }
            throw err;
        } finally {
            clearTimeout(timeoutTimer);
            clearTimeout(idleTimer);
            if (this._currentAbortController === controller) {
                this._currentAbortController = null;
            }
        }
    }
}

export const chatService = new ChatService();
