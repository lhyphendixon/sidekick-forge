#!/usr/bin/env python3
"""
Fix double agent response by ensuring only one container per room
"""
import docker
import json
import sys

def fix_double_responses():
    print("🔧 Fixing double agent responses...")
    
    client = docker.from_env()
    containers = client.containers.list(filters={"name": "agent_"})
    
    # Group containers by room
    room_containers = {}
    for container in containers:
        try:
            # Get environment variables
            env_vars = container.attrs['Config']['Env']
            room_name = None
            
            for env in env_vars:
                if env.startswith('ROOM_NAME='):
                    room_name = env.split('=', 1)[1]
                    break
            
            if room_name:
                if room_name not in room_containers:
                    room_containers[room_name] = []
                room_containers[room_name].append(container)
        except Exception as e:
            print(f"Error processing container {container.name}: {e}")
    
    # Check for duplicates
    print(f"\n📊 Found {len(room_containers)} unique rooms")
    
    duplicates_fixed = 0
    for room_name, containers in room_containers.items():
        if len(containers) > 1:
            print(f"\n⚠️ Room '{room_name}' has {len(containers)} containers:")
            # Keep the newest container (last in list)
            containers.sort(key=lambda c: c.attrs['Created'])
            
            for i, container in enumerate(containers[:-1]):
                print(f"   🗑️ Stopping duplicate container: {container.name}")
                try:
                    container.stop(timeout=5)
                    container.remove()
                    duplicates_fixed += 1
                except Exception as e:
                    print(f"   ❌ Error stopping {container.name}: {e}")
            
            print(f"   ✅ Keeping container: {containers[-1].name}")
    
    if duplicates_fixed > 0:
        print(f"\n✅ Fixed {duplicates_fixed} duplicate containers")
    else:
        print("\n✅ No duplicate containers found")
    
    # Show final state
    print("\n📋 Final container state:")
    remaining = client.containers.list(filters={"name": "agent_"})
    for container in remaining:
        env_vars = container.attrs['Config']['Env']
        room_name = "unknown"
        for env in env_vars:
            if env.startswith('ROOM_NAME='):
                room_name = env.split('=', 1)[1]
                break
        print(f"   - {container.name} -> Room: {room_name}")

if __name__ == "__main__":
    fix_double_responses()