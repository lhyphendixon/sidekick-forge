# Ken Burns Image Provider Implementation Plan

## Overview
Add a new video provider type that generates AI images via RunWare.ai during conversations, displayed with a Ken Burns (pan/zoom) effect. The LLM uses a tool to generate contextual images that visualize the conversation.

## Key Decisions
- **Trigger**: Tool-based - LLM calls `generate_scene_image` with a prompt
- **Timing**: Async - image generation starts immediately while speech continues
- **Images**: One per response, ephemeral (not persisted)
- **Ken Burns**: Random pan/zoom direction
- **Model**: FLUX.2 [klein] 9B Base via RunWare.ai

---

## Phase 1: RunWare.ai Service Integration

### 1.1 Create RunWare Service
**File**: `app/services/runware_service.py`

```python
class RunWareService:
    """Service for generating images via RunWare.ai API"""

    BASE_URL = "https://api.runware.ai/v1"

    async def generate_image(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 576,  # 16:9 aspect ratio for video
        model: str = "runware:100@1",  # FLUX.2 klein 9B
        negative_prompt: str = "",
    ) -> str:
        """Generate image and return URL"""
        # Returns image URL for immediate use
```

### 1.2 Add Configuration
**File**: `app/config.py`
- Add `RUNWARE_API_KEY` environment variable
- Add default model and image settings

**File**: `app/models/client.py` (settings schema)
- Add `runware` configuration block to client settings

---

## Phase 2: Agent Worker Tool Implementation

### 2.1 Create Image Generation Tool
**File**: `app/agent_modules/tools/image_generation_tool.py`

```python
@function_tool
async def generate_scene_image(
    context: RunContext,
    image_prompt: str,
    style_hint: Optional[str] = None
) -> str:
    """
    Generate an image to visualize the current topic.

    Args:
        image_prompt: Detailed description of the scene to generate
        style_hint: Optional style guidance (e.g., "futuristic", "photorealistic")

    Returns:
        Confirmation that image generation has started
    """
```

### 2.2 Tool Registration
**File**: `app/agent_modules/agent_worker.py`
- Register `generate_scene_image` tool for agents with ken_burns video provider
- Tool should trigger async image generation via RunWare
- Push image URL to frontend via LiveKit data channel

### 2.3 System Prompt Enhancement
For agents using Ken Burns mode, add instruction:
```
When discussing visual concepts, scenarios, or the future, use the generate_scene_image
tool to create an illustrative image. Provide a detailed, vivid prompt that captures
the essence of what you're describing.
```

---

## Phase 3: LiveKit Data Channel Integration

### 3.1 Image Delivery Mechanism
**File**: `app/agent_modules/agent_worker.py`

Use LiveKit's data channel to send image URLs to the frontend:
```python
async def send_image_to_frontend(self, image_url: str, transition: str = "kenburns"):
    """Send generated image URL to frontend via data channel"""
    message = {
        "type": "scene_image",
        "url": image_url,
        "transition": transition,
        "timestamp": time.time()
    }
    await self.room.local_participant.publish_data(
        json.dumps(message).encode(),
        reliable=True
    )
```

---

## Phase 4: Frontend Ken Burns Video Component

### 4.1 New Video Provider Component
**File**: `app/static/js/kenburns-video-provider.js`

```javascript
class KenBurnsVideoProvider {
    constructor(containerElement) {
        this.container = containerElement;
        this.currentImage = null;
        this.nextImage = null;
    }

    // Handle incoming image from data channel
    async loadImage(imageUrl) {
        // Preload image
        // Crossfade from current to new
        // Apply random Ken Burns animation
    }

    applyKenBurnsEffect(imgElement) {
        // Random starting position
        // Random zoom direction (in or out)
        // Random pan direction
        // CSS animation over ~15-20 seconds
    }
}
```

### 4.2 Ken Burns CSS Animations
**File**: `app/static/css/kenburns.css`

```css
.kenburns-container {
    position: relative;
    overflow: hidden;
    width: 100%;
    height: 100%;
}

.kenburns-image {
    position: absolute;
    width: 120%;  /* Larger than container for pan room */
    height: 120%;
    object-fit: cover;
    animation: kenburns 20s ease-in-out infinite;
}

/* Multiple animation variants for randomization */
@keyframes kenburns-zoom-in-left { ... }
@keyframes kenburns-zoom-out-right { ... }
/* etc. */
```

### 4.3 Transcript Overlay
- Reuse existing transcript overlay from video avatar implementation
- Position over Ken Burns image with semi-transparent background
- Scrolling/fading text as sidekick speaks

### 4.4 Integration with Chat Interface
**File**: `app/templates/admin/partials/voice_chat_panel.html`

Add conditional rendering for ken_burns video provider:
```html
{% if agent.metadata.video_provider == 'ken_burns' %}
    <div id="kenburns-video-container" class="...">
        <div class="kenburns-image-layer"></div>
        <div class="transcript-overlay"></div>
    </div>
{% endif %}
```

---

## Phase 5: Configuration & Database

### 5.1 Video Provider Options
**File**: `app/templates/admin/agent_detail.html`

Add "Ken Burns / AI Generated Images" to video provider dropdown:
```html
<option value="ken_burns">Ken Burns (AI Generated Images)</option>
```

### 5.2 Ken Burns Settings UI
When ken_burns is selected, show additional options:
- Style preset (futuristic, realistic, artistic, etc.)
- Image generation frequency
- Ken Burns animation speed

### 5.3 Client-Level RunWare Configuration
**File**: `app/templates/admin/client_detail.html`

Add RunWare configuration section (similar to other API keys):
- API Key field
- Default model selection
- Usage limits/quotas

---

## Phase 6: Agent Metadata Schema

### 6.1 Update Agent Metadata
```python
{
    "video_provider": "ken_burns",
    "ken_burns_settings": {
        "style_preset": "futuristic",  # or "realistic", "artistic", etc.
        "animation_duration": 20,  # seconds
        "auto_generate": false,  # If true, auto-generate without tool call
        "default_negative_prompt": "blurry, low quality, text, watermark"
    }
}
```

---

## Implementation Order

1. **Phase 1**: RunWare service (backend foundation)
2. **Phase 2**: Agent tool (LLM integration)
3. **Phase 3**: LiveKit data channel (image delivery)
4. **Phase 4**: Frontend component (display)
5. **Phase 5**: Configuration UI (admin interface)
6. **Phase 6**: Schema updates (database)

---

## Files to Create
- `app/services/runware_service.py` - RunWare API client
- `app/agent_modules/tools/image_generation_tool.py` - Function tool
- `app/static/js/kenburns-video-provider.js` - Frontend component
- `app/static/css/kenburns.css` - Ken Burns animations

## Files to Modify
- `app/config.py` - Add RUNWARE_API_KEY
- `app/agent_modules/agent_worker.py` - Register tool, data channel
- `app/templates/admin/agent_detail.html` - Video provider option
- `app/templates/admin/client_detail.html` - RunWare config section
- `app/templates/admin/partials/voice_chat_panel.html` - Ken Burns container
- `app/static/js/voice-chat.js` - Handle ken_burns data channel messages

---

## API Reference: RunWare.ai

**Endpoint**: `POST https://api.runware.ai/v1/images/generations`

**Request**:
```json
{
    "model": "runware:100@1",
    "prompt": "A futuristic cityscape with flying vehicles...",
    "negative_prompt": "blurry, low quality",
    "width": 1024,
    "height": 576,
    "steps": 4,
    "scheduler": "FlowMatchEulerDiscreteScheduler",
    "numberResults": 1
}
```

**Response**:
```json
{
    "data": [{
        "imageURL": "https://...",
        "positivePrompt": "...",
        "seed": 12345
    }]
}
```

---

## Testing Plan

1. **Unit Tests**: RunWare service mocking
2. **Integration Test**: Tool registration and execution
3. **Manual Test**: End-to-end with Light Bridge sidekick
4. **Performance Test**: Image generation latency measurement

---

## Rollout

1. Deploy backend changes (service, tool)
2. Deploy frontend changes (Ken Burns component)
3. Configure Light Bridge sidekick with ken_burns provider
4. Test with real conversations
5. Iterate on prompts and timing
