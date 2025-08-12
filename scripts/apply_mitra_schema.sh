#!/bin/bash

# Apply Mitra Politi Schema Script
# This script helps apply the corrected schema to the Mitra database

echo "==========================================="
echo "Mitra Politi Schema Application Script"
echo "==========================================="
echo ""

# Check if service role key is provided
if [ -z "$1" ]; then
    echo "Usage: ./apply_mitra_schema.sh <SERVICE_ROLE_KEY>"
    echo ""
    echo "Example:"
    echo "  ./apply_mitra_schema.sh 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...'"
    echo ""
    echo "You can find the service role key in:"
    echo "  Supabase Dashboard → Settings → API → Service Role Key"
    exit 1
fi

SERVICE_KEY="$1"
SUPABASE_URL="https://uyswpsluhkebudoqdnhk.supabase.co"

echo "Step 1: Testing connection to Mitra database..."
echo "-----------------------------------------------"

# Test connection with a simple query
python3 -c "
import sys
from supabase import create_client

try:
    client = create_client('$SUPABASE_URL', '$SERVICE_KEY')
    # Try a simple query to test connection
    result = client.table('agents').select('*').limit(1).execute()
    print('✅ Connection successful!')
except Exception as e:
    print(f'❌ Connection failed: {e}')
    print('Please check your service role key')
    sys.exit(1)
"

if [ $? -ne 0 ]; then
    echo ""
    echo "Failed to connect to database. Please check your service role key."
    exit 1
fi

echo ""
echo "Step 2: Schema to apply"
echo "------------------------"
echo "The fixed schema file addresses the ivfflat dimension limit by:"
echo "  • Using only 1024-dimensional vectors for all embeddings"
echo "  • Removing the problematic 4096-dimensional column"
echo "  • Ensuring all ivfflat indexes are within the 2000 dimension limit"
echo ""
echo "Schema file: /root/sidekick-forge/scripts/mitra_politi_schema_fixed.sql"
echo ""

echo "Step 3: Instructions to apply the schema"
echo "-----------------------------------------"
echo "1. Go to: https://uyswpsluhkebudoqdnhk.supabase.co/project/uyswpsluhkebudoqdnhk/sql/new"
echo "2. Copy the contents of: /root/sidekick-forge/scripts/mitra_politi_schema_fixed.sql"
echo "3. Paste into the SQL editor"
echo "4. Click 'Run' to execute"
echo ""
echo "The schema is designed to be idempotent (safe to run multiple times)."
echo ""

read -p "Have you applied the schema? (y/n): " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    echo "Step 4: Verifying schema application..."
    echo "----------------------------------------"
    
    MITRA_SERVICE_KEY="$SERVICE_KEY" python3 /root/sidekick-forge/scripts/verify_mitra_schema.py
    
    if [ $? -eq 0 ]; then
        echo ""
        echo "✅ Schema successfully applied and verified!"
        echo ""
        echo "Next steps:"
        echo "1. Update the service role key in Sidekick Forge platform database"
        echo "2. Test document upload functionality"
        echo "3. Test agent creation"
    else
        echo ""
        echo "⚠️  Schema verification found some issues. Please review the output above."
    fi
else
    echo ""
    echo "Please apply the schema first, then run this script again to verify."
fi

echo ""
echo "==========================================="
echo "Script completed"
echo "==========================================="