#!/usr/bin/env python3
"""
Schema Migration Script for Mitra Politi Client Database
Ensures consistency with Autonomite Agent schema
"""

import os
import sys
import asyncio
from supabase import create_client, Client
import json

# Mitra Politi Database Configuration
MITRA_DB_URL = "https://uyswpsluhkebudoqdnhk.supabase.co"
MITRA_SERVICE_KEY = os.getenv("MITRA_SERVICE_KEY", "")  # Will need to be provided

# Reference Autonomite Database (for comparison)
AUTONOMITE_DB_URL = "https://yuowazxcxwhczywurmmw.supabase.co"
AUTONOMITE_SERVICE_KEY = os.getenv("AUTONOMITE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.tN4FaKbNTCPU7ooCh9kH-qZcxeHCDo46Y0LfOjzKO0o")

# Required tables and their essential columns
REQUIRED_SCHEMA = {
    "agents": [
        ("id", "uuid", "PRIMARY KEY"),
        ("name", "text", None),
        ("slug", "text", None),
        ("description", "text", None),
        ("system_prompt", "text", None),
        ("voice_settings", "text", None),
        ("ui_settings", "jsonb", None),
        ("enabled", "boolean", None),
        ("created_at", "timestamp with time zone", "DEFAULT NOW()"),
        ("updated_at", "timestamp with time zone", "DEFAULT NOW()"),
        ("n8n_text_webhook_url", "text", None),
        ("n8n_rag_webhook_url", "text", None),
        ("provider_config", "jsonb", None),
        ("livekit_enabled", "boolean", None),
        ("agent_image", "text", None)
    ],
    "conversations": [
        ("id", "uuid", "PRIMARY KEY"),
        ("user_id", "uuid", None),
        ("summary", "text", None),
        ("created_at", "timestamp with time zone", "DEFAULT NOW()"),
        ("updated_at", "timestamp with time zone", "DEFAULT NOW()"),
        ("conversation_title", "text", None),
        ("status", "text", None),
        ("metadata", "jsonb", None),
        ("channel", "text", None),
        ("agent_id", "text", None)
    ],
    "conversation_transcripts": [
        ("id", "uuid", "PRIMARY KEY"),
        ("conversation_id", "uuid", None),
        ("user_id", "uuid", None),
        ("session_id", "text", None),
        ("transcript", "text", None),
        ("created_at", "timestamp with time zone", "DEFAULT NOW()"),
        ("updated_at", "timestamp with time zone", "DEFAULT NOW()"),
        ("metadata", "jsonb", None),
        ("channel", "text", None),
        ("embeddings", "vector(1024)", None),
        ("agent_id", "uuid", None),
        ("content", "text", None),
        ("message", "text", None),
        ("role", "text", None),
        ("user_message", "text", None),
        ("assistant_message", "text", None)
    ],
    "documents": [
        ("id", "bigserial", "PRIMARY KEY"),
        ("content", "text", None),
        ("embedding", "vector(4096)", None),
        ("summary", "text", None),
        ("metadata", "jsonb", None),
        ("agent_permissions", "text[]", None),
        ("parent_document_id", "text", None),
        ("chunk_index", "integer", None),
        ("is_chunk", "boolean", None),
        ("original_filename", "text", None),
        ("file_size", "integer", None),
        ("processing_status", "text", None),
        ("created_at", "timestamp with time zone", "DEFAULT NOW()"),
        ("updated_at", "timestamp with time zone", "DEFAULT NOW()"),
        ("document_type", "text", None),
        ("user_id", "uuid", None),
        ("title", "text", None),
        ("file_name", "text", None),
        ("file_type", "text", None),
        ("file_url", "text", None),
        ("status", "text", None),
        ("embeddings", "vector(1024)", None),
        ("chunk_count", "integer", None),
        ("processing_metadata", "jsonb", None)
    ],
    "document_chunks": [
        ("id", "uuid", "PRIMARY KEY"),
        ("document_id", "bigint", None),
        ("chunk_index", "integer", None),
        ("content", "text", None),
        ("embeddings", "vector(1024)", None),
        ("chunk_metadata", "jsonb", None),
        ("created_at", "timestamp with time zone", "DEFAULT NOW()"),
        ("updated_at", "timestamp with time zone", "DEFAULT NOW()")
    ],
    "global_settings": [
        ("id", "uuid", "PRIMARY KEY"),
        ("setting_key", "text", None),
        ("setting_value", "text", None),
        ("is_encrypted", "boolean", None),
        ("description", "text", None),
        ("created_at", "timestamp with time zone", "DEFAULT NOW()"),
        ("updated_at", "timestamp with time zone", "DEFAULT NOW()"),
        ("is_secret", "boolean", None)
    ],
    "agent_documents": [
        ("id", "uuid", "PRIMARY KEY"),
        ("agent_id", "uuid", None),
        ("document_id", "bigint", None),
        ("enabled", "boolean", "DEFAULT true"),
        ("created_at", "timestamp with time zone", "DEFAULT NOW()"),
        ("updated_at", "timestamp with time zone", "DEFAULT NOW()")
    ]
}

# Required RPC functions for RAG (matching Autonomite exactly)
REQUIRED_FUNCTIONS = [
    """
    CREATE OR REPLACE FUNCTION match_documents(
        query_embedding vector,
        match_count integer DEFAULT 5
    )
    RETURNS TABLE(
        id uuid,
        content text,
        metadata jsonb,
        similarity float8
    )
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RETURN QUERY
        SELECT 
            dc.id,
            dc.content,
            dc.chunk_metadata as metadata,
            1 - (dc.embeddings <=> query_embedding) as similarity
        FROM document_chunks dc
        WHERE dc.embeddings IS NOT NULL
        ORDER BY dc.embeddings <=> query_embedding
        LIMIT match_count;
    END;
    $$;
    """,
    """
    CREATE OR REPLACE FUNCTION match_conversation_transcripts_secure(
        query_embeddings vector,
        agent_slug_param text,
        user_id_param uuid,
        match_count integer DEFAULT 5
    )
    RETURNS TABLE(
        conversation_id uuid,
        user_message text,
        agent_response text,
        similarity float8,
        created_at timestamp with time zone
    )
    LANGUAGE plpgsql
    AS $$
    BEGIN
      RETURN QUERY
      SELECT 
        u.conversation_id,
        u.content                      AS user_message,
        a.content                      AS agent_response,
        1 - (u.embeddings <=> query_embeddings) AS similarity,
        u.created_at
      FROM conversation_transcripts u
      JOIN conversation_transcripts a ON a.conversation_id = u.conversation_id AND a.role = 'assistant'
      JOIN agents ag ON u.agent_id = ag.id
      WHERE u.role = 'user'
        AND u.embeddings IS NOT NULL
        AND u.user_id = user_id_param
        AND ag.slug = agent_slug_param
      ORDER BY u.embeddings <=> query_embeddings
      LIMIT match_count;
    END;
    $$;
    """,
    """
    CREATE OR REPLACE FUNCTION match_documents(
        p_query_embedding vector,
        p_agent_slug text,
        p_match_threshold float8,
        p_match_count integer
    )
    RETURNS TABLE(
        id bigint,
        title text,
        content text,
        similarity float8
    )
    LANGUAGE plpgsql
    AS $$
    BEGIN
      RETURN QUERY
      SELECT 
        d.id,
        d.title::text        AS title,
        d.content,
        1 - (d.embeddings <=> p_query_embedding) AS similarity
      FROM documents d
      JOIN agent_documents ad ON d.id = ad.document_id
      JOIN agents a ON ad.agent_id = a.id
      WHERE a.slug = p_agent_slug
        AND 1 - (d.embeddings <=> p_query_embedding) > p_match_threshold
      ORDER BY d.embeddings <=> p_query_embedding
      LIMIT p_match_count;
    END;
    $$;
    """,
    """
    CREATE OR REPLACE FUNCTION match_conversation_transcripts_agent(
        query_embeddings vector,
        user_id_param uuid,
        agent_slug_param text,
        match_count integer DEFAULT 3
    )
    RETURNS TABLE(
        id uuid,
        conversation_id uuid,
        content text,
        role text,
        metadata jsonb,
        created_at timestamp with time zone,
        similarity float8
    )
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RETURN QUERY
        SELECT 
            ct.id,
            ct.conversation_id,
            ct.content,
            ct.role,
            ct.metadata,
            ct.created_at,
            1 - (ct.embeddings <=> query_embeddings) AS similarity
        FROM public.conversation_transcripts ct
        JOIN public.conversations c ON ct.conversation_id = c.id
        WHERE 
            ct.embeddings IS NOT NULL
            AND ct.user_id = user_id_param
            AND c.metadata->>'agent_slug' = agent_slug_param
        ORDER BY ct.embeddings <=> query_embeddings
        LIMIT match_count;
    END;
    $$;
    """
]

async def check_extension(supabase: Client, extension_name: str) -> bool:
    """Check if an extension is installed"""
    try:
        result = supabase.rpc("check_extension", {"extension_name": extension_name}).execute()
        return result.data
    except:
        # Try raw SQL query approach
        return False

async def check_table_exists(supabase: Client, table_name: str) -> bool:
    """Check if a table exists"""
    try:
        # Try to select from the table with limit 0
        result = supabase.table(table_name).select("*").limit(0).execute()
        return True
    except Exception as e:
        if "relation" in str(e).lower() and "does not exist" in str(e).lower():
            return False
        # Table might exist but have permission issues
        return None

async def get_table_columns(supabase: Client, table_name: str) -> list:
    """Get columns of a table"""
    try:
        # Get schema information
        result = supabase.table(table_name).select("*").limit(0).execute()
        # This won't give us column info directly, we'd need a different approach
        return []
    except:
        return []

def generate_migration_sql(missing_items):
    """Generate SQL migration script"""
    sql_statements = []
    
    # Add extension if needed
    if missing_items.get("vector_extension"):
        sql_statements.append("CREATE EXTENSION IF NOT EXISTS vector;")
    
    # Create missing tables
    for table_name in missing_items.get("missing_tables", []):
        if table_name in REQUIRED_SCHEMA:
            columns_sql = []
            for col_name, col_type, constraint in REQUIRED_SCHEMA[table_name]:
                col_def = f"{col_name} {col_type}"
                if constraint:
                    col_def += f" {constraint}"
                columns_sql.append(col_def)
            
            create_table = f"""
CREATE TABLE IF NOT EXISTS {table_name} (
    {',\n    '.join(columns_sql)}
);"""
            sql_statements.append(create_table)
    
    # Add missing columns to existing tables
    for table_name, columns in missing_items.get("missing_columns", {}).items():
        for col_name, col_type, constraint in columns:
            alter_sql = f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
            if constraint and "DEFAULT" in constraint:
                alter_sql += f" {constraint}"
            sql_statements.append(alter_sql + ";")
    
    # Add RPC functions
    if missing_items.get("missing_functions"):
        sql_statements.extend(REQUIRED_FUNCTIONS)
    
    # Add indexes
    sql_statements.extend([
        "CREATE INDEX IF NOT EXISTS idx_conversation_transcripts_user_id ON conversation_transcripts(user_id);",
        "CREATE INDEX IF NOT EXISTS idx_conversation_transcripts_conversation_id ON conversation_transcripts(conversation_id);",
        "CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id);",
        "CREATE INDEX IF NOT EXISTS idx_agent_documents_agent_id ON agent_documents(agent_id);",
        "CREATE INDEX IF NOT EXISTS idx_agent_documents_document_id ON agent_documents(document_id);"
    ])
    
    return "\n\n".join(sql_statements)

async def main():
    print("=== Mitra Politi Database Schema Migration ===\n")
    
    if not MITRA_SERVICE_KEY:
        print("ERROR: Please set MITRA_SERVICE_KEY environment variable")
        print("Usage: MITRA_SERVICE_KEY='your-key-here' python3 migrate_mitra_politi_schema.py")
        sys.exit(1)
    
    # Connect to Mitra Politi database
    print(f"Connecting to Mitra Politi database...")
    mitra_client = create_client(MITRA_DB_URL, MITRA_SERVICE_KEY)
    
    missing_items = {
        "vector_extension": False,
        "missing_tables": [],
        "missing_columns": {},
        "missing_functions": True  # Assume functions need to be created
    }
    
    # Check each required table
    print("\nChecking tables...")
    for table_name in REQUIRED_SCHEMA.keys():
        exists = await check_table_exists(mitra_client, table_name)
        if exists is False:
            print(f"  ❌ Table '{table_name}' is missing")
            missing_items["missing_tables"].append(table_name)
        elif exists is True:
            print(f"  ✅ Table '{table_name}' exists")
            # TODO: Check for missing columns in existing tables
        else:
            print(f"  ⚠️  Table '{table_name}' status unknown (permission issue?)")
    
    # Generate migration SQL
    if missing_items["missing_tables"] or missing_items["missing_columns"] or missing_items["missing_functions"]:
        print("\n=== Generating Migration SQL ===\n")
        migration_sql = generate_migration_sql(missing_items)
        
        # Save to file
        migration_file = "/root/sidekick-forge/scripts/mitra_politi_migration.sql"
        with open(migration_file, "w") as f:
            f.write(f"-- Mitra Politi Database Migration\n")
            f.write(f"-- Generated for: {MITRA_DB_URL}\n")
            f.write(f"-- Date: {__import__('datetime').datetime.now()}\n\n")
            f.write(migration_sql)
        
        print(f"Migration SQL saved to: {migration_file}")
        print("\nTo apply the migration:")
        print(f"1. Review the migration file: {migration_file}")
        print(f"2. Apply it using Supabase SQL Editor or psql")
        print(f"3. Re-run this script to verify")
    else:
        print("\n✅ All required schema elements are present!")
    
    # Test basic connectivity
    print("\n=== Testing Basic Operations ===")
    try:
        # Try to query agents table
        result = mitra_client.table("agents").select("*").limit(1).execute()
        print(f"✅ Successfully queried agents table")
    except Exception as e:
        print(f"⚠️  Could not query agents table: {e}")
    
    try:
        # Try to query global_settings
        result = mitra_client.table("global_settings").select("*").limit(1).execute()
        print(f"✅ Successfully queried global_settings table")
    except Exception as e:
        print(f"⚠️  Could not query global_settings table: {e}")

if __name__ == "__main__":
    asyncio.run(main())