#!/usr/bin/env python3
"""
Reindex Autonomite documents with Qwen/Qwen3-Embedding-4B model.
Fast parallel processing version.
"""

import os
import asyncio
import httpx
from supabase import create_client


def require_env(name: str) -> str:
    """Fetch a required environment variable or exit explicitly."""
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


# Configuration
PLATFORM_URL = require_env('PLATFORM_SUPABASE_URL')
PLATFORM_KEY = require_env('PLATFORM_SUPABASE_SERVICE_ROLE_KEY')
AUTONOMITE_CLIENT_ID = require_env('AUTONOMITE_CLIENT_ID')
SILICONFLOW_URL = require_env('SILICONFLOW_URL')
SILICONFLOW_API_KEY = require_env('SILICONFLOW_API_KEY')
EMBEDDING_MODEL = 'Qwen/Qwen3-Embedding-4B'
BATCH_SIZE = 50  # Smaller batches to avoid timeouts
CONCURRENT_REQUESTS = 10  # Parallel API calls
EMBEDDING_DIMENSION = 1024
START_OFFSET = 25200  # Resume from where we left off


async def generate_embedding(text: str, client: httpx.AsyncClient, semaphore: asyncio.Semaphore) -> list:
    """Generate embedding using SiliconFlow API with rate limiting."""
    if not text or not text.strip():
        return None

    text = text[:30000] if len(text) > 30000 else text

    async with semaphore:
        try:
            response = await client.post(
                SILICONFLOW_URL,
                json={
                    "model": EMBEDDING_MODEL,
                    "input": text,
                    "encoding_format": "float",
                    "dimensions": EMBEDDING_DIMENSION
                },
                headers={
                    "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
                    "Content-Type": "application/json"
                },
                timeout=60.0
            )

            if response.status_code == 200:
                data = response.json()
                return data.get("data", [{}])[0].get("embedding", [])
            elif response.status_code == 429:
                # Rate limited, wait and retry
                await asyncio.sleep(2)
                return await generate_embedding(text, client, semaphore)
            else:
                return None
        except Exception as e:
            print(f"  Error: {e}")
            return None


async def process_chunk(chunk: dict, autonomite, http_client: httpx.AsyncClient, semaphore: asyncio.Semaphore) -> bool:
    """Process a single chunk."""
    chunk_id = chunk['id']
    content = chunk.get('content', '')

    if not content or not content.strip():
        return True  # Skip empty

    embedding = await generate_embedding(content, http_client, semaphore)

    if embedding:
        try:
            autonomite.table('document_chunks').update({
                'embeddings': embedding
            }).eq('id', chunk_id).execute()
            return True
        except Exception as e:
            print(f"  DB error: {e}")
            return False
    return False


async def reindex_chunks():
    """Main reindexing function with parallel processing."""
    print(f"Starting parallel reindex with {EMBEDDING_MODEL}...")
    print(f"Concurrent requests: {CONCURRENT_REQUESTS}")

    platform = create_client(PLATFORM_URL, PLATFORM_KEY)

    clients = platform.table('clients').select(
        'supabase_url, supabase_service_role_key'
    ).eq('id', AUTONOMITE_CLIENT_ID).execute()

    if not clients.data:
        print("ERROR: Autonomite client not found!")
        return

    c = clients.data[0]
    autonomite = create_client(c['supabase_url'], c['supabase_service_role_key'])

    print(f"Using API key: {SILICONFLOW_API_KEY[:20]}...")

    total_result = autonomite.table('document_chunks').select('id', count='exact').execute()
    total_chunks = total_result.count
    print(f"Total chunks: {total_chunks}")

    processed = START_OFFSET  # Already processed
    errors = 0
    offset = START_OFFSET

    semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)

    async with httpx.AsyncClient() as http_client:
        while offset < total_chunks:
            try:
                batch = autonomite.table('document_chunks').select(
                    'id, content'
                ).order('id').range(offset, offset + BATCH_SIZE - 1).execute()
            except Exception as e:
                print(f"  Query error: {e}, retrying...")
                await asyncio.sleep(5)
                continue

            if not batch.data:
                break

            batch_num = offset // BATCH_SIZE + 1
            print(f"\nBatch {batch_num}: {offset + 1} - {offset + len(batch.data)} of {total_chunks}")

            # Process batch in parallel
            tasks = [
                process_chunk(chunk, autonomite, http_client, semaphore)
                for chunk in batch.data
            ]
            results = await asyncio.gather(*tasks)

            batch_success = sum(1 for r in results if r)
            batch_errors = len(results) - batch_success
            processed += batch_success
            errors += batch_errors

            print(f"  Processed: {processed} | Errors: {errors} | Progress: {processed * 100 // total_chunks}%")

            offset += BATCH_SIZE

    print(f"\n=== Reindexing Complete ===")
    print(f"Successfully processed: {processed}")
    print(f"Errors: {errors}")


if __name__ == "__main__":
    asyncio.run(reindex_chunks())
