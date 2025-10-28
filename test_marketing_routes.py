#!/usr/bin/env python3
"""
Quick diagnostic to test if marketing routes are properly configured
"""
import sys
import os

# Add the app directory to the path
sys.path.insert(0, os.path.dirname(__file__))

try:
    print("Testing marketing routes configuration...\n")
    
    # Test 1: Import routes
    print("1. Testing import...")
    from app.marketing.routes import router as marketing_router
    print("   ✅ Marketing routes imported successfully")
    
    # Test 2: Check route paths
    print("\n2. Checking registered routes...")
    routes = [(r.path, r.methods) for r in marketing_router.routes if hasattr(r, 'path')]
    for path, methods in routes:
        print(f"   ✅ {path} - {methods}")
    
    # Test 3: Import main app
    print("\n3. Testing main app import...")
    from app.main import app
    print("   ✅ Main app imported successfully")
    
    # Test 4: Check if marketing routes are in app
    print("\n4. Checking if marketing routes are registered in main app...")
    app_routes = [(r.path, getattr(r, 'methods', None)) for r in app.routes if hasattr(r, 'path')]
    marketing_paths = ['/', '/pricing', '/features', '/about', '/contact', '/signup']
    
    found_routes = []
    for path in marketing_paths:
        matching = [r for r in app_routes if r[0] == path]
        if matching:
            found_routes.append(path)
            print(f"   ✅ {path} is registered")
        else:
            print(f"   ❌ {path} NOT found in app routes")
    
    if len(found_routes) == len(marketing_paths):
        print("\n✅ SUCCESS: All marketing routes are properly registered!")
    else:
        print(f"\n⚠️  WARNING: Only {len(found_routes)}/{len(marketing_paths)} routes found")
        print("   This might indicate the routes weren't included in main.py")
    
    # Test 5: Check for conflicts
    print("\n5. Checking for route conflicts...")
    root_routes = [r for r in app_routes if r[0] == '/']
    if len(root_routes) > 1:
        print(f"   ⚠️  WARNING: Multiple routes found for '/' - last one wins!")
        for r in root_routes:
            print(f"      - {r}")
    else:
        print("   ✅ No conflicts found")
        
except Exception as e:
    print(f"\n❌ ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

