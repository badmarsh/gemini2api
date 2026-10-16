/**
 * Canvas & Sandbox Engine for Gemini2API Playground
 * Supports live preview of HTML/JS/CSS/SVG, code inspection, console capture, and export.
 */

export class CanvasEngine {
    constructor(options = {}) {
        this.container = options.container || null;
        this.currentArtifact = null;
        this.activeTab = 'preview';
        this.logs = [];
        this._initMessageListener();
    }

    _initMessageListener() {
        window.addEventListener('message', (event) => {
            if (event.data && event.data.source === 'canvas-sandbox') {
                this.logs.push({
                    type: event.data.type || 'log',
                    message: event.data.message,
                    timestamp: new Date().toLocaleTimeString()
                });
                this._renderConsoleLogs();
            }
        });
    }

    setContainer(element) {
        this.container = element;
    }

    static extractArtifacts(text) {
        if (!text) return [];
        const artifacts = [];
        const regex = /```(html|svg|javascript|js|mermaid|python)?\s*\n([\s\S]*?)```/gi;
        let match;
        let idx = 1;
        while ((match = regex.exec(text)) !== null) {
            const lang = (match[1] || 'html').toLowerCase();
            const code = match[2].trim();
            let type = 'html';
            if (lang === 'svg' || code.startsWith('<svg')) type = 'svg';
            else if (lang === 'mermaid') type = 'mermaid';
            else if (lang === 'javascript' || lang === 'js') type = 'js';
            else if (lang === 'python') type = 'python';

            artifacts.push({
                id: `artifact-${idx++}`,
                type,
                lang,
                title: `${type.toUpperCase()} Application`,
                code
            });
        }
        return artifacts;
    }

    loadArtifact(artifact) {
        this.currentArtifact = artifact;
        this.logs = [];
        this.render();
    }

    render() {
        if (!this.container) return;
        if (!this.currentArtifact) {
            this.container.innerHTML = `
                <div class="canvas-placeholder">
                    <i class="fas fa-cubes"></i>
                    <p>No active canvas application.</p>
                </div>
            `;
            return;
        }

        this.container.innerHTML = `
            <div class="canvas-wrapper">
                <div class="canvas-header">
                    <div class="canvas-title">
                        <i class="fas fa-code"></i>
                        <span>${this._escapeHtml(this.currentArtifact.title || 'App Canvas')}</span>
                    </div>
                    <div class="canvas-tabs">
                        <button class="canvas-tab ${this.activeTab === 'preview' ? 'active' : ''}" data-tab="preview"><i class="fas fa-play"></i> Preview</button>
                        <button class="canvas-tab ${this.activeTab === 'code' ? 'active' : ''}" data-tab="code"><i class="fas fa-code"></i> Code</button>
                        <button class="canvas-tab ${this.activeTab === 'console' ? 'active' : ''}" data-tab="console"><i class="fas fa-terminal"></i> Console (${this.logs.length})</button>
                    </div>
                    <div class="canvas-actions">
                        <button class="btn btn-outline btn-sm" id="canvas-copy-btn" title="Copy Code"><i class="fas fa-copy"></i></button>
                        <button class="btn btn-outline btn-sm" id="canvas-download-btn" title="Download HTML"><i class="fas fa-download"></i></button>
                        <button class="btn btn-outline btn-sm" id="canvas-close-btn" title="Close Canvas"><i class="fas fa-times"></i></button>
                    </div>
                </div>
                <div class="canvas-body">
                    <div class="canvas-view canvas-preview-view ${this.activeTab === 'preview' ? 'active' : ''}">
                        <iframe id="canvas-sandbox-frame" sandbox="allow-scripts allow-modals allow-forms" class="canvas-iframe"></iframe>
                    </div>
                    <div class="canvas-view canvas-code-view ${this.activeTab === 'code' ? 'active' : ''}">
                        <pre class="canvas-code-block"><code></code></pre>
                    </div>
                    <div class="canvas-view canvas-console-view ${this.activeTab === 'console' ? 'active' : ''}">
                        <div class="canvas-console-logs"></div>
                    </div>
                </div>
            </div>
        `;

        this._bindEvents();
        this._updateViewContent();
    }

    _bindEvents() {
        const tabs = this.container.querySelectorAll('.canvas-tab');
        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                this.activeTab = tab.dataset.tab;
                this.container.querySelectorAll('.canvas-tab').forEach(t => t.classList.remove('active'));
                this.container.querySelectorAll('.canvas-view').forEach(v => v.classList.remove('active'));
                tab.classList.add('active');
                const targetView = this.container.querySelector(`.canvas-${this.activeTab}-view`);
                if (targetView) targetView.classList.add('active');
            });
        });

        const copyBtn = this.container.querySelector('#canvas-copy-btn');
        if (copyBtn) {
            copyBtn.addEventListener('click', async () => {
                if (this.currentArtifact?.code) {
                    await navigator.clipboard.writeText(this.currentArtifact.code);
                    copyBtn.innerHTML = '<i class="fas fa-check"></i>';
                    setTimeout(() => { copyBtn.innerHTML = '<i class="fas fa-copy"></i>'; }, 1500);
                }
            });
        }

        const downloadBtn = this.container.querySelector('#canvas-download-btn');
        if (downloadBtn) {
            downloadBtn.addEventListener('click', () => {
                if (!this.currentArtifact) return;
                const blob = new Blob([this._buildFullHtml(this.currentArtifact.code, this.currentArtifact.type)], { type: 'text/html' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `${this.currentArtifact.type}-app.html`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
            });
        }

        const closeBtn = this.container.querySelector('#canvas-close-btn');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => {
                this.currentArtifact = null;
                this.render();
                const wrapper = document.querySelector('.playground-container');
                if (wrapper) wrapper.classList.remove('has-active-canvas');
            });
        }
    }

    _updateViewContent() {
        if (!this.currentArtifact) return;
        const codeEl = this.container.querySelector('.canvas-code-block code');
        if (codeEl) {
            codeEl.textContent = this.currentArtifact.code;
        }
        const iframe = this.container.querySelector('#canvas-sandbox-frame');
        if (iframe) {
            iframe.srcdoc = this._buildFullHtml(this.currentArtifact.code, this.currentArtifact.type);
        }
        this._renderConsoleLogs();
    }

    _renderConsoleLogs() {
        const consoleBox = this.container?.querySelector('.canvas-console-logs');
        if (!consoleBox) return;
        if (this.logs.length === 0) {
            consoleBox.innerHTML = '<div class="console-empty">No console messages logged.</div>';
            return;
        }
        consoleBox.innerHTML = this.logs.map(log => `
            <div class="console-line console-${log.type}">
                <span class="console-time">${log.timestamp}</span>
                <span class="console-badge">[${log.type.toUpperCase()}]</span>
                <span class="console-msg">${this._escapeHtml(log.message)}</span>
            </div>
        `).join('');
        consoleBox.scrollTop = consoleBox.scrollHeight;
    }

    _escapeHtml(str) {
        return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    _buildFullHtml(code, type) {
        if (type === 'svg') {
            return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{margin:0;padding:2rem;display:flex;align-items:center;justify-content:center;min-height:100vh;background:#0f172a;}svg{max-width:90vw;max-height:85vh;height:auto;}</style></head><body>${code}</body></html>`;
        }
        const bridge = '<script>(function(){const oL=console.log,oE=console.error,oW=console.warn;function s(t,a){try{const m=Array.from(a).map(x=>typeof x==='object'?JSON.stringify(x):String(x)).join(' ');window.parent.postMessage({source:'canvas-sandbox',type:t,message:m},'*');}catch(e){}}console.log=function(){s('log',arguments);oL.apply(console,arguments);};console.warn=function(){s('warn',arguments);oW.apply(console,arguments);};console.error=function(){s('error',arguments);oE.apply(console,arguments);};window.onerror=function(m,u,l){s('error',[m+' (line '+l+')']);};})();<\/script>';
        if (code.includes('<html') || code.includes('<!DOCTYPE')) {
            return code.replace('<head>', '<head>' + bridge);
        }
        return `<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><script src="https://cdn.tailwindcss.com"><\/script><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">${bridge}<style>body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;}</style></head><body class="bg-slate-900 text-slate-100 min-h-screen p-4">${code}</body></html>`;
    }
}

export const canvasEngine = new CanvasEngine();
