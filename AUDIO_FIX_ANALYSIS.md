# Audio Issue Analysis - Critical Architecture Problem

## The Problem
**Audio has NEVER been working** - 0 TTS synthesis logs found in entire history.

## Root Cause
Incompatible dual-layer architecture:
```
AgentSession (LiveKit v1.0+) 
    ↓
SidekickAgent (extends old voice.Agent pattern)
```

This creates a conflict where:
1. AgentSession expects to handle TTS automatically from LLM chunks
2. SidekickAgent intercepts LLM output for transcript storage
3. The TTS synthesis never gets triggered

## Evidence
- 0 logs containing "synthesize", "speaking", or TTS activity
- The morning sessions that "worked" only had transcripts, no audio
- The `llm_node` override in SidekickAgent breaks the automatic TTS flow

## The Fix Needed
Remove the dual-layer architecture. Options:

### Option 1: Use AgentSession directly (Recommended)
- Remove SidekickAgent 
- Handle RAG/transcripts via AgentSession event handlers
- This is the proper LiveKit v1.0+ pattern

### Option 2: Use voice.Agent directly (Not recommended)
- Remove AgentSession wrapper
- Use SidekickAgent as the main agent
- Manually handle TTS synthesis

## Quick Fix
Comment out the SidekickAgent and use a simple agent that works with AgentSession.

## Note
The voice_id issue (invalid Cartesia ID) is a secondary problem. The primary issue is that TTS is never being called due to the architecture conflict.