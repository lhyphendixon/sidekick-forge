/**
 * Content Catalyst Widget
 *
 * Interactive widget for configuring and running the Content Catalyst
 * multi-phase article generation pipeline.
 */

console.log('[cc-widget] Script loaded, BaseWidget available:', typeof BaseWidget !== 'undefined');

class ContentCatalystWidget extends BaseWidget {
    constructor(config) {
        super(config);
        this.phases = [
            { id: 'input', label: 'Configure', icon: '⚙️' },
            { id: 'research', label: 'Researching', icon: '🔍' },
            { id: 'architecture', label: 'Structuring', icon: '🏗️' },
            { id: 'drafting', label: 'Writing', icon: '✍️' },
            { id: 'integrity', label: 'Fact-checking', icon: '✓' },
            { id: 'polishing', label: 'Polishing', icon: '✨' },
            { id: 'complete', label: 'Complete', icon: '🎉' }
        ];
        this.currentPhase = 'input';
        this.runId = null;
        this.articles = null;
        this.uploadedFile = null;
        this.pollInterval = null;
        // Document picker state
        this.selectedDocument = null;
        this.documentList = [];
        this.documentPickerOpen = false;
    }

    render(container) {
        super.render(container);
        this.element.className = 'cc-widget glass-container rounded-2xl overflow-hidden';

        // Check if we're restoring from saved state
        if (this.config.restoredState && this.config.restoredState.state === 'complete') {
            console.log('[cc-widget] Restoring completed state from saved data');
            this.runId = this.config.restoredState.run_id;
            this.articles = this.config.restoredState.articles;
            this.renderCompletePhase();
        } else {
            this.renderInputPhase();
        }
    }

    renderInputPhase() {
        this.element.innerHTML = `
            <div class="cc-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="cc-icon w-10 h-10 rounded-xl bg-gradient-to-br from-brand-teal to-brand-orange flex items-center justify-center text-xl">
                        📝
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">Content Catalyst</h3>
                        <p class="text-white/50 text-sm">Generate research-backed articles</p>
                    </div>
                </div>
            </div>

            <div class="cc-body p-4 space-y-4">
                <!-- Always-visible Instructions Field -->
                <div class="cc-instructions-field">
                    <label class="cc-instructions-label">Instructions (optional)</label>
                    <textarea
                        id="cc-instructions-input"
                        class="cc-instructions-textarea"
                        rows="2"
                        placeholder="Describe what you want to create, any specific angle, or additional context..."
                    >${this.escapeHtml(this.config.suggested_topic || '')}</textarea>
                </div>

                <!-- Source Type Tabs -->
                <div class="cc-source-tabs flex gap-2">
                    <button type="button" class="cc-tab active flex-1 py-2 px-3 rounded-lg text-sm font-medium transition-all" data-source="document">
                        📄 Document
                    </button>
                    <button type="button" class="cc-tab flex-1 py-2 px-3 rounded-lg text-sm font-medium transition-all" data-source="url">
                        🔗 URL
                    </button>
                    <button type="button" class="cc-tab flex-1 py-2 px-3 rounded-lg text-sm font-medium transition-all" data-source="mp3">
                        🎙️ Audio
                    </button>
                </div>

                <!-- Document Selection (default) -->
                <div class="cc-input-section" data-section="document">
                    <label class="block text-white/70 text-sm mb-2">Select a knowledge base document</label>
                    <div class="cc-document-selection">
                        ${this.selectedDocument ? `
                            <div class="cc-selected-document" id="cc-select-document-btn">
                                <span class="cc-selected-document-icon">📄</span>
                                <span class="cc-selected-document-title">${this.escapeHtml(this.selectedDocument.title)}</span>
                                <span class="cc-selected-document-change">Change</span>
                            </div>
                        ` : `
                            <button type="button" id="cc-select-document-btn" class="w-full py-3 px-4 rounded-xl border-2 border-dashed border-white/20 text-white/60 hover:border-brand-teal/50 hover:text-white/80 transition-all flex items-center justify-center gap-2">
                                <span>📄</span>
                                <span>Click to select a document</span>
                            </button>
                        `}
                    </div>
                </div>

                <!-- URL Input -->
                <div class="cc-input-section hidden" data-section="url">
                    <label class="block text-white/70 text-sm mb-2">Enter a URL to analyze</label>
                    <input
                        type="url"
                        id="cc-url-input"
                        class="w-full glass-input rounded-xl p-3 text-white"
                        placeholder="https://example.com/article"
                    />
                </div>

                <!-- Audio Upload -->
                <div class="cc-input-section hidden" data-section="mp3">
                    <label class="block text-white/70 text-sm mb-2">Upload an audio file to transcribe</label>
                    <div class="cc-upload-zone border-2 border-dashed border-white/20 rounded-xl p-6 text-center cursor-pointer hover:border-brand-teal/50 transition-colors">
                        <input type="file" id="cc-audio-input" accept="audio/*" class="hidden" />
                        <div class="cc-upload-content">
                            <div class="text-3xl mb-2">🎵</div>
                            <p class="text-white/70 text-sm">Click or drag audio file here</p>
                            <p class="text-white/40 text-xs mt-1">MP3, WAV, M4A up to 100MB</p>
                        </div>
                        <div class="cc-upload-preview hidden">
                            <div class="flex items-center gap-3 justify-center">
                                <span class="text-2xl">🎵</span>
                                <span class="cc-filename text-white/90"></span>
                                <button type="button" class="cc-remove-file text-red-400 hover:text-red-300">✕</button>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Options Row -->
                <div class="grid grid-cols-2 gap-4">
                    <div>
                        <label class="block text-white/70 text-sm mb-2">Word Count</label>
                        <select id="cc-word-count" class="w-full glass-input rounded-xl p-3 text-white">
                            <option value="1000">~1,000 words</option>
                            <option value="1500" selected>~1,500 words</option>
                            <option value="2000">~2,000 words</option>
                            <option value="2500">~2,500 words</option>
                            <option value="3000">~3,000 words</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-white/70 text-sm mb-2">Writing Style</label>
                        <select id="cc-style" class="w-full glass-input rounded-xl p-3 text-white">
                            <option value="">Default</option>
                            <option value="professional">Professional</option>
                            <option value="conversational">Conversational</option>
                            <option value="technical">Technical</option>
                            <option value="journalistic">Journalistic</option>
                            <option value="academic">Academic</option>
                        </select>
                    </div>
                </div>

                <!-- Advanced Options (collapsible) -->
                <details class="cc-advanced">
                    <summary class="text-white/50 text-sm cursor-pointer hover:text-white/70 transition-colors">
                        Advanced Options
                    </summary>
                    <div class="mt-3 space-y-3">
                        <label class="flex items-center gap-3 cursor-pointer">
                            <input type="checkbox" id="cc-use-perplexity" checked class="cc-checkbox" />
                            <span class="text-white/70 text-sm">Use web research (Perplexity)</span>
                        </label>
                        <label class="flex items-center gap-3 cursor-pointer">
                            <input type="checkbox" id="cc-use-kb" checked class="cc-checkbox" />
                            <span class="text-white/70 text-sm">Search knowledge base</span>
                        </label>
                    </div>
                </details>
            </div>

            <div class="cc-footer p-4 border-t border-white/10">
                <button type="button" id="cc-submit-btn" class="w-full py-3 px-6 rounded-xl bg-gradient-to-r from-brand-teal to-brand-orange text-white font-semibold hover:opacity-90 transition-opacity flex items-center justify-center gap-2">
                    <span>Generate Articles</span>
                    <span class="cc-submit-arrow">→</span>
                </button>
            </div>

            <!-- Document Picker Overlay (hidden by default) -->
            <div class="cc-document-picker-overlay" id="cc-document-picker-overlay">
                <div class="cc-document-picker" id="cc-document-picker">
                    <div class="cc-document-picker-header">
                        <h3>Select a Document</h3>
                        <button type="button" class="cc-document-picker-close" id="cc-document-picker-close">×</button>
                    </div>
                    <div class="cc-document-picker-search">
                        <input type="text" id="cc-document-search" placeholder="Search documents..." />
                    </div>
                    <div class="cc-document-picker-list" id="cc-document-list">
                        <!-- Documents rendered here -->
                    </div>
                </div>
            </div>
        `;

        // Set default source type to document
        this.config.sourceType = 'document';
        this.bindInputEvents();
    }

    bindInputEvents() {
        console.log('[cc-widget] bindInputEvents called');
        console.log('[cc-widget] this.element:', this.element);

        // Source type tabs
        const tabs = this.element.querySelectorAll('.cc-tab');
        console.log('[cc-widget] Found tabs:', tabs.length);
        tabs.forEach(tab => {
            tab.addEventListener('click', () => this.switchSourceType(tab.dataset.source));
        });

        // Document picker button
        const selectDocBtn = this.element.querySelector('#cc-select-document-btn');
        selectDocBtn?.addEventListener('click', () => this.openDocumentPicker());

        // Document picker close button
        const closePickerBtn = this.element.querySelector('#cc-document-picker-close');
        closePickerBtn?.addEventListener('click', () => this.closeDocumentPicker());

        // Document picker overlay click (close on background click)
        const overlay = this.element.querySelector('#cc-document-picker-overlay');
        overlay?.addEventListener('click', (e) => {
            if (e.target === overlay) this.closeDocumentPicker();
        });

        // Document search input
        const searchInput = this.element.querySelector('#cc-document-search');
        searchInput?.addEventListener('input', (e) => this.filterDocuments(e.target.value));

        // File upload
        const uploadZone = this.element.querySelector('.cc-upload-zone');
        const fileInput = this.element.querySelector('#cc-audio-input');

        uploadZone?.addEventListener('click', () => fileInput?.click());
        uploadZone?.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadZone.classList.add('border-brand-teal');
        });
        uploadZone?.addEventListener('dragleave', () => {
            uploadZone.classList.remove('border-brand-teal');
        });
        uploadZone?.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadZone.classList.remove('border-brand-teal');
            const file = e.dataTransfer.files[0];
            if (file) this.handleFileSelect(file);
        });

        fileInput?.addEventListener('change', (e) => {
            if (e.target.files[0]) this.handleFileSelect(e.target.files[0]);
        });

        // Remove file button
        this.element.querySelector('.cc-remove-file')?.addEventListener('click', (e) => {
            e.stopPropagation();
            this.removeFile();
        });

        // Submit button
        const submitBtn = this.element.querySelector('#cc-submit-btn');
        console.log('[cc-widget] Submit button found:', !!submitBtn);
        submitBtn?.addEventListener('click', (e) => {
            console.log('[cc-widget] Submit button clicked!');
            e.preventDefault();
            // Add loading state immediately
            submitBtn.classList.add('loading');
            submitBtn.disabled = true;
            this.submit().finally(() => {
                // Remove loading state if we return to input phase
                if (this.currentPhase === 'input') {
                    submitBtn.classList.remove('loading');
                    submitBtn.disabled = false;
                }
            });
        });
    }

    // Document Picker Methods
    async openDocumentPicker() {
        console.log('[cc-widget] Opening document picker');
        this.documentPickerOpen = true;

        const overlay = this.element.querySelector('#cc-document-picker-overlay');
        const picker = this.element.querySelector('#cc-document-picker');
        const list = this.element.querySelector('#cc-document-list');

        // Show loading state
        list.innerHTML = `
            <div class="cc-document-picker-loading">
                <div class="spinner"></div>
                <p>Loading documents...</p>
            </div>
        `;

        // Show overlay and picker with animation
        overlay?.classList.add('show');
        requestAnimationFrame(() => {
            picker?.classList.add('show');
        });

        // Load documents
        await this.loadDocuments();
    }

    closeDocumentPicker() {
        console.log('[cc-widget] Closing document picker');
        this.documentPickerOpen = false;

        const overlay = this.element.querySelector('#cc-document-picker-overlay');
        const picker = this.element.querySelector('#cc-document-picker');

        // Animate out
        picker?.classList.remove('show');
        setTimeout(() => {
            overlay?.classList.remove('show');
        }, 300);

        // Clear search
        const searchInput = this.element.querySelector('#cc-document-search');
        if (searchInput) searchInput.value = '';
    }

    async loadDocuments() {
        console.log('[cc-widget] Loading documents for agent:', this.config.agentId);

        try {
            const url = `/api/v1/content-catalyst/documents/${this.config.agentId}?client_id=${this.config.clientId}`;
            const response = await fetch(url);

            if (!response.ok) {
                throw new Error('Failed to load documents');
            }

            const data = await response.json();
            this.documentList = data.documents || [];
            console.log('[cc-widget] Loaded documents:', this.documentList.length);

            this.renderDocumentList(this.documentList);
        } catch (error) {
            console.error('[cc-widget] Failed to load documents:', error);
            const list = this.element.querySelector('#cc-document-list');
            list.innerHTML = `
                <div class="cc-document-picker-empty">
                    <p>Failed to load documents</p>
                    <p style="font-size: 12px; margin-top: 8px;">${error.message}</p>
                </div>
            `;
        }
    }

    renderDocumentList(documents) {
        const list = this.element.querySelector('#cc-document-list');

        if (!documents || documents.length === 0) {
            list.innerHTML = `
                <div class="cc-document-picker-empty">
                    <p>No documents available</p>
                    <p style="font-size: 12px; margin-top: 8px;">Upload documents to your knowledge base first</p>
                </div>
            `;
            return;
        }

        list.innerHTML = documents.map(doc => `
            <div class="cc-document-item ${this.selectedDocument?.id === doc.id ? 'selected' : ''}" data-doc-id="${doc.id}" data-doc-title="${this.escapeHtml(doc.title)}">
                <div class="cc-document-icon">📄</div>
                <div class="cc-document-info">
                    <div class="cc-document-title">${this.escapeHtml(doc.title)}</div>
                    <div class="cc-document-meta">${this.formatDate(doc.created_at)}</div>
                </div>
            </div>
        `).join('');

        // Bind click events
        list.querySelectorAll('.cc-document-item').forEach(item => {
            item.addEventListener('click', () => {
                const docId = parseInt(item.dataset.docId);
                const docTitle = item.dataset.docTitle;
                this.selectDocument(docId, docTitle);
            });
        });
    }

    filterDocuments(searchTerm) {
        const term = searchTerm.toLowerCase().trim();
        if (!term) {
            this.renderDocumentList(this.documentList);
            return;
        }

        const filtered = this.documentList.filter(doc =>
            doc.title.toLowerCase().includes(term)
        );
        this.renderDocumentList(filtered);
    }

    selectDocument(docId, docTitle) {
        console.log('[cc-widget] Selected document:', docId, docTitle);
        this.selectedDocument = { id: docId, title: docTitle };

        // Update the UI to show selected document
        const selection = this.element.querySelector('.cc-document-selection');
        if (selection) {
            selection.innerHTML = `
                <div class="cc-selected-document" id="cc-select-document-btn">
                    <span class="cc-selected-document-icon">📄</span>
                    <span class="cc-selected-document-title">${this.escapeHtml(docTitle)}</span>
                    <span class="cc-selected-document-change">Change</span>
                </div>
            `;
            // Rebind click event
            selection.querySelector('#cc-select-document-btn')?.addEventListener('click', () => this.openDocumentPicker());
        }

        // Close the picker
        this.closeDocumentPicker();
    }

    formatDate(dateStr) {
        if (!dateStr) return '';
        try {
            const date = new Date(dateStr);
            return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
        } catch {
            return dateStr;
        }
    }

    switchSourceType(type) {
        console.log('[cc-widget] switchSourceType called with:', type);
        // Update tabs
        this.element.querySelectorAll('.cc-tab').forEach(tab => {
            tab.classList.toggle('active', tab.dataset.source === type);
        });

        // Show/hide sections
        this.element.querySelectorAll('.cc-input-section').forEach(section => {
            section.classList.toggle('hidden', section.dataset.section !== type);
        });

        this.config.sourceType = type;
        console.log('[cc-widget] sourceType set to:', this.config.sourceType);
    }

    handleFileSelect(file) {
        if (!file.type.startsWith('audio/')) {
            alert('Please select an audio file');
            return;
        }
        if (file.size > 100 * 1024 * 1024) {
            alert('File is too large. Maximum size is 100MB.');
            return;
        }

        this.uploadedFile = file;

        // Update UI
        const uploadContent = this.element.querySelector('.cc-upload-content');
        const uploadPreview = this.element.querySelector('.cc-upload-preview');
        const filename = this.element.querySelector('.cc-filename');

        uploadContent?.classList.add('hidden');
        uploadPreview?.classList.remove('hidden');
        if (filename) filename.textContent = file.name;
    }

    removeFile() {
        this.uploadedFile = null;

        const uploadContent = this.element.querySelector('.cc-upload-content');
        const uploadPreview = this.element.querySelector('.cc-upload-preview');
        const fileInput = this.element.querySelector('#cc-audio-input');

        uploadContent?.classList.remove('hidden');
        uploadPreview?.classList.add('hidden');
        if (fileInput) fileInput.value = '';
    }

    async submit() {
        console.log('[cc-widget] Submit called, sourceType:', this.config.sourceType);
        console.log('[cc-widget] Config:', JSON.stringify(this.config, null, 2));
        console.log('[cc-widget] uploadedFile:', this.uploadedFile);
        console.log('[cc-widget] selectedDocument:', this.selectedDocument);

        const sourceType = this.config.sourceType || 'document';
        let sourceContent = '';
        let documentId = null;
        let documentTitle = null;

        // Get text instructions (always available)
        const textInstructions = this.element.querySelector('#cc-instructions-input')?.value?.trim() || '';

        // Get source content based on type
        if (sourceType === 'document') {
            if (!this.selectedDocument) {
                alert('Please select a document');
                return;
            }
            documentId = this.selectedDocument.id;
            documentTitle = this.selectedDocument.title;
            sourceContent = `Document: ${documentTitle}`;
        } else if (sourceType === 'url') {
            sourceContent = this.element.querySelector('#cc-url-input')?.value?.trim();
            if (!sourceContent) {
                alert('Please enter a URL');
                return;
            }
        } else if (sourceType === 'mp3') {
            console.log('[cc-widget] MP3 mode, uploadedFile:', this.uploadedFile);
            if (!this.uploadedFile) {
                alert('Please upload an audio file');
                return;
            }
            // Upload file first, then use the returned URL
            console.log('[cc-widget] Calling uploadAudioFile...');
            try {
                sourceContent = await this.uploadAudioFile();
                console.log('[cc-widget] uploadAudioFile returned:', sourceContent);
            } catch (uploadError) {
                console.error('[cc-widget] uploadAudioFile threw error:', uploadError);
                this.renderError('Upload failed: ' + uploadError.message);
                return;
            }
            if (!sourceContent) {
                console.log('[cc-widget] No sourceContent from upload, stopping');
                return;
            }
        }

        const wordCount = parseInt(this.element.querySelector('#cc-word-count')?.value || '1500');
        const style = this.element.querySelector('#cc-style')?.value || '';
        const usePerplexity = this.element.querySelector('#cc-use-perplexity')?.checked ?? true;
        const useKnowledgeBase = this.element.querySelector('#cc-use-kb')?.checked ?? true;

        // Show progress phase
        this.renderProgressPhase();

        // Build payload
        const payload = {
            source_type: sourceType,
            source_content: sourceContent,
            target_word_count: wordCount,
            style_prompt: style || undefined,
            use_perplexity: usePerplexity,
            use_knowledge_base: useKnowledgeBase
        };

        // Add text instructions if provided
        if (textInstructions) {
            payload.text_instructions = textInstructions;
        }

        // Add document info if document source
        if (sourceType === 'document' && documentId) {
            payload.document_id = documentId;
            payload.document_title = documentTitle;
        }

        // Call the API
        try {
            await this.startGeneration(payload);
        } catch (error) {
            console.error('[cc-widget] Generation failed:', error);
            this.renderError(error.message || 'Generation failed');
        }
    }

    async uploadAudioFile() {
        console.log('[cc-widget] uploadAudioFile called');
        console.log('[cc-widget] clientId:', this.config.clientId);
        console.log('[cc-widget] file:', this.uploadedFile?.name, this.uploadedFile?.size);

        this.setPhase('research'); // Show uploading state

        const formData = new FormData();
        formData.append('file', this.uploadedFile);

        const uploadUrl = `/api/v1/content-catalyst/upload-mp3?client_id=${this.config.clientId}`;
        console.log('[cc-widget] Upload URL:', uploadUrl);

        try {
            const response = await fetch(uploadUrl, {
                method: 'POST',
                body: formData
            });

            console.log('[cc-widget] Upload response status:', response.status);

            if (!response.ok) {
                const error = await response.json();
                console.error('[cc-widget] Upload error response:', error);
                throw new Error(error.detail || 'Upload failed');
            }

            const data = await response.json();
            console.log('[cc-widget] Upload success:', data);
            return data.file_url;
        } catch (error) {
            console.error('[cc-widget] Upload failed:', error);
            this.renderError('Failed to upload audio file: ' + error.message);
            return null;
        }
    }

    async startGeneration(payload) {
        console.log('[cc-widget] startGeneration called with:', payload);

        const params = new URLSearchParams({
            client_id: this.config.clientId
        });
        if (this.config.agentId) params.append('agent_id', this.config.agentId);
        if (this.config.userId) params.append('user_id', this.config.userId);
        if (this.config.conversationId) params.append('conversation_id', this.config.conversationId);

        const startUrl = `/api/v1/content-catalyst/start?${params}`;
        console.log('[cc-widget] Start URL:', startUrl);

        const response = await fetch(startUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        console.log('[cc-widget] Start response status:', response.status);

        if (!response.ok) {
            const error = await response.json();
            console.error('[cc-widget] Start error response:', error);
            throw new Error(error.detail || 'Failed to start generation');
        }

        const data = await response.json();
        console.log('[cc-widget] Start success:', data);

        if (data.success) {
            this.runId = data.run_id;
            this.articles = {
                article_1: data.article_1,
                article_2: data.article_2
            };
            this.renderCompletePhase();
            // Store result for persistence
            await this.storeResult();
        } else {
            throw new Error(data.message || 'Generation failed');
        }
    }

    async storeResult() {
        if (!this.runId || !this.articles || !this.config.conversationId) {
            console.log('[cc-widget] Cannot store result: missing runId, articles, or conversationId');
            return;
        }

        try {
            const params = new URLSearchParams({
                client_id: this.config.clientId,
                conversation_id: this.config.conversationId
            });

            const response = await fetch(`/api/v1/content-catalyst/store-result?${params}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    run_id: this.runId,
                    article_1: this.articles.article_1,
                    article_2: this.articles.article_2
                })
            });

            if (response.ok) {
                console.log('[cc-widget] Result stored for persistence');
            } else {
                console.warn('[cc-widget] Failed to store result:', await response.text());
            }
        } catch (err) {
            console.warn('[cc-widget] Error storing result:', err);
        }
    }

    renderProgressPhase() {
        this.currentPhase = 'research';
        this.element.innerHTML = `
            <div class="cc-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="cc-icon w-10 h-10 rounded-xl bg-gradient-to-br from-brand-teal to-brand-orange flex items-center justify-center text-xl animate-pulse">
                        ⚡
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">Generating Articles</h3>
                        <p class="text-white/50 text-sm">This may take a minute...</p>
                    </div>
                </div>
            </div>

            <div class="cc-body p-4">
                <div class="cc-progress-steps space-y-3">
                    ${this.phases.slice(1, -1).map((phase, index) => `
                        <div class="cc-step flex items-center gap-3" data-phase="${phase.id}">
                            <div class="cc-step-icon w-8 h-8 rounded-full flex items-center justify-center text-sm
                                ${index === 0 ? 'bg-brand-teal/30 text-brand-teal animate-pulse' : 'bg-white/10 text-white/40'}">
                                ${phase.icon}
                            </div>
                            <div class="flex-1">
                                <div class="text-white/90 text-sm font-medium">${phase.label}</div>
                            </div>
                            <div class="cc-step-status text-white/40 text-xs">
                                ${index === 0 ? 'In progress...' : 'Waiting'}
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }

    setPhase(phaseId) {
        this.currentPhase = phaseId;
        const phaseIndex = this.phases.findIndex(p => p.id === phaseId);

        this.element.querySelectorAll('.cc-step').forEach((step, index) => {
            const icon = step.querySelector('.cc-step-icon');
            const status = step.querySelector('.cc-step-status');
            const stepPhaseIndex = index + 1; // Offset by 1 since we skip 'input'

            if (stepPhaseIndex < phaseIndex) {
                // Completed
                icon.className = 'cc-step-icon w-8 h-8 rounded-full flex items-center justify-center text-sm bg-green-500/30 text-green-400';
                status.textContent = 'Done';
                status.className = 'cc-step-status text-green-400 text-xs';
            } else if (stepPhaseIndex === phaseIndex) {
                // Current
                icon.className = 'cc-step-icon w-8 h-8 rounded-full flex items-center justify-center text-sm bg-brand-teal/30 text-brand-teal animate-pulse';
                status.textContent = 'In progress...';
                status.className = 'cc-step-status text-brand-teal text-xs';
            } else {
                // Pending
                icon.className = 'cc-step-icon w-8 h-8 rounded-full flex items-center justify-center text-sm bg-white/10 text-white/40';
                status.textContent = 'Waiting';
                status.className = 'cc-step-status text-white/40 text-xs';
            }
        });
    }

    renderCompletePhase() {
        this.currentPhase = 'complete';
        this.element.innerHTML = `
            <div class="cc-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="cc-icon w-10 h-10 rounded-xl bg-gradient-to-br from-green-500 to-brand-teal flex items-center justify-center text-xl">
                        🎉
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">Articles Generated!</h3>
                        <p class="text-white/50 text-sm">Choose your preferred version</p>
                    </div>
                </div>
            </div>

            <div class="cc-body p-4 space-y-4">
                <div class="cc-article-card glass-elevated rounded-xl p-4 cursor-pointer hover:bg-white/5 transition-colors" data-article="1">
                    <div class="flex items-start gap-3">
                        <div class="text-2xl">📄</div>
                        <div class="flex-1 min-w-0">
                            <h4 class="text-white font-medium mb-1">Article Variation 1</h4>
                            <p class="text-white/50 text-sm">${this.articles?.article_1?.word_count || '~1500'} words</p>
                            <p class="text-white/60 text-sm mt-2 line-clamp-2">${this.getArticlePreview(this.articles?.article_1?.content)}</p>
                        </div>
                        <button type="button" class="cc-view-btn px-3 py-1.5 rounded-lg bg-brand-teal/20 text-brand-teal text-sm hover:bg-brand-teal/30 transition-colors">
                            View
                        </button>
                    </div>
                </div>

                <div class="cc-article-card glass-elevated rounded-xl p-4 cursor-pointer hover:bg-white/5 transition-colors" data-article="2">
                    <div class="flex items-start gap-3">
                        <div class="text-2xl">📄</div>
                        <div class="flex-1 min-w-0">
                            <h4 class="text-white font-medium mb-1">Article Variation 2</h4>
                            <p class="text-white/50 text-sm">${this.articles?.article_2?.word_count || '~1500'} words</p>
                            <p class="text-white/60 text-sm mt-2 line-clamp-2">${this.getArticlePreview(this.articles?.article_2?.content)}</p>
                        </div>
                        <button type="button" class="cc-view-btn px-3 py-1.5 rounded-lg bg-brand-teal/20 text-brand-teal text-sm hover:bg-brand-teal/30 transition-colors">
                            View
                        </button>
                    </div>
                </div>
            </div>

            <div class="cc-footer p-4 border-t border-white/10 flex gap-3">
                <button type="button" class="cc-new-btn flex-1 py-2.5 px-4 rounded-xl border border-white/20 text-white/70 hover:bg-white/5 transition-colors">
                    Generate New
                </button>
            </div>
        `;

        this.bindCompleteEvents();
    }

    bindCompleteEvents() {
        // View article buttons
        this.element.querySelectorAll('.cc-article-card').forEach(card => {
            card.addEventListener('click', () => {
                const articleNum = card.dataset.article;
                const article = articleNum === '1' ? this.articles?.article_1 : this.articles?.article_2;
                if (article) {
                    this.showArticleModal(article, articleNum);
                }
            });
        });

        // Generate new button
        this.element.querySelector('.cc-new-btn')?.addEventListener('click', () => {
            this.articles = null;
            this.runId = null;
            this.renderInputPhase();
        });
    }

    showArticleModal(article, variantNum) {
        // Create modal overlay
        const modal = document.createElement('div');
        modal.className = 'cc-modal fixed inset-0 z-50 flex items-center justify-center p-4';
        modal.innerHTML = `
            <div class="cc-modal-backdrop absolute inset-0 bg-black/80 backdrop-blur-sm"></div>
            <div class="cc-modal-content relative w-full max-w-4xl max-h-[90vh] glass-container rounded-2xl overflow-hidden flex flex-col">
                <div class="cc-modal-header p-4 border-b border-white/10 flex items-center justify-between">
                    <div class="flex items-center gap-3">
                        <div class="text-2xl">📝</div>
                        <div>
                            <h3 class="text-white font-semibold">Article Variation ${variantNum}</h3>
                            <p class="text-white/50 text-sm">${article.word_count} words</p>
                        </div>
                    </div>
                    <div class="flex items-center gap-2">
                        <button type="button" class="cc-copy-btn px-3 py-1.5 rounded-lg bg-brand-teal/20 text-brand-teal text-sm hover:bg-brand-teal/30 transition-colors flex items-center gap-2">
                            <span>📋</span> Copy
                        </button>
                        <button type="button" class="cc-modal-close w-8 h-8 rounded-lg hover:bg-white/10 flex items-center justify-center text-white/70 hover:text-white transition-colors">
                            ✕
                        </button>
                    </div>
                </div>
                <div class="cc-modal-body flex-1 overflow-y-auto p-6">
                    <div class="prose prose-invert prose-lg max-w-none">
                        ${this.renderMarkdown(article.content)}
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        // Animate in
        requestAnimationFrame(() => {
            modal.classList.add('show');
        });

        // Close handlers
        modal.querySelector('.cc-modal-backdrop')?.addEventListener('click', () => this.closeModal(modal));
        modal.querySelector('.cc-modal-close')?.addEventListener('click', () => this.closeModal(modal));

        // Copy button
        modal.querySelector('.cc-copy-btn')?.addEventListener('click', async () => {
            try {
                await navigator.clipboard.writeText(article.content);
                const btn = modal.querySelector('.cc-copy-btn');
                if (btn) {
                    btn.innerHTML = '<span>✓</span> Copied!';
                    setTimeout(() => {
                        btn.innerHTML = '<span>📋</span> Copy';
                    }, 2000);
                }
            } catch (err) {
                console.error('Failed to copy:', err);
            }
        });

        // ESC to close
        const escHandler = (e) => {
            if (e.key === 'Escape') {
                this.closeModal(modal);
                document.removeEventListener('keydown', escHandler);
            }
        };
        document.addEventListener('keydown', escHandler);
    }

    closeModal(modal) {
        modal.classList.remove('show');
        setTimeout(() => modal.remove(), 300);
    }

    renderError(message) {
        this.currentPhase = 'error';
        this.element.innerHTML = `
            <div class="cc-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="cc-icon w-10 h-10 rounded-xl bg-red-500/20 flex items-center justify-center text-xl">
                        ⚠️
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">Generation Failed</h3>
                        <p class="text-white/50 text-sm">Something went wrong</p>
                    </div>
                </div>
            </div>

            <div class="cc-body p-4">
                <div class="bg-red-500/10 border border-red-500/30 rounded-xl p-4">
                    <p class="text-red-400 text-sm">${this.escapeHtml(message)}</p>
                </div>
            </div>

            <div class="cc-footer p-4 border-t border-white/10">
                <button type="button" class="cc-retry-btn w-full py-3 px-6 rounded-xl bg-white/10 text-white font-medium hover:bg-white/20 transition-colors">
                    Try Again
                </button>
            </div>
        `;

        this.element.querySelector('.cc-retry-btn')?.addEventListener('click', () => {
            this.renderInputPhase();
        });
    }

    getArticlePreview(content) {
        if (!content) return 'No content available';
        // Get first ~150 chars, try to break at word boundary
        const preview = content.substring(0, 150);
        const lastSpace = preview.lastIndexOf(' ');
        return (lastSpace > 100 ? preview.substring(0, lastSpace) : preview) + '...';
    }

    renderMarkdown(content) {
        if (window.marked && window.DOMPurify) {
            return DOMPurify.sanitize(marked.parse(content || ''));
        }
        return this.escapeHtml(content || '').replace(/\n/g, '<br>');
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    update(data) {
        if (data.phase) {
            this.setPhase(data.phase);
        }
        if (data.complete && data.articles) {
            this.articles = data.articles;
            this.renderCompletePhase();
        }
        if (data.error) {
            this.renderError(data.error);
        }
    }
}

// Register the widget
if (window.widgetRegistry) {
    window.widgetRegistry.register('content_catalyst', ContentCatalystWidget);
}

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { ContentCatalystWidget };
}
