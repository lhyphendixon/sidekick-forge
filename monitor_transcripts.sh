#!/bin/bash

# Monitor conversation_transcripts for assistant chunks
echo "Monitoring conversation_transcripts for assistant messages..."
echo "Press Ctrl+C to stop"
echo ""

# Get DB credentials from .env
source /root/sidekick-forge/.env

while true; do
    clear
    echo "=== Assistant Transcript Chunks (Last 20) ==="
    echo ""
    
    PGPASSWORD=$DB_PASSWORD psql -h localhost -U forge_admin -d sidekick_forge_db -c "
    SELECT 
        id,
        conversation_id,
        role,
        sequence,
        turn_id,
        LEFT(content, 50) as content_preview,
        CASE 
            WHEN citations IS NOT NULL THEN 'YES' 
            ELSE 'NO' 
        END as has_citations,
        created_at
    FROM conversation_transcripts
    WHERE role = 'assistant'
    ORDER BY created_at DESC
    LIMIT 20;
    " 2>/dev/null || echo "Error connecting to database"
    
    echo ""
    echo "Refreshing in 2 seconds..."
    sleep 2
done