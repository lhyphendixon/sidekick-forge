# Agent Sync Implementation

## Overview
Fixed the sync mechanism in the agent platform so that when an agent is updated via the UI, it updates both:
1. The `agents` table (which it was already doing)
2. The `agent_configurations` table (which was missing)

## Changes Made

### 1. **Modified `AgentService.update_agent()` in `/opt/autonomite-saas/app/services/agent_service_supabase.py`**
   - Added call to `_update_agent_configuration()` after successfully updating the agents table
   - Preserves the voice_settings dict before JSON conversion to pass to configuration update

### 2. **Added `_update_agent_configuration()` method in `/opt/autonomite-saas/app/services/agent_service_supabase.py`**
   - Updates the `agent_configurations` table with new settings
   - Properly builds the `provider_config` JSON structure expected by the agent runtime
   - Maps voice settings to the correct provider-specific fields
   - Handles all TTS providers: OpenAI, ElevenLabs, Cartesia, Speechify
   - Updates LLM and STT provider settings

### 3. **Updated global agent handling in `/opt/autonomite-saas/app/api/v1/agents.py`**
   - Added initialization check for supabase_manager
   - Added call to `_update_global_agent_configuration()` for global agents
   - Properly parses voice_settings from JSON string

### 4. **Added `_update_global_agent_configuration()` function in `/opt/autonomite-saas/app/api/v1/agents.py`**
   - Handles agent_configurations updates for global agents
   - Mirrors the functionality of the service method but for global Supabase

## Structure of agent_configurations Table

The `agent_configurations` table contains:
- `id`: UUID primary key
- `agent_slug`: Reference to the agent
- `agent_name`: Display name of the agent
- `system_prompt`: The agent's system prompt
- `voice_id`: Direct voice ID field
- `temperature`: LLM temperature setting
- `provider_config`: JSON object with nested configuration:
  ```json
  {
    "llm": {
      "provider": "groq",
      "model": "mixtral-8x7b-32768",
      "temperature": 0.9
    },
    "tts": {
      "provider": "cartesia",
      "voice_id": "test-voice-123",
      "model": "sonic-english",
      "output_format": "pcm_44100"
    },
    "stt": {
      "provider": "deepgram",
      "model": "nova-2"
    }
  }
  ```
- `voice_settings`: JSON copy of the voice settings from the UI
- Various API key fields (for client-specific configurations)
- Timestamps: `created_at`, `updated_at`, `last_updated`

## Testing

Created `/opt/autonomite-saas/scripts/test_agent_sync.py` to verify the sync mechanism:
- Updates an agent via the API
- Checks both `agents` and `agent_configurations` tables
- Verifies the provider_config structure is correct

## Important Notes

1. The sync only updates existing `agent_configurations` entries - it doesn't create new ones
2. If no configuration exists, a warning is logged but the agent update still succeeds
3. The provider_config structure matches what the agent runtime expects
4. All provider-specific settings are properly mapped (voice IDs, models, etc.)
5. Global agents use a separate code path but the same update logic