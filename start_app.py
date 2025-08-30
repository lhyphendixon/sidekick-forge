#!/usr/bin/env python3
"""
Start the sidekick-forge application with manual environment loading
"""
import os
import sys

# Load .env manually first
try:
    with open('.env', 'r') as f:
        for line in f:
            if '=' in line and not line.strip().startswith('#'):
                key, value = line.strip().split('=', 1)
                os.environ[key] = value
    print("✅ Environment variables loaded from .env")
except Exception as e:
    print(f"❌ Error loading .env: {e}")
    sys.exit(1)

# Verify critical environment variables
required_vars = ['SUPABASE_URL', 'SUPABASE_SERVICE_KEY', 'SUPABASE_ANON_KEY']
for var in required_vars:
    if not os.getenv(var):
        print(f"❌ Missing required environment variable: {var}")
        sys.exit(1)

print("✅ All required environment variables present")

# Now start the application
try:
    import uvicorn
    print("🚀 Starting Sidekick Forge application with corrected client service...")
    uvicorn.run("app.main:app", host="0.0.0.0", port=8001, reload=False, log_level="info")
except Exception as e:
    print(f"❌ Error starting application: {e}")
    sys.exit(1)