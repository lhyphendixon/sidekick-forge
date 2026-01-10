/**
 * Sidekick Forge Widget System
 *
 * Base infrastructure for rendering interactive ability widgets in chat.
 * Widgets are triggered by the agent and rendered inline with chat messages.
 */

class WidgetRegistry {
    constructor() {
        this.widgets = new Map();
        this.activeWidgets = new Map(); // Track active widget instances by ID
    }

    /**
     * Register a widget type
     * @param {string} type - Widget type identifier (e.g., 'content_catalyst')
     * @param {class} widgetClass - Widget class that extends BaseWidget
     */
    register(type, widgetClass) {
        this.widgets.set(type, widgetClass);
        console.log(`[widgets] Registered widget type: ${type}`);
    }

    /**
     * Create and render a widget
     * @param {string} type - Widget type
     * @param {Object} config - Widget configuration
     * @param {HTMLElement} container - Container to render into
     * @returns {BaseWidget} Widget instance
     */
    create(type, config, container) {
        const WidgetClass = this.widgets.get(type);
        if (!WidgetClass) {
            console.error(`[widgets] Unknown widget type: ${type}`);
            return null;
        }

        const widget = new WidgetClass(config);
        const widgetId = config.id || `widget-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

        widget.id = widgetId;
        widget.render(container);

        this.activeWidgets.set(widgetId, widget);
        console.log(`[widgets] Created widget: ${type} (${widgetId})`);

        return widget;
    }

    /**
     * Get an active widget by ID
     * @param {string} widgetId - Widget ID
     * @returns {BaseWidget|null}
     */
    get(widgetId) {
        return this.activeWidgets.get(widgetId) || null;
    }

    /**
     * Update a widget with new data (e.g., progress updates)
     * @param {string} widgetId - Widget ID
     * @param {Object} data - Update data
     */
    update(widgetId, data) {
        const widget = this.activeWidgets.get(widgetId);
        if (widget) {
            widget.update(data);
        }
    }

    /**
     * Destroy a widget
     * @param {string} widgetId - Widget ID
     */
    destroy(widgetId) {
        const widget = this.activeWidgets.get(widgetId);
        if (widget) {
            widget.destroy();
            this.activeWidgets.delete(widgetId);
        }
    }
}

/**
 * Base Widget Class
 * All widgets should extend this class
 */
class BaseWidget {
    constructor(config) {
        this.config = config;
        this.id = null;
        this.element = null;
        this.state = 'idle'; // idle, loading, active, complete, error
    }

    /**
     * Render the widget into a container
     * @param {HTMLElement} container
     */
    render(container) {
        this.element = document.createElement('div');
        this.element.className = 'widget-container';
        this.element.setAttribute('data-widget-id', this.id);
        this.element.setAttribute('data-widget-type', this.constructor.name);
        container.appendChild(this.element);
    }

    /**
     * Update widget state/data
     * @param {Object} data
     */
    update(data) {
        // Override in subclass
    }

    /**
     * Clean up widget
     */
    destroy() {
        if (this.element && this.element.parentNode) {
            this.element.parentNode.removeChild(this.element);
        }
    }

    /**
     * Emit an event from the widget
     * @param {string} eventName
     * @param {Object} detail
     */
    emit(eventName, detail = {}) {
        const event = new CustomEvent(`widget:${eventName}`, {
            bubbles: true,
            detail: { widgetId: this.id, ...detail }
        });
        this.element?.dispatchEvent(event);
    }
}

// Global widget registry instance
window.widgetRegistry = new WidgetRegistry();

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { WidgetRegistry, BaseWidget };
}
