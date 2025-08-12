# Mitra Politi Database Setup Instructions

## Overview
This document provides instructions for setting up the Mitra Politi client database to be consistent with the Autonomite Agent schema.

## Database Information
- **Database URL**: `https://uyswpsluhkebudoqdnhk.supabase.co`
- **Client Name**: Mitra Politi
- **Purpose**: Multi-tenant client database for Sidekick Forge platform

## Files Created
1. **`mitra_politi_full_schema.sql`** - Complete SQL schema that needs to be applied
2. **`migrate_mitra_politi_schema.py`** - Python script to check and generate migration
3. **`verify_mitra_schema.py`** - Verification script to confirm schema is correct

## Setup Steps

### Step 1: Get the Service Role Key
You need the service role key for the Mitra Politi Supabase project. This can be found in:
- Supabase Dashboard → Settings → API → Service Role Key

### Step 2: Apply the Schema
1. Open the Supabase SQL Editor for the Mitra Politi project
2. Copy the contents of `/root/sidekick-forge/scripts/mitra_politi_full_schema.sql`
3. Paste and run the SQL in the editor
4. Check for any errors and resolve them

### Step 3: Verify the Schema
Run the verification script to ensure everything is set up correctly:

```bash
export MITRA_SERVICE_KEY='your-service-role-key-here'
python3 /root/sidekick-forge/scripts/verify_mitra_schema.py
```

Or as a one-liner:
```bash
MITRA_SERVICE_KEY='your-service-role-key' python3 /root/sidekick-forge/scripts/verify_mitra_schema.py
```

### Step 4: Update Platform Configuration
Once the schema is verified, update the Sidekick Forge platform database with the Mitra Politi credentials:

1. The client entry should be created in the platform database
2. Store encrypted Supabase credentials
3. Configure default settings

## Schema Components

### Core Tables
- **agents** - Agent configurations and settings
- **conversations** - Conversation records
- **conversation_transcripts** - Message history with embeddings
- **documents** - Document storage with embeddings
- **document_chunks** - Chunked documents for RAG
- **agent_documents** - Agent-document associations
- **global_settings** - Client-wide configuration
- **messages** - Individual messages

### Vector Search Functions
- **match_documents()** - Similarity search for documents
- **match_conversation_transcripts_secure()** - Similarity search for conversation history

### Key Features
- pgvector extension for embeddings (1024 dimensions for conversation/document chunks, 4096 for raw documents)
- RLS (Row Level Security) enabled on all tables
- Automatic updated_at triggers
- Proper indexes for performance

## Troubleshooting

### If tables already exist
The schema uses `CREATE TABLE IF NOT EXISTS` so it's safe to run multiple times.

### If vector extension fails
Make sure the pgvector extension is enabled in Supabase:
1. Go to Database → Extensions
2. Search for "vector"
3. Enable it if not already enabled

### If RPC functions fail
The functions require the vector extension. Make sure it's enabled first.

### Permission Issues
If you get permission errors, make sure you're using the service role key, not the anon key.

## Testing the Setup

After setup, you can test by:

1. Creating a test agent:
```sql
INSERT INTO agents (name, slug, description, enabled)
VALUES ('Test Agent', 'test-agent', 'Test agent for Mitra Politi', true);
```

2. Checking vector search:
```sql
SELECT match_documents(
    ARRAY_FILL(0.0, ARRAY[1024])::vector,
    5
);
```

## Next Steps

Once the schema is set up:
1. Configure agent settings in the agents table
2. Set up global_settings for API keys and providers
3. Test agent deployment through the Sidekick Forge platform
4. Configure WordPress plugin or other client integrations

## Support

For issues or questions:
- Check the verification script output for specific errors
- Review Supabase logs for SQL errors
- Ensure all extensions are enabled
- Verify service role key permissions