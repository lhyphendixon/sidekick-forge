#!/bin/bash

# Apply Mitra Politi Schema (Based on successful KCG implementation)

echo "==========================================="
echo "Mitra Politi Schema Setup"
echo "Using KCG-based schema (proven to work)"
echo "==========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}This schema is based on the successful KCG implementation from yesterday.${NC}"
echo -e "${GREEN}It uses 1024-dimensional vectors throughout (no ivfflat errors).${NC}"
echo ""

echo "Schema file location:"
echo "  /root/sidekick-forge/scripts/mitra_politi_kcg_based_schema.sql"
echo ""

echo "To apply this schema:"
echo "1. Go to Mitra's Supabase SQL Editor:"
echo "   https://uyswpsluhkebudoqdnhk.supabase.co/project/uyswpsluhkebudoqdnhk/sql/new"
echo ""
echo "2. Copy the entire contents of the schema file"
echo ""
echo "3. Paste into the SQL editor"
echo ""
echo "4. Click 'Run' to execute"
echo ""

echo -e "${YELLOW}Note: This schema includes:${NC}"
echo "  • All essential tables (agents, conversations, documents, etc.)"
echo "  • 1024-dimensional vectors (no ivfflat errors)"
echo "  • RAG functions for document and conversation search"
echo "  • Global settings for API configurations"
echo ""

read -p "Would you like to see the first 50 lines of the schema? (y/n): " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    head -n 50 /root/sidekick-forge/scripts/mitra_politi_kcg_based_schema.sql
    echo ""
    echo "... (truncated)"
    echo ""
fi

echo -e "${GREEN}After applying the schema, run the verification script:${NC}"
echo "  MITRA_SERVICE_KEY='your-key' python3 /root/sidekick-forge/scripts/verify_mitra_schema.py"
echo ""

echo "==========================================="
echo "Ready to apply the KCG-based schema"
echo "==========================================="