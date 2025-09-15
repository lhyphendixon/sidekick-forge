#!/usr/bin/env python3
"""
Backfill embeddings for KCG's documents using SiliconFlow API and Supabase client
"""
import asyncio
import math
import time
import json
import httpx
from supabase import create_client
from app.integrations.supabase_client import supabase_manager

# Configuration
SILICONFLOW_API_KEY = "sk-ovnjdayihxmskrqmdgkbuzyfadczdvwixbirttbfklizpwkf"
EMBED_MODEL = "Qwen/Qwen3-Embedding-4B"  # configured to 1024 dims
BATCH_SIZE = 10  # Start small to avoid rate limits
TARGET_TABLE = "documents"  # Start with documents table
TEXT_COL = "content"
ID_COL = "id"

# SiliconFlow API setup
EMBED_URL = "https://api.siliconflow.com/v1/embeddings"
HEADERS = {"Authorization": f"Bearer {SILICONFLOW_API_KEY}", "Content-Type": "application/json"}

def embed(texts):
    """Generate embeddings using SiliconFlow API"""
    payload = {"model": EMBED_MODEL, "input": texts}
    r = httpx.post(EMBED_URL, headers=HEADERS, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    vecs = [item["embedding"] for item in data.get("data", [])]
    if not vecs or any(len(v) != 1024 for v in vecs):
        raise RuntimeError(f"Unexpected embedding result or dimension: {json.dumps(data)[:300]}...")
    return vecs

async def main():
    # Initialize Supabase connection
    await supabase_manager.initialize()
    admin = supabase_manager.admin_client
    
    # Get client's Supabase credentials
    client_result = admin.table('clients').select('*').eq('id', '72aefd69-c233-42c4-9e5e-c36891c26543').execute()
    if not client_result.data:
        print("Client not found")
        return
        
    customer = client_result.data[0]
    client_db = create_client(customer['supabase_url'], customer['supabase_service_role_key'])
    
    # Get documents without embeddings
    print(f"Fetching {TARGET_TABLE} without embeddings...")
    docs_result = client_db.table(TARGET_TABLE).select('id, title, content').execute()
    
    # Filter documents that need embeddings
    docs_to_process = []
    for doc in docs_result.data:
        if doc.get('content'):  # Only process if content exists
            docs_to_process.append(doc)
    
    total = len(docs_to_process)
    print(f"Found {total} documents to process")
    
    if total == 0:
        print("No documents to process")
        return
    
    # Process in batches
    pages = math.ceil(total / BATCH_SIZE)
    
    for page in range(pages):
        start_idx = page * BATCH_SIZE
        end_idx = min(start_idx + BATCH_SIZE, total)
        batch = docs_to_process[start_idx:end_idx]
        
        print(f"\nProcessing batch {page+1}/{pages} ({len(batch)} documents)")
        
        # Prepare texts for embedding
        texts = []
        for doc in batch:
            content = doc.get('content', '')
            # Truncate to reasonable length
            if len(content) > 8000:
                content = content[:8000]
            texts.append(content)
        
        try:
            # Generate embeddings
            print(f"  Generating embeddings...")
            vecs = embed(texts)
            
            # Update each document with its embedding
            for doc, vec in zip(batch, vecs):
                doc_id = doc['id']
                title = doc.get('title', 'Unknown')[:50]
                
                # Update the document with the embedding vector
                update_result = client_db.table(TARGET_TABLE).update({
                    'embedding': vec  # Store as array directly
                }).eq('id', doc_id).execute()
                
                if update_result.data:
                    print(f"  ✓ Updated: {title}...")
                else:
                    print(f"  ✗ Failed to update: {title}...")
            
            print(f"  Batch {page+1} complete")
            
        except Exception as e:
            print(f"  Error processing batch {page+1}: {e}")
            continue
        
        # Rate limiting
        if page < pages - 1:
            time.sleep(1)  # Pause between batches
    
    print("\n=== Verification ===")
    
    # Check how many documents now have embeddings
    docs_with_embeddings = client_db.table(TARGET_TABLE).select('id, embedding').execute()
    
    count_with_embeddings = 0
    embedding_dims = set()
    
    for doc in docs_with_embeddings.data:
        emb = doc.get('embedding')
        if emb and isinstance(emb, list) and len(emb) > 0:
            count_with_embeddings += 1
            embedding_dims.add(len(emb))
    
    print(f"Documents with embeddings: {count_with_embeddings}/{total}")
    print(f"Embedding dimensions found: {embedding_dims}")
    
    # Now process document_chunks if they exist
    print("\n=== Processing Document Chunks ===")
    try:
        chunks_result = client_db.table('document_chunks').select('id, content').limit(1).execute()
        if chunks_result.data:
            print("Document chunks table exists, processing chunks...")
            
            # Get all chunks
            all_chunks = client_db.table('document_chunks').select('id, content').execute()
            chunks_to_process = [c for c in all_chunks.data if c.get('content')]
            
            total_chunks = len(chunks_to_process)
            print(f"Found {total_chunks} chunks to process")
            
            chunk_pages = math.ceil(total_chunks / BATCH_SIZE)
            
            for page in range(chunk_pages):
                start_idx = page * BATCH_SIZE
                end_idx = min(start_idx + BATCH_SIZE, total_chunks)
                batch = chunks_to_process[start_idx:end_idx]
                
                print(f"Processing chunk batch {page+1}/{chunk_pages}")
                
                texts = [c.get('content', '')[:8000] for c in batch]
                
                try:
                    vecs = embed(texts)
                    
                    for chunk, vec in zip(batch, vecs):
                        client_db.table('document_chunks').update({
                            'embeddings': vec  # Note: chunks use 'embeddings' not 'embedding'
                        }).eq('id', chunk['id']).execute()
                    
                    print(f"  Chunk batch {page+1} complete")
                    
                except Exception as e:
                    print(f"  Error processing chunk batch: {e}")
                    continue
                
                if page < chunk_pages - 1:
                    time.sleep(1)
                    
    except Exception as e:
        print(f"Document chunks not available or error: {e}")
    
    print("\n=== Backfill Complete ===")

if __name__ == "__main__":
    asyncio.run(main())