#!/bin/bash
# Fix for LiveKit v1.2.2 compatibility
# This script patches the entrypoint.py to remove the old ChatContext.append() call

# Check if the old code exists
if grep -q "chat_ctx.append" /app/entrypoint.py; then
    echo "Fixing ChatContext compatibility issue..."
    
    # Remove the problematic lines (387-389)
    sed -i '387,389d' /app/entrypoint.py
    
    echo "Fix applied successfully"
else
    echo "No fix needed - code is already updated"
fi

# Run the original entrypoint
exec python /app/entrypoint.py "$@"