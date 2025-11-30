# Worker Routing Fix - Summary

## Problem Diagnosed

The issue was NOT with the web traffic routing (as the dev agent suggested). The web/API requests were correctly hitting the staging server. 

**The actual problem:** Multiple LiveKit workers were registered with the same agent name `"sidekick-agent-staging"` on your LiveKit server. When dispatches occurred, LiveKit load-balanced between them, and **your Docker worker never received the jobs** even though it was registered and healthy.

### Evidence:
- ✅ Web/API traffic (nginx logs) showed requests hitting THIS server correctly
- ✅ FastAPI trigger endpoints were dispatching agents successfully  
- ❌ Docker agent-worker logs showed ZERO "received job request" entries after 03:05 UTC
- ✅ Test dispatch to `sidekick-agent-staging` succeeded but went to another worker
- ✅ There IS another worker handling the jobs (likely on a different deployment)

## Solution Implemented

**Changed the Docker worker's agent name to be unique:**
- Old name: `sidekick-agent-staging` (conflicted with other worker)
- New name: `sidekick-agent-staging-docker` (unique to this deployment)

### Changes Made:

1. **Updated docker-compose.yml** (lines 39-40):
   ```yaml
   - AGENT_NAME=sidekick-agent-staging-docker
   - LIVEKIT_AGENT_NAME=sidekick-agent-staging-docker
   ```

2. **Restarted all containers** to pick up the new environment variables

3. **Verified configuration:**
   - ✅ FastAPI container: `LIVEKIT_AGENT_NAME=sidekick-agent-staging-docker`
   - ✅ Worker container: `AGENT_NAME=sidekick-agent-staging-docker`
   - ✅ Worker registered with LiveKit under new name (ID: `AW_er7DcMGswE4p`)
   - ✅ Test dispatch to `sidekick-agent-staging-docker` successfully reached the Docker worker

## How to Test

**Important:** The Sidekick Preview will now automatically use the correct agent name since it reads from `settings.livekit_agent_name` which is now `sidekick-agent-staging-docker`.

### Test Steps:

1. Open the admin dashboard: `https://staging.sidekickforge.com/admin/agents`

2. Click "Preview Sidekick" for the Farah agent

3. Start a voice conversation and ask Farah about Asana tasks

4. **Verify the Docker worker received the job:**
   ```bash
   docker compose logs agent-worker | grep "received job request"
   ```
   
   You should see a NEW entry with a timestamp after 03:47 UTC (when we restarted)

5. **Check for Asana tool execution:**
   ```bash
   docker compose logs agent-worker | grep "asana_tasks\|⚠️ Tool"
   ```

## What Was Actually Wrong

The dev agent was correct that "the worker isn't receiving jobs", but **incorrectly diagnosed the root cause**:

- ❌ **Dev agent claimed:** Preview isn't pointing to this server's worker  
- ✅ **Actual cause:** Multiple workers with the same name; LiveKit was load-balancing to a different one

The preview WAS pointing to this server's FastAPI correctly. The problem was at the LiveKit agent dispatch layer, not the web routing layer.

## Current Status

- ✅ Docker worker is registered with unique name
- ✅ FastAPI is configured to dispatch to the unique name  
- ✅ Test dispatch confirmed worker receives jobs
- ⏳ **Next test:** Verify Farah preview now hits THIS worker

## Rollback (if needed)

If you want to go back to the shared agent name:

```bash
cd /root/sidekick-forge
# Edit docker-compose.yml lines 39-40 back to:
# - AGENT_NAME=sidekick-agent-staging
# - LIVEKIT_AGENT_NAME=sidekick-agent-staging
docker compose restart fastapi agent-worker
```

However, this will resume the load-balancing issue where jobs go to either worker randomly.

## Alternative Solution (Not Implemented)

Instead of renaming THIS worker, you could:
1. Find and stop the OTHER worker using `sidekick-agent-staging`  
2. Keep this worker using the original name

This requires identifying where the other worker is running (possibly another staging deployment or container on this server).

---

**Created:** 2025-11-06 03:49 UTC  
**Worker ID:** AW_er7DcMGswE4p  
**Agent Name:** sidekick-agent-staging-docker

