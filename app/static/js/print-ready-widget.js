/**
 * PrintReady Widget
 * Inline widget for building a print-ready HTML document from one or more conversations.
 */
class PrintReadyWidget extends BaseWidget {
    constructor(config = {}) {
        super(config);
        this.conversations = [];
        this.isLoadingConversations = false;
        this.isGenerating = false;
    }

    render(container) {
        super.render(container);
        this.element.className = 'print-ready-widget glass-container rounded-2xl overflow-hidden';
        this.element.innerHTML = this.getWidgetHTML();
        this.attachEventListeners();
        this.loadConversationList();
    }

    getWidgetHTML() {
        const currentLabel = this.config.currentConversationTitle || 'Current conversation';
        return `
            <div class="print-ready-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-brand-teal to-brand-orange flex items-center justify-center text-xl">
                        🖨️
                    </div>
                    <div>
                        <h3 class="text-white font-semibold text-base">PrintReady</h3>
                        <p class="text-white/60 text-sm">Build one print-ready document from selected conversations.</p>
                    </div>
                </div>
            </div>

            <div class="print-ready-body p-4 space-y-4">
                <div class="print-ready-current rounded-xl border border-white/10 p-3">
                    <label class="print-ready-checkbox-row">
                        <input type="checkbox" id="pr-current-checkbox" class="print-ready-checkbox" checked ${this.config.conversationId ? '' : 'disabled'}>
                        <span class="text-white text-sm">${this.escapeHtml(currentLabel)}</span>
                    </label>
                    ${this.config.conversationId ? '' : '<p class="text-white/40 text-xs mt-2">No active conversation is available yet.</p>'}
                </div>

                <div class="print-ready-history rounded-xl border border-white/10 p-3">
                    <p class="text-white/80 text-sm mb-2">Select additional conversations</p>
                    <div id="pr-conversation-list" class="print-ready-conversation-list text-white/60 text-sm">
                        Loading conversations...
                    </div>
                </div>

                <div class="print-ready-actions grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <button type="button" id="pr-download-btn" class="print-ready-btn print-ready-btn-secondary">Download PDF</button>
                    <button type="button" id="pr-print-btn" class="print-ready-btn print-ready-btn-primary">Print PDF</button>
                </div>
                <p id="pr-status" class="text-white/50 text-xs hidden">Preparing your PDF...</p>
            </div>
        `;
    }

    attachEventListeners() {
        this.element.querySelector('#pr-download-btn')?.addEventListener('click', () => {
            this.generateOutput('download');
        });

        this.element.querySelector('#pr-print-btn')?.addEventListener('click', () => {
            this.generateOutput('print');
        });
    }

    async loadConversationList() {
        const listEl = this.element.querySelector('#pr-conversation-list');
        if (!listEl) return;
        if (!this.config.userId || !this.config.clientId || !this.config.agentSlug) {
            listEl.innerHTML = '<p class="text-white/40 text-xs">Conversation history is unavailable for this session.</p>';
            return;
        }

        this.isLoadingConversations = true;
        try {
            const params = new URLSearchParams({
                client_id: this.config.clientId,
                user_id: this.config.userId,
                agent_slug: this.config.agentSlug,
                limit: '50',
            });
            const res = await fetch(`/api/embed/conversations?${params.toString()}`);
            const data = await res.json();
            if (!data.success) {
                throw new Error(data.error || 'Failed to load conversations');
            }

            this.conversations = Array.isArray(data.conversations) ? data.conversations : [];
            const currentId = this.config.conversationId;
            const additional = this.conversations.filter((c) => c && c.id && c.id !== currentId);

            if (additional.length === 0) {
                listEl.innerHTML = '<p class="text-white/40 text-xs">No additional conversations found.</p>';
                return;
            }

            listEl.innerHTML = additional.map((conv) => {
                const title = this.escapeHtml(conv.title || 'Untitled conversation');
                const created = conv.created_at ? new Date(conv.created_at).toLocaleString() : '';
                return `
                    <label class="print-ready-checkbox-row">
                        <input type="checkbox" class="print-ready-checkbox pr-conv-checkbox" value="${this.escapeHtml(conv.id)}">
                        <span class="print-ready-conv-label">
                            <span class="text-white/90 text-sm">${title}</span>
                            ${created ? `<span class="text-white/40 text-xs">${this.escapeHtml(created)}</span>` : ''}
                        </span>
                    </label>
                `;
            }).join('');
        } catch (err) {
            console.error('[print-ready-widget] Failed to load conversations:', err);
            listEl.innerHTML = '<p class="text-red-300 text-xs">Unable to load conversations right now.</p>';
        } finally {
            this.isLoadingConversations = false;
        }
    }

    getSelectedConversationIds() {
        const ids = [];
        const includeCurrent = this.element.querySelector('#pr-current-checkbox')?.checked;
        if (includeCurrent && this.config.conversationId) {
            ids.push(this.config.conversationId);
        }

        this.element.querySelectorAll('.pr-conv-checkbox:checked').forEach((input) => {
            if (input.value && !ids.includes(input.value)) {
                ids.push(input.value);
            }
        });
        return ids;
    }

    async generateOutput(mode) {
        if (this.isLoadingConversations || this.isGenerating) return;
        const ids = this.getSelectedConversationIds();
        if (!ids.length) {
            this.notify('Select at least one conversation first.');
            return;
        }

        try {
            this.setBusyState(true, mode);
            const response = await fetch('/api/embed/print-ready/pdf', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    client_id: this.config.clientId,
                    user_id: this.config.userId,
                    conversation_ids: ids,
                    filename: this.buildFilename(ids.length),
                    user_label: this.resolveUserLabel(),
                    assistant_label: this.resolveAssistantLabel(),
                }),
            });

            if (!response.ok) {
                let detail = `Request failed (${response.status})`;
                try {
                    const errorData = await response.json();
                    detail = errorData?.detail || detail;
                } catch (_) {
                    // Non-JSON error; keep status detail.
                }
                throw new Error(detail);
            }

            const blob = await response.blob();
            const blobUrl = URL.createObjectURL(blob);
            if (mode === 'print') {
                this.openPrintPdf(blobUrl);
            } else {
                this.downloadPdf(blobUrl, this.buildFilename(ids.length));
            }
        } catch (err) {
            console.error('[print-ready-widget] Failed to generate output:', err);
            this.notify('Could not prepare print-ready output.');
        } finally {
            this.setBusyState(false, mode);
        }
    }

    openPrintPdf(blobUrl) {
        const printWindow = window.open(blobUrl, '_blank', 'noopener,noreferrer');
        if (!printWindow) {
            this.notify('Pop-up blocked. Please allow pop-ups to print.');
            return;
        }
        printWindow.focus();
        setTimeout(() => {
            try {
                printWindow.print();
            } catch (err) {
                console.error('[print-ready-widget] Print failed:', err);
            }
        }, 500);
        setTimeout(() => URL.revokeObjectURL(blobUrl), 12000);
    }

    downloadPdf(blobUrl, filename) {
        const link = document.createElement('a');
        link.href = blobUrl;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(blobUrl), 3000);
    }

    buildFilename(conversationCount) {
        const timestamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
        return `print-ready-${conversationCount}-conversation-${timestamp}.pdf`;
    }

    resolveAssistantLabel() {
        const fromConfig = String(this.config.agentName || '').trim();
        return fromConfig || null;
    }

    resolveUserLabel() {
        try {
            if (typeof session !== 'undefined' && session?.user) {
                const user = session.user;
                const fullName = String(user.user_metadata?.full_name || '').trim();
                if (fullName) return fullName;
                const name = String(user.user_metadata?.name || '').trim();
                if (name) return name;
                const email = String(user.email || '').trim();
                if (email) return email;
            }
        } catch (_) {
            // Fall back to server-side name resolution
        }
        return null;
    }

    setBusyState(isBusy, mode) {
        this.isGenerating = isBusy;
        const downloadBtn = this.element?.querySelector('#pr-download-btn');
        const printBtn = this.element?.querySelector('#pr-print-btn');
        const statusEl = this.element?.querySelector('#pr-status');
        const checkboxes = this.element?.querySelectorAll('.print-ready-checkbox') || [];

        if (downloadBtn) {
            downloadBtn.disabled = isBusy;
            downloadBtn.textContent = isBusy && mode === 'download' ? 'Generating PDF...' : 'Download PDF';
        }
        if (printBtn) {
            printBtn.disabled = isBusy;
            printBtn.textContent = isBusy && mode === 'print' ? 'Preparing Print...' : 'Print PDF';
        }
        if (statusEl) {
            statusEl.classList.toggle('hidden', !isBusy);
            statusEl.textContent = isBusy ? 'Preparing your PDF. This may take a moment...' : '';
        }
        checkboxes.forEach((input) => {
            input.disabled = isBusy;
        });
    }

    escapeHtml(value) {
        const text = String(value ?? '');
        return text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    notify(message) {
        try {
            window.alert(message);
        } catch (_) {
            console.warn('[print-ready-widget]', message);
        }
    }
}

if (window.widgetRegistry) {
    window.widgetRegistry.register('print_ready', PrintReadyWidget);
} else {
    window.addEventListener('DOMContentLoaded', () => {
        if (window.widgetRegistry) {
            window.widgetRegistry.register('print_ready', PrintReadyWidget);
        }
    });
}
