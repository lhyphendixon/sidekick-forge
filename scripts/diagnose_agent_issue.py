#!/usr/bin/env python3
"""
Diagnose why agent isn't responding
"""
import subprocess
import time

print("🔍 Diagnosing agent issue...")

# 1. Check container status
print("\n1. Checking container status:")
result = subprocess.run(["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}", "--filter", "name=agent"], 
                       capture_output=True, text=True)
print(result.stdout)

# 2. Get last 30 lines of logs
print("\n2. Recent agent logs:")
result = subprocess.run(["docker", "logs", "sidekick-forge_agent-worker_1", "--tail", "30"], 
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
print(result.stdout)

# 3. Check for specific events
print("\n3. Checking for key events in logs:")
events_to_check = ["User said:", "VAD updated:", "Agent responded:", "ERROR", "Exception", "User started speaking"]
for event in events_to_check:
    count = result.stdout.count(event)
    print(f"  - '{event}': {count} occurrences")

# 4. Check agent worker processes
print("\n4. Checking agent processes:")
ps_result = subprocess.run(["docker", "exec", "sidekick-forge_agent-worker_1", "ps", "aux"], 
                          capture_output=True, text=True)
python_processes = [line for line in ps_result.stdout.split('\n') if 'python' in line]
print(f"  Found {len(python_processes)} python processes")
for proc in python_processes[:3]:  # Show first 3
    print(f"  - {proc[:80]}...")