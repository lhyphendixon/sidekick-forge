# LiveKit 500 Error Fix

## Issue
After implementing client-specific LiveKit credentials, the system was throwing a 500 error:
```
Error: 500: Internal error triggering agent: LiveKitManager.__init__() got an unexpected keyword argument 'url'
```

## Root Cause
The `LiveKitManager` class in `/opt/autonomite-saas/app/integrations/livekit_client.py` doesn't accept constructor parameters. It's designed to read credentials from the global `settings` object.

## Solution
Modified `/opt/autonomite-saas/app/services/livekit_client_manager.py` to:
1. Create a default `LiveKitManager` instance
2. Override the instance attributes with client-specific credentials
3. Call `initialize()` to establish the connection

```python
# Create instance without parameters
client_livekit = LiveKitManager()

# Override attributes with client credentials
client_livekit.api_key = api_key
client_livekit.api_secret = api_secret
client_livekit.url = server_url

# Initialize with client credentials
await client_livekit.initialize()
```

## Verification
The fix has been tested and confirmed to work:
- LiveKitManager instances can have their attributes overridden
- Tokens are generated correctly with the overridden credentials
- The multi-tenant isolation architecture is preserved

## Next Steps
1. Restart the FastAPI service to apply the fix
2. Configure clients with unique LiveKit credentials
3. Test end-to-end voice chat with proper isolation