#!/usr/bin/env python3
"""
Update LiveKit credentials in the system
This script ensures LiveKit credentials are properly configured
"""
import os
import sys
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def update_env_file(url, api_key, api_secret):
    """Update .env file with LiveKit credentials"""
    env_path = "/root/sidekick-forge/.env"
    
    # Read existing env
    lines = []
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            lines = f.readlines()
    
    # Update or add LiveKit credentials
    updated = False
    new_lines = []
    for line in lines:
        if line.startswith('LIVEKIT_URL='):
            new_lines.append(f'LIVEKIT_URL={url}\n')
            updated = True
        elif line.startswith('LIVEKIT_API_KEY='):
            new_lines.append(f'LIVEKIT_API_KEY={api_key}\n')
            updated = True
        elif line.startswith('LIVEKIT_API_SECRET='):
            new_lines.append(f'LIVEKIT_API_SECRET={api_secret}\n')
            updated = True
        else:
            new_lines.append(line)
    
    # Add if not found
    if not updated:
        new_lines.extend([
            f'\n# LiveKit Configuration\n',
            f'LIVEKIT_URL={url}\n',
            f'LIVEKIT_API_KEY={api_key}\n',
            f'LIVEKIT_API_SECRET={api_secret}\n'
        ])
    
    # Write back
    with open(env_path, 'w') as f:
        f.writelines(new_lines)
    
    logger.info(f"Updated {env_path}")

def validate_credentials(url, api_key, api_secret):
    """Validate LiveKit credentials"""
    try:
        from livekit import api
        token = api.AccessToken(api_key, api_secret)
        token.with_identity("validation-test")
        token.with_grants(api.VideoGrants(
            room_join=True,
            room="validation-test"
        ))
        jwt_token = token.to_jwt()
        logger.info("✅ Credentials validated successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Credential validation failed: {e}")
        return False

def main():
    if len(sys.argv) != 4:
        print("Usage: python update_livekit_credentials.py <url> <api_key> <api_secret>")
        print("Example: python update_livekit_credentials.py wss://example.livekit.cloud API123 SECRET456")
        sys.exit(1)
    
    url = sys.argv[1]
    api_key = sys.argv[2]
    api_secret = sys.argv[3]
    
    # Validate
    if not validate_credentials(url, api_key, api_secret):
        logger.error("Invalid credentials provided")
        sys.exit(1)
    
    # Update .env
    update_env_file(url, api_key, api_secret)
    
    # Update environment for current session
    os.environ["LIVEKIT_URL"] = url
    os.environ["LIVEKIT_API_KEY"] = api_key
    os.environ["LIVEKIT_API_SECRET"] = api_secret
    
    logger.info("✅ LiveKit credentials updated successfully")
    logger.info("⚠️  Please restart services to apply changes:")
    logger.info("   docker-compose restart")

if __name__ == "__main__":
    main()