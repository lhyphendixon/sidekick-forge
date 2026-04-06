/**
 * Evernote Widget
 *
 * Interactive widget for browsing, searching, and previewing Evernote notes.
 * Triggered when user asks to interact with Evernote.
 */

console.log('[evernote-widget] Script loaded, BaseWidget available:', typeof BaseWidget !== 'undefined');

class EvernoteWidget extends BaseWidget {
    constructor(config) {
        super(config);
        this.currentView = 'search'; // search | notebooks | note
        this.notebooks = [];
        this.notes = [];
        this.selectedNote = null;
        this.isLoading = false;
    }

    render(container) {
        super.render(container);
        this.element.className = 'evernote-widget glass-container rounded-2xl overflow-hidden';
        this.renderSearchView();
    }

    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    renderSearchView() {
        const suggestedQuery = this.config.suggested_query || '';

        this.element.innerHTML = `
            <div class="evernote-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="evernote-icon w-10 h-10 rounded-xl bg-gradient-to-br from-green-500 to-green-700 flex items-center justify-center">
                        <svg width="20" height="20" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M20 16h14l10 10v22a4 4 0 0 1-4 4H20a4 4 0 0 1-4-4V20a4 4 0 0 1 4-4z" fill="#fff"/>
                            <path d="M34 16l10 10H38a4 4 0 0 1-4-4V16z" fill="#cce8d4"/>
                            <rect x="22" y="30" width="16" height="2" rx="1" fill="#00A82D"/>
                            <rect x="22" y="35" width="12" height="2" rx="1" fill="#00A82D"/>
                            <rect x="22" y="40" width="14" height="2" rx="1" fill="#00A82D"/>
                        </svg>
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">Evernote</h3>
                        <p class="text-white/60 text-sm">Search and browse your notes</p>
                    </div>
                </div>
            </div>

            <div class="evernote-body p-4 space-y-3">
                <div class="evernote-tabs flex gap-2 mb-3">
                    <button type="button" class="evernote-tab active" data-view="search">Search Notes</button>
                    <button type="button" class="evernote-tab" data-view="notebooks">Notebooks</button>
                </div>

                <div class="evernote-search-bar flex gap-2">
                    <input type="text"
                           id="evernote-search-input"
                           class="glass-input flex-1 text-sm text-white px-3 py-2 rounded-lg"
                           placeholder="Search your Evernote notes..."
                           value="${this.escapeHtml(suggestedQuery)}">
                    <button type="button" id="evernote-search-btn"
                            class="evernote-btn evernote-btn-primary px-4 py-2 rounded-lg text-sm font-medium">
                        Search
                    </button>
                </div>

                <div id="evernote-results" class="evernote-results mt-3">
                    ${suggestedQuery ? '<p class="text-white/40 text-sm">Press Search or Enter to find notes...</p>' : '<p class="text-white/40 text-sm">Enter a search term to find notes in Evernote.</p>'}
                </div>
            </div>
        `;

        this.attachSearchListeners();

        // Auto-search if we have a suggested query
        if (suggestedQuery) {
            this.performSearch(suggestedQuery);
        }
    }

    renderNotebooksView() {
        const resultsEl = this.element.querySelector('#evernote-results');
        if (!resultsEl) return;

        resultsEl.innerHTML = '<p class="text-white/40 text-sm">Loading notebooks...</p>';
        this.loadNotebooks();
    }

    attachSearchListeners() {
        // Tab switching
        this.element.querySelectorAll('.evernote-tab').forEach(tab => {
            tab.addEventListener('click', (e) => {
                this.element.querySelectorAll('.evernote-tab').forEach(t => t.classList.remove('active'));
                e.target.classList.add('active');

                const view = e.target.dataset.view;
                if (view === 'notebooks') {
                    this.renderNotebooksView();
                } else {
                    const resultsEl = this.element.querySelector('#evernote-results');
                    if (resultsEl) {
                        resultsEl.innerHTML = '<p class="text-white/40 text-sm">Enter a search term to find notes.</p>';
                    }
                }
            });
        });

        // Search
        const searchBtn = this.element.querySelector('#evernote-search-btn');
        const searchInput = this.element.querySelector('#evernote-search-input');

        searchBtn?.addEventListener('click', () => {
            const query = searchInput?.value?.trim();
            if (query) this.performSearch(query);
        });

        searchInput?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                const query = searchInput?.value?.trim();
                if (query) this.performSearch(query);
            }
        });
    }

    async performSearch(query) {
        const resultsEl = this.element.querySelector('#evernote-results');
        if (!resultsEl) return;

        resultsEl.innerHTML = `
            <div class="flex items-center gap-2 text-white/40 text-sm">
                <div class="evernote-spinner"></div>
                <span>Searching Evernote...</span>
            </div>
        `;

        try {
            // Send search request through the sidekick's text endpoint
            const response = await this.sendAgentMessage(`search my evernote notes for "${query}"`);

            if (response && response.notes && response.notes.length > 0) {
                this.notes = response.notes;
                this.renderNotesList(resultsEl);
            } else {
                resultsEl.innerHTML = `<p class="text-white/40 text-sm">No notes found matching "${this.escapeHtml(query)}".</p>`;
            }
        } catch (err) {
            console.error('[evernote-widget] Search failed:', err);
            resultsEl.innerHTML = `<p class="text-red-400 text-sm">Search failed. The sidekick will handle this request directly.</p>`;
        }
    }

    async loadNotebooks() {
        const resultsEl = this.element.querySelector('#evernote-results');
        if (!resultsEl) return;

        try {
            const response = await this.sendAgentMessage('list my evernote notebooks');

            if (response && response.notebooks && response.notebooks.length > 0) {
                this.notebooks = response.notebooks;
                this.renderNotebooksList(resultsEl);
            } else {
                resultsEl.innerHTML = '<p class="text-white/40 text-sm">No notebooks found.</p>';
            }
        } catch (err) {
            console.error('[evernote-widget] Failed to load notebooks:', err);
            resultsEl.innerHTML = '<p class="text-red-400 text-sm">Failed to load notebooks.</p>';
        }
    }

    renderNotesList(container) {
        if (!this.notes.length) {
            container.innerHTML = '<p class="text-white/40 text-sm">No notes to display.</p>';
            return;
        }

        const html = this.notes.map((note, idx) => `
            <div class="evernote-note-item rounded-lg p-3 cursor-pointer" data-index="${idx}">
                <div class="flex items-start justify-between gap-2">
                    <div class="flex-1 min-w-0">
                        <h4 class="text-white text-sm font-medium truncate">${this.escapeHtml(note.title || 'Untitled')}</h4>
                        <p class="text-white/40 text-xs mt-1">${note.updated || ''}</p>
                    </div>
                    <span class="text-white/30 text-xs shrink-0">View</span>
                </div>
            </div>
        `).join('');

        container.innerHTML = `<div class="evernote-notes-list space-y-2">${html}</div>`;

        // Attach click listeners to note items
        container.querySelectorAll('.evernote-note-item').forEach(item => {
            item.addEventListener('click', () => {
                const idx = parseInt(item.dataset.index, 10);
                const note = this.notes[idx];
                if (note) {
                    this.viewNote(note);
                }
            });
        });
    }

    renderNotebooksList(container) {
        if (!this.notebooks.length) {
            container.innerHTML = '<p class="text-white/40 text-sm">No notebooks to display.</p>';
            return;
        }

        const html = this.notebooks.map(nb => `
            <div class="evernote-notebook-item rounded-lg p-3">
                <div class="flex items-center gap-2">
                    <span class="text-green-400 text-lg">📓</span>
                    <span class="text-white text-sm">${this.escapeHtml(nb.name || 'Untitled')}</span>
                </div>
            </div>
        `).join('');

        container.innerHTML = `<div class="evernote-notebooks-list space-y-2">${html}</div>`;
    }

    async viewNote(note) {
        const resultsEl = this.element.querySelector('#evernote-results');
        if (!resultsEl) return;

        resultsEl.innerHTML = `
            <div class="flex items-center gap-2 text-white/40 text-sm">
                <div class="evernote-spinner"></div>
                <span>Loading note...</span>
            </div>
        `;

        try {
            const response = await this.sendAgentMessage(`read my evernote note "${note.title || note.guid}"`);

            resultsEl.innerHTML = `
                <div class="evernote-note-detail rounded-lg border border-white/10 p-4">
                    <div class="flex items-center justify-between mb-3">
                        <h4 class="text-white font-semibold">${this.escapeHtml(note.title || 'Untitled')}</h4>
                        <button type="button" class="evernote-back-btn text-white/40 text-xs hover:text-white/70">
                            &larr; Back
                        </button>
                    </div>
                    <div class="evernote-note-content text-white/80 text-sm whitespace-pre-wrap">${
                        this.escapeHtml(response?.content || response?.text || 'Unable to load note content.')
                    }</div>
                </div>
            `;

            resultsEl.querySelector('.evernote-back-btn')?.addEventListener('click', () => {
                this.renderNotesList(resultsEl);
            });
        } catch (err) {
            console.error('[evernote-widget] Failed to load note:', err);
            resultsEl.innerHTML = '<p class="text-red-400 text-sm">Failed to load note content.</p>';
        }
    }

    /**
     * Send a message through the sidekick agent for processing.
     * This delegates to the parent chat system.
     */
    async sendAgentMessage(message) {
        // The widget communicates via the existing chat system
        // For now, this is a placeholder — the agent handles Evernote requests
        // through the normal tool invocation pipeline
        console.log('[evernote-widget] Sending agent message:', message);

        // Dispatch a custom event that the chat system can pick up
        const event = new CustomEvent('widget-agent-request', {
            detail: { message, widgetId: this.id, type: 'evernote' },
            bubbles: true,
        });
        this.element.dispatchEvent(event);

        // Return null — the agent's response will come back through the normal flow
        return null;
    }
}

// Register with widget system
if (typeof window.widgetRegistry !== 'undefined') {
    window.widgetRegistry.register('evernote', EvernoteWidget);
    console.log('[evernote-widget] Widget registered with registry');
} else {
    document.addEventListener('DOMContentLoaded', () => {
        if (typeof window.widgetRegistry !== 'undefined') {
            window.widgetRegistry.register('evernote', EvernoteWidget);
            console.log('[evernote-widget] Widget registered with registry (delayed)');
        }
    });
}
