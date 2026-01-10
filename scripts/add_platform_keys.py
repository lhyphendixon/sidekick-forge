#!/usr/bin/env python3
"""
Add platform API keys for Adventurer tier.

Usage:
    python scripts/add_platform_keys.py

This script reads API keys from environment variables and inserts them
into the platform_api_keys table. It's idempotent (uses upsert).

Required environment variables:
    - CEREBRAS_API_KEY: Cerebras API key for LLM
    - CARTESIA_API_KEY: Cartesia API key for TTS/STT
    - SILICONFLOW_API_KEY: SiliconFlow API key for embeddings/reranking
    - SUPABASE_URL: Platform Supabase URL
    - SUPABASE_SERVICE_ROLE_KEY: Platform Supabase service role key
"""
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    from supabase import create_client

    # Get Supabase credentials
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

    if not supabase_url or not supabase_key:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)

    client = create_client(supabase_url, supabase_key)

    # Platform API keys to add
    keys_to_add = [
        {
            "key_name": "cerebras_api_key",
            "provider": "cerebras",
            "description": "Cerebras API key for LLM (GLM 4.6)",
            "env_var": "CEREBRAS_API_KEY",
        },
        {
            "key_name": "cartesia_api_key",
            "provider": "cartesia",
            "description": "Cartesia API key for TTS and STT",
            "env_var": "CARTESIA_API_KEY",
        },
        {
            "key_name": "siliconflow_api_key",
            "provider": "siliconflow",
            "description": "SiliconFlow API key for embeddings (Qwen3) and reranking",
            "env_var": "SILICONFLOW_API_KEY",
        },
        {
            "key_name": "deepgram_api_key",
            "provider": "deepgram",
            "description": "Deepgram API key for fallback STT",
            "env_var": "DEEPGRAM_API_KEY",
        },
    ]

    print("Adding platform API keys for Adventurer tier...")
    added = 0
    skipped = 0

    for key_info in keys_to_add:
        key_value = os.getenv(key_info["env_var"])

        if not key_value:
            print(f"  SKIP: {key_info['key_name']} - {key_info['env_var']} not set")
            skipped += 1
            continue

        try:
            result = client.table('platform_api_keys').upsert({
                "key_name": key_info["key_name"],
                "key_value": key_value,
                "provider": key_info["provider"],
                "description": key_info["description"],
                "is_active": True,
            }, on_conflict="key_name").execute()

            if result.data:
                print(f"  OK: {key_info['key_name']} ({key_info['provider']})")
                added += 1
            else:
                print(f"  WARN: {key_info['key_name']} - no data returned")
        except Exception as e:
            print(f"  ERROR: {key_info['key_name']} - {e}")

    print(f"\nDone! Added/updated: {added}, Skipped (missing env var): {skipped}")


if __name__ == "__main__":
    main()
