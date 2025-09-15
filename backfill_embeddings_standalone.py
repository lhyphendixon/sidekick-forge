#!/usr/bin/env python3
"""
Standalone backfill embeddings script for KCG's documents using SiliconFlow API
"""
import math
import time
import json
import httpx
from supabase import create_client

# KCG Client Configuration
SUPABASE_URL = "https://qbeftummyzfiyihfsyup.supabase.co"
SUPABASE_SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFiZWZ0dW1teXpmaXlpaGZzeXVwIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1NDc5NDQzNCwiZXhwIjoyMDcwMzcwNDM0fQ.9amCZRJHA2qHdwJ9cfrIH5dCq5sYf2WM6Pm9hUHwvbQ"
SILICONFLOW_API_KEY = "sk-ovnjdayihxmskrqmdgkbuzyfadczdvwixbirttbfklizpwkf"

# Embedding Configuration
EMBED_MODEL = "Qwen/Qwen3-Embedding-4B"  # 1024 dimensions
BATCH_SIZE = 5  # Small batch to avoid rate limits
TARGET_TABLE = "documents"  # or "document_chunks"
TEXT_COL = "content"
ID_COL = "id"

# SiliconFlow API setup
EMBED_URL = "https://api.siliconflow.com/v1/embeddings"
HEADERS = {"Authorization": f"Bearer {SILICONFLOW_API_KEY}", "Content-Type": "application/json"}

def embed(texts):
    """Generate embeddings using SiliconFlow API"""
    print(f"    Calling SiliconFlow API for {len(texts)} texts...")
    payload = {"model": EMBED_MODEL, "input": texts}
    r = httpx.post(EMBED_URL, headers=HEADERS, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    vecs = [item["embedding"] for item in data.get("data", [])]
    if not vecs:
        raise RuntimeError(f"No embeddings returned: {json.dumps(data)[:300]}...")
    if any(len(v) != 1024 for v in vecs):
        raise RuntimeError(f"Unexpected embedding dimension (expected 1024): {[len(v) for v in vecs]}")
    print(f"    Received {len(vecs)} embeddings of dimension 1024")
    return vecs

def main():
    print("=== KCG Embeddings Backfill Script ===\n")
    
    # Connect to client's Supabase
    print(f"Connecting to Supabase...")
    client_db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    print(f"Connected to {SUPABASE_URL}\n")
    
    # Get documents without embeddings
    print(f"Fetching {TARGET_TABLE}...")
    docs_result = client_db.table(TARGET_TABLE).select('id, title, content').execute()
    
    # Filter documents that need embeddings
    docs_to_process = []
    for doc in docs_result.data:
        if doc.get('content'):  # Only process if content exists
            docs_to_process.append(doc)
    
    total = len(docs_to_process)
    print(f"Found {total} documents with content\n")
    
    if total == 0:
        print("No documents to process")
        return
    
    # Process in batches
    pages = math.ceil(total / BATCH_SIZE)
    successful = 0
    failed = 0
    
    for page in range(pages):
        start_idx = page * BATCH_SIZE
        end_idx = min(start_idx + BATCH_SIZE, total)
        batch = docs_to_process[start_idx:end_idx]
        
        print(f"Processing batch {page+1}/{pages} (documents {start_idx+1}-{end_idx}):")
        
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
            vecs = embed(texts)
            
            # Update each document with its embedding
            for doc, vec in zip(batch, vecs):
                doc_id = doc['id']
                title = (doc.get('title', 'Unknown'))[:40]
                
                try:
                    # Update the document with the embedding vector
                    update_result = client_db.table(TARGET_TABLE).update({
                        'embedding': vec  # Store as array
                    }).eq('id', doc_id).execute()
                    
                    if update_result.data:
                        print(f"  ✓ {title}...")
                        successful += 1
                    else:
                        print(f"  ✗ Failed: {title}...")
                        failed += 1
                except Exception as e:
                    print(f"  ✗ Error updating {title}: {str(e)[:100]}")
                    failed += 1
            
        except Exception as e:
            print(f"  ✗ Batch error: {e}")
            failed += len(batch)
            continue
        
        # Rate limiting
        if page < pages - 1:
            print(f"  Waiting 2 seconds before next batch...")
            time.sleep(2)
        print()
    
    print("=== Summary ===")
    print(f"Successfully updated: {successful} documents")
    print(f"Failed: {failed} documents")
    
    # Verify embeddings
    print("\n=== Verification ===")
    docs_with_embeddings = client_db.table(TARGET_TABLE).select('id, embedding').execute()
    
    count_with_embeddings = 0
    embedding_dims = set()
    
    for doc in docs_with_embeddings.data:
        emb = doc.get('embedding')
        if emb and isinstance(emb, list) and len(emb) > 0:
            count_with_embeddings += 1
            embedding_dims.add(len(emb))
    
    print(f"Documents with embeddings: {count_with_embeddings}/{len(docs_with_embeddings.data)}")
    print(f"Embedding dimensions: {embedding_dims}")
    
    # Test the match_documents RPC
    print("\n=== Testing RAG Search ===")
    try:
        # Generate a test embedding
        test_text = "health and wellness"
        print(f"Generating test embedding for: '{test_text}'")
        test_vec = embed([test_text])[0]
        
        # Test search without agent filter first
        search_result = client_db.rpc("match_documents", {
            "p_query_embedding": test_vec,
            "p_agent_slug": "",  # Empty to test without filter
            "p_match_threshold": 0.0,
            "p_match_count": 3
        }).execute()
        
        if search_result.data:
            print(f"✓ RAG search works! Found {len(search_result.data)} results without agent filter")
            for i, doc in enumerate(search_result.data[:3], 1):
                print(f"  {i}. {doc.get('title', 'Unknown')[:50]}... (similarity: {doc.get('similarity', 0):.3f})")
        else:
            print("✗ No results from RAG search (even without agent filter)")
        
        # Test with 'able' agent filter
        print("\nTesting with 'able' agent filter:")
        search_with_agent = client_db.rpc("match_documents", {
            "p_query_embedding": test_vec,
            "p_agent_slug": "able",
            "p_match_threshold": 0.0,
            "p_match_count": 3
        }).execute()
        
        if search_with_agent.data:
            print(f"✓ Found {len(search_with_agent.data)} results for 'able' agent")
        else:
            print("✗ No results for 'able' agent (permissions may need updating)")
            
    except Exception as e:
        print(f"Error testing RAG search: {e}")
    
    print("\n=== Backfill Complete ===")

if __name__ == "__main__":
    main()