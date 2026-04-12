/**
 * Image Catalyst Widget
 *
 * Interactive widget for AI image generation with two modes:
 * - Thumbnail/Promotional: Nano Banana 2 Pro (Gemini 3 Pro Image) for polished marketing images
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
            { id: 'complete', label: 'Complete', icon: '✅' },
            { id: 'editing', label: 'Editing', icon: '✏️' }
        ];
        this.currentPhase = 'input';
        this.runId = null;
        this.generatedImage = null;
        this.uploadedReferenceUrl = null;
        this.uploadedReferencePreview = null;
    }

    /**
     * Parse a fetch Response that should be JSON, but degrade gracefully when
     * it isn't (e.g. nginx returns an HTML 504/502 page when the upstream
     * Runware request times out). Always returns an object the caller can
     * inspect via `data.success` / `data.error` etc.
     */
    static async _parseResponse(res, label) {
        const ct = (res.headers.get('content-type') || '').toLowerCase();
        const text = await res.text();

        if (ct.includes('application/json')) {
            try {
                return JSON.parse(text);
            } catch (e) {
                return {
                    success: false,
                    error: `Server returned malformed JSON (HTTP ${res.status}). ${e.message}`,
                };
            }
        }

        // Non-JSON response — almost always an HTML error page from nginx
        // (504 Gateway Timeout, 502 Bad Gateway, 413, etc.) or a plain-text
        // proxy error. Surface the status code so the user knows it's a
        // gateway/timeout problem rather than blaming the widget.
        let friendly;
        if (res.status === 504) {
            friendly = 'Image provider timed out (HTTP 504). The upstream model took too long to respond — please try again.';
        } else if (res.status === 502) {
            friendly = 'Bad gateway (HTTP 502). The image service is unreachable — please try again shortly.';
        } else if (res.status === 413) {
            friendly = 'Reference image is too large (HTTP 413). Please use a smaller file.';
        } else if (res.status >= 500) {
            friendly = `Server error during ${label} (HTTP ${res.status}). Please try again.`;
        } else {
            friendly = `Unexpected non-JSON response during ${label} (HTTP ${res.status}).`;
        }

        return { success: false, error: friendly };
    }

    render(container) {
        super.render(container);
        this.element.className = 'ic-widget glass-container rounded-2xl overflow-hidden';

        // Delegated keyboard handler — survives innerHTML resets
        this.element.addEventListener('keydown', (e) => {
            const ta = e.target;
            if (ta.tagName !== 'TEXTAREA') return;
            if (e.key !== 'Enter') return;
            e.stopPropagation();
            if (e.shiftKey) {
                // Let the browser insert the newline, then auto-resize
                requestAnimationFrame(() => this._autoResize(ta));
                return;
            }
            e.preventDefault();
            if (ta.id === 'ic-prompt') this._startGeneration();
            else if (ta.id === 'ic-edit-prompt') this._startEdit();
        });

        // Delegated input handler for auto-resize
        this.element.addEventListener('input', (e) => {
            if (e.target.tagName === 'TEXTAREA') this._autoResize(e.target);
        });

        // Delegated paste handler for auto-resize
        this.element.addEventListener('paste', (e) => {
            if (e.target.tagName === 'TEXTAREA') {
                requestAnimationFrame(() => this._autoResize(e.target));
            }
        });

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
                    <textarea id="ic-prompt" class="ic-textarea w-full rounded-xl bg-white/5 border border-white/10 text-white p-3 text-sm focus:outline-none focus:border-purple-500/50"
                              rows="2" style="overflow-y:hidden;min-height:2.8em;max-height:${this._isFullscreen() ? 600 : 300}px;white-space:pre-wrap;resize:none" placeholder="Describe the image you want to create...">${this.escapeHtml(suggestedPrompt)}</textarea>
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

                <!-- Thumbnail-specific: Dimensions Dropdown -->
                <div id="ic-thumbnail-opts" class="${suggestedMode === 'thumbnail' ? '' : 'hidden'}">
                    <label class="text-white/70 text-sm font-medium mb-2 block">Dimensions</label>
                    <select id="ic-dim-select" class="w-full rounded-xl bg-white/5 border border-white/10 text-white p-3 text-sm focus:outline-none focus:border-purple-500/50 appearance-none cursor-pointer"
                            style="background-image: url('data:image/svg+xml;charset=UTF-8,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%2212%22 height=%2212%22 viewBox=%220 0 12 12%22%3E%3Cpath d=%22M3 5l3 3 3-3%22 fill=%22none%22 stroke=%22%23999%22 stroke-width=%221.5%22/%3E%3C/svg%3E'); background-repeat: no-repeat; background-position: right 12px center;">
                        <optgroup label="1K Resolution">
                            <option value="1024x1024" ${suggestedDim === '1024x1024' ? 'selected' : ''}>1024 × 1024 — Square (1:1)</option>
                            <option value="1376x768" ${suggestedDim === '1376x768' ? 'selected' : ''}>1376 × 768 — Landscape (16:9)</option>
                            <option value="768x1376" ${suggestedDim === '768x1376' ? 'selected' : ''}>768 × 1376 — Portrait (9:16)</option>
                            <option value="1264x848" ${suggestedDim === '1264x848' ? 'selected' : ''}>1264 × 848 — Landscape (3:2)</option>
                            <option value="848x1264" ${suggestedDim === '848x1264' ? 'selected' : ''}>848 × 1264 — Portrait (2:3)</option>
                            <option value="1200x896" ${suggestedDim === '1200x896' ? 'selected' : ''}>1200 × 896 — Landscape (4:3)</option>
                            <option value="896x1200" ${suggestedDim === '896x1200' ? 'selected' : ''}>896 × 1200 — Portrait (3:4)</option>
                            <option value="1152x928" ${suggestedDim === '1152x928' ? 'selected' : ''}>1152 × 928 — Landscape (5:4)</option>
                            <option value="928x1152" ${suggestedDim === '928x1152' ? 'selected' : ''}>928 × 1152 — Portrait (4:5)</option>
                            <option value="1584x672" ${suggestedDim === '1584x672' ? 'selected' : ''}>1584 × 672 — Ultrawide (21:9)</option>
                        </optgroup>
                        <optgroup label="2K Resolution">
                            <option value="2048x2048" ${suggestedDim === '2048x2048' ? 'selected' : ''}>2048 × 2048 — Square (1:1)</option>
                            <option value="2752x1536" ${suggestedDim === '2752x1536' ? 'selected' : ''}>2752 × 1536 — Landscape (16:9)</option>
                            <option value="1536x2752" ${suggestedDim === '1536x2752' ? 'selected' : ''}>1536 × 2752 — Portrait (9:16)</option>
                            <option value="2528x1696" ${suggestedDim === '2528x1696' ? 'selected' : ''}>2528 × 1696 — Landscape (3:2)</option>
                            <option value="1696x2528" ${suggestedDim === '1696x2528' ? 'selected' : ''}>1696 × 2528 — Portrait (2:3)</option>
                            <option value="2400x1792" ${suggestedDim === '2400x1792' ? 'selected' : ''}>2400 × 1792 — Landscape (4:3)</option>
                            <option value="1792x2400" ${suggestedDim === '1792x2400' ? 'selected' : ''}>1792 × 2400 — Portrait (3:4)</option>
                            <option value="2304x1856" ${suggestedDim === '2304x1856' ? 'selected' : ''}>2304 × 1856 — Landscape (5:4)</option>
                            <option value="1856x2304" ${suggestedDim === '1856x2304' ? 'selected' : ''}>1856 × 2304 — Portrait (4:5)</option>
                            <option value="3168x1344" ${suggestedDim === '3168x1344' ? 'selected' : ''}>3168 × 1344 — Ultrawide (21:9)</option>
                        </optgroup>
                    </select>
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
                if (thumbOpts) thumbOpts.classList.toggle('hidden', mode !== 'thumbnail');
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

        // Initial auto-resize in case of pre-filled content
        const promptTextarea = this.element.querySelector('#ic-prompt');
        if (promptTextarea) {
            requestAnimationFrame(() => this._autoResize(promptTextarea));
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

        // Validate clientId before upload
        if (!this.config.clientId) {
            console.error('[ic-widget] No clientId configured!', this.config);
            alert('Failed to upload reference image: No client ID configured');
            return;
        }

        const uploadUrl = `/api/v1/image-catalyst/upload?client_id=${this.config.clientId}`;
        console.log('[ic-widget] Uploading to:', uploadUrl);

        try {
            const res = await fetch(uploadUrl, {
                method: 'POST',
                body: formData
            });
            console.log('[ic-widget] Upload response status:', res.status);
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
            alert('Failed to upload reference image: ' + (err.message || 'Network error'));
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

        // Get dimensions for thumbnail mode
        let width = 1024, height = 1024;
        if (mode === 'thumbnail') {
            const dimSelect = this.element.querySelector('#ic-dim-select');
            if (dimSelect && dimSelect.value) {
                const [w, h] = dimSelect.value.split('x').map(Number);
                width = w; height = h;
            }
        }

        // Save settings so "New Image" can carry them over
        this._lastSettings = {
            mode,
            prompt,
            dimension: `${width}x${height}`
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
            if (this.uploadedReferenceUrl) body.reference_image_url = this.uploadedReferenceUrl;

            const res = await fetch(`/api/v1/image-catalyst/start?${params}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });

            const data = await ImageCatalystWidget._parseResponse(res, 'generation');
            console.log('[ic-widget] Generation result:', data);

            if (data.success && data.image_url) {
                this.runId = data.run_id;
                this.generatedImage = {
                    image_url: data.image_url,
                    mode,
                    prompt,
                    width,
                    height,
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
        const timeStr = img.generation_time_ms ? `${(img.generation_time_ms / 1000).toFixed(1)}s` : '';
        const metaParts = [timeStr].filter(Boolean).join(' · ');

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
                    ${img.mode === 'thumbnail' ? `
                    <button class="ic-edit-btn flex-1 py-2 rounded-lg bg-white/10 hover:bg-white/15 text-white text-sm transition-colors">
                        Edit Image
                    </button>
                    ` : ''}
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

        // Bind edit
        const editBtn = this.element.querySelector('.ic-edit-btn');
        if (editBtn) {
            editBtn.addEventListener('click', () => this.renderEditPhase());
        }
    }

    renderEditPhase() {
        this.currentPhase = 'editing';
        const img = this.generatedImage || {};

        this.element.innerHTML = `
            <div class="ic-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="ic-icon w-10 h-10 rounded-xl bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center text-xl">
                        ✏️
                    </div>
                    <div class="flex-1">
                        <h3 class="text-white font-semibold">Edit Image</h3>
                        <p class="text-white/50 text-sm">Describe the changes you want</p>
                    </div>
                </div>
            </div>

            <div class="ic-body p-4 space-y-3">
                <div class="ic-edit-preview rounded-xl overflow-hidden mb-3 opacity-80">
                    <img src="${this.escapeHtml(img.image_url || '')}" alt="Image to edit"
                         class="w-full h-auto" loading="lazy">
                </div>

                <div class="ic-edit-prompt-section">
                    <label class="text-white/70 text-sm font-medium mb-2 block">Edit Instructions</label>
                    <textarea id="ic-edit-prompt" class="ic-textarea w-full rounded-xl bg-white/5 border border-white/10 text-white p-3 text-sm focus:outline-none focus:border-purple-500/50"
                              rows="2" style="overflow-y:hidden;min-height:2.8em;max-height:300px;white-space:pre-wrap;resize:none" placeholder="e.g. Change the background to blue, add a logo in the corner, make the text larger..."></textarea>
                </div>

                <div class="flex gap-2">
                    <button class="ic-edit-cancel-btn flex-1 py-2.5 rounded-xl bg-white/10 hover:bg-white/15 text-white text-sm transition-colors">
                        Cancel
                    </button>
                    <button id="ic-edit-apply-btn" class="flex-1 py-2.5 rounded-xl bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 text-white font-semibold text-sm transition-all disabled:opacity-50 disabled:cursor-not-allowed">
                        Apply Edit
                    </button>
                </div>
            </div>
        `;

        // Bind events
        const cancelBtn = this.element.querySelector('.ic-edit-cancel-btn');
        if (cancelBtn) {
            cancelBtn.addEventListener('click', () => this.renderCompletePhase());
        }

        const applyBtn = this.element.querySelector('#ic-edit-apply-btn');
        if (applyBtn) {
            applyBtn.addEventListener('click', () => this._startEdit());
        }

        // Focus the edit prompt
        const editPrompt = this.element.querySelector('#ic-edit-prompt');
        if (editPrompt) editPrompt.focus();
    }

    _isFullscreen() {
        return !!document.fullscreenElement ||
               !!document.querySelector('.glass-container.pseudo-fullscreen');
    }

    _autoResize(textarea) {
        const maxH = this._isFullscreen() ? 600 : 300;
        textarea.style.height = 'auto';
        const clamped = Math.min(textarea.scrollHeight, maxH);
        textarea.style.height = clamped + 'px';
        textarea.style.maxHeight = maxH + 'px';
        textarea.style.overflowY = textarea.scrollHeight > maxH ? 'auto' : 'hidden';
    }

    async _startEdit() {
        const editPrompt = this.element.querySelector('#ic-edit-prompt')?.value?.trim();
        if (!editPrompt) {
            alert('Please describe what changes you want to make.');
            return;
        }

        const img = this.generatedImage || {};
        const sourceUrl = img.image_url;
        if (!sourceUrl) {
            alert('No source image available to edit.');
            return;
        }

        // Show generating state
        this.renderGeneratingPhase(editPrompt, 'thumbnail');

        try {
            const params = new URLSearchParams({
                client_id: this.config.clientId,
                agent_id: this.config.agentId
            });
            if (this.config.userId) params.set('user_id', this.config.userId);
            if (this.config.conversationId) params.set('conversation_id', this.config.conversationId);

            // Carry over dimensions from the current image
            const editWidth = img.width || 1024;
            const editHeight = img.height || 1024;

            const body = {
                source_image_url: sourceUrl,
                edit_prompt: editPrompt,
                width: editWidth,
                height: editHeight,
            };

            if (this.runId) body.original_run_id = this.runId;

            const res = await fetch(`/api/v1/image-catalyst/edit?${params}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });

            const data = await ImageCatalystWidget._parseResponse(res, 'edit');
            console.log('[ic-widget] Edit result:', data);

            if (data.success && data.image_url) {
                this.runId = data.run_id;
                this.generatedImage = {
                    image_url: data.image_url,
                    mode: img.mode || 'thumbnail',
                    prompt: editPrompt,
                    width: editWidth,
                    height: editHeight,
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
                this.renderError(data.error || data.detail || data.message || 'Edit failed');
            }
        } catch (err) {
            console.error('[ic-widget] Edit error:', err);
            this.renderError('Failed to edit image: ' + err.message);
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
