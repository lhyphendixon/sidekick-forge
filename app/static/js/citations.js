/**
 * Citations UI Component
 * Handles rendering and interaction with RAG citations in chat messages
 */

class CitationsComponent {
    constructor() {
        this.maxDocumentsShown = 1; // Show only 1 source by default (collapsed)
        this.maxChunksPerDoc = 3;   // Maximum chunks per document in tooltip
    }

    /**
     * Render citations for a message
     * @param {Array} citations - Array of citation objects
     * @param {string} messageId - Unique message identifier
     * @returns {HTMLElement} Citations container element
     */
    render(citations, messageId) {
        if (!citations || citations.length === 0) {
            return null;
        }

        // Group citations by document
        const docGroups = this.groupCitationsByDocument(citations);

        // Sort: Prediction Market first, then DocumentSense, then by similarity
        const sortedDocGroups = this.sortDocGroups(docGroups);

        // Check for special citation types to adjust how many to show
        const hasPredictionMarket = sortedDocGroups.some(doc =>
            doc.source === 'prediction_market' || doc.source_type === 'prediction_market'
        );
        const hasDocumentSense = sortedDocGroups.some(doc =>
            doc.source === 'documentsense' || (doc.title && doc.title.startsWith('DocumentSense:'))
        );

        // Determine how many docs to show by default
        let docsToShow = this.maxDocumentsShown;
        if (hasPredictionMarket) docsToShow++;
        if (hasDocumentSense) docsToShow++;
        docsToShow = Math.min(docsToShow, sortedDocGroups.length);

        const topDocuments = sortedDocGroups.slice(0, docsToShow);

        // Create container
        const container = document.createElement('div');
        container.className = 'citations-container';
        container.setAttribute('data-message-id', messageId);

        // Create header
        const header = document.createElement('div');
        header.className = 'citations-header';
        header.innerHTML = `
            <svg class="citations-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                <polyline points="14,2 14,8 20,8"></polyline>
                <line x1="16" y1="13" x2="8" y2="13"></line>
                <line x1="16" y1="17" x2="8" y2="17"></line>
                <polyline points="10,9 9,9 8,9"></polyline>
            </svg>
            <span class="citations-label">Sources (${topDocuments.length})</span>
        `;
        container.appendChild(header);

        // Create citations list
        const citationsList = document.createElement('div');
        citationsList.className = 'citations-list';

        topDocuments.forEach((docGroup, index) => {
            const citationItem = this.createCitationItem(docGroup, index + 1);
            citationsList.appendChild(citationItem);
        });

        container.appendChild(citationsList);

        // Add expand/collapse functionality if there are more documents than shown
        if (sortedDocGroups.length > docsToShow) {
            this.addExpandButton(container, citations, sortedDocGroups.length, messageId, docsToShow);
        }

        return container;
    }

    /**
     * Group citations by document ID
     * @param {Array} citations - Array of citation objects
     * @returns {Array} Array of document groups
     */
    groupCitationsByDocument(citations) {
        const groups = new Map();

        citations.forEach(citation => {
            // Use document_id if doc_id is not present (for DocumentSense citations)
            const docId = citation.doc_id || citation.document_id || citation.id;

            if (!groups.has(docId)) {
                groups.set(docId, {
                    doc_id: docId,
                    title: citation.title,
                    source_url: citation.source_url,
                    source_type: citation.source_type,
                    source: citation.source,  // Preserve source field for DocumentSense detection
                    chunks: [],
                    bestSimilarity: citation.similarity || 1.0
                });
            }

            const group = groups.get(docId);
            group.chunks.push(citation);
            group.bestSimilarity = Math.max(group.bestSimilarity, citation.similarity || 1.0);
        });

        return Array.from(groups.values());
    }

    /**
     * Check if a doc group is a prediction market citation
     */
    isPredictionMarket(docGroup) {
        return docGroup.source === 'prediction_market' || docGroup.source_type === 'prediction_market';
    }

    /**
     * Create a single citation item
     * @param {Object} docGroup - Document group object
     * @param {number} index - Citation index for display
     * @returns {HTMLElement} Citation item element
     */
    createCitationItem(docGroup, index) {
        // Prediction market gets its own special rendering
        if (this.isPredictionMarket(docGroup)) {
            return this.createPredictionMarketItem(docGroup);
        }

        const item = document.createElement('div');

        // Check if this is a DocumentSense citation
        const isDocumentSense = docGroup.source === 'documentsense' ||
                                (docGroup.title && docGroup.title.startsWith('DocumentSense:'));

        item.className = isDocumentSense ? 'citation-item citation-documentsense' : 'citation-item';

        // Extract domain from URL for display (with null check)
        const sourceUrl = docGroup.source_url || '';
        const domain = this.extractDomain(sourceUrl);

        // Truncate long titles (with null check)
        const title = docGroup.title || 'Untitled Source';
        const displayTitle = this.truncateText(title, 50);

        // Build citation HTML with proper fallbacks
        const linkHtml = sourceUrl
            ? `<a href="${sourceUrl}"
                   target="_blank"
                   rel="noopener noreferrer"
                   class="citation-link"
                   title="${title}">
                    ${displayTitle}
                </a>`
            : `<span class="citation-link" title="${title}">${displayTitle}</span>`;

        item.innerHTML = `
            <div class="citation-content">
                <span class="citation-index">[${index}]</span>
                ${linkHtml}
                ${domain ? `<span class="citation-domain">${domain}</span>` : ''}
                ${docGroup.chunks.length > 1 ?
                    `<span class="citation-chunk-count">(${docGroup.chunks.length} sections)</span>` :
                    ''}
            </div>
        `;

        // Add tooltip with chunk details if there are multiple chunks
        if (docGroup.chunks.length > 1) {
            this.addTooltip(item, docGroup);
        }

        return item;
    }

    /**
     * Create prediction market insight item with expandable market data
     * @param {Object} docGroup - Document group with prediction market data
     * @returns {HTMLElement} Prediction market citation element
     */
    createPredictionMarketItem(docGroup) {
        const item = document.createElement('div');
        item.className = 'citation-item citation-prediction-market';

        // Parse market data from the first chunk's content
        let marketData = {};
        try {
            const chunk = docGroup.chunks[0];
            if (chunk && chunk.content) {
                marketData = typeof chunk.content === 'string'
                    ? JSON.parse(chunk.content)
                    : chunk.content;
            }
        } catch (e) {
            console.warn('Failed to parse prediction market data:', e);
        }

        const markets = marketData.markets || [];
        const query = marketData.query || '';
        const source = marketData.source || 'Polymarket';

        // Build the collapsed header
        item.innerHTML = `
            <div class="citation-content pm-header" role="button" tabindex="0" aria-expanded="false">
                <svg class="pm-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="22,12 18,12 15,21 9,3 6,12 2,12"></polyline>
                </svg>
                <span class="citation-link">Prediction Market Insight</span>
                <span class="citation-domain">${source}</span>
                <svg class="pm-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="6,9 12,15 18,9"></polyline>
                </svg>
            </div>
            <div class="pm-panel" style="display:none;">
                ${this.renderMarketPanel(markets, query, source)}
            </div>
        `;

        // Toggle expand/collapse on click
        const header = item.querySelector('.pm-header');
        const panel = item.querySelector('.pm-panel');
        const chevron = item.querySelector('.pm-chevron');

        header.addEventListener('click', () => {
            const expanded = panel.style.display !== 'none';
            panel.style.display = expanded ? 'none' : 'block';
            chevron.style.transform = expanded ? '' : 'rotate(180deg)';
            header.setAttribute('aria-expanded', !expanded);
        });
        header.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                header.click();
            }
        });

        return item;
    }

    /**
     * Render the expandable market data panel
     * @param {Array} markets - Array of market objects
     * @param {string} query - Original search query
     * @param {string} source - Data source name
     * @returns {string} HTML string for the panel
     */
    renderMarketPanel(markets, query, source) {
        if (!markets || markets.length === 0) {
            return '<div class="pm-empty">No market data available</div>';
        }

        const rows = markets.map(market => {
            const question = market.question || '';
            const probs = market.probabilities || {};
            const volume = market.volume_usd || 0;

            // Find the "Yes" probability or the first probability
            let yesProb = probs['Yes'];
            if (yesProb === undefined) {
                const values = Object.values(probs);
                yesProb = values.length > 0 ? values[0] : null;
            }

            const probDisplay = yesProb !== null && yesProb !== undefined
                ? `${yesProb}%`
                : 'N/A';

            const volumeDisplay = volume >= 1000000
                ? `$${(volume / 1000000).toFixed(1)}M`
                : volume >= 1000
                    ? `$${(volume / 1000).toFixed(0)}K`
                    : `$${Math.round(volume)}`;

            // Short question — strip "Will ... win the ..." prefix for cleaner display
            const shortQ = question
                .replace(/^Will\s+/i, '')
                .replace(/\s+win the .+$/i, '');

            return `
                <div class="pm-row">
                    <div class="pm-row-label">${shortQ}</div>
                    <div class="pm-row-bar-wrap">
                        <div class="pm-row-bar" style="width:${Math.min(yesProb || 0, 100)}%"></div>
                    </div>
                    <div class="pm-row-prob">${probDisplay}</div>
                    <div class="pm-row-vol">${volumeDisplay}</div>
                </div>
            `;
        }).join('');

        return `
            <div class="pm-markets">
                ${rows}
            </div>
            <div class="pm-footer">
                <span>Data from ${source} &middot; reflects crowd sentiment, not certainty</span>
            </div>
        `;
    }

    /**
     * Add tooltip showing chunk details
     * @param {HTMLElement} element - Element to add tooltip to
     * @param {Object} docGroup - Document group with chunks
     */
    addTooltip(element, docGroup) {
        let tooltipTimeout;
        let tooltip;

        element.addEventListener('mouseenter', () => {
            tooltipTimeout = setTimeout(() => {
                tooltip = this.createTooltip(docGroup);
                document.body.appendChild(tooltip);
                this.positionTooltip(tooltip, element);
            }, 500); // 500ms delay
        });

        element.addEventListener('mouseleave', () => {
            clearTimeout(tooltipTimeout);
            if (tooltip) {
                tooltip.remove();
                tooltip = null;
            }
        });
    }

    /**
     * Create tooltip content
     * @param {Object} docGroup - Document group with chunks
     * @returns {HTMLElement} Tooltip element
     */
    createTooltip(docGroup) {
        const tooltip = document.createElement('div');
        tooltip.className = 'citation-tooltip';

        const chunksToShow = docGroup.chunks
            .sort((a, b) => b.similarity - a.similarity)
            .slice(0, this.maxChunksPerDoc);

        const content = chunksToShow.map(chunk => {
            const preview = this.truncateText(chunk.content || '', 100);
            const location = chunk.page_number
                ? `Page ${chunk.page_number}`
                : `Section ${chunk.chunk_index + 1}`;

            return `
                <div class="tooltip-chunk">
                    <div class="tooltip-chunk-location">${location}</div>
                    <div class="tooltip-chunk-preview">"${preview}"</div>
                    <div class="tooltip-chunk-similarity">Relevance: ${Math.round(chunk.similarity * 100)}%</div>
                </div>
            `;
        }).join('');

        tooltip.innerHTML = `
            <div class="tooltip-header">${docGroup.title}</div>
            <div class="tooltip-chunks">${content}</div>
            ${docGroup.chunks.length > this.maxChunksPerDoc ?
                `<div class="tooltip-footer">+${docGroup.chunks.length - this.maxChunksPerDoc} more sections</div>` :
                ''}
        `;

        return tooltip;
    }

    /**
     * Position tooltip relative to target element
     * @param {HTMLElement} tooltip - Tooltip element
     * @param {HTMLElement} target - Target element
     */
    positionTooltip(tooltip, target) {
        const targetRect = target.getBoundingClientRect();
        const tooltipRect = tooltip.getBoundingClientRect();

        let top = targetRect.bottom + 10;
        let left = targetRect.left;

        // Adjust if tooltip would go off-screen
        if (left + tooltipRect.width > window.innerWidth) {
            left = window.innerWidth - tooltipRect.width - 10;
        }

        if (top + tooltipRect.height > window.innerHeight) {
            top = targetRect.top - tooltipRect.height - 10;
        }

        tooltip.style.position = 'fixed';
        tooltip.style.top = `${top}px`;
        tooltip.style.left = `${left}px`;
        tooltip.style.zIndex = '1000';
    }

    /**
     * Add expand/collapse button for hidden citations
     * @param {HTMLElement} container - Citations container
     * @param {Array} allCitations - All citations array
     * @param {number} totalDocCount - Total number of unique documents
     * @param {string} messageId - Message ID
     * @param {number} currentlyShown - Number of documents currently shown
     */
    addExpandButton(container, allCitations, totalDocCount, messageId, currentlyShown) {
        const hiddenCount = totalDocCount - currentlyShown;
        const expandButton = document.createElement('button');
        expandButton.className = 'citations-expand-btn';
        expandButton.textContent = `view ${hiddenCount} more source${hiddenCount > 1 ? 's' : ''}`;

        // Store currentlyShown on the container for collapse
        container.setAttribute('data-docs-shown', currentlyShown);

        expandButton.addEventListener('click', () => {
            // Toggle expanded state
            const isExpanded = container.classList.contains('citations-expanded');

            if (isExpanded) {
                // Collapse
                this.collapseCitations(container, allCitations, totalDocCount, messageId);
            } else {
                // Expand
                this.expandCitations(container, allCitations, messageId);
            }
        });

        container.appendChild(expandButton);
    }

    /**
     * Sort document groups: Prediction Market first, then DocumentSense, then by similarity
     * @param {Array} docGroups - Array of document groups
     * @returns {Array} Sorted document groups
     */
    sortDocGroups(docGroups) {
        return docGroups.sort((a, b) => {
            const aIsPM = this.isPredictionMarket(a);
            const bIsPM = this.isPredictionMarket(b);
            if (aIsPM && !bIsPM) return -1;
            if (!aIsPM && bIsPM) return 1;
            const aIsDS = a.source === 'documentsense' || (a.title && a.title.startsWith('DocumentSense:'));
            const bIsDS = b.source === 'documentsense' || (b.title && b.title.startsWith('DocumentSense:'));
            if (aIsDS && !bIsDS) return -1;
            if (!aIsDS && bIsDS) return 1;
            return b.bestSimilarity - a.bestSimilarity;
        });
    }

    /**
     * Expand citations to show all documents
     * @param {HTMLElement} container - Citations container
     * @param {Array} allCitations - All citations array
     * @param {string} messageId - Message ID
     */
    expandCitations(container, allCitations, messageId) {
        const docGroups = this.groupCitationsByDocument(allCitations);
        const sortedDocGroups = this.sortDocGroups(docGroups);
        const citationsList = container.querySelector('.citations-list');
        const expandBtn = container.querySelector('.citations-expand-btn');

        // Clear current list
        citationsList.innerHTML = '';

        // Add all documents
        sortedDocGroups.forEach((docGroup, index) => {
            const citationItem = this.createCitationItem(docGroup, index + 1);
            citationsList.appendChild(citationItem);
        });

        // Update button
        expandBtn.textContent = 'Show fewer sources';
        container.classList.add('citations-expanded');
    }

    /**
     * Collapse citations to show only top documents
     * @param {HTMLElement} container - Citations container
     * @param {Array} allCitations - All citations array
     * @param {number} totalDocCount - Total number of unique documents
     * @param {string} messageId - Message ID
     */
    collapseCitations(container, allCitations, totalDocCount, messageId) {
        const docGroups = this.groupCitationsByDocument(allCitations);
        const sortedDocGroups = this.sortDocGroups(docGroups);

        // Get the number of docs to show from the stored attribute
        const docsToShow = parseInt(container.getAttribute('data-docs-shown')) || this.maxDocumentsShown;
        const topDocuments = sortedDocGroups.slice(0, docsToShow);

        const citationsList = container.querySelector('.citations-list');
        const expandBtn = container.querySelector('.citations-expand-btn');

        // Clear current list
        citationsList.innerHTML = '';

        // Add top documents only
        topDocuments.forEach((docGroup, index) => {
            const citationItem = this.createCitationItem(docGroup, index + 1);
            citationsList.appendChild(citationItem);
        });

        // Update button
        const hiddenCount = totalDocCount - docsToShow;
        expandBtn.textContent = `view ${hiddenCount} more source${hiddenCount > 1 ? 's' : ''}`;
        container.classList.remove('citations-expanded');
    }

    /**
     * Extract domain from URL
     * @param {string} url - Full URL
     * @returns {string} Domain name
     */
    extractDomain(url) {
        if (!url) return '';
        try {
            return new URL(url).hostname.replace('www.', '');
        } catch {
            return url; // Return original if not a valid URL
        }
    }

    /**
     * Truncate text to specified length
     * @param {string} text - Text to truncate
     * @param {number} maxLength - Maximum length
     * @returns {string} Truncated text
     */
    truncateText(text, maxLength) {
        if (!text) return '';
        if (text.length <= maxLength) return text;
        return text.substring(0, maxLength - 3) + '...';
    }
}

// Create global instance
window.CitationsComponent = CitationsComponent;

// Export for module use
if (typeof module !== 'undefined' && module.exports) {
    module.exports = CitationsComponent;
}
