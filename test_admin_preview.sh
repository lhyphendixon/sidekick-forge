#!/bin/bash

echo "=== Admin Preview Voice Chat Fix ==="
echo ""
echo "The issue: Your current preview is using a non-existent user ID (427b15ad-8dd8-491b-b096-6523e13e998c)"
echo "This user doesn't exist in the client's database, causing RAG context to fail per our no-fallback policy."
echo ""
echo "Solution: Start a fresh preview session with a valid shadow user"
echo ""
echo "Step 1: Create/get shadow user credentials"
echo "---------------------------------------"

# Call ensure-client-user endpoint
echo "Calling ensure-client-user endpoint..."
RESPONSE=$(curl -s -X POST http://localhost:8000/api/v2/admin/ensure-client-user \
  -H "Content-Type: application/json" \
  -d '{
    "platform_user_id": "351bb07b-03fc-4fb4-b09b-748ef8a72084",
    "platform_user_email": "l-dixon@autonomite.net",
    "client_id": "11389177-e4d8-49a9-9a00-f77bb4de6592"
  }')

# Check if successful
if echo "$RESPONSE" | grep -q "client_user_id"; then
    echo "✅ Shadow user ready!"
    echo ""
    echo "Shadow User Details:"
    echo "$RESPONSE" | jq '.'
    
    # Extract the JWT
    JWT=$(echo "$RESPONSE" | jq -r '.client_jwt')
    USER_ID=$(echo "$RESPONSE" | jq -r '.client_user_id')
    
    echo ""
    echo "Step 2: Use these credentials in your admin preview"
    echo "----------------------------------------------------"
    echo "1. Close your current admin preview"
    echo "2. Start a new preview session"
    echo "3. The embed should use this JWT for authentication:"
    echo ""
    echo "JWT (valid for 15 minutes):"
    echo "$JWT"
    echo ""
    echo "User ID: $USER_ID"
    echo ""
    echo "This shadow user has a valid profile and will work with RAG context."
else
    echo "❌ Failed to create shadow user:"
    echo "$RESPONSE" | jq '.'
    
    # Try to handle the email conflict
    if echo "$RESPONSE" | grep -q "already been registered"; then
        echo ""
        echo "The shadow user already exists. Trying with incremented email..."
        
        # Generate a unique email
        TIMESTAMP=$(date +%s)
        RESPONSE=$(curl -s -X POST http://localhost:8000/api/v2/admin/ensure-client-user \
          -H "Content-Type: application/json" \
          -d "{
            \"platform_user_id\": \"351bb07b-03fc-4fb4-b09b-748ef8a72084\",
            \"platform_user_email\": \"l-dixon+shadow${TIMESTAMP}@autonomite.net\",
            \"client_id\": \"11389177-e4d8-49a9-9a00-f77bb4de6592\"
          }")
        
        if echo "$RESPONSE" | grep -q "client_user_id"; then
            echo "✅ New shadow user created!"
            echo ""
            echo "Shadow User Details:"
            echo "$RESPONSE" | jq '.'
            
            JWT=$(echo "$RESPONSE" | jq -r '.client_jwt')
            USER_ID=$(echo "$RESPONSE" | jq -r '.client_user_id')
            
            echo ""
            echo "Use this JWT in your admin preview:"
            echo "$JWT"
            echo ""
            echo "User ID: $USER_ID"
        else
            echo "Failed to create new shadow user:"
            echo "$RESPONSE" | jq '.'
        fi
    fi
fi