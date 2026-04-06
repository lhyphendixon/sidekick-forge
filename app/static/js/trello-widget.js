/**
 * Trello Widget
 *
 * Interactive widget for searching Trello boards and cards.
 * Triggered when user asks to interact with Trello.
 */

console.log('[trello-widget] Script loaded, BaseWidget available:', typeof BaseWidget !== 'undefined');

class TrelloWidget extends BaseWidget {
    constructor(config) {
        super(config);
        this.results = [];
        this.isLoading = false;
    }

    render(container) {
        super.render(container);
        this.element.className = 'trello-widget glass-container rounded-2xl overflow-hidden';
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
            <div class="trello-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="trello-icon w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500 to-blue-700 flex items-center justify-center">
                        <svg width="20" height="20" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <rect x="14" y="14" width="15" height="28" rx="3" fill="#fff"/>
                            <rect x="35" y="14" width="15" height="20" rx="3" fill="#fff"/>
                        </svg>
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">Trello</h3>
                        <p class="text-white/60 text-sm">Search your boards and cards</p>
                    </div>
                </div>
            </div>

            <div class="trello-body p-4 space-y-3">
                <div class="trello-search-bar flex gap-2">
                    <input type="text"
                           id="trello-search-input"
                           class="glass-input flex-1 text-sm text-white px-3 py-2 rounded-lg"
                           placeholder="Search Trello cards and boards..."
                           value="${this.escapeHtml(suggestedQuery)}">
                    <button type="button" id="trello-search-btn"
                            class="trello-btn trello-btn-primary px-4 py-2 rounded-lg text-sm font-medium">
                        Search
                    </button>
                </div>

                <div id="trello-results" class="trello-results mt-3">
                    ${suggestedQuery ? '<p class="text-white/40 text-sm">Press Search or Enter to find cards...</p>' : '<p class="text-white/40 text-sm">Enter a search term to find cards and boards in Trello.</p>'}
                </div>
            </div>
        `;

        this.attachSearchListeners();

        if (suggestedQuery) {
            this.performSearch(suggestedQuery);
        }
    }

    attachSearchListeners() {
        const searchBtn = this.element.querySelector('#trello-search-btn');
        const searchInput = this.element.querySelector('#trello-search-input');

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
        const resultsEl = this.element.querySelector('#trello-results');
        if (!resultsEl) return;

        resultsEl.innerHTML = `
            <div class="flex items-center gap-2 text-white/40 text-sm">
                <div class="trello-spinner"></div>
                <span>Searching Trello...</span>
            </div>
        `;

        try {
            const response = await this.sendAgentMessage(`search trello for "${query}"`);

            if (response && response.cards && response.cards.length > 0) {
                this.renderCardsList(resultsEl, response.cards);
            } else if (response && response.boards && response.boards.length > 0) {
                this.renderBoardsList(resultsEl, response.boards);
            } else {
                resultsEl.innerHTML = `<p class="text-white/40 text-sm">No results found for "${this.escapeHtml(query)}".</p>`;
            }
        } catch (err) {
            console.error('[trello-widget] Search failed:', err);
            resultsEl.innerHTML = `<p class="text-red-400 text-sm">Search failed. The sidekick will handle this request directly.</p>`;
        }
    }

    renderCardsList(container, cards) {
        if (!cards.length) {
            container.innerHTML = '<p class="text-white/40 text-sm">No cards to display.</p>';
            return;
        }

        const html = cards.map(card => {
            const due = card.due ? ` <span class="text-white/30 text-xs">(due: ${card.due.substring(0, 10)})</span>` : '';
            const labels = (card.labels || []).map(l =>
                `<span class="trello-label" style="background:${l.color || '#666'}">${this.escapeHtml(l.name || '')}</span>`
            ).join('');
            const url = card.url ? ` <a href="${card.url}" target="_blank" class="text-blue-400 text-xs hover:underline">Open</a>` : '';

            return `
                <div class="trello-card-item rounded-lg p-3">
                    <div class="flex items-start justify-between gap-2">
                        <div class="flex-1 min-w-0">
                            <h4 class="text-white text-sm font-medium">${this.escapeHtml(card.name || 'Untitled')}</h4>
                            <div class="flex items-center gap-2 mt-1 flex-wrap">
                                ${labels}
                                ${due}
                                ${url}
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }).join('');

        container.innerHTML = `<div class="trello-cards-list space-y-2">${html}</div>`;
    }

    renderBoardsList(container, boards) {
        const html = boards.map(board => `
            <div class="trello-board-item rounded-lg p-3">
                <div class="flex items-center gap-2">
                    <span class="text-blue-400 text-lg">📋</span>
                    <span class="text-white text-sm">${this.escapeHtml(board.name || 'Untitled')}</span>
                    ${board.url ? `<a href="${board.url}" target="_blank" class="text-blue-400 text-xs hover:underline ml-auto">Open</a>` : ''}
                </div>
            </div>
        `).join('');

        container.innerHTML = `<div class="trello-boards-list space-y-2">${html}</div>`;
    }

    async sendAgentMessage(message) {
        console.log('[trello-widget] Sending agent message:', message);
        const event = new CustomEvent('widget-agent-request', {
            detail: { message, widgetId: this.id, type: 'trello' },
            bubbles: true,
        });
        this.element.dispatchEvent(event);
        return null;
    }
}

// Register with widget system
if (typeof window.widgetRegistry !== 'undefined') {
    window.widgetRegistry.register('trello', TrelloWidget);
    console.log('[trello-widget] Widget registered with registry');
} else {
    document.addEventListener('DOMContentLoaded', () => {
        if (typeof window.widgetRegistry !== 'undefined') {
            window.widgetRegistry.register('trello', TrelloWidget);
            console.log('[trello-widget] Widget registered with registry (delayed)');
        }
    });
}
