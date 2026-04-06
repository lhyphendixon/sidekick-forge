/**
 * Campaign Scan Widget
 *
 * Interactive widget for email/newsletter proofreading and visual review.
 * Shows three phases: waiting (forward email), processing, and results.
 */

class CampaignScanWidget extends BaseWidget {
    constructor(config) {
        super(config);
        this.phases = [
            { id: 'waiting', label: 'Forward Email', icon: '\u2709\uFE0F' },
            { id: 'processing', label: 'Analyzing', icon: '\uD83D\uDD0D' },
            { id: 'complete', label: 'Results', icon: '\u2705' }
        ];
        this.currentPhase = 'waiting';
        this.pollInterval = null;
        this.scanData = null;
    }

    render(container) {
        super.render(container);
        this.element.className = 'campaign-scan-widget glass-container rounded-2xl overflow-hidden';

        if (this.config.restoredState) {
            const state = this.config.restoredState;
            if (state.status === 'complete' && state.results) {
                this.scanData = state;
                this.renderResultsPhase();
                return;
            }
        }

        // Create a pending scan record so the webhook knows we're waiting
        this.initScan();
        this.renderWaitingPhase();
    }

    async initScan() {
        const agentSlug = this.config.agentSlug || this.config.agentId || '';
        const userId = this.config.userId || '';
        const clientId = this.config.clientId || '';
        if (!agentSlug || !userId || !clientId) {
            console.warn('[campaign-scan] Missing agentId, userId, or clientId');
            return;
        }

        try {
            // Create a pending scan record so the email webhook routes
            // forwarded emails to the campaign scan pipeline
            const params = new URLSearchParams({
                agent_slug: agentSlug,
                user_id: userId,
                client_id: clientId,
            });
            const resp = await fetch(`/api/v1/campaign-scan/start?${params}`, {
                method: 'POST'
            });
            const data = await resp.json();
            console.log('[campaign-scan] Scan started:', data);
        } catch (e) {
            console.error('[campaign-scan] Failed to start scan:', e);
        }

        // Start polling for when the forwarded email arrives
        this.startPolling();
    }

    renderWaitingPhase() {
        const agentEmail = this.config.agent_email || this.config.agentEmail || '';
        const agentName = this.config.agentName || 'your sidekick';

        this.element.innerHTML = `
            <div class="cs-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="cs-icon w-10 h-10 rounded-xl bg-gradient-to-br from-teal-500 to-cyan-600 flex items-center justify-center text-xl">
                        \uD83D\uDCE7
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">Campaign Scan</h3>
                        <p class="text-white/50 text-sm">Email & newsletter review</p>
                    </div>
                </div>
            </div>

            <div class="cs-body p-4 space-y-4">
                <div class="text-center space-y-3">
                    <p class="text-white/80 text-sm">
                        Forward the email you'd like reviewed to:
                    </p>

                    <div class="cs-email-box flex items-center gap-2 p-3 rounded-xl bg-white/5 border border-white/10">
                        <span class="flex-1 text-white font-mono text-sm truncate" id="cs-email-addr">${this.escapeHtml(agentEmail)}</span>
                        <button type="button" id="cs-copy-btn" class="px-3 py-1.5 rounded-lg bg-white/10 text-white/70 text-xs hover:bg-white/20 hover:text-white transition-all flex items-center gap-1">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/>
                            </svg>
                            Copy
                        </button>
                    </div>

                    <!-- Scanning animation -->
                    <div class="cs-waiting-animation py-6">
                        <div class="cs-scan-ring">
                            <div class="cs-scan-dot"></div>
                            <div class="cs-scan-dot"></div>
                            <div class="cs-scan-dot"></div>
                        </div>
                        <p class="text-white/40 text-xs mt-4">Waiting for your email...</p>
                    </div>

                    <p class="text-white/40 text-xs">
                        Send a test email from your CRM or email platform. I'll check it for typos, visual issues, and more.
                    </p>
                </div>
            </div>
        `;

        // Bind copy button
        this.element.querySelector('#cs-copy-btn')?.addEventListener('click', () => {
            const addr = this.element.querySelector('#cs-email-addr')?.textContent;
            if (addr) {
                navigator.clipboard.writeText(addr).then(() => {
                    const btn = this.element.querySelector('#cs-copy-btn');
                    if (btn) {
                        btn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg> Copied!';
                        setTimeout(() => {
                            btn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg> Copy';
                        }, 2000);
                    }
                });
            }
        });
    }

    renderProcessingPhase(subject) {
        this.currentPhase = 'processing';
        this.element.innerHTML = `
            <div class="cs-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="cs-icon w-10 h-10 rounded-xl bg-gradient-to-br from-teal-500 to-cyan-600 flex items-center justify-center text-xl">
                        \uD83D\uDD0D
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">Campaign Scan</h3>
                        <p class="text-white/50 text-sm">Analyzing your email</p>
                    </div>
                </div>
            </div>

            <div class="cs-body p-4">
                ${subject ? `
                    <div class="p-3 rounded-xl bg-white/5 border border-white/10 mb-4">
                        <p class="text-white/50 text-xs">Email received</p>
                        <p class="text-white/90 text-sm font-medium">${this.escapeHtml(subject)}</p>
                    </div>
                ` : ''}

                <div class="cs-progress-steps space-y-3 mb-4">
                    <div class="cs-step active">
                        <div class="cs-step-dot"></div>
                        <span class="text-white/80 text-sm">Checking copy & content</span>
                    </div>
                    <div class="cs-step">
                        <div class="cs-step-dot"></div>
                        <span class="text-white/50 text-sm">Analyzing visuals & layout</span>
                    </div>
                    <div class="cs-step">
                        <div class="cs-step-dot"></div>
                        <span class="text-white/50 text-sm">Generating report</span>
                    </div>
                </div>

                <div class="flex justify-center py-4">
                    <div class="cs-spinner"></div>
                </div>
                <p class="text-white/40 text-xs text-center">This may take 30-60 seconds</p>
            </div>
        `;
    }

    renderResultsPhase() {
        this.currentPhase = 'complete';
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
            this.pollInterval = null;
        }

        const results = this.scanData?.results || {};
        const errors = results.errors || [];
        const warnings = results.warnings || [];
        const suggestions = results.suggestions || [];
        const score = results.score || 0;
        const summary = results.summary || 'Analysis complete.';

        const totalIssues = errors.length + warnings.length + suggestions.length;

        // Score color
        let scoreColor = 'from-green-500 to-emerald-600';
        if (score < 60) scoreColor = 'from-red-500 to-rose-600';
        else if (score < 80) scoreColor = 'from-yellow-500 to-amber-600';

        this.element.innerHTML = `
            <div class="cs-header p-4 border-b border-white/10">
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-3">
                        <div class="cs-icon w-10 h-10 rounded-xl bg-gradient-to-br ${scoreColor} flex items-center justify-center text-lg font-bold text-white">
                            ${score}
                        </div>
                        <div>
                            <h3 class="text-white font-semibold">Campaign Scan Results</h3>
                            <p class="text-white/50 text-sm">${this.escapeHtml(this.scanData?.email_subject || '')}</p>
                        </div>
                    </div>
                </div>
            </div>

            <div class="cs-body p-4 space-y-4">
                <!-- Summary -->
                <div class="p-3 rounded-xl bg-white/5 border border-white/10">
                    <p class="text-white/90 text-sm">${this.escapeHtml(summary)}</p>
                </div>

                <!-- Stats bar -->
                <div class="flex gap-2 text-xs">
                    ${errors.length ? `<span class="px-2 py-1 rounded-full bg-red-500/20 text-red-400">${errors.length} error${errors.length !== 1 ? 's' : ''}</span>` : ''}
                    ${warnings.length ? `<span class="px-2 py-1 rounded-full bg-yellow-500/20 text-yellow-400">${warnings.length} warning${warnings.length !== 1 ? 's' : ''}</span>` : ''}
                    ${suggestions.length ? `<span class="px-2 py-1 rounded-full bg-blue-500/20 text-blue-400">${suggestions.length} suggestion${suggestions.length !== 1 ? 's' : ''}</span>` : ''}
                    ${totalIssues === 0 ? '<span class="px-2 py-1 rounded-full bg-green-500/20 text-green-400">No issues found!</span>' : ''}
                </div>

                <!-- Findings -->
                <div class="cs-findings space-y-2 max-h-80 overflow-y-auto pr-1">
                    ${errors.map(e => this.renderFinding('error', e)).join('')}
                    ${warnings.map(w => this.renderFinding('warning', w)).join('')}
                    ${suggestions.map(s => this.renderFinding('suggestion', s)).join('')}
                </div>
            </div>
        `;
    }

    renderFinding(severity, item) {
        const colors = {
            error: { bg: 'bg-red-500/10', border: 'border-red-500/20', badge: 'bg-red-500/30 text-red-300', label: 'Error' },
            warning: { bg: 'bg-yellow-500/10', border: 'border-yellow-500/20', badge: 'bg-yellow-500/30 text-yellow-300', label: 'Warning' },
            suggestion: { bg: 'bg-blue-500/10', border: 'border-blue-500/20', badge: 'bg-blue-500/30 text-blue-300', label: 'Tip' },
        };
        const c = colors[severity] || colors.suggestion;

        return `
            <div class="p-3 rounded-xl ${c.bg} border ${c.border}">
                <div class="flex items-start gap-2">
                    <span class="px-1.5 py-0.5 rounded text-[10px] font-semibold ${c.badge} shrink-0 mt-0.5">${c.label}</span>
                    <div class="space-y-1 min-w-0">
                        <p class="text-white/80 text-sm">${this.escapeHtml(item.issue || '')}</p>
                        ${item.text ? `<p class="text-white/40 text-xs font-mono truncate">"${this.escapeHtml(item.text)}"</p>` : ''}
                        ${item.suggestion ? `<p class="text-white/60 text-xs">\u2192 ${this.escapeHtml(item.suggestion)}</p>` : ''}
                    </div>
                </div>
            </div>
        `;
    }

    renderError(message) {
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
            this.pollInterval = null;
        }
        this.element.innerHTML = `
            <div class="cs-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="cs-icon w-10 h-10 rounded-xl bg-gradient-to-br from-red-500 to-rose-600 flex items-center justify-center text-xl">
                        \u26A0\uFE0F
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">Scan Error</h3>
                        <p class="text-white/50 text-sm">Something went wrong</p>
                    </div>
                </div>
            </div>
            <div class="cs-body p-4">
                <div class="p-3 rounded-xl bg-red-500/10 border border-red-500/20 mb-4">
                    <p class="text-red-400 text-sm">${this.escapeHtml(message)}</p>
                </div>
                <button type="button" id="cs-retry-btn" class="w-full py-2 px-4 rounded-xl bg-white/10 text-white/80 text-sm hover:bg-white/20 transition-all">
                    Try Again
                </button>
            </div>
        `;
        this.element.querySelector('#cs-retry-btn')?.addEventListener('click', () => {
            this.currentPhase = 'waiting';
            this.scanData = null;
            this.initScan();
            this.renderWaitingPhase();
        });
    }

    startPolling() {
        if (this.pollInterval) clearInterval(this.pollInterval);

        const agentSlug = this.config.agentSlug || this.config.agentId || '';
        const userId = this.config.userId || '';
        if (!agentSlug || !userId) return;

        this.pollInterval = setInterval(async () => {
            try {
                const resp = await fetch(
                    `/api/v1/campaign-scan/status?agent_slug=${encodeURIComponent(agentSlug)}&user_id=${encodeURIComponent(userId)}`
                );
                if (!resp.ok) return;
                const data = await resp.json();

                if (data.status === 'processing' && this.currentPhase === 'waiting') {
                    this.renderProcessingPhase(data.email_subject || '');
                } else if (data.status === 'complete') {
                    this.scanData = data;
                    this.renderResultsPhase();
                } else if (data.status === 'failed') {
                    this.renderError(data.error || 'Scan failed');
                }
            } catch (e) {
                console.error('[campaign-scan] Polling error:', e);
            }
        }, 3000);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text || '';
        return div.innerHTML;
    }

    update(data) {
        if (data.status === 'complete' && data.results) {
            this.scanData = data;
            this.renderResultsPhase();
        } else if (data.error) {
            this.renderError(data.error);
        }
    }

    destroy() {
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
            this.pollInterval = null;
        }
        super.destroy();
    }
}

// Register
if (window.widgetRegistry) {
    window.widgetRegistry.register('campaign_scan', CampaignScanWidget);
}
