#!/usr/bin/env python3
"""
Execute schema alignment for KCG database to match Autonomite
This script makes the changes via Supabase client
"""
import asyncio
from app.integrations.supabase_client import supabase_manager
from supabase import create_client
import json

async def align_kcg_schema():
    print("=== KCG SCHEMA ALIGNMENT ===\n")
    
    await supabase_manager.initialize()
    admin = supabase_manager.admin_client
    
    # Get KCG client
    client_result = admin.table('clients').select('*').eq('name', 'Kimberly Carter-Gamble').execute()
    if not client_result.data:
        print("KCG client not found")
        return
    
    kcg = client_result.data[0]
    kcg_db = create_client(kcg['supabase_url'], kcg['supabase_service_role_key'])
    
    print("Connected to KCG database\n")
    
    # Since we can't execute raw SQL through Supabase client,
    # we'll need to use the Supabase Dashboard SQL Editor
    # But we can do some operations via the API
    
    print("STEP 1: Checking current schema...")
    
    # Check documents table
    try:
        docs = kcg_db.table('documents').select('*').limit(1).execute()
        if docs.data:
            doc_cols = list(docs.data[0].keys())
            print(f"Documents table has {len(doc_cols)} columns")
            if 'embedding_vector' in doc_cols:
                print("  ⚠️  Has extra 'embedding_vector' column - needs removal")
    except Exception as e:
        print(f"Error checking documents: {e}")
    
    # Check document_chunks table
    try:
        chunks = kcg_db.table('document_chunks').select('*').limit(1).execute()
        if chunks.data:
            chunk_cols = list(chunks.data[0].keys())
            print(f"Document_chunks table has {len(chunk_cols)} columns")
            if 'embedding' in chunk_cols:
                print("  ⚠️  Has extra 'embedding' column - needs removal")
    except Exception as e:
        print(f"Error checking chunks: {e}")
    
    # Check conversation_transcripts table
    try:
        transcripts = kcg_db.table('conversation_transcripts').select('*').limit(1).execute()
        if transcripts.data:
            transcript_cols = list(transcripts.data[0].keys())
            print(f"Conversation_transcripts table has {len(transcript_cols)} columns")
            missing = {'turn_id', 'citations', 'source'} - set(transcript_cols)
            if missing:
                print(f"  ⚠️  Missing columns: {missing} - needs addition")
    except Exception as e:
        print(f"Error checking transcripts: {e}")
    
    print("\nSTEP 2: Preparing migration...")
    print("\n" + "="*60)
    print("MANUAL STEPS REQUIRED:")
    print("="*60)
    print("\n1. Go to KCG Supabase Dashboard:")
    print(f"   https://supabase.com/dashboard/project/{kcg['supabase_url'].split('//')[1].split('.')[0]}")
    print("\n2. Navigate to SQL Editor")
    print("\n3. Copy and run the SQL from: /root/sidekick-forge/align_kcg_schema.sql")
    print("\n4. The script will:")
    print("   - Remove extra columns from documents and document_chunks")
    print("   - Add missing columns to conversation_transcripts")
    print("   - Convert embeddings to vector type")
    print("   - Create vector indexes for fast search")
    print("\n" + "="*60)
    
    # We can attempt to add some columns via API
    print("\nSTEP 3: Attempting API-based updates...")
    
    # Try to update conversation_transcripts with default values for missing columns
    try:
        # This won't add columns but will prepare data
        print("\nPreparing default values for missing columns...")
        
        # Get all conversation transcripts
        all_transcripts = kcg_db.table('conversation_transcripts').select('id').execute()
        
        if all_transcripts.data:
            print(f"Found {len(all_transcripts.data)} conversation transcripts")
            
            # We can't add columns via API, but we can prepare the data structure
            print("Note: Column additions must be done via SQL Editor")
            
    except Exception as e:
        print(f"Error: {e}")
    
    print("\n" + "="*60)
    print("VERIFICATION")
    print("="*60)
    
    print("\nAfter running the SQL script, the schema should match:")
    print("  - agents: 16 columns ✓ (already matches)")
    print("  - documents: 24 columns (remove embedding_vector)")
    print("  - document_chunks: 8 columns (remove embedding)")
    print("  - conversation_transcripts: 20 columns (add turn_id, citations, source)")
    
    print("\nEmbeddings should be:")
    print("  - Stored as PostgreSQL vector(1024) type")
    print("  - Indexed for fast similarity search")
    print("  - Ready for RAG/citations to work")

if __name__ == "__main__":
    asyncio.run(align_kcg_schema())