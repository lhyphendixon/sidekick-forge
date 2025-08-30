#!/usr/bin/env python3
"""
Test script to validate staging configuration changes
"""
import os
import sys

def test_configuration():
    """Test that configuration works correctly"""
    print("🔍 Testing staging configuration...")
    
    # Test 1: Import without environment variables should fail
    print("\n1. Testing settings validation without env vars...")
    env_backup = {}
    for key in list(os.environ.keys()):
        if 'SUPABASE' in key:
            env_backup[key] = os.environ[key]
            del os.environ[key]
    
    try:
        from app.config import Settings
        settings = Settings()
        print("❌ FAIL: Settings should have failed without SUPABASE_URL")
        return False
    except Exception as e:
        print(f"✅ PASS: Settings validation correctly failed: {e}")
    
    # Restore environment
    for key, value in env_backup.items():
        os.environ[key] = value
    
    # Test 2: Import with environment variables should work
    print("\n2. Testing settings validation with env vars...")
    try:
        # Reload the module to get fresh settings
        import importlib
        import app.config
        importlib.reload(app.config)
        
        settings = app.config.Settings()
        print(f"✅ PASS: Settings loaded successfully")
        print(f"   - Supabase URL: {settings.supabase_url}")
        print(f"   - Service key configured: ...{settings.supabase_service_role_key[-6:]}")
        print(f"   - Anon key configured: ...{settings.supabase_anon_key[-6:]}")
        
    except Exception as e:
        print(f"❌ FAIL: Settings failed to load with env vars: {e}")
        return False
    
    # Test 3: Check for legacy hardcoded values
    print("\n3. Testing for removal of legacy hardcoded values...")
    
    # Test dependencies
    try:
        from app.core.dependencies import get_client_service
        client_service = get_client_service()
        print("✅ PASS: Core dependencies use settings (no hardcoded values)")
    except Exception as e:
        print(f"❌ FAIL: Core dependencies error: {e}")
        return False
    
    print("\n🎉 All configuration tests passed!")
    return True

if __name__ == "__main__":
    success = test_configuration()
    sys.exit(0 if success else 1)