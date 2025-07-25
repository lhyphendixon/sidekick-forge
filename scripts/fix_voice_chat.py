#!/usr/bin/env python3
"""
Voice Chat Fix Script
Comprehensive fix for voice chat issues
"""
import subprocess
import time
import json
import requests
import sys


def log(message, level="INFO"):
    """Simple logging function"""
    print(f"[{level}] {message}")


def run_command(cmd, check=True):
    """Run shell command and return output"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        log(f"Command failed: {cmd}", "ERROR")
        log(f"Error: {e.stderr}", "ERROR")
        if check:
            raise
        return None


def test_api_connection():
    """Test if API is accessible"""
    try:
        response = requests.get("http://localhost:8000/health")
        return response.status_code == 200
    except:
        return False


def restart_agent_container(client_id, agent_slug):
    """Restart the agent container to apply fixes"""
    container_name = f"agent_{client_id.replace('-', '')}_{agent_slug.replace('-', '_')}"
    log(f"Restarting container: {container_name}")
    
    # Stop the container
    run_command(f"docker stop {container_name}", check=False)
    time.sleep(2)
    
    # Remove the container
    run_command(f"docker rm {container_name}", check=False)
    
    log("Container stopped and removed. It will be recreated on next trigger.")
    return True


def test_voice_trigger(client_id, agent_slug):
    """Test triggering voice agent"""
    log(f"Testing voice trigger for {agent_slug}")
    
    url = "http://localhost:8000/api/v1/trigger-agent"
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": f"test-api-key-{client_id}"
    }
    
    data = {
        "agent_slug": agent_slug,
        "mode": "voice",
        "room_name": f"test_fix_{int(time.time())}",
        "user_id": "test_user",
        "client_id": client_id
    }
    
    try:
        response = requests.post(url, json=data, headers=headers)
        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                log("✅ Voice trigger successful!")
                log(f"Room: {result['data']['room_name']}")
                log(f"Token provided: {'user_token' in result['data'].get('livekit_config', {})}")
                return True
            else:
                log(f"❌ Trigger failed: {result.get('message')}", "ERROR")
        else:
            log(f"❌ API error: {response.status_code} - {response.text}", "ERROR")
    except Exception as e:
        log(f"❌ Request failed: {str(e)}", "ERROR")
    
    return False


def check_container_health(client_id, agent_slug):
    """Check if agent container is healthy"""
    container_name = f"agent_{client_id.replace('-', '')}_{agent_slug.replace('-', '_')}"
    
    # Check if container exists
    exists = run_command(f"docker ps -a --filter name={container_name} --format '{{{{.Names}}}}'", check=False)
    if not exists:
        log(f"Container {container_name} does not exist", "WARNING")
        return False
    
    # Check if running
    status = run_command(f"docker ps --filter name={container_name} --format '{{{{.Status}}}}'", check=False)
    if not status:
        log(f"Container {container_name} is not running", "WARNING")
        return False
    
    log(f"Container {container_name} status: {status}")
    
    # Check recent logs for errors
    logs = run_command(f"docker logs {container_name} --tail 20 2>&1", check=False)
    if logs:
        error_count = logs.lower().count("error")
        if error_count > 0:
            log(f"Found {error_count} errors in container logs", "WARNING")
    
    return True


def main():
    """Main fix routine"""
    log("=== Voice Chat Fix Script ===")
    
    # Default values
    client_id = "df91fd06-816f-4273-a903-5a4861277040"
    agent_slug = "clarence-coherence"
    
    # Allow override from command line
    if len(sys.argv) > 1:
        client_id = sys.argv[1]
    if len(sys.argv) > 2:
        agent_slug = sys.argv[2]
    
    log(f"Client ID: {client_id}")
    log(f"Agent Slug: {agent_slug}")
    
    # Step 1: Check API connection
    log("\n1. Checking API connection...")
    if not test_api_connection():
        log("❌ API is not accessible. Please ensure FastAPI is running.", "ERROR")
        return 1
    log("✅ API is accessible")
    
    # Step 2: Check container health
    log("\n2. Checking container health...")
    container_healthy = check_container_health(client_id, agent_slug)
    
    # Step 3: Restart container if needed
    if not container_healthy:
        log("\n3. Container needs restart...")
        restart_agent_container(client_id, agent_slug)
        time.sleep(3)
    
    # Step 4: Test voice trigger
    log("\n4. Testing voice trigger...")
    if test_voice_trigger(client_id, agent_slug):
        log("\n✅ Voice chat should now be working!")
        log("\nRecommendations:")
        log("1. Try the voice preview again in the admin dashboard")
        log("2. Make sure your microphone permissions are granted")
        log("3. Check the browser console for any client-side errors")
        log("4. If still not working, check container logs with:")
        log(f"   docker logs -f agent_{client_id.replace('-', '')}_{agent_slug.replace('-', '_')}")
    else:
        log("\n❌ Voice trigger test failed", "ERROR")
        log("\nTroubleshooting steps:")
        log("1. Check if the agent exists for this client")
        log("2. Verify LiveKit credentials are configured")
        log("3. Check container logs for specific errors")
        log("4. Run the diagnostic endpoint when available")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())