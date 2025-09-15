# Voice Transcript Debugging Guide

## Issues Identified

1. **Double Agent Response**: Two agent tracks subscribed (agent-AJ_mK7XmgKCzGfU and agent-AJ_ddP9dYXPXgcd)
   - This means two agents joined the room
   - Could be from multiple worker instances or retry logic

2. **No Transcript Logs**: None of our logging appears
   - Event handlers might not be firing
   - Or logs are not being captured/displayed

## Changes Made

### 1. Enhanced Event Handlers (entrypoint.py)
- Made event handlers async (was sync before)
- Added logging when events are registered
- Added logging when events fire
- Changed from `asyncio.create_task` to `await` for assistant transcript handling

### 2. Enhanced Logging

#### Key Log Messages to Look For:
```
📝 Registering user_speech_committed event handler...
📝 Registering agent_speech_committed event handler...
🎤 user_speech_committed event fired!
🤖 agent_speech_committed event fired!
💬 Captured user speech: [text]
🤖 Captured assistant speech: [text]
🔔 on_user_turn_completed called!
📝 _handle_assistant_transcript called with text: [text]
🔄 store_turn called!
📤 Attempting to insert user row
✅ User row inserted successfully
📤 Attempting to insert assistant row
✅ Assistant row inserted successfully
✅ Stored complete turn | turn_id=xxx
```

### 3. Debug Information Added
- Session type logging
- Agent type logging
- Available events logging (if accessible)

## Double Agent Issue

### Possible Causes:
1. **Multiple Worker Instances**: Check if multiple agent containers are running
   ```bash
   docker ps | grep agent
   ```

2. **Retry Logic**: Check if the dispatch is retrying on failure

3. **Frontend Double-Trigger**: Check if the frontend is calling the API twice

### How to Debug:
1. Check agent container logs:
   ```bash
   docker logs -f [agent-container-id] 2>&1 | grep -E "Starting agent|dispatch|room"
   ```

2. Check FastAPI logs for double dispatch:
   ```bash
   docker logs -f [fastapi-container-id] 2>&1 | grep "dispatch_agent_job"
   ```

## Transcript Storage Flow

### Expected Flow:
1. User speaks → `user_speech_committed` event
2. Event handler sets `agent._current_user_transcript`
3. Agent processes (RAG happens in `on_user_turn_completed`)
4. Assistant speaks → `agent_speech_committed` event
5. Event handler calls `agent._handle_assistant_transcript()`
6. Turn is stored with both messages

### What Could Go Wrong:
1. **Events Not Firing**: The event names might be wrong
2. **Event Handlers Not Registered**: The session might not support these events
3. **Agent Not Connected**: The agent might not be properly connected to the session
4. **Database Connection**: Supabase client might not be available
5. **Missing Fields**: Required fields might be missing

## Next Steps

1. **Check Container Logs**:
   ```bash
   docker-compose logs -f agent 2>&1 | tee agent_logs.txt
   ```

2. **Look for Key Messages**:
   - "Registering" messages show handlers are being set up
   - "event fired" messages show events are triggering
   - "Captured" messages show text is being extracted
   - "store_turn called" shows database write attempt

3. **Check for Errors**:
   - Look for Python exceptions
   - Look for "Failed to" messages
   - Check for "Cannot store turn" warnings

4. **Verify Database**:
   - Check if the columns exist in the database
   - Check if any rows are being written at all
   - Look for permission errors

## Alternative Approaches

If events aren't working, we could:
1. Hook into the agent's message generation directly
2. Use room data events to track messages
3. Implement a message queue for transcript storage
4. Use the LiveKit recording API as a fallback