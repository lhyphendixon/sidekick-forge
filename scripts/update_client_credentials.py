#!/usr/bin/env python3
"""
Update client credentials in Redis
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis
import json

# Connect to Redis
redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)

# Get the current client data
cache_key = "client:autonomite"
client_json = redis_client.get(cache_key)

if client_json:
    client_data = json.loads(client_json)
    print("Current service_role_key (last 20 chars):", client_data['settings']['supabase']['service_role_key'][-20:])
    
    # Update with the correct service_role_key
    client_data['settings']['supabase']['service_role_key'] = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY"
    
    # Also update the anon key to match
    client_data['settings']['supabase']['anon_key'] = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzU3ODQ1NzMsImV4cCI6MjA1MTM2MDU3M30.SmqTIWrScKQWkJ2_PICWVJYpRSKfvqkRcjMMt0ApH1U"
    
    # Save back to Redis
    redis_client.setex(cache_key, 86400, json.dumps(client_data))
    print("Updated service_role_key!")
    
    # Clear any agent caches to force re-sync
    for key in redis_client.scan_iter("agent:autonomite:*"):
        redis_client.delete(key)
    print("Cleared agent caches")
    
    # Clear agents list cache
    redis_client.delete("agents:client:autonomite")
    print("Cleared agents list cache")
else:
    print("Client not found in Redis")