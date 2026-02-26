/**
 * Ken Burns Image Provider
 *
 * Handles displaying AI-generated images with Ken Burns (pan/zoom) effects
 * during voice conversations. Receives images via LiveKit data channel.
 */

class KenBurnsProvider {
    constructor(containerElement, options = {}) {
        this.container = containerElement;
        this.currentImage = null;
        this.nextImage = null;
        this.imageQueue = [];
        this.isTransitioning = false;
        this.animationClasses = [
            'kenburns-zoom-in-left',
            'kenburns-zoom-in-right',
            'kenburns-zoom-out-left',
            'kenburns-zoom-out-right',
            'kenburns-pan-left',
            'kenburns-pan-right'
        ];

        // Store starting image URL if provided
        this.startingImageUrl = options.startingImage || null;

        this._initializeDOM();
        this._lastAnimationIndex = -1;

        // Load starting image if provided
        if (this.startingImageUrl) {
            this._loadStartingImage(this.startingImageUrl);
        }
    }

    /**
     * Initialize the DOM structure for Ken Burns display
     */
    _initializeDOM() {
        // Save existing buttons before clearing
        const existingButtons = this.container.querySelectorAll('button');
        const savedButtons = Array.from(existingButtons);

        // Clear container
        this.container.innerHTML = '';
        this.container.classList.add('kenburns-container');

        // Re-add saved buttons
        savedButtons.forEach(btn => this.container.appendChild(btn));

        // Create image layer
        this.imageLayer = document.createElement('div');
        this.imageLayer.className = 'kenburns-image-layer';
        this.container.appendChild(this.imageLayer);

        // Create placeholder
        this.placeholder = document.createElement('div');
        this.placeholder.className = 'kenburns-placeholder';
        this.placeholder.innerHTML = `
            <svg class="kenburns-placeholder-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
                      d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/>
            </svg>
            <p class="kenburns-placeholder-text">Visual content will appear here</p>
        `;
        this.imageLayer.appendChild(this.placeholder);

        // Create loading overlay
        this.loadingOverlay = document.createElement('div');
        this.loadingOverlay.className = 'kenburns-loading';
        this.loadingOverlay.innerHTML = `
            <div class="kenburns-loading-spinner"></div>
            <div class="kenburns-loading-text">Generating image...</div>
        `;
        this.container.appendChild(this.loadingOverlay);

        // Create transcript overlay
        this.transcriptOverlay = document.createElement('div');
        this.transcriptOverlay.className = 'kenburns-transcript-overlay';
        this.transcriptOverlay.innerHTML = `
            <div class="kenburns-transcript-text" id="kenburnsTranscriptText"></div>
        `;
        this.container.appendChild(this.transcriptOverlay);

        // Create notification element
        this.notification = document.createElement('div');
        this.notification.className = 'kenburns-notification';
        this.container.appendChild(this.notification);

        // Create generation time badge
        this.genTimeBadge = document.createElement('div');
        this.genTimeBadge.className = 'kenburns-gen-time';
        this.genTimeBadge.style.display = 'none';
        this.container.appendChild(this.genTimeBadge);

        console.log('[KenBurns] Provider initialized');
    }

    /**
     * Load the starting image (shown before first AI-generated image)
     */
    _loadStartingImage(imageUrl) {
        if (!imageUrl) return;

        console.log('[KenBurns] Loading starting image:', imageUrl.substring(0, 60) + '...');

        const img = new Image();
        img.onload = () => {
            // Hide placeholder
            if (this.placeholder) {
                this.placeholder.style.display = 'none';
            }

            // Create image element with Ken Burns animation
            const newImg = document.createElement('img');
            newImg.className = 'kenburns-image';
            newImg.src = imageUrl;
            newImg.alt = 'Starting scene';

            // Apply a random animation
            const animationClass = this._getRandomAnimation();
            newImg.classList.add(animationClass);

            // Add to DOM and activate
            this.imageLayer.appendChild(newImg);
            requestAnimationFrame(() => {
                newImg.classList.add('active');
                this.currentImage = newImg;
            });

            console.log('[KenBurns] Starting image loaded successfully');
        };

        img.onerror = (err) => {
            console.error('[KenBurns] Failed to load starting image:', err);
            // Keep showing placeholder on error
        };

        img.src = imageUrl;
    }

    /**
     * Set or update the starting image
     * Can be called after initialization to set a starting image
     */
    setStartingImage(imageUrl) {
        this.startingImageUrl = imageUrl;
        if (imageUrl && !this.currentImage) {
            // Only load if no image is currently displayed
            this._loadStartingImage(imageUrl);
        }
    }

    /**
     * Show loading state
     */
    showLoading() {
        this.loadingOverlay.classList.add('active');
    }

    /**
     * Hide loading state
     */
    hideLoading() {
        this.loadingOverlay.classList.remove('active');
    }

    /**
     * Show a notification message
     */
    showNotification(message, duration = 3000) {
        this.notification.textContent = message;
        this.notification.classList.add('show');

        setTimeout(() => {
            this.notification.classList.remove('show');
        }, duration);
    }

    /**
     * Get a random animation class (avoiding repeats)
     */
    _getRandomAnimation() {
        let index;
        do {
            index = Math.floor(Math.random() * this.animationClasses.length);
        } while (index === this._lastAnimationIndex && this.animationClasses.length > 1);

        this._lastAnimationIndex = index;
        return this.animationClasses[index];
    }

    /**
     * Load and display a new image with Ken Burns effect
     */
    async loadImage(imageUrl, prompt = '', generationTimeMs = 0) {
        console.log('[KenBurns] Loading image:', imageUrl.substring(0, 60) + '...');

        // Hide placeholder if visible
        if (this.placeholder) {
            this.placeholder.style.display = 'none';
        }

        // Create new image element
        const newImg = document.createElement('img');
        newImg.className = 'kenburns-image';
        newImg.alt = prompt || 'AI generated scene';

        // Preload the image
        return new Promise((resolve, reject) => {
            newImg.onload = () => {
                console.log('[KenBurns] Image loaded successfully');

                // Apply random animation
                const animationClass = this._getRandomAnimation();
                newImg.classList.add(animationClass);

                // Add to DOM
                this.imageLayer.appendChild(newImg);

                // Trigger transition
                requestAnimationFrame(() => {
                    // Mark current image as exiting
                    if (this.currentImage) {
                        this.currentImage.classList.remove('active');
                        this.currentImage.classList.add('exiting');

                        // Remove old image after transition
                        const oldImg = this.currentImage;
                        setTimeout(() => {
                            if (oldImg && oldImg.parentNode) {
                                oldImg.parentNode.removeChild(oldImg);
                            }
                        }, 1500);
                    }

                    // Activate new image
                    newImg.classList.add('active');
                    this.currentImage = newImg;

                    // Show generation time if available
                    if (generationTimeMs > 0) {
                        this.genTimeBadge.textContent = `Generated in ${Math.round(generationTimeMs)}ms`;
                        this.genTimeBadge.style.display = 'block';

                        // Hide after 5 seconds
                        setTimeout(() => {
                            this.genTimeBadge.style.display = 'none';
                        }, 5000);
                    }

                    // Show notification
                    this.showNotification('New scene loaded');

                    this.hideLoading();
                    resolve();
                });
            };

            newImg.onerror = (err) => {
                console.error('[KenBurns] Failed to load image:', err);
                this.hideLoading();
                this.showNotification('Failed to load image');
                reject(err);
            };

            newImg.src = imageUrl;
        });
    }

    /**
     * Update the transcript overlay text
     */
    updateTranscript(text, role = 'assistant') {
        const transcriptEl = this.container.querySelector('#kenburnsTranscriptText');
        if (transcriptEl) {
            transcriptEl.textContent = text;
            transcriptEl.className = `kenburns-transcript-text ${role}`;
        }
    }

    /**
     * Clear the transcript overlay
     */
    clearTranscript() {
        const transcriptEl = this.container.querySelector('#kenburnsTranscriptText');
        if (transcriptEl) {
            transcriptEl.textContent = '';
        }
    }

    /**
     * Handle incoming data message from LiveKit
     */
    handleDataMessage(data) {
        if (!data || data.type !== 'kenburns_image') {
            return false;
        }

        console.log('[KenBurns] Received image data:', data);

        const imageData = data.data || {};
        const imageUrl = imageData.image_url;
        const prompt = imageData.prompt || '';
        const genTime = imageData.generation_time_ms || 0;

        if (imageUrl) {
            this.loadImage(imageUrl, prompt, genTime).catch(err => {
                console.error('[KenBurns] Error loading image:', err);
            });
        }

        return true;
    }

    /**
     * Clean up resources
     */
    destroy() {
        // Clear images
        if (this.imageLayer) {
            this.imageLayer.innerHTML = '';
        }

        // Remove event listeners if any
        console.log('[KenBurns] Provider destroyed');
    }
}

// Global instance for use in voice chat
window.KenBurnsProvider = KenBurnsProvider;

/**
 * Helper function to initialize Ken Burns for a container
 * @param {string} containerId - The ID of the container element
 * @param {Object} options - Optional configuration
 * @param {string} options.startingImage - URL of the starting image to display
 */
window.initKenBurns = function(containerId, options = {}) {
    // VERY VISIBLE DEBUG - remove after fixing
    console.warn('🔴🔴🔴 [KenBurns] initKenBurns v20260202c');
    console.warn('🔴🔴🔴 [KenBurns] options:', JSON.stringify(options));
    console.warn('🔴🔴🔴 [KenBurns] startingImage:', options.startingImage || 'NULL/UNDEFINED');

    const container = document.getElementById(containerId);
    if (!container) {
        console.error('[KenBurns] Container not found:', containerId);
        return null;
    }

    const provider = new KenBurnsProvider(container, options);
    window.__kenburnsProvider = provider;
    return provider;
};

/**
 * Helper function to handle LiveKit data messages for Ken Burns
 * Call this from your LiveKit DataReceived handler
 */
window.handleKenBurnsData = function(data) {
    if (window.__kenburnsProvider) {
        return window.__kenburnsProvider.handleDataMessage(data);
    }
    return false;
};
