# Agent-Guided Wizard System - Implementation Plan

## Executive Summary

Transform the sidekick creation wizard from a browser-dependent Web Speech API implementation to a **LiveKit-powered agent-guided experience**. An AI agent (Farah Qubit or any configured sidekick) will:
- Speak prompts using LiveKit's real-time audio
- Listen to natural language responses via LiveKit STT
- Use special "form-filling" tools to populate wizard fields
- Intelligently extract data from conversational responses

Additionally, create a **Wizard Creator** feature (superadmin-only) allowing custom lead-generation wizards with configurable questions and guide sidekicks.

---

## Part 1: Agent-Guided Sidekick Creation Wizard

### 1.1 Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           WIZARD MODAL (Frontend)                        │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  LiveKit Room Component (embedded)                               │    │
│  │  - Audio I/O via WebRTC (cross-browser)                         │    │
│  │  - Receives data messages from agent                            │    │
│  │  - Updates Alpine.js state based on tool calls                  │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              ↕ WebRTC                                    │
└─────────────────────────────────────────────────────────────────────────┘
                               ↕
┌─────────────────────────────────────────────────────────────────────────┐
│                         LIVEKIT CLOUD / SELF-HOSTED                      │
└─────────────────────────────────────────────────────────────────────────┘
                               ↕
┌─────────────────────────────────────────────────────────────────────────┐
│                           AGENT WORKER                                   │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Wizard Guide Agent (Farah Qubit)                               │    │
│  │  - STT → User speech transcribed                                │    │
│  │  - LLM → Understands intent, calls tools                        │    │
│  │  - TTS → Speaks responses                                       │    │
│  │  - Tools → wizard_set_field, wizard_next_step, etc.             │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              ↓ Tool Call                                 │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Wizard Form Tools                                              │    │
│  │  - wizard_set_field(field_name, value)                          │    │
│  │  - wizard_next_step()                                           │    │
│  │  - wizard_previous_step()                                       │    │
│  │  - wizard_generate_avatar(prompt)                               │    │
│  │  - wizard_select_voice(voice_id)                                │    │
│  │  - wizard_complete()                                            │    │
│  │  → Sends DataMessage to room with field updates                 │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 New Tool Type: `wizard`

Add a new tool type specifically for wizard form management.

**Schema Definition:**
```python
# In app/models/tools.py
ToolType = Literal["mcp", "n8n", "sidekick", "code", "asana", "helpscout",
                   "builtin", "content_catalyst", "documentsense", "wizard"]

class WizardToolConfig(BaseModel):
    """Configuration for wizard form-filling tools"""
    wizard_definition_id: str        # Reference to wizard definition
    field_mappings: Dict[str, str]   # Tool param → form field mappings
    send_data_messages: bool = True  # Whether to send LiveKit data messages
```

### 1.3 Wizard Tools to Implement

**File:** `/root/sidekick-forge/app/agent_modules/wizard_tools.py`

```python
"""
Wizard Form Management Tools

These tools allow an agent to guide a user through a wizard flow,
collecting information conversationally and filling form fields.
"""

from livekit.agents.llm.tool_context import function_tool as lk_function_tool
from livekit.agents.llm.tool_context import ToolError
from livekit import rtc
import json
from typing import Optional, Dict, Any, List

class WizardToolBuilder:
    """Builds wizard-specific tools for an agent session"""

    def __init__(self, room: rtc.Room, wizard_config: Dict[str, Any]):
        self.room = room
        self.wizard_config = wizard_config
        self.current_step = 1
        self.form_data = {}
        self.steps = wizard_config.get("steps", [])

    def build_tools(self) -> List[Any]:
        """Build all wizard tools with room context"""
        return [
            self._build_set_field_tool(),
            self._build_next_step_tool(),
            self._build_previous_step_tool(),
            self._build_get_current_state_tool(),
            self._build_generate_avatar_tool(),
            self._build_select_voice_tool(),
            self._build_complete_wizard_tool(),
        ]

    def _send_data_message(self, message_type: str, data: Dict[str, Any]):
        """Send a data message to the room for frontend sync"""
        payload = json.dumps({
            "type": message_type,
            "data": data,
            "timestamp": time.time()
        }).encode()

        asyncio.create_task(
            self.room.local_participant.publish_data(
                payload,
                kind=rtc.DataPacketKind.KIND_RELIABLE
            )
        )

    def _build_set_field_tool(self):
        """Tool to set a wizard form field value"""

        async def set_wizard_field(
            field_name: str,
            value: str,
            confidence: Optional[float] = None
        ) -> str:
            """
            Set a field value in the wizard form.

            Args:
                field_name: The name of the field to set (e.g., 'name', 'personality_description')
                value: The extracted value from the user's response
                confidence: Optional confidence score (0-1) for the extraction

            Returns:
                Confirmation of the field update
            """
            valid_fields = ["name", "slug", "personality_description", "voice_id",
                          "avatar_prompt", "config_mode"]

            if field_name not in valid_fields:
                raise ToolError(f"Invalid field: {field_name}. Valid fields: {valid_fields}")

            self.form_data[field_name] = value

            # Auto-generate slug from name
            if field_name == "name":
                import re
                slug = value.lower()
                slug = re.sub(r'[^a-z0-9\s-]', '', slug)
                slug = re.sub(r'[\s_]+', '-', slug)
                self.form_data["slug"] = slug

            # Send data message to frontend
            self._send_data_message("wizard_field_update", {
                "field": field_name,
                "value": value,
                "form_data": self.form_data,
                "current_step": self.current_step
            })

            return json.dumps({
                "status": "success",
                "field": field_name,
                "value": value,
                "message": f"Set {field_name} to '{value}'"
            })

        return lk_function_tool(raw_schema={
            "name": "set_wizard_field",
            "description": "Set a field value in the sidekick creation wizard. Use this when the user provides information for a wizard step. Extract the relevant value from their response.",
            "parameters": {
                "type": "object",
                "properties": {
                    "field_name": {
                        "type": "string",
                        "enum": ["name", "personality_description", "voice_id", "avatar_prompt", "config_mode"],
                        "description": "The field to update"
                    },
                    "value": {
                        "type": "string",
                        "description": "The value extracted from the user's response"
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence score 0-1 for the extraction"
                    }
                },
                "required": ["field_name", "value"]
            }
        })(set_wizard_field)

    def _build_next_step_tool(self):
        """Tool to advance to the next wizard step"""

        async def wizard_next_step() -> str:
            """
            Advance to the next step in the wizard.
            Call this when the current step is complete and the user is ready to continue.
            """
            if self.current_step >= len(self.steps):
                return json.dumps({
                    "status": "error",
                    "message": "Already at the last step"
                })

            self.current_step += 1
            next_step = self.steps[self.current_step - 1]

            self._send_data_message("wizard_step_change", {
                "current_step": self.current_step,
                "step_info": next_step,
                "form_data": self.form_data
            })

            return json.dumps({
                "status": "success",
                "current_step": self.current_step,
                "step_title": next_step.get("title"),
                "step_instruction": next_step.get("instruction"),
                "message": f"Moved to step {self.current_step}: {next_step.get('title')}"
            })

        return lk_function_tool(raw_schema={
            "name": "wizard_next_step",
            "description": "Advance to the next step in the wizard. Use when the current step has been completed.",
            "parameters": {"type": "object", "properties": {}}
        })(wizard_next_step)

    def _build_previous_step_tool(self):
        """Tool to go back to previous step"""

        async def wizard_previous_step() -> str:
            """Go back to the previous wizard step."""
            if self.current_step <= 1:
                return json.dumps({
                    "status": "error",
                    "message": "Already at the first step"
                })

            self.current_step -= 1
            prev_step = self.steps[self.current_step - 1]

            self._send_data_message("wizard_step_change", {
                "current_step": self.current_step,
                "step_info": prev_step,
                "form_data": self.form_data
            })

            return json.dumps({
                "status": "success",
                "current_step": self.current_step,
                "message": f"Went back to step {self.current_step}"
            })

        return lk_function_tool(raw_schema={
            "name": "wizard_previous_step",
            "description": "Go back to the previous wizard step. Use when the user wants to change something.",
            "parameters": {"type": "object", "properties": {}}
        })(wizard_previous_step)

    def _build_get_current_state_tool(self):
        """Tool to get current wizard state"""

        async def get_wizard_state() -> str:
            """Get the current state of the wizard including filled fields and current step."""
            current_step_info = self.steps[self.current_step - 1] if self.steps else {}

            return json.dumps({
                "current_step": self.current_step,
                "total_steps": len(self.steps),
                "current_step_info": current_step_info,
                "filled_fields": self.form_data,
                "remaining_required": self._get_remaining_required()
            })

        return lk_function_tool(raw_schema={
            "name": "get_wizard_state",
            "description": "Get the current state of the wizard. Use this to check progress or remind yourself what's been filled.",
            "parameters": {"type": "object", "properties": {}}
        })(get_wizard_state)

    def _build_complete_wizard_tool(self):
        """Tool to complete and submit the wizard"""

        async def complete_wizard() -> str:
            """
            Complete the wizard and create the sidekick.
            Only call this when all required fields are filled and the user confirms.
            """
            required = ["name", "personality_description"]
            missing = [f for f in required if f not in self.form_data]

            if missing:
                return json.dumps({
                    "status": "error",
                    "message": f"Cannot complete - missing required fields: {missing}"
                })

            self._send_data_message("wizard_complete", {
                "form_data": self.form_data,
                "ready_to_submit": True
            })

            return json.dumps({
                "status": "ready",
                "message": "Wizard is ready to submit. The frontend will now create the sidekick.",
                "form_data": self.form_data
            })

        return lk_function_tool(raw_schema={
            "name": "complete_wizard",
            "description": "Complete the wizard and signal that the sidekick should be created. Only use when all required information has been collected.",
            "parameters": {"type": "object", "properties": {}}
        })(complete_wizard)
```

### 1.4 System Prompt for Wizard Guide Agent

The wizard guide agent needs a specialized system prompt:

```python
WIZARD_GUIDE_SYSTEM_PROMPT = """You are Farah Qubit, a friendly AI assistant helping the user create their own AI sidekick.

## Your Role
You are guiding the user through a wizard to create a new sidekick. You have special tools to fill in the wizard form based on the user's responses.

## Wizard Steps
1. **Name** - Ask what they want to name their sidekick
2. **Personality** - Ask them to describe how their sidekick should communicate
3. **Voice Selection** - Help them choose a voice (you can describe available voices)
4. **Avatar** - Ask if they want to describe an avatar or generate a default one
5. **Knowledge Base** - Mention they can upload documents (handled via UI)
6. **Configuration** - Ask if they want default settings or advanced configuration
7. **API Keys** - Remind them to add API keys (handled via UI)
8. **Review & Launch** - Summarize what was created and confirm launch

## Guidelines
- Be conversational and warm, not robotic
- Extract the key information from natural responses
  - If they say "I want to call my sidekick Herman", extract "Herman" as the name
  - If they say "Make it professional but friendly", use that as the personality description
- Use the set_wizard_field tool to fill fields as you collect information
- Use wizard_next_step when ready to move on
- Confirm what you understood before moving to the next step
- If the user seems unsure, offer suggestions or examples

## Available Voices (for Step 3)
You can describe these voices when helping them choose:
- Professional British male
- Friendly American female
- Warm and calm female
- Energetic young male
(The frontend will show actual voice samples)

## Current Step Context
The frontend will keep you updated on the current step. Focus your questions on the current step's requirements.

Remember: Your goal is to make creating a sidekick feel like a friendly conversation, not filling out a form."""
```

### 1.5 Frontend Changes

**File:** `/root/sidekick-forge/app/templates/admin/wizard/wizard_modal.html`

Key changes:
1. Add LiveKit room connection when voice is enabled
2. Listen for data messages from agent to update form state
3. Remove browser Web Speech API code
4. Add voice activity indicator

```javascript
// New state for LiveKit connection
livekitConnected: false,
livekitRoom: null,
livekitToken: null,

// Enable voice mode - now connects to LiveKit
async enableVoice() {
    this.voiceEnabled = true;

    try {
        // Request wizard guide session from backend
        const token = localStorage.getItem('admin_token');
        const response = await fetch('/api/v1/wizard/sessions/' + this.sessionId + '/voice', {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json'
            }
        });

        if (!response.ok) throw new Error('Failed to start voice session');

        const { room_name, token: lkToken, ws_url } = await response.json();

        // Connect to LiveKit room
        this.livekitRoom = new LivekitClient.Room();

        // Listen for data messages from agent
        this.livekitRoom.on('dataReceived', (payload, participant) => {
            this.handleAgentMessage(JSON.parse(new TextDecoder().decode(payload)));
        });

        // Connect
        await this.livekitRoom.connect(ws_url, lkToken);
        this.livekitConnected = true;

        // Enable local microphone
        await this.livekitRoom.localParticipant.setMicrophoneEnabled(true);

    } catch (e) {
        console.error('Failed to connect to voice session:', e);
        alert('Voice mode unavailable. Please type your answers.');
    }
},

// Handle messages from the wizard guide agent
handleAgentMessage(message) {
    console.log('Agent message:', message);

    switch (message.type) {
        case 'wizard_field_update':
            // Update form field
            const { field, value, form_data } = message.data;
            this.stepData[field] = value;
            if (field === 'name') {
                this.stepData.slug = form_data.slug;
            }
            break;

        case 'wizard_step_change':
            // Move to new step
            this.currentStep = message.data.current_step;
            break;

        case 'wizard_complete':
            // Ready to submit
            if (message.data.ready_to_submit) {
                this.completeWizard();
            }
            break;
    }
}
```

### 1.6 Backend API Endpoint

**File:** `/root/sidekick-forge/app/api/v1/wizard.py`

Add endpoint to create voice-guided wizard session:

```python
@router.post("/sessions/{session_id}/voice")
async def start_wizard_voice_session(
    session_id: str,
    auth: AuthContext = Depends(require_user_auth)
) -> Dict[str, Any]:
    """
    Start a voice-guided wizard session with Farah Qubit (or configured guide).

    Creates a LiveKit room with the wizard guide agent and returns connection details.
    """
    # Verify session ownership
    session = await wizard_session_service.get_session(session_id)
    if not session or session["user_id"] != auth.user_id:
        raise HTTPException(status_code=404, detail="Session not found")

    client_id = session["client_id"]

    # Get wizard guide configuration (default: Farah Qubit)
    guide_agent_slug = "farah-qubit"  # Could be configurable

    # Build wizard context for the agent
    wizard_config = {
        "session_id": session_id,
        "steps": list(WIZARD_STEP_PROMPTS.values()),
        "current_step": session.get("current_step", 1),
        "form_data": session.get("step_data", {})
    }

    # Create room with wizard-specific metadata
    room_name = f"wizard-{session_id}"

    room_metadata = {
        "type": "wizard_guide",
        "wizard_config": wizard_config,
        "system_prompt": WIZARD_GUIDE_SYSTEM_PROMPT,
        "client_id": client_id,
        # Include API keys, voice settings, etc.
    }

    # Create LiveKit room
    room = await livekit_manager.create_room(
        name=room_name,
        metadata=room_metadata,
        enable_agent_dispatch=True,
        agent_name="wizard-guide-agent"
    )

    # Create participant token
    token = livekit_manager.create_token(
        identity=f"user-{auth.user_id}",
        room_name=room_name,
        dispatch_agent_name="wizard-guide-agent",
        dispatch_metadata=room_metadata
    )

    return {
        "room_name": room_name,
        "token": token,
        "ws_url": settings.LIVEKIT_WS_URL
    }
```

### 1.7 Agent Worker Integration

**File:** `/root/sidekick-forge/agent-worker/generic_agent.py`

Add wizard mode handling:

```python
async def entrypoint(ctx: JobContext):
    # ... existing code ...

    # Check if this is a wizard guide session
    if combined_metadata.get("type") == "wizard_guide":
        wizard_config = combined_metadata.get("wizard_config", {})

        # Build wizard-specific tools
        from wizard_tools import WizardToolBuilder
        wizard_tool_builder = WizardToolBuilder(ctx.room, wizard_config)
        wizard_tools = wizard_tool_builder.build_tools()

        # Add wizard tools to the tool set
        all_tools.extend(wizard_tools)

        # Override system prompt for wizard mode
        system_prompt = combined_metadata.get("system_prompt", WIZARD_GUIDE_SYSTEM_PROMPT)
```

---

## Part 2: Wizard Creator Feature (Superadmin Only)

### 2.1 Database Schema

**Platform Supabase - New Tables:**

```sql
-- Wizard definitions (templates)
CREATE TABLE wizard_definitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) NOT NULL,
    description TEXT,
    guide_agent_id UUID,  -- The sidekick that guides this wizard
    guide_agent_slug VARCHAR(255),  -- Cached for quick lookup
    status VARCHAR(50) DEFAULT 'draft',  -- draft, published, archived
    settings JSONB DEFAULT '{}',
    webhook_url TEXT,  -- Where to POST completed wizard data
    created_by UUID REFERENCES auth.users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(client_id, slug)
);

-- Wizard steps
CREATE TABLE wizard_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wizard_id UUID REFERENCES wizard_definitions(id) ON DELETE CASCADE,
    step_order INTEGER NOT NULL,
    title VARCHAR(255) NOT NULL,
    instruction TEXT,
    field_name VARCHAR(100) NOT NULL,  -- The data field this step collects
    field_type VARCHAR(50) DEFAULT 'text',  -- text, textarea, select, multi_select, voice_only
    field_options JSONB DEFAULT '[]',  -- For select/multi_select types
    validation JSONB DEFAULT '{}',  -- min_length, max_length, pattern, required
    voice_prompt TEXT,  -- What the agent should say for this step
    extraction_hints TEXT,  -- Hints for the LLM on extracting data from responses
    is_required BOOLEAN DEFAULT TRUE,
    skip_conditions JSONB DEFAULT '[]',  -- Conditions to skip this step
    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(wizard_id, step_order)
);

-- Wizard sessions (execution instances)
CREATE TABLE wizard_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wizard_id UUID REFERENCES wizard_definitions(id) ON DELETE SET NULL,
    client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
    visitor_id VARCHAR(255),  -- Anonymous visitor identifier
    user_id UUID,  -- If authenticated
    current_step INTEGER DEFAULT 1,
    status VARCHAR(50) DEFAULT 'in_progress',  -- in_progress, completed, abandoned
    collected_data JSONB DEFAULT '{}',
    metadata JSONB DEFAULT '{}',  -- UTM params, referrer, etc.
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    livekit_room_name VARCHAR(255)
);

-- Indexes
CREATE INDEX idx_wizard_definitions_client ON wizard_definitions(client_id);
CREATE INDEX idx_wizard_steps_wizard ON wizard_steps(wizard_id);
CREATE INDEX idx_wizard_executions_wizard ON wizard_executions(wizard_id);
CREATE INDEX idx_wizard_executions_status ON wizard_executions(status);
```

### 2.2 Wizard Definition Models

**File:** `/root/sidekick-forge/app/models/wizard_definition.py`

```python
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

class FieldType(str, Enum):
    TEXT = "text"
    TEXTAREA = "textarea"
    SELECT = "select"
    MULTI_SELECT = "multi_select"
    EMAIL = "email"
    PHONE = "phone"
    NUMBER = "number"
    DATE = "date"
    VOICE_ONLY = "voice_only"  # No visual input, voice capture only

class WizardStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"

class FieldValidation(BaseModel):
    required: bool = True
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None  # Regex pattern
    custom_error: Optional[str] = None

class WizardStepCreate(BaseModel):
    title: str
    instruction: Optional[str] = None
    field_name: str
    field_type: FieldType = FieldType.TEXT
    field_options: List[Dict[str, str]] = []  # [{value: "...", label: "..."}]
    validation: FieldValidation = FieldValidation()
    voice_prompt: Optional[str] = None
    extraction_hints: Optional[str] = None
    is_required: bool = True

class WizardStep(WizardStepCreate):
    id: str
    wizard_id: str
    step_order: int
    created_at: datetime

class WizardDefinitionCreate(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    guide_agent_slug: Optional[str] = None
    webhook_url: Optional[str] = None
    settings: Dict[str, Any] = {}

class WizardDefinition(WizardDefinitionCreate):
    id: str
    client_id: str
    guide_agent_id: Optional[str] = None
    status: WizardStatus = WizardStatus.DRAFT
    created_by: str
    created_at: datetime
    updated_at: datetime
    steps: List[WizardStep] = []

class WizardExecutionCreate(BaseModel):
    wizard_id: str
    visitor_id: Optional[str] = None
    metadata: Dict[str, Any] = {}

class WizardExecution(BaseModel):
    id: str
    wizard_id: str
    client_id: str
    visitor_id: Optional[str] = None
    user_id: Optional[str] = None
    current_step: int = 1
    status: str = "in_progress"
    collected_data: Dict[str, Any] = {}
    metadata: Dict[str, Any] = {}
    started_at: datetime
    completed_at: Optional[datetime] = None
```

### 2.3 Wizard Definition Service

**File:** `/root/sidekick-forge/app/services/wizard_definition_service.py`

```python
"""
Wizard Definition Service

Manages wizard templates (definitions) and their steps.
Wizards are client-scoped and can be published for use.
"""

from typing import List, Optional, Dict, Any
from uuid import UUID
import logging

from app.models.wizard_definition import (
    WizardDefinition, WizardDefinitionCreate,
    WizardStep, WizardStepCreate, WizardStatus
)
from app.integrations.supabase_client import supabase_manager

logger = logging.getLogger(__name__)

class WizardDefinitionService:
    """Service for managing wizard definitions"""

    async def create_wizard(
        self,
        client_id: str,
        user_id: str,
        data: WizardDefinitionCreate
    ) -> WizardDefinition:
        """Create a new wizard definition"""
        if not supabase_manager._initialized:
            await supabase_manager.initialize()

        # Resolve guide agent if provided
        guide_agent_id = None
        if data.guide_agent_slug:
            # Look up agent ID from slug
            # ... implementation
            pass

        result = supabase_manager.admin_client.table("wizard_definitions").insert({
            "client_id": client_id,
            "name": data.name,
            "slug": data.slug,
            "description": data.description,
            "guide_agent_id": guide_agent_id,
            "guide_agent_slug": data.guide_agent_slug,
            "webhook_url": data.webhook_url,
            "settings": data.settings,
            "status": WizardStatus.DRAFT.value,
            "created_by": user_id
        }).execute()

        return WizardDefinition(**result.data[0])

    async def get_wizard(
        self,
        wizard_id: str,
        include_steps: bool = True
    ) -> Optional[WizardDefinition]:
        """Get a wizard definition by ID"""
        result = supabase_manager.admin_client.table("wizard_definitions").select(
            "*"
        ).eq("id", wizard_id).single().execute()

        if not result.data:
            return None

        wizard = WizardDefinition(**result.data)

        if include_steps:
            steps_result = supabase_manager.admin_client.table("wizard_steps").select(
                "*"
            ).eq("wizard_id", wizard_id).order("step_order").execute()

            wizard.steps = [WizardStep(**s) for s in steps_result.data]

        return wizard

    async def list_wizards(
        self,
        client_id: str,
        status: Optional[WizardStatus] = None
    ) -> List[WizardDefinition]:
        """List all wizards for a client"""
        query = supabase_manager.admin_client.table("wizard_definitions").select(
            "*"
        ).eq("client_id", client_id)

        if status:
            query = query.eq("status", status.value)

        result = query.order("created_at", desc=True).execute()

        return [WizardDefinition(**w) for w in result.data]

    async def add_step(
        self,
        wizard_id: str,
        data: WizardStepCreate,
        position: Optional[int] = None
    ) -> WizardStep:
        """Add a step to a wizard"""
        # Get current max step order
        result = supabase_manager.admin_client.table("wizard_steps").select(
            "step_order"
        ).eq("wizard_id", wizard_id).order("step_order", desc=True).limit(1).execute()

        if position is None:
            max_order = result.data[0]["step_order"] if result.data else 0
            step_order = max_order + 1
        else:
            step_order = position
            # Shift existing steps
            # ... implementation

        insert_result = supabase_manager.admin_client.table("wizard_steps").insert({
            "wizard_id": wizard_id,
            "step_order": step_order,
            "title": data.title,
            "instruction": data.instruction,
            "field_name": data.field_name,
            "field_type": data.field_type.value,
            "field_options": data.field_options,
            "validation": data.validation.dict(),
            "voice_prompt": data.voice_prompt,
            "extraction_hints": data.extraction_hints,
            "is_required": data.is_required
        }).execute()

        return WizardStep(**insert_result.data[0])

    async def update_step(
        self,
        step_id: str,
        data: Dict[str, Any]
    ) -> WizardStep:
        """Update a wizard step"""
        result = supabase_manager.admin_client.table("wizard_steps").update(
            data
        ).eq("id", step_id).execute()

        return WizardStep(**result.data[0])

    async def delete_step(self, step_id: str) -> bool:
        """Delete a wizard step and reorder remaining steps"""
        # Get step info first
        step = supabase_manager.admin_client.table("wizard_steps").select(
            "*"
        ).eq("id", step_id).single().execute()

        if not step.data:
            return False

        wizard_id = step.data["wizard_id"]
        step_order = step.data["step_order"]

        # Delete the step
        supabase_manager.admin_client.table("wizard_steps").delete().eq(
            "id", step_id
        ).execute()

        # Reorder subsequent steps
        supabase_manager.admin_client.rpc("reorder_wizard_steps", {
            "p_wizard_id": wizard_id,
            "p_deleted_order": step_order
        }).execute()

        return True

    async def publish_wizard(self, wizard_id: str) -> WizardDefinition:
        """Publish a wizard (make it available for use)"""
        result = supabase_manager.admin_client.table("wizard_definitions").update({
            "status": WizardStatus.PUBLISHED.value
        }).eq("id", wizard_id).execute()

        return WizardDefinition(**result.data[0])

    async def archive_wizard(self, wizard_id: str) -> WizardDefinition:
        """Archive a wizard"""
        result = supabase_manager.admin_client.table("wizard_definitions").update({
            "status": WizardStatus.ARCHIVED.value
        }).eq("id", wizard_id).execute()

        return WizardDefinition(**result.data[0])


# Singleton instance
wizard_definition_service = WizardDefinitionService()
```

### 2.4 Superadmin Wizard Management Page

**File:** `/root/sidekick-forge/app/templates/admin/wizards.html`

```html
{% extends "admin/base.html" %}

{% block title %}Wizard Creator{% endblock %}

{% block content %}
<div class="p-6 max-w-7xl mx-auto">
    <!-- Header -->
    <div class="mb-6">
        <div class="flex items-center justify-between">
            <div>
                <h1 class="text-2xl font-bold text-dark-text">Wizard Creator</h1>
                <p class="text-dark-text-secondary">Create custom lead generation wizards</p>
            </div>
            <button onclick="openCreateWizardModal()"
                    class="btn-primary px-4 py-2 rounded text-sm font-medium">
                Create New Wizard
            </button>
        </div>
    </div>

    <!-- Wizards List -->
    <div class="grid gap-4">
        {% for wizard in wizards %}
        <div class="bg-dark-surface rounded-lg border border-dark-border p-4">
            <div class="flex items-center justify-between">
                <div class="flex items-center gap-4">
                    <div class="w-12 h-12 rounded-lg bg-brand-teal/20 flex items-center justify-center">
                        <svg class="w-6 h-6 text-brand-teal" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                                  d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
                        </svg>
                    </div>
                    <div>
                        <h3 class="font-semibold text-dark-text">{{ wizard.name }}</h3>
                        <p class="text-sm text-dark-text-secondary">
                            {{ wizard.steps|length }} steps · Guide: {{ wizard.guide_agent_slug or 'Not assigned' }}
                        </p>
                    </div>
                </div>
                <div class="flex items-center gap-3">
                    <span class="px-2 py-1 rounded text-xs font-medium
                        {% if wizard.status == 'published' %}bg-green-500/20 text-green-400
                        {% elif wizard.status == 'draft' %}bg-amber-500/20 text-amber-400
                        {% else %}bg-gray-500/20 text-gray-400{% endif %}">
                        {{ wizard.status|title }}
                    </span>
                    <button onclick="editWizard('{{ wizard.id }}')"
                            class="btn-secondary px-3 py-1.5 text-sm">
                        Edit
                    </button>
                    <button onclick="previewWizard('{{ wizard.id }}')"
                            class="btn-secondary px-3 py-1.5 text-sm">
                        Preview
                    </button>
                </div>
            </div>
        </div>
        {% else %}
        <div class="bg-dark-surface rounded-lg border border-dark-border p-8 text-center">
            <svg class="mx-auto h-12 w-12 text-gray-400 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                      d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
            </svg>
            <p class="text-gray-500 mb-4">No wizards yet. Create your first lead generation wizard!</p>
            <button onclick="openCreateWizardModal()"
                    class="btn-primary px-4 py-2 rounded text-sm font-medium">
                Create First Wizard
            </button>
        </div>
        {% endfor %}
    </div>
</div>

<!-- Wizard Editor Modal (loads via HTMX) -->
<div id="wizard-editor-container"></div>

<script>
function openCreateWizardModal() {
    htmx.ajax('GET', '/admin/wizards/create', {target: '#wizard-editor-container', swap: 'innerHTML'});
}

function editWizard(wizardId) {
    htmx.ajax('GET', `/admin/wizards/${wizardId}/edit`, {target: '#wizard-editor-container', swap: 'innerHTML'});
}

function previewWizard(wizardId) {
    window.open(`/wizard/${wizardId}/preview`, '_blank');
}
</script>
{% endblock %}
```

### 2.5 API Endpoints for Wizard Definitions

**File:** `/root/sidekick-forge/app/api/v1/wizard_definitions.py`

```python
"""
Wizard Definitions API

Superadmin-only endpoints for creating and managing wizard templates.
"""

from fastapi import APIRouter, HTTPException, Depends
from typing import List, Optional

from app.models.wizard_definition import (
    WizardDefinition, WizardDefinitionCreate,
    WizardStep, WizardStepCreate, WizardStatus
)
from app.services.wizard_definition_service import wizard_definition_service
from app.middleware.auth import require_superadmin_auth
from app.models.user import AuthContext

router = APIRouter(prefix="/wizard-definitions", tags=["wizard-definitions"])


@router.post("", response_model=WizardDefinition)
async def create_wizard(
    data: WizardDefinitionCreate,
    auth: AuthContext = Depends(require_superadmin_auth)
) -> WizardDefinition:
    """Create a new wizard definition (superadmin only)"""
    return await wizard_definition_service.create_wizard(
        client_id=auth.client_id,
        user_id=auth.user_id,
        data=data
    )


@router.get("", response_model=List[WizardDefinition])
async def list_wizards(
    status: Optional[WizardStatus] = None,
    auth: AuthContext = Depends(require_superadmin_auth)
) -> List[WizardDefinition]:
    """List all wizards for the client"""
    return await wizard_definition_service.list_wizards(
        client_id=auth.client_id,
        status=status
    )


@router.get("/{wizard_id}", response_model=WizardDefinition)
async def get_wizard(
    wizard_id: str,
    auth: AuthContext = Depends(require_superadmin_auth)
) -> WizardDefinition:
    """Get a specific wizard definition"""
    wizard = await wizard_definition_service.get_wizard(wizard_id)
    if not wizard or wizard.client_id != auth.client_id:
        raise HTTPException(status_code=404, detail="Wizard not found")
    return wizard


@router.post("/{wizard_id}/steps", response_model=WizardStep)
async def add_wizard_step(
    wizard_id: str,
    data: WizardStepCreate,
    position: Optional[int] = None,
    auth: AuthContext = Depends(require_superadmin_auth)
) -> WizardStep:
    """Add a step to a wizard"""
    # Verify ownership
    wizard = await wizard_definition_service.get_wizard(wizard_id, include_steps=False)
    if not wizard or wizard.client_id != auth.client_id:
        raise HTTPException(status_code=404, detail="Wizard not found")

    return await wizard_definition_service.add_step(wizard_id, data, position)


@router.put("/{wizard_id}/steps/{step_id}", response_model=WizardStep)
async def update_wizard_step(
    wizard_id: str,
    step_id: str,
    data: WizardStepCreate,
    auth: AuthContext = Depends(require_superadmin_auth)
) -> WizardStep:
    """Update a wizard step"""
    return await wizard_definition_service.update_step(step_id, data.dict())


@router.delete("/{wizard_id}/steps/{step_id}")
async def delete_wizard_step(
    wizard_id: str,
    step_id: str,
    auth: AuthContext = Depends(require_superadmin_auth)
) -> dict:
    """Delete a wizard step"""
    success = await wizard_definition_service.delete_step(step_id)
    if not success:
        raise HTTPException(status_code=404, detail="Step not found")
    return {"success": True}


@router.post("/{wizard_id}/publish", response_model=WizardDefinition)
async def publish_wizard(
    wizard_id: str,
    auth: AuthContext = Depends(require_superadmin_auth)
) -> WizardDefinition:
    """Publish a wizard (make it available for use)"""
    return await wizard_definition_service.publish_wizard(wizard_id)


@router.post("/{wizard_id}/archive", response_model=WizardDefinition)
async def archive_wizard(
    wizard_id: str,
    auth: AuthContext = Depends(require_superadmin_auth)
) -> WizardDefinition:
    """Archive a wizard"""
    return await wizard_definition_service.archive_wizard(wizard_id)
```

### 2.6 Public Wizard Execution Endpoint

For lead-gen wizards that need to be publicly accessible:

```python
# Public execution endpoint (no auth required for lead gen)
@router.post("/execute/{client_slug}/{wizard_slug}")
async def start_wizard_execution(
    client_slug: str,
    wizard_slug: str,
    visitor_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Start a public wizard execution for lead generation.

    No authentication required - uses client_slug and wizard_slug.
    Returns LiveKit connection details for voice-guided experience.
    """
    # Look up client and wizard
    # Create execution record
    # Create LiveKit room
    # Return connection details
    pass
```

---

## Part 3: Implementation Phases

### Phase 1: Agent-Guided Sidekick Wizard (1-2 weeks)
1. Create wizard tools module (`wizard_tools.py`)
2. Add wizard mode handling in agent worker
3. Create `/sessions/{id}/voice` endpoint
4. Update wizard modal with LiveKit integration
5. Handle data messages for field updates
6. Test end-to-end flow

### Phase 2: Wizard Definition Infrastructure (1 week)
1. Create database migrations
2. Implement wizard definition models
3. Implement wizard definition service
4. Create API endpoints
5. Add superadmin auth middleware check

### Phase 3: Wizard Creator UI (1-2 weeks)
1. Create wizards list page
2. Create wizard editor modal/page
3. Implement step editor with drag-and-drop reordering
4. Add guide agent selection
5. Preview functionality

### Phase 4: Public Wizard Execution (1 week)
1. Create public execution endpoint
2. Embeddable wizard widget
3. Webhook delivery for completed wizards
4. Analytics/tracking

---

## Part 4: Future Enhancements

### 4.1 Conditional Logic
- Skip steps based on previous answers
- Branch to different paths
- Dynamic step generation

### 4.2 Rich Field Types
- File uploads
- Signature capture
- Location/address autocomplete
- Calendar scheduling

### 4.3 Multi-Language Support
- Translatable step prompts
- Language detection
- Multi-language TTS voices

### 4.4 Analytics Dashboard
- Completion rates
- Drop-off points
- Average completion time
- Lead quality scoring

### 4.5 Integrations
- CRM sync (HubSpot, Salesforce)
- Email marketing (Mailchimp, ConvertKit)
- Slack notifications
- Zapier/Make webhooks

---

## File Summary

### New Files to Create
```
/app/agent_modules/wizard_tools.py          # Wizard form-filling tools
/app/models/wizard_definition.py            # Wizard definition models
/app/services/wizard_definition_service.py  # Wizard CRUD service
/app/api/v1/wizard_definitions.py           # Wizard definition API
/app/templates/admin/wizards.html           # Wizard list page
/app/templates/admin/wizard_editor.html     # Wizard editor modal
```

### Files to Modify
```
/app/api/v1/wizard.py                       # Add voice session endpoint
/app/api/v1/__init__.py                     # Register new router
/app/templates/admin/wizard/wizard_modal.html  # LiveKit integration
/agent-worker/generic_agent.py              # Wizard mode handling
/app/main_multitenant.py                    # Register wizards admin route
```

### Database Migrations
```
-- wizard_definitions table
-- wizard_steps table
-- wizard_executions table
-- RLS policies for client scoping
```
