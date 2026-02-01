/**
 * LINGUA Widget
 *
 * Interactive widget for audio transcription and subtitle translation.
 * Uses AssemblyAI for transcription and LLM for translation.
 */

console.log('[lingua-widget] Script loaded, BaseWidget available:', typeof BaseWidget !== 'undefined');

class LinguaWidget extends BaseWidget {
    constructor(config) {
        super(config);
        this.phases = [
            { id: 'input', label: 'Configure', icon: '⚙️' },
            { id: 'uploading', label: 'Uploading', icon: '📤' },
            { id: 'transcribing', label: 'Transcribing', icon: '🎤' },
            { id: 'translating', label: 'Translating', icon: '🌐' },
            { id: 'complete', label: 'Complete', icon: '✅' }
        ];
        this.currentPhase = 'input';
        this.runId = null;
        this.uploadedFile = null;
        this.results = null;
        this.pollInterval = null;

        // Available languages
        this.translationLanguages = {
            'es': 'Spanish',
            'fr': 'French',
            'de': 'German',
            'it': 'Italian',
            'pt': 'Portuguese',
            'nl': 'Dutch',
            'ru': 'Russian',
            'ja': 'Japanese',
            'zh': 'Chinese',
            'ko': 'Korean',
            'ar': 'Arabic',
            'hi': 'Hindi'
        };
    }

    render(container) {
        super.render(container);
        this.element.className = 'lingua-widget glass-container rounded-2xl overflow-hidden';

        // Check if we're restoring from saved state
        if (this.config.restoredState && this.config.restoredState.state === 'complete') {
            console.log('[lingua-widget] Restoring completed state from saved data');
            this.runId = this.config.restoredState.run_id;
            this.results = this.config.restoredState.data;
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
        const languageCheckboxes = Object.entries(this.translationLanguages).map(([code, name]) => `
            <label class="lingua-lang-option">
                <input type="checkbox" name="target_lang" value="${code}" class="lingua-lang-checkbox">
                <span class="lingua-lang-label">${name}</span>
            </label>
        `).join('');

        this.element.innerHTML = `
            <div class="lingua-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="lingua-icon w-10 h-10 rounded-xl bg-gradient-to-br from-brand-teal to-brand-orange flex items-center justify-center text-xl">
                        🌐
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">LINGUA</h3>
                        <p class="text-white/50 text-sm">Audio Transcription & Subtitles</p>
                    </div>
                </div>
            </div>

            <div class="lingua-body p-4 space-y-4">
                <!-- File Upload Zone -->
                <div class="lingua-upload-section">
                    <label class="block text-white/70 text-sm mb-2">Upload Audio File</label>
                    <div class="lingua-upload-zone border-2 border-dashed border-white/20 rounded-xl p-6 text-center cursor-pointer hover:border-brand-teal/50 transition-colors" id="lingua-dropzone">
                        <input type="file" id="lingua-file-input" accept="audio/*,.mp3,.wav,.m4a,.flac,.ogg,.webm" class="hidden">
                        <div class="lingua-upload-content" id="lingua-upload-content">
                            <span class="text-3xl block mb-2">🎵</span>
                            <span class="text-white/60 text-sm">Drop audio file here or click to upload</span>
                            <span class="text-white/40 text-xs block mt-1">MP3, WAV, M4A, FLAC up to 100MB</span>
                        </div>
                        <div class="lingua-file-selected hidden" id="lingua-file-selected">
                            <span class="text-3xl block mb-2">✅</span>
                            <span class="text-white text-sm block" id="lingua-file-name"></span>
                            <span class="text-white/40 text-xs block" id="lingua-file-size"></span>
                            <button type="button" class="mt-2 text-xs text-brand-teal hover:underline" id="lingua-change-file">Change file</button>
                        </div>
                    </div>
                </div>

                <!-- Source Language -->
                <div class="lingua-source-lang">
                    <label class="block text-white/70 text-sm mb-2">Source Language</label>
                    <select id="lingua-source-lang" class="w-full glass-input rounded-xl p-3 text-white bg-white/5">
                        <option value="auto" selected>Auto-detect</option>
                        <option value="en">English</option>
                        <option value="es">Spanish</option>
                        <option value="fr">French</option>
                        <option value="de">German</option>
                        <option value="it">Italian</option>
                        <option value="pt">Portuguese</option>
                        <option value="ja">Japanese</option>
                        <option value="zh">Chinese</option>
                        <option value="ko">Korean</option>
                    </select>
                </div>

                <!-- Translation Languages -->
                <div class="lingua-translate-section">
                    <label class="block text-white/70 text-sm mb-2">Translate to (optional)</label>
                    <div class="lingua-lang-grid grid grid-cols-3 gap-2">
                        ${languageCheckboxes}
                    </div>
                </div>

                <!-- Output Formats -->
                <div class="lingua-formats">
                    <label class="block text-white/70 text-sm mb-2">Output Formats</label>
                    <div class="flex gap-4">
                        <label class="lingua-format-option">
                            <input type="checkbox" name="output_format" value="srt" checked class="lingua-format-checkbox">
                            <span class="lingua-format-label">SRT</span>
                        </label>
                        <label class="lingua-format-option">
                            <input type="checkbox" name="output_format" value="vtt" checked class="lingua-format-checkbox">
                            <span class="lingua-format-label">VTT</span>
                        </label>
                        <label class="lingua-format-option">
                            <input type="checkbox" name="output_format" value="txt" checked class="lingua-format-checkbox">
                            <span class="lingua-format-label">Plain Text</span>
                        </label>
                    </div>
                </div>

                <!-- Start Button -->
                <button type="button" id="lingua-start-btn" class="w-full py-3 px-4 rounded-xl bg-gradient-to-r from-brand-teal to-brand-orange text-white font-semibold transition-all hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed" disabled>
                    Start Transcription
                </button>
            </div>
        `;

        this.bindInputEvents();
    }

    bindInputEvents() {
        const dropzone = this.element.querySelector('#lingua-dropzone');
        const fileInput = this.element.querySelector('#lingua-file-input');
        const startBtn = this.element.querySelector('#lingua-start-btn');
        const changeFileBtn = this.element.querySelector('#lingua-change-file');

        // File drag and drop
        dropzone.addEventListener('click', () => fileInput.click());
        dropzone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropzone.classList.add('border-brand-teal');
        });
        dropzone.addEventListener('dragleave', () => {
            dropzone.classList.remove('border-brand-teal');
        });
        dropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropzone.classList.remove('border-brand-teal');
            if (e.dataTransfer.files.length) {
                this.handleFileSelect(e.dataTransfer.files[0]);
            }
        });

        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length) {
                this.handleFileSelect(e.target.files[0]);
            }
        });

        changeFileBtn?.addEventListener('click', (e) => {
            e.stopPropagation();
            this.uploadedFile = null;
            this.updateUploadUI();
            startBtn.disabled = true;
        });

        startBtn.addEventListener('click', () => this.startProcessing());
    }

    handleFileSelect(file) {
        // Validate file type
        const allowedTypes = ['audio/', 'video/webm'];
        if (!allowedTypes.some(type => file.type.includes(type.replace('/', '')))) {
            alert('Please select a valid audio file (MP3, WAV, M4A, FLAC, OGG, WEBM)');
            return;
        }

        // Validate file size (100MB max)
        if (file.size > 100 * 1024 * 1024) {
            alert('File is too large. Maximum size is 100MB.');
            return;
        }

        this.uploadedFile = file;
        this.updateUploadUI();

        // Enable start button
        const startBtn = this.element.querySelector('#lingua-start-btn');
        startBtn.disabled = false;
    }

    updateUploadUI() {
        const uploadContent = this.element.querySelector('#lingua-upload-content');
        const fileSelected = this.element.querySelector('#lingua-file-selected');
        const fileName = this.element.querySelector('#lingua-file-name');
        const fileSize = this.element.querySelector('#lingua-file-size');

        if (this.uploadedFile) {
            uploadContent.classList.add('hidden');
            fileSelected.classList.remove('hidden');
            fileName.textContent = this.uploadedFile.name;
            fileSize.textContent = this.formatFileSize(this.uploadedFile.size);
        } else {
            uploadContent.classList.remove('hidden');
            fileSelected.classList.add('hidden');
        }
    }

    formatFileSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    async startProcessing() {
        if (!this.uploadedFile) {
            alert('Please select an audio file first');
            return;
        }

        // Get selected options
        const sourceLang = this.element.querySelector('#lingua-source-lang').value;
        const targetLangs = Array.from(this.element.querySelectorAll('input[name="target_lang"]:checked'))
            .map(cb => cb.value);
        const outputFormats = Array.from(this.element.querySelectorAll('input[name="output_format"]:checked'))
            .map(cb => cb.value);

        this.setPhase('uploading');

        try {
            // Upload file
            const fileUrl = await this.uploadFile();

            this.setPhase('transcribing');

            // Start processing (returns immediately, processing happens in background)
            const response = await fetch(`/api/v1/lingua/start?client_id=${this.config.clientId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    source_audio_url: fileUrl,
                    source_language: sourceLang === 'auto' ? null : sourceLang,
                    target_languages: targetLangs,
                    output_formats: outputFormats
                })
            });

            // Check content type before parsing
            const contentType = response.headers.get('content-type') || '';
            if (!contentType.includes('application/json')) {
                const text = await response.text();
                console.error('[lingua-widget] Non-JSON response from start:', text.substring(0, 500));
                throw new Error(`Server returned non-JSON response (${response.status}). Check console for details.`);
            }

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || data.message || JSON.stringify(data));
            }

            this.runId = data.run_id;
            console.log('[lingua-widget] Processing started, run_id:', this.runId);

            // Poll for status until complete
            await this.pollForCompletion();

        } catch (error) {
            console.error('[lingua-widget] Processing error:', error);
            this.renderError(error.message);
        }
    }

    async pollForCompletion() {
        const maxAttempts = 120; // 10 minutes at 5 second intervals
        const pollInterval = 5000; // 5 seconds

        for (let attempt = 0; attempt < maxAttempts; attempt++) {
            try {
                const response = await fetch(`/api/v1/lingua/status/${this.runId}?client_id=${this.config.clientId}`);

                if (!response.ok) {
                    const errorData = await response.json().catch(() => ({}));
                    throw new Error(errorData.detail || `Status check failed: ${response.status}`);
                }

                const status = await response.json();
                console.log('[lingua-widget] Status:', status.status, 'Progress:', status.progress_percent);

                // Update phase based on status
                if (status.status === 'transcribing') {
                    this.setPhase('transcribing');
                } else if (status.status === 'translating') {
                    this.setPhase('translating');
                }

                // Update progress display
                this.updateProgress(status.progress_percent, status.current_phase);

                if (status.status === 'complete') {
                    this.results = {
                        transcript: status.transcript,
                        translations: status.translations,
                        download_urls: status.download_urls
                    };
                    this.setPhase('complete');
                    this.renderCompletePhase();

                    // Store result in conversation if we have conversation ID
                    if (this.config.conversationId && this.runId) {
                        this.storeResult();
                    }
                    return;
                } else if (status.status === 'failed') {
                    throw new Error(status.error || 'Processing failed');
                }

                // Wait before next poll
                await new Promise(resolve => setTimeout(resolve, pollInterval));

            } catch (error) {
                console.error('[lingua-widget] Polling error:', error);
                throw error;
            }
        }

        throw new Error('Processing timed out. Please try again with a shorter audio file.');
    }

    updateProgress(percent, phase) {
        const progressBar = this.element.querySelector('.lingua-progress-bar');
        const phaseText = this.element.querySelector('.lingua-phase-text');

        if (progressBar) {
            progressBar.style.width = `${percent}%`;
        }
        if (phaseText && phase) {
            phaseText.textContent = phase;
        }
    }

    async uploadFile() {
        const formData = new FormData();
        formData.append('file', this.uploadedFile);

        const response = await fetch(`/api/v1/lingua/upload?client_id=${this.config.clientId}`, {
            method: 'POST',
            body: formData
        });

        // Check content type before parsing
        const contentType = response.headers.get('content-type') || '';
        if (!contentType.includes('application/json')) {
            const text = await response.text();
            console.error('[lingua-widget] Non-JSON response from upload:', text.substring(0, 500));
            throw new Error(`Server returned non-JSON response (${response.status}). Check console for details.`);
        }

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || 'Upload failed');
        }

        return data.file_url;
    }

    setPhase(phase) {
        this.currentPhase = phase;
        this.renderProgressPhase();
    }

    renderProgressPhase() {
        const phaseIndex = this.phases.findIndex(p => p.id === this.currentPhase);
        const currentPhase = this.phases[phaseIndex] || this.phases[0];

        this.element.innerHTML = `
            <div class="lingua-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="lingua-icon w-10 h-10 rounded-xl bg-gradient-to-br from-brand-teal to-brand-orange flex items-center justify-center text-xl animate-pulse">
                        🌐
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">LINGUA</h3>
                        <p class="lingua-phase-text text-white/50 text-sm">${currentPhase.label}...</p>
                    </div>
                </div>
            </div>

            <div class="lingua-body p-6">
                <div class="mb-6">
                    <div class="h-2 bg-white/10 rounded-full overflow-hidden">
                        <div class="lingua-progress-bar h-full bg-gradient-to-r from-brand-teal to-brand-orange transition-all duration-500"
                             style="width: ${((phaseIndex + 1) / this.phases.length) * 100}%"></div>
                    </div>
                </div>

                <div class="lingua-phases space-y-3">
                    ${this.phases.map((phase, i) => {
                        let status = 'pending';
                        if (i < phaseIndex) status = 'complete';
                        else if (i === phaseIndex) status = 'active';

                        return `
                            <div class="lingua-phase flex items-center gap-3 ${status === 'active' ? 'text-white' : 'text-white/40'}">
                                <span class="lingua-phase-icon w-6 h-6 flex items-center justify-center text-sm">
                                    ${status === 'complete' ? '✅' : status === 'active' ? '⏳' : '○'}
                                </span>
                                <span class="lingua-phase-label">${phase.label}</span>
                            </div>
                        `;
                    }).join('')}
                </div>
            </div>
        `;
    }

    renderCompletePhase() {
        if (!this.results || !this.results.download_urls) {
            this.renderError('No results available');
            return;
        }

        const { transcript, translations, download_urls } = this.results;

        // Build download sections for each language
        const downloadSections = Object.entries(download_urls).map(([langCode, formats]) => {
            const langName = langCode === transcript?.language_code
                ? `${this.getLanguageName(langCode)} (Original)`
                : this.getLanguageName(langCode);

            return `
                <div class="lingua-download-section mb-4">
                    <h4 class="text-white/70 text-sm mb-2">${langName}</h4>
                    <div class="flex gap-2">
                        ${formats.srt ? `<button type="button" class="lingua-download-btn" data-lang="${langCode}" data-format="srt">📄 SRT</button>` : ''}
                        ${formats.vtt ? `<button type="button" class="lingua-download-btn" data-lang="${langCode}" data-format="vtt">📄 VTT</button>` : ''}
                        ${formats.txt ? `<button type="button" class="lingua-download-btn" data-lang="${langCode}" data-format="txt">📄 TXT</button>` : ''}
                    </div>
                </div>
            `;
        }).join('');

        // Preview of transcript
        const previewText = transcript?.text ? transcript.text.substring(0, 200) + (transcript.text.length > 200 ? '...' : '') : '';

        this.element.innerHTML = `
            <div class="lingua-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="lingua-icon w-10 h-10 rounded-xl bg-gradient-to-br from-brand-teal to-brand-orange flex items-center justify-center text-xl">
                        ✅
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">LINGUA - Complete!</h3>
                        <p class="text-white/50 text-sm">${transcript?.word_count || 0} words transcribed</p>
                    </div>
                </div>
            </div>

            <div class="lingua-body p-4 space-y-4">
                <!-- Transcript Preview -->
                ${previewText ? `
                    <div class="lingua-preview">
                        <label class="block text-white/70 text-sm mb-2">Transcript Preview</label>
                        <div class="glass-input rounded-xl p-3 text-white/80 text-sm max-h-32 overflow-y-auto">
                            ${this.escapeHtml(previewText)}
                        </div>
                    </div>
                ` : ''}

                <!-- Download Sections -->
                <div class="lingua-downloads">
                    <label class="block text-white/70 text-sm mb-3">Download Files</label>
                    ${downloadSections}
                </div>

                <!-- Actions -->
                <div class="lingua-actions flex gap-3">
                    <button type="button" id="lingua-copy-btn" class="flex-1 py-2 px-4 rounded-xl bg-white/10 text-white hover:bg-white/20 transition-colors">
                        📋 Copy Transcript
                    </button>
                    <button type="button" id="lingua-new-btn" class="flex-1 py-2 px-4 rounded-xl bg-gradient-to-r from-brand-teal to-brand-orange text-white hover:opacity-90 transition-opacity">
                        🔄 New Transcription
                    </button>
                </div>
            </div>
        `;

        this.bindCompleteEvents();
    }

    bindCompleteEvents() {
        // Download buttons
        this.element.querySelectorAll('.lingua-download-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const lang = btn.dataset.lang;
                const format = btn.dataset.format;
                this.downloadFile(lang, format);
            });
        });

        // Copy transcript
        const copyBtn = this.element.querySelector('#lingua-copy-btn');
        copyBtn?.addEventListener('click', () => {
            if (this.results?.transcript?.text) {
                navigator.clipboard.writeText(this.results.transcript.text);
                copyBtn.textContent = '✅ Copied!';
                setTimeout(() => copyBtn.textContent = '📋 Copy Transcript', 2000);
            }
        });

        // New transcription
        const newBtn = this.element.querySelector('#lingua-new-btn');
        newBtn?.addEventListener('click', () => {
            this.uploadedFile = null;
            this.results = null;
            this.runId = null;
            this.currentPhase = 'input';
            this.renderInputPhase();
        });
    }

    downloadFile(langCode, format) {
        if (!this.results?.download_urls?.[langCode]?.[format]) {
            alert('Download not available');
            return;
        }

        const content = this.results.download_urls[langCode][format];
        const mimeTypes = {
            'srt': 'application/x-subrip',
            'vtt': 'text/vtt',
            'txt': 'text/plain'
        };

        const blob = new Blob([content], { type: mimeTypes[format] || 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `transcript_${langCode}.${format}`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    getLanguageName(code) {
        const languages = {
            'en': 'English',
            'es': 'Spanish',
            'fr': 'French',
            'de': 'German',
            'it': 'Italian',
            'pt': 'Portuguese',
            'nl': 'Dutch',
            'ru': 'Russian',
            'ja': 'Japanese',
            'zh': 'Chinese',
            'ko': 'Korean',
            'ar': 'Arabic',
            'hi': 'Hindi',
            'auto': 'Auto-detected'
        };
        return languages[code] || code.toUpperCase();
    }

    renderError(message) {
        this.element.innerHTML = `
            <div class="lingua-header p-4 border-b border-white/10">
                <div class="flex items-center gap-3">
                    <div class="lingua-icon w-10 h-10 rounded-xl bg-red-500/20 flex items-center justify-center text-xl">
                        ❌
                    </div>
                    <div>
                        <h3 class="text-white font-semibold">LINGUA - Error</h3>
                        <p class="text-red-400 text-sm">Processing failed</p>
                    </div>
                </div>
            </div>

            <div class="lingua-body p-4 space-y-4">
                <div class="glass-input rounded-xl p-4 bg-red-500/10 border border-red-500/20">
                    <p class="text-red-400 text-sm">${this.escapeHtml(message)}</p>
                </div>

                <button type="button" id="lingua-retry-btn" class="w-full py-3 px-4 rounded-xl bg-gradient-to-r from-brand-teal to-brand-orange text-white font-semibold">
                    Try Again
                </button>
            </div>
        `;

        this.element.querySelector('#lingua-retry-btn')?.addEventListener('click', () => {
            this.renderInputPhase();
        });
    }

    async storeResult() {
        try {
            await fetch(`/api/v1/lingua/store-result?client_id=${this.config.clientId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    conversation_id: this.config.conversationId,
                    run_id: this.runId,
                    result_data: this.results
                })
            });
        } catch (error) {
            console.warn('[lingua-widget] Failed to store result:', error);
        }
    }

    update(data) {
        if (data.phase) {
            this.setPhase(data.phase);
        }
        if (data.results) {
            this.results = data.results;
            this.renderCompletePhase();
        }
        if (data.error) {
            this.renderError(data.error);
        }
    }
}

// Register widget type
if (typeof window.widgetRegistry !== 'undefined') {
    window.widgetRegistry.register('lingua', LinguaWidget);
    console.log('[lingua-widget] Widget registered with registry');
} else {
    console.warn('[lingua-widget] Widget registry not found, will register on load');
    document.addEventListener('DOMContentLoaded', () => {
        if (typeof window.widgetRegistry !== 'undefined') {
            window.widgetRegistry.register('lingua', LinguaWidget);
            console.log('[lingua-widget] Widget registered with registry (delayed)');
        }
    });
}
