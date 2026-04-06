/**
 * Descript Connect Widget
 *
 * Interactive widget for uploading video, configuring editing presets,
 * and monitoring Descript AI-powered video editing via the Descript API.
 */

console.log('[descript-widget] Script loaded, BaseWidget available:', typeof BaseWidget !== 'undefined');

class DescriptWidget extends BaseWidget {
    constructor(config) {
        super(config);
        this.phases = [
            { id: 'input', label: 'Configure', icon: '\u2699\uFE0F' },
            { id: 'uploading', label: 'Uploading', icon: '\u2B06\uFE0F' },
            { id: 'importing', label: 'Importing', icon: '\uD83D\uDCE5' },
            { id: 'editing', label: 'Editing', icon: '\u2702\uFE0F' },
            { id: 'complete', label: 'Complete', icon: '\u2705' }
        ];
        this.currentPhase = 'input';
        this.runId = null;
        this.uploadedFile = null;
        this.uploadedFileUrl = null;
        this.pollInterval = null;
        this.projectUrl = null;
        this.agentResponse = null;
    }

    render(container) {
        super.render(container);
        this.element.className = 'descript-widget glass-container rounded-2xl overflow-hidden';

        if (this.config.restoredState && this.config.restoredState.state === 'complete') {
            console.log('[descript-widget] Restoring completed state');
            this.runId = this.config.restoredState.run_id;
            this.projectUrl = this.config.restoredState.project_url;
            this.agentResponse = this.config.restoredState.agent_response;
            this.renderCompletePhase();
        } else {
            this.renderInputPhase();
        }
    }

    renderInputPhase() {
        const suggestedInstructions = this.config.suggested_instructions || '';

        this.element.innerHTML = `
            <div class="descript-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="descript-icon w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-xl">
                        \uD83C\uDFAC
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">Descript Connect</h3>
                        <p class="text-white/50 text-sm">AI-powered video editing</p>
                    </div>
                </div>
            </div>

            <div class="descript-body p-4 space-y-4">
                <!-- Video Upload -->
                <div class="descript-upload-zone" id="descript-drop-zone">
                    <div class="descript-upload-content" id="descript-upload-content">
                        <div class="text-3xl mb-2">\uD83C\uDFA5</div>
                        <p class="text-white/70 text-sm font-medium">Drag & drop a video file here</p>
                        <p class="text-white/40 text-xs mt-1">MP4, MOV, AVI, WebM (max 500MB)</p>
                        <button type="button" class="mt-3 px-4 py-2 rounded-lg bg-white/10 text-white/80 text-sm hover:bg-white/20 transition-all" id="descript-browse-btn">
                            Browse Files
                        </button>
                        <input type="file" id="descript-file-input" class="hidden" accept=".mp4,.mov,.avi,.webm,video/mp4,video/quicktime,video/x-msvideo,video/webm">
                    </div>
                    <div class="descript-file-info hidden" id="descript-file-info">
                        <div class="flex items-center gap-3">
                            <span class="text-2xl">\uD83C\uDFA5</span>
                            <div class="flex-1 min-w-0">
                                <p class="text-white text-sm font-medium truncate" id="descript-file-name"></p>
                                <p class="text-white/50 text-xs" id="descript-file-size"></p>
                            </div>
                            <button type="button" class="text-white/50 hover:text-red-400 transition-colors" id="descript-remove-file">\u2715</button>
                        </div>
                    </div>
                </div>

                <!-- Editing Presets -->
                <div class="descript-presets space-y-2">
                    <label class="block text-white/70 text-sm font-medium">Editing Presets</label>
                    <div class="grid grid-cols-2 gap-2">
                        <label class="descript-checkbox-card">
                            <input type="checkbox" id="descript-filler" class="hidden">
                            <div class="descript-check-inner">
                                <span class="descript-check-icon">\uD83D\uDDE3\uFE0F</span>
                                <span class="text-sm">Remove Filler Words</span>
                            </div>
                        </label>
                        <label class="descript-checkbox-card">
                            <input type="checkbox" id="descript-silences" class="hidden">
                            <div class="descript-check-inner">
                                <span class="descript-check-icon">\uD83D\uDD07</span>
                                <span class="text-sm">Remove Silences</span>
                            </div>
                        </label>
                        <label class="descript-checkbox-card">
                            <input type="checkbox" id="descript-studio" class="hidden">
                            <div class="descript-check-inner">
                                <span class="descript-check-icon">\uD83C\uDF99\uFE0F</span>
                                <span class="text-sm">Studio Sound</span>
                            </div>
                        </label>
                        <label class="descript-checkbox-card">
                            <input type="checkbox" id="descript-captions" class="hidden">
                            <div class="descript-check-inner">
                                <span class="descript-check-icon">\uD83D\uDCDD</span>
                                <span class="text-sm">Generate Captions</span>
                            </div>
                        </label>
                    </div>
                </div>

                <!-- Clips Section -->
                <div class="descript-clips-section">
                    <label class="descript-checkbox-card full-width">
                        <input type="checkbox" id="descript-clips-toggle" class="hidden">
                        <div class="descript-check-inner">
                            <span class="descript-check-icon">\u2702\uFE0F</span>
                            <span class="text-sm font-medium">Create Clips</span>
                        </div>
                    </label>
                    <div class="descript-clips-options hidden mt-2 p-3 rounded-xl bg-white/5 border border-white/10 space-y-3" id="descript-clips-options">
                        <div class="flex gap-3">
                            <div class="flex-1">
                                <label class="block text-white/60 text-xs mb-1">Number of Clips</label>
                                <select id="descript-clip-count" class="descript-select">
                                    <option value="1">1</option>
                                    <option value="2">2</option>
                                    <option value="3" selected>3</option>
                                    <option value="4">4</option>
                                    <option value="5">5</option>
                                </select>
                            </div>
                            <div class="flex-1">
                                <label class="block text-white/60 text-xs mb-1">Clip Length (sec)</label>
                                <input type="number" id="descript-clip-length" class="descript-input" value="30" min="5" max="300" step="5">
                            </div>
                            <div class="flex-1">
                                <label class="block text-white/60 text-xs mb-1">Resolution</label>
                                <select id="descript-clip-resolution" class="descript-select">
                                    <option value="1080p" selected>1080p</option>
                                    <option value="720p">720p</option>
                                    <option value="480p">480p</option>
                                    <option value="4k">4K</option>
                                </select>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Custom Instructions -->
                <div>
                    <label class="block text-white/70 text-sm font-medium mb-1">Custom Instructions (optional)</label>
                    <textarea
                        id="descript-custom-instructions"
                        class="descript-textarea"
                        rows="2"
                        placeholder="Add specific editing instructions..."
                    >${this.escapeHtml(suggestedInstructions)}</textarea>
                </div>

                <!-- Submit -->
                <button type="button" id="descript-submit-btn" class="descript-submit-btn w-full py-3 px-4 rounded-xl font-semibold text-sm transition-all disabled:opacity-40 disabled:cursor-not-allowed" disabled>
                    Start Editing
                </button>
            </div>
        `;

        this.bindInputEvents();
    }

    bindInputEvents() {
        const dropZone = this.element.querySelector('#descript-drop-zone');
        const browseBtn = this.element.querySelector('#descript-browse-btn');
        const fileInput = this.element.querySelector('#descript-file-input');
        const removeBtn = this.element.querySelector('#descript-remove-file');
        const clipsToggle = this.element.querySelector('#descript-clips-toggle');
        const clipsOptions = this.element.querySelector('#descript-clips-options');
        const submitBtn = this.element.querySelector('#descript-submit-btn');

        // Drag and drop
        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropZone.classList.add('drag-over');
        });
        dropZone.addEventListener('dragleave', () => {
            dropZone.classList.remove('drag-over');
        });
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.classList.remove('drag-over');
            const file = e.dataTransfer.files[0];
            if (file) this.handleFileSelect(file);
        });

        // Browse button
        browseBtn.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', (e) => {
            if (e.target.files[0]) this.handleFileSelect(e.target.files[0]);
        });

        // Remove file
        removeBtn.addEventListener('click', () => {
            this.uploadedFile = null;
            this.element.querySelector('#descript-upload-content').classList.remove('hidden');
            this.element.querySelector('#descript-file-info').classList.add('hidden');
            submitBtn.disabled = true;
        });

        // Clips toggle
        clipsToggle.addEventListener('change', () => {
            clipsOptions.classList.toggle('hidden', !clipsToggle.checked);
        });

        // Checkbox card styling
        this.element.querySelectorAll('.descript-checkbox-card input[type="checkbox"]').forEach(cb => {
            cb.addEventListener('change', () => {
                cb.closest('.descript-checkbox-card').classList.toggle('checked', cb.checked);
            });
        });

        // Submit
        submitBtn.addEventListener('click', () => {
            submitBtn.disabled = true;
            submitBtn.textContent = 'Starting...';
            this.submit().finally(() => {
                if (this.currentPhase === 'input') {
                    submitBtn.disabled = false;
                    submitBtn.textContent = 'Start Editing';
                }
            });
        });
    }

    handleFileSelect(file) {
        const allowedTypes = ['video/mp4', 'video/quicktime', 'video/x-msvideo', 'video/webm'];
        if (!allowedTypes.includes(file.type)) {
            this.renderError('Invalid file type. Please upload MP4, MOV, AVI, or WebM.');
            return;
        }
        const maxSize = 500 * 1024 * 1024;
        if (file.size > maxSize) {
            this.renderError('File too large. Maximum size is 500MB.');
            return;
        }

        this.uploadedFile = file;

        // Show file info
        this.element.querySelector('#descript-upload-content').classList.add('hidden');
        this.element.querySelector('#descript-file-info').classList.remove('hidden');
        this.element.querySelector('#descript-file-name').textContent = file.name;
        this.element.querySelector('#descript-file-size').textContent = `${(file.size / (1024 * 1024)).toFixed(1)} MB`;
        this.element.querySelector('#descript-submit-btn').disabled = false;
    }

    async submit() {
        if (!this.uploadedFile) return;

        const clientId = this.config.clientId || this.config.client_id;
        if (!clientId) {
            this.renderError('Client ID not available.');
            return;
        }

        // Gather options
        const options = {
            remove_filler_words: this.element.querySelector('#descript-filler').checked,
            remove_silences: this.element.querySelector('#descript-silences').checked,
            studio_sound: this.element.querySelector('#descript-studio').checked,
            generate_captions: this.element.querySelector('#descript-captions').checked,
            create_clips: this.element.querySelector('#descript-clips-toggle').checked,
            clip_count: parseInt(this.element.querySelector('#descript-clip-count').value, 10),
            clip_length_seconds: parseInt(this.element.querySelector('#descript-clip-length').value, 10),
            clip_resolution: this.element.querySelector('#descript-clip-resolution').value,
            custom_instructions: this.element.querySelector('#descript-custom-instructions').value.trim(),
        };

        try {
            // Phase: Uploading
            this.setPhase('uploading');
            this.renderProgressPhase('Uploading video...');

            // Upload to Supabase
            const formData = new FormData();
            formData.append('file', this.uploadedFile);

            const uploadResp = await fetch(`/api/v1/descript/upload-video?client_id=${encodeURIComponent(clientId)}`, {
                method: 'POST',
                body: formData,
            });

            if (!uploadResp.ok) {
                const err = await uploadResp.json().catch(() => ({}));
                throw new Error(err.detail || 'Video upload failed');
            }

            const uploadData = await uploadResp.json();
            this.uploadedFileUrl = uploadData.file_url;

            // Phase: Start edit pipeline
            this.setPhase('importing');
            this.renderProgressPhase('Starting Descript import...');

            const editResp = await fetch(`/api/v1/descript/edit?client_id=${encodeURIComponent(clientId)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    video_url: this.uploadedFileUrl,
                    filename: this.uploadedFile.name,
                    ...options,
                }),
            });

            if (!editResp.ok) {
                const err = await editResp.json().catch(() => ({}));
                throw new Error(err.detail || 'Failed to start editing');
            }

            const editData = await editResp.json();
            this.runId = editData.run_id;

            // Start polling
            this.startPolling();

        } catch (error) {
            console.error('[descript-widget] Submit error:', error);
            this.renderError(error.message || 'An error occurred');
        }
    }

    startPolling() {
        if (this.pollInterval) clearInterval(this.pollInterval);

        this.pollInterval = setInterval(async () => {
            try {
                const resp = await fetch(`/api/v1/descript/status/${this.runId}`);
                if (!resp.ok) return;

                const data = await resp.json();

                // Update phase
                if (data.status === 'importing') {
                    this.setPhase('importing');
                    this.renderProgressPhase(data.phase || 'Importing video into Descript...');
                } else if (data.status === 'editing') {
                    this.setPhase('editing');
                    this.renderProgressPhase(data.phase || 'Applying AI edits...');
                } else if (data.status === 'complete') {
                    clearInterval(this.pollInterval);
                    this.pollInterval = null;
                    this.projectUrl = data.project_url;
                    this.agentResponse = data.agent_response;
                    this.setPhase('complete');
                    this.renderCompletePhase();
                } else if (data.status === 'error') {
                    clearInterval(this.pollInterval);
                    this.pollInterval = null;
                    this.renderError(data.error || 'An error occurred during editing');
                }
            } catch (e) {
                console.error('[descript-widget] Polling error:', e);
            }
        }, 5000);
    }

    renderProgressPhase(message) {
        this.element.innerHTML = `
            <div class="descript-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="descript-icon w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-xl">
                        \uD83C\uDFAC
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">Descript Connect</h3>
                        <p class="text-white/50 text-sm">Processing your video</p>
                    </div>
                </div>
            </div>

            <div class="descript-body p-4">
                <!-- Phase Progress -->
                <div class="descript-phase-bar flex items-center gap-1 mb-6">
                    ${this.phases.map((phase, i) => {
                        const phaseIndex = this.phases.findIndex(p => p.id === this.currentPhase);
                        const thisIndex = i;
                        let stateClass = 'pending';
                        if (thisIndex < phaseIndex) stateClass = 'done';
                        else if (thisIndex === phaseIndex) stateClass = 'active';
                        return `
                            <div class="descript-phase-step ${stateClass} flex-1">
                                <div class="descript-phase-dot"></div>
                                <span class="text-xs mt-1 block text-center">${phase.label}</span>
                            </div>
                        `;
                    }).join('')}
                </div>

                <!-- Status -->
                <div class="flex flex-col items-center justify-center py-8">
                    <div class="descript-spinner mb-4"></div>
                    <p class="text-white/80 text-sm font-medium">${this.escapeHtml(message)}</p>
                    <p class="text-white/40 text-xs mt-2">This may take a few minutes</p>
                </div>
            </div>
        `;
    }

    renderCompletePhase() {
        this.element.innerHTML = `
            <div class="descript-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="descript-icon w-10 h-10 rounded-xl bg-gradient-to-br from-green-500 to-emerald-600 flex items-center justify-center text-xl">
                        \u2705
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">Editing Complete</h3>
                        <p class="text-white/50 text-sm">Your video has been processed</p>
                    </div>
                </div>
            </div>

            <div class="descript-body p-4 space-y-4">
                ${this.agentResponse ? `
                    <div class="p-3 rounded-xl bg-white/5 border border-white/10">
                        <p class="text-white/60 text-xs font-medium mb-1">Edit Summary</p>
                        <p class="text-white/90 text-sm">${this.escapeHtml(this.agentResponse)}</p>
                    </div>
                ` : ''}

                ${this.projectUrl ? `
                    <a href="${this.escapeHtml(this.projectUrl)}" target="_blank" rel="noopener noreferrer"
                       class="descript-open-btn flex items-center justify-center gap-2 w-full py-3 px-4 rounded-xl font-semibold text-sm transition-all">
                        <span>\uD83D\uDD17</span>
                        <span>Open in Descript</span>
                    </a>
                    <p class="text-white/40 text-xs text-center">
                        Review and export your edited video in Descript's dashboard
                    </p>
                ` : `
                    <p class="text-white/50 text-sm text-center">
                        Edits applied. Check your Descript dashboard for results.
                    </p>
                `}
            </div>
        `;
    }

    setPhase(phaseId) {
        this.currentPhase = phaseId;
    }

    renderError(message) {
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
            this.pollInterval = null;
        }

        this.element.innerHTML = `
            <div class="descript-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="descript-icon w-10 h-10 rounded-xl bg-gradient-to-br from-red-500 to-rose-600 flex items-center justify-center text-xl">
                        \u26A0\uFE0F
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">Error</h3>
                        <p class="text-white/50 text-sm">Something went wrong</p>
                    </div>
                </div>
            </div>
            <div class="descript-body p-4">
                <div class="p-3 rounded-xl bg-red-500/10 border border-red-500/20 mb-4">
                    <p class="text-red-400 text-sm">${this.escapeHtml(message)}</p>
                </div>
                <button type="button" id="descript-retry-btn" class="w-full py-2 px-4 rounded-xl bg-white/10 text-white/80 text-sm hover:bg-white/20 transition-all">
                    Try Again
                </button>
            </div>
        `;

        this.element.querySelector('#descript-retry-btn')?.addEventListener('click', () => {
            this.currentPhase = 'input';
            this.uploadedFile = null;
            this.uploadedFileUrl = null;
            this.runId = null;
            this.renderInputPhase();
        });
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
        if (data.complete) {
            this.projectUrl = data.project_url;
            this.agentResponse = data.agent_response;
            this.renderCompletePhase();
        }
        if (data.error) {
            this.renderError(data.error);
        }
    }
}

// Register the widget
if (window.widgetRegistry) {
    window.widgetRegistry.register('descript', DescriptWidget);
}
