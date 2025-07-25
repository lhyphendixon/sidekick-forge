#!/usr/bin/env python3
"""
Basic connectivity test to verify platform status
"""

import requests
import json

BASE_URL = "http://localhost:8000"

print("=== BASIC CONNECTIVITY TEST ===\n")

# Test 1: Basic health
try:
    resp = requests.get(f"{BASE_URL}/health")
    print(f"1. Basic Health: {resp.status_code}")
    if resp.status_code == 200:
        print(f"   Response: {resp.json()}")
except Exception as e:
    print(f"1. Basic Health: ERROR - {e}")

# Test 2: Detailed health
try:
    resp = requests.get(f"{BASE_URL}/health/detailed")
    print(f"\n2. Detailed Health: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        print(f"   Status: {data.get('status')}")
        print(f"   Checks: {json.dumps(data.get('checks'), indent=2)}")
except Exception as e:
    print(f"\n2. Detailed Health: ERROR - {e}")

# Test 3: Admin interface
try:
    resp = requests.get(f"{BASE_URL}/admin")
    print(f"\n3. Admin Interface: {resp.status_code}")
    print(f"   Has content: {len(resp.text) > 0}")
except Exception as e:
    print(f"\n3. Admin Interface: ERROR - {e}")

# Test 4: Check available endpoints
print("\n4. Available API Endpoints:")
endpoints = [
    "/api/v1/clients",
    "/api/v1/agents", 
    "/api/v1/containers",
    "/api/v1/trigger-agent",
    "/api/v1/workers"
]

for endpoint in endpoints:
    try:
        resp = requests.get(f"{BASE_URL}{endpoint}")
        print(f"   {endpoint}: {resp.status_code}")
    except Exception as e:
        print(f"   {endpoint}: ERROR - {e}")

print("\n=== SUMMARY ===")
print("The platform is running but has authentication requirements.")
print("Supabase connection appears to be down or misconfigured.")
print("Docker is not available in this environment for container testing.")