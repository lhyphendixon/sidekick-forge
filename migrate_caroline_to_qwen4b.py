#!/usr/bin/env python3
"""
Migration script: Caroline Cory embeddings to Qwen3-Embedding-4B

This script regenerates all document chunk and conversation transcript embeddings
using the Qwen/Qwen3-Embedding-4B model via SiliconFlow.
"""

import os
import sys
import time
import json
import httpx
from typing import List, Optional

# Configuration
CLIENT_ID = "4abb05ac-08dc-4928-ae30-249e2e7d9cc1"
SILICONFLOW_API_KEY = "sk-ymdlyrjlwbzomkdlccgpvgpiabzokwtrfgwtovmvawvuksqq"
MODEL = "Qwen/Qwen3-Embedding-4B"
DIMENSION = 1024
BATCH_SIZE = 20  # Texts per API call
DELAY_BETWEEN_BATCHES = 1.0  # Seconds

# Load environment
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

def get_embedding(texts: List[str], http_client: httpx.Client) -> List[List[float]]:
    """Generate embeddings for a list of texts."""
    response = http_client.post(
        "https://api.siliconflow.com/v1/embeddings",
        headers={
            "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": MODEL,
            "input": texts,
            "encoding_format": "float",
            "dimensions": DIMENSION
        },
        timeout=60.0
    )

    if response.status_code != 200:
        raise Exception(f"API error {response.status_code}: {response.text}")

    data = response.json()
    return [item["embedding"] for item in data["data"]]


def migrate_document_chunks(client_sb, http_client: httpx.Client):
    """Regenerate embeddings for all document chunks."""
    print("\n" + "="*60)
    print("PHASE 1: Document Chunks")
    print("="*60)

    # Get total count
    total_result = client_sb.table('document_chunks').select('id', count='exact').execute()
    total = total_result.count
    print(f"Total document chunks: {total}")

    # Process in batches
    processed = 0
    failed = 0
    offset = 0

    while offset < total:
        # Fetch batch of chunks
        batch = client_sb.table('document_chunks').select(
            'id, content'
        ).order('id').range(offset, offset + BATCH_SIZE - 1).execute()

        if not batch.data:
            break

        # Prepare texts for embedding
        chunk_ids = []
        texts = []
        for chunk in batch.data:
            content = chunk.get('content', '')
            if content and len(content.strip()) > 10:
                chunk_ids.append(chunk['id'])
                # Truncate very long content
                texts.append(content[:8000])

        if texts:
            try:
                # Generate embeddings
                embeddings = get_embedding(texts, http_client)

                # Update each chunk
                for chunk_id, embedding in zip(chunk_ids, embeddings):
                    client_sb.table('document_chunks').update({
                        'embeddings': embedding
                    }).eq('id', chunk_id).execute()

                processed += len(texts)

            except Exception as e:
                print(f"  ❌ Batch error at offset {offset}: {e}")
                failed += len(texts)

        offset += BATCH_SIZE

        # Progress update
        progress = (offset / total) * 100
        print(f"  Progress: {min(offset, total)}/{total} ({progress:.1f}%) - Processed: {processed}, Failed: {failed}", end='\r')

        # Rate limiting
        time.sleep(DELAY_BETWEEN_BATCHES)

    print(f"\n✅ Document chunks complete: {processed} processed, {failed} failed")
    return processed, failed


def migrate_conversation_transcripts(client_sb, http_client: httpx.Client):
    """Regenerate embeddings for all conversation transcripts."""
    print("\n" + "="*60)
    print("PHASE 2: Conversation Transcripts")
    print("="*60)

    # Get total count
    total_result = client_sb.table('conversation_transcripts').select('id', count='exact').execute()
    total = total_result.count
    print(f"Total conversation transcripts: {total}")

    # Process in batches
    processed = 0
    failed = 0
    offset = 0

    while offset < total:
        # Fetch batch
        batch = client_sb.table('conversation_transcripts').select(
            'id, content'
        ).order('id').range(offset, offset + BATCH_SIZE - 1).execute()

        if not batch.data:
            break

        # Prepare texts
        transcript_ids = []
        texts = []
        for transcript in batch.data:
            content = transcript.get('content', '')
            if content and len(content.strip()) > 5:
                transcript_ids.append(transcript['id'])
                texts.append(content[:8000])

        if texts:
            try:
                embeddings = get_embedding(texts, http_client)

                for transcript_id, embedding in zip(transcript_ids, embeddings):
                    client_sb.table('conversation_transcripts').update({
                        'embeddings': embedding
                    }).eq('id', transcript_id).execute()

                processed += len(texts)

            except Exception as e:
                print(f"  ❌ Batch error at offset {offset}: {e}")
                failed += len(texts)

        offset += BATCH_SIZE
        progress = (offset / total) * 100
        print(f"  Progress: {min(offset, total)}/{total} ({progress:.1f}%) - Processed: {processed}, Failed: {failed}", end='\r')

        time.sleep(DELAY_BETWEEN_BATCHES)

    print(f"\n✅ Conversation transcripts complete: {processed} processed, {failed} failed")
    return processed, failed


def verify_embeddings(client_sb):
    """Verify embedding dimensions and counts."""
    print("\n" + "="*60)
    print("VERIFICATION")
    print("="*60)

    # Check document chunks
    chunks_with_emb = client_sb.table('document_chunks').select('id', count='exact').not_.is_('embeddings', 'null').execute()
    chunks_total = client_sb.table('document_chunks').select('id', count='exact').execute()
    print(f"Document chunks: {chunks_with_emb.count}/{chunks_total.count} have embeddings")

    # Check a sample dimension
    sample = client_sb.table('document_chunks').select('embeddings').not_.is_('embeddings', 'null').limit(1).execute()
    if sample.data and sample.data[0].get('embeddings'):
        dim = len(sample.data[0]['embeddings'])
        print(f"  Sample dimension: {dim} {'✅' if dim == DIMENSION else '❌'}")

    # Check conversation transcripts
    trans_with_emb = client_sb.table('conversation_transcripts').select('id', count='exact').not_.is_('embeddings', 'null').execute()
    trans_total = client_sb.table('conversation_transcripts').select('id', count='exact').execute()
    print(f"Conversation transcripts: {trans_with_emb.count}/{trans_total.count} have embeddings")


def main():
    print("="*60)
    print("Caroline Cory Migration to Qwen3-Embedding-4B")
    print("="*60)
    print(f"Model: {MODEL}")
    print(f"Dimension: {DIMENSION}")
    print(f"Batch size: {BATCH_SIZE}")

    # Connect to platform database
    platform_sb = create_client(
        os.environ.get('SUPABASE_URL'),
        os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
    )

    # Get client credentials
    result = platform_sb.table('clients').select(
        'supabase_url, supabase_service_role_key'
    ).eq('id', CLIENT_ID).single().execute()

    client_sb = create_client(
        result.data['supabase_url'],
        result.data['supabase_service_role_key']
    )

    # Create HTTP client for embedding API
    http_client = httpx.Client(timeout=60.0)

    try:
        # Migrate document chunks
        doc_processed, doc_failed = migrate_document_chunks(client_sb, http_client)

        # Migrate conversation transcripts
        trans_processed, trans_failed = migrate_conversation_transcripts(client_sb, http_client)

        # Verify
        verify_embeddings(client_sb)

        print("\n" + "="*60)
        print("MIGRATION COMPLETE")
        print("="*60)
        print(f"Document chunks: {doc_processed} processed, {doc_failed} failed")
        print(f"Transcripts: {trans_processed} processed, {trans_failed} failed")

    finally:
        http_client.close()


if __name__ == "__main__":
    main()
