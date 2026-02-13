/**
 * Image Catalyst Widget
 *
 * Interactive widget for AI image generation with two modes:
 * - Thumbnail/Promotional: GPT Image 1.5 for polished marketing images
 * - General: FLUX.2 Dev for creative/general imagery
 * Supports reference image upload and prompt input.
 */

console.log('[ic-widget] Script loaded, BaseWidget available:', typeof BaseWidget !== 'undefined');

class ImageCatalystWidget extends BaseWidget {
    constructor(config) {
        super(config);
        this.phases = [
            { id: 'input', label: 'Configure', icon: '⚙️' },
            { id: 'generating', label: 'Generating', icon: '🎨' },
            { id: 'complete', label: 'Complete', icon: '✅' }
        ];
        this.currentPhase = 'input';
        this.runId = null;
        this.generatedImage = null;
        this.uploadedReferenceUrl = null;
        this.uploadedReferencePreview = null;
    }

    render(container) {
        super.render(container);
        this.element.className = 'ic-widget glass-container rounded-2xl overflow-hidden';

        // Check if restoring from saved state
        if (this.config.restoredState && this.config.restoredState.state === 'complete') {
            console.log('[ic-widget] Restoring completed state');
            this.runId = this.config.restoredState.run_id;
            this.generatedImage = this.config.restoredState.data;
            this.renderCompletePhase();
        } else {
            this.renderInputPhase();
        }
    }

    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    renderInputPhase() {
        // Use last-used settings if available, otherwise fall back to initial config
        const last = this._lastSettings || {};
        const suggestedMode = last.mode || this.config.suggested_mode || 'general';
        const suggestedPrompt = last.prompt || this.config.suggested_prompt || '';
        const suggestedDim = last.dimension || '1024x1024';
        const suggestedQuality = last.quality || 'high';

        this.element.innerHTML = `
            <div class="ic-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="ic-icon w-10 h-10 rounded-xl bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center text-xl">
                        🖼️
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">Image Catalyst</h3>
                        <p class="text-white/50 text-sm">AI-powered image generation</p>
                    </div>
                </div>
            </div>

            <div class="ic-body p-4 space-y-4">
                <!-- Mode Selection -->
                <div class="ic-mode-section">
                    <label class="text-white/70 text-sm font-medium mb-2 block">Generation Mode</label>
                    <div class="grid grid-cols-2 gap-2">
                        <button class="ic-mode-btn ${suggestedMode === 'thumbnail' ? 'ic-mode-active' : ''}"
                                data-mode="thumbnail">
                            <div class="text-lg mb-1">📸</div>
                            <div class="text-white text-sm font-medium">Thumbnail / Promo</div>
                            <div class="text-white/40 text-xs">Marketing &amp; promotional</div>
                        </button>
                        <button class="ic-mode-btn ${suggestedMode === 'general' ? 'ic-mode-active' : ''}"
                                data-mode="general">
                            <div class="text-lg mb-1">🎨</div>
                            <div class="text-white text-sm font-medium">General</div>
                            <div class="text-white/40 text-xs">Creative &amp; artistic</div>
                        </button>
                    </div>
                </div>

                <!-- Prompt Input -->
                <div class="ic-prompt-section">
                    <label class="text-white/70 text-sm font-medium mb-2 block">Image Description</label>
                    <textarea id="ic-prompt" class="ic-textarea w-full rounded-xl bg-white/5 border border-white/10 text-white p-3 text-sm resize-none focus:outline-none focus:border-purple-500/50"
                              rows="3" placeholder="Describe the image you want to create...">${this.escapeHtml(suggestedPrompt)}</textarea>
                </div>

                <!-- Reference Image Upload -->
                <div class="ic-reference-section">
                    <label class="text-white/70 text-sm font-medium mb-2 block">Reference Image (optional)</label>
                    <div id="ic-ref-dropzone" class="ic-dropzone rounded-xl border-2 border-dashed border-white/10 p-4 text-center cursor-pointer hover:border-purple-500/30 transition-colors">
                        <div id="ic-ref-preview" class="${this.uploadedReferencePreview ? '' : 'hidden'}">
                            <img id="ic-ref-img" class="max-h-32 mx-auto rounded-lg mb-2" alt="Reference"
                                 ${this.uploadedReferencePreview ? `src="${this.escapeHtml(this.uploadedReferencePreview)}"` : ''}>
                            <button id="ic-ref-remove" class="text-white/40 hover:text-red-400 text-xs">Remove</button>
                        </div>
                        <div id="ic-ref-placeholder" class="${this.uploadedReferencePreview ? 'hidden' : ''}">
                            <div class="text-white/30 text-2xl mb-1">📎</div>
                            <div class="text-white/40 text-sm">Click or drag to upload reference image</div>
                            <div class="text-white/30 text-xs mt-1">PNG, JPG, WEBP up to 10MB</div>
                        </div>
                        <input type="file" id="ic-ref-input" class="hidden" accept="image/png,image/jpeg,image/webp">
                    </div>
                </div>

                <!-- Thumbnail-specific: Dimensions -->
                <div id="ic-thumbnail-opts" class="${suggestedMode === 'thumbnail' ? '' : 'hidden'}">
                    <label class="text-white/70 text-sm font-medium mb-2 block">Dimensions</label>
                    <div class="grid grid-cols-3 gap-2">
                        <button class="ic-dim-btn ${suggestedDim === '1024x1024' ? 'ic-dim-active' : ''}" data-dim="1024x1024">
                            <div class="text-white text-xs">1024×1024</div>
                            <div class="text-white/30 text-xs">Square</div>
                        </button>
                        <button class="ic-dim-btn ${suggestedDim === '1536x1024' ? 'ic-dim-active' : ''}" data-dim="1536x1024">
                            <div class="text-white text-xs">1536×1024</div>
                            <div class="text-white/30 text-xs">Landscape</div>
                        </button>
                        <button class="ic-dim-btn ${suggestedDim === '1024x1536' ? 'ic-dim-active' : ''}" data-dim="1024x1536">
                            <div class="text-white text-xs">1024×1536</div>
                            <div class="text-white/30 text-xs">Portrait</div>
                        </button>
                    </div>
                </div>

                <!-- Quality (thumbnail) -->
                <div id="ic-quality-opts" class="${suggestedMode === 'thumbnail' ? '' : 'hidden'}">
                    <label class="text-white/70 text-sm font-medium mb-2 block">Quality</label>
                    <div class="grid grid-cols-3 gap-2">
                        <button class="ic-qual-btn ${suggestedQuality === 'low' ? 'ic-qual-active' : ''}" data-quality="low">
                            <div class="text-white text-xs">Low</div>
                            <div class="text-white/30 text-xs">Faster</div>
                        </button>
                        <button class="ic-qual-btn ${suggestedQuality === 'medium' ? 'ic-qual-active' : ''}" data-quality="medium">
                            <div class="text-white text-xs">Medium</div>
                        </button>
                        <button class="ic-qual-btn ${suggestedQuality === 'high' ? 'ic-qual-active' : ''}" data-quality="high">
                            <div class="text-white text-xs">High</div>
                            <div class="text-white/30 text-xs">Best</div>
                        </button>
                    </div>
                </div>

                <!-- Generate Button -->
                <button id="ic-generate-btn" class="w-full py-3 rounded-xl bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 text-white font-semibold text-sm transition-all disabled:opacity-50 disabled:cursor-not-allowed">
                    Generate Image
                </button>
            </div>
        `;

        this._bindInputEvents();
    }

    _bindInputEvents() {
        // Mode selection
        this.element.querySelectorAll('.ic-mode-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                this.element.querySelectorAll('.ic-mode-btn').forEach(b => b.classList.remove('ic-mode-active'));
                btn.classList.add('ic-mode-active');

                const mode = btn.dataset.mode;
                const thumbOpts = this.element.querySelector('#ic-thumbnail-opts');
                const qualOpts = this.element.querySelector('#ic-quality-opts');
                if (thumbOpts) thumbOpts.classList.toggle('hidden', mode !== 'thumbnail');
                if (qualOpts) qualOpts.classList.toggle('hidden', mode !== 'thumbnail');
            });
        });

        // Dimension selection
        this.element.querySelectorAll('.ic-dim-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                this.element.querySelectorAll('.ic-dim-btn').forEach(b => b.classList.remove('ic-dim-active'));
                btn.classList.add('ic-dim-active');
            });
        });

        // Quality selection
        this.element.querySelectorAll('.ic-qual-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                this.element.querySelectorAll('.ic-qual-btn').forEach(b => b.classList.remove('ic-qual-active'));
                btn.classList.add('ic-qual-active');
            });
        });

        // Reference image upload
        const dropzone = this.element.querySelector('#ic-ref-dropzone');
        const fileInput = this.element.querySelector('#ic-ref-input');

        if (dropzone && fileInput) {
            dropzone.addEventListener('click', (e) => {
                if (e.target.id !== 'ic-ref-remove') fileInput.click();
            });

            dropzone.addEventListener('dragover', (e) => {
                e.preventDefault();
                dropzone.classList.add('border-purple-500/50');
            });
            dropzone.addEventListener('dragleave', () => {
                dropzone.classList.remove('border-purple-500/50');
            });
            dropzone.addEventListener('drop', (e) => {
                e.preventDefault();
                dropzone.classList.remove('border-purple-500/50');
                if (e.dataTransfer.files.length > 0) this._handleReferenceFile(e.dataTransfer.files[0]);
            });

            fileInput.addEventListener('change', () => {
                if (fileInput.files.length > 0) this._handleReferenceFile(fileInput.files[0]);
            });
        }

        // Remove reference
        const removeBtn = this.element.querySelector('#ic-ref-remove');
        if (removeBtn) {
            removeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.uploadedReferenceUrl = null;
                this.uploadedReferencePreview = null;
                const preview = this.element.querySelector('#ic-ref-preview');
                const placeholder = this.element.querySelector('#ic-ref-placeholder');
                if (preview) preview.classList.add('hidden');
                if (placeholder) placeholder.classList.remove('hidden');
            });
        }

        // Generate button
        const genBtn = this.element.querySelector('#ic-generate-btn');
        if (genBtn) {
            genBtn.addEventListener('click', () => this._startGeneration());
        }
    }

    async _handleReferenceFile(file) {
        if (!file.type.startsWith('image/')) {
            alert('Please upload an image file (PNG, JPG, or WEBP)');
            return;
        }
        if (file.size > 10 * 1024 * 1024) {
            alert('File too large. Maximum size is 10MB.');
            return;
        }

        // Show preview
        const reader = new FileReader();
        reader.onload = (e) => {
            this.uploadedReferencePreview = e.target.result;
            const preview = this.element.querySelector('#ic-ref-preview');
            const placeholder = this.element.querySelector('#ic-ref-placeholder');
            const img = this.element.querySelector('#ic-ref-img');
            if (img) img.src = e.target.result;
            if (preview) preview.classList.remove('hidden');
            if (placeholder) placeholder.classList.add('hidden');
        };
        reader.readAsDataURL(file);

        // Upload to backend
        const formData = new FormData();
        formData.append('file', file);

        try {
            const res = await fetch(`/api/v1/image-catalyst/upload?client_id=${this.config.clientId}`, {
                method: 'POST',
                body: formData
            });
            const data = await res.json();
            if (data.success && data.file_url) {
                this.uploadedReferenceUrl = data.file_url;
                console.log('[ic-widget] Reference image uploaded:', data.file_url);
            } else {
                console.error('[ic-widget] Upload failed:', data);
                alert('Failed to upload reference image: ' + (data.detail || data.message || 'Unknown error'));
            }
        } catch (err) {
            console.error('[ic-widget] Upload error:', err);
            alert('Failed to upload reference image');
        }
    }

    async _startGeneration() {
        const prompt = this.element.querySelector('#ic-prompt')?.value?.trim();
        if (!prompt) {
            alert('Please describe the image you want to create.');
            return;
        }

        const activeMode = this.element.querySelector('.ic-mode-btn.ic-mode-active');
        const mode = activeMode?.dataset.mode || 'general';

        // Get dimension and quality for thumbnail mode
        let width = 1024, height = 1024, quality = 'high';
        if (mode === 'thumbnail') {
            const activeDim = this.element.querySelector('.ic-dim-btn.ic-dim-active');
            if (activeDim) {
                const [w, h] = activeDim.dataset.dim.split('x').map(Number);
                width = w; height = h;
            }
            const activeQual = this.element.querySelector('.ic-qual-btn.ic-qual-active');
            if (activeQual) quality = activeQual.dataset.quality;
        }

        // Save settings so "New Image" can carry them over
        this._lastSettings = {
            mode,
            prompt,
            dimension: `${width}x${height}`,
            quality
        };

        // Transition to generating phase
        this.renderGeneratingPhase(prompt, mode);

        try {
            const params = new URLSearchParams({
                client_id: this.config.clientId,
                agent_id: this.config.agentId
            });
            if (this.config.userId) params.set('user_id', this.config.userId);
            if (this.config.conversationId) params.set('conversation_id', this.config.conversationId);

            const body = {
                mode,
                prompt,
                width,
                height
            };
            if (mode === 'thumbnail') body.quality = quality;
            if (this.uploadedReferenceUrl) body.reference_image_url = this.uploadedReferenceUrl;

            const res = await fetch(`/api/v1/image-catalyst/start?${params}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });

            const data = await res.json();
            console.log('[ic-widget] Generation result:', data);

            if (data.success && data.image_url) {
                this.runId = data.run_id;
                this.generatedImage = {
                    image_url: data.image_url,
                    mode,
                    prompt,
                    seed: data.seed,
                    generation_time_ms: data.generation_time_ms,
                    cost: data.cost
                };
                this.renderCompletePhase();

                // Store result in conversation
                if (this.config.conversationId && this.runId) {
                    this._storeResult();
                }
            } else {
                this.renderError(data.error || data.detail || data.message || 'Generation failed');
            }
        } catch (err) {
            console.error('[ic-widget] Generation error:', err);
            this.renderError('Failed to generate image: ' + err.message);
        }
    }

    renderGeneratingPhase(prompt, mode) {
        this.currentPhase = 'generating';
        const modeLabel = mode === 'thumbnail' ? 'Thumbnail / Promotional' : 'General / Creative';
        this.element.innerHTML = `
            <div class="ic-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="ic-icon w-10 h-10 rounded-xl bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center text-xl">
                        🎨
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">Image Catalyst</h3>
                        <p class="text-white/50 text-sm">${this.escapeHtml(modeLabel)}</p>
                    </div>
                </div>
            </div>

            <div class="ic-body p-6 text-center">
                <div class="ic-spinner mx-auto mb-4"></div>
                <div class="text-white font-medium mb-2">Generating your image...</div>
                <div class="text-white/40 text-sm">${this.escapeHtml(prompt.length > 100 ? prompt.slice(0, 100) + '...' : prompt)}</div>
                <div class="text-white/30 text-xs mt-3">This usually takes 5-15 seconds</div>
            </div>
        `;
    }

    renderCompletePhase() {
        this.currentPhase = 'complete';
        const img = this.generatedImage || {};
        const costStr = img.cost ? `$${parseFloat(img.cost).toFixed(4)}` : '';
        const timeStr = img.generation_time_ms ? `${(img.generation_time_ms / 1000).toFixed(1)}s` : '';
        const metaParts = [timeStr, costStr].filter(Boolean).join(' · ');

        this.element.innerHTML = `
            <div class="ic-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="ic-icon w-10 h-10 rounded-xl bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center text-xl">
                        ✅
                    </div>
                    <div class="flex-1">
                        <h3 class="text-white font-semibold">Image Catalyst</h3>
                        <p class="text-white/50 text-sm">Generation complete${metaParts ? ' · ' + metaParts : ''}</p>
                    </div>
                </div>
            </div>

            <div class="ic-body p-4">
                <div class="ic-result-image rounded-xl overflow-hidden mb-3">
                    <img src="${this.escapeHtml(img.image_url || '')}" alt="Generated image"
                         class="w-full h-auto" loading="lazy"
                         onerror="this.parentElement.innerHTML='<div class=\\'p-8 text-center text-white/40\\'>Failed to load image</div>'">
                </div>

                <div class="text-white/50 text-sm mb-3 line-clamp-2">${this.escapeHtml(img.prompt || '')}</div>

                <div class="flex gap-2">
                    <a href="${this.escapeHtml(img.image_url || '#')}" download target="_blank"
                       class="flex-1 py-2 rounded-lg bg-white/10 hover:bg-white/15 text-white text-sm text-center transition-colors">
                        Download
                    </a>
                    <button class="ic-regenerate-btn flex-1 py-2 rounded-lg bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 text-white text-sm transition-all">
                        New Image
                    </button>
                </div>
            </div>
        `;

        // Bind regenerate
        const regenBtn = this.element.querySelector('.ic-regenerate-btn');
        if (regenBtn) {
            regenBtn.addEventListener('click', () => this.renderInputPhase());
        }
    }

    renderError(message) {
        this.currentPhase = 'input';
        this.element.innerHTML = `
            <div class="ic-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="ic-icon w-10 h-10 rounded-xl bg-red-500/20 flex items-center justify-center text-xl">
                        ⚠️
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">Image Catalyst</h3>
                        <p class="text-red-400 text-sm">Generation failed</p>
                    </div>
                </div>
            </div>

            <div class="ic-body p-4 space-y-3">
                <div class="rounded-xl bg-red-500/10 border border-red-500/20 p-3">
                    <div class="text-red-300 text-sm">${this.escapeHtml(message)}</div>
                </div>
                <button class="ic-retry-btn w-full py-2 rounded-xl bg-white/10 hover:bg-white/15 text-white text-sm transition-colors">
                    Try Again
                </button>
            </div>
        `;

        const retryBtn = this.element.querySelector('.ic-retry-btn');
        if (retryBtn) {
            retryBtn.addEventListener('click', () => this.renderInputPhase());
        }
    }

    async _storeResult() {
        try {
            const res = await fetch(`/api/v1/image-catalyst/store-result?client_id=${this.config.clientId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    run_id: this.runId,
                    conversation_id: this.config.conversationId
                })
            });
            const data = await res.json();
            console.log('[ic-widget] Result stored:', data);
        } catch (err) {
            console.warn('[ic-widget] Failed to store result:', err);
        }
    }

    update(data) {
        if (data.phase) {
            if (data.phase === 'complete' && data.image) {
                this.generatedImage = data.image;
                this.renderCompletePhase();
            }
        }
        if (data.error) {
            this.renderError(data.error);
        }
    }
}

// Register widget type
if (typeof window.widgetRegistry !== 'undefined') {
    window.widgetRegistry.register('image_catalyst', ImageCatalystWidget);
    console.log('[ic-widget] Widget registered with registry');
} else {
    console.warn('[ic-widget] Widget registry not found, will register on load');
    document.addEventListener('DOMContentLoaded', () => {
        if (typeof window.widgetRegistry !== 'undefined') {
            window.widgetRegistry.register('image_catalyst', ImageCatalystWidget);
            console.log('[ic-widget] Widget registered with registry (delayed)');
        }
    });
}
