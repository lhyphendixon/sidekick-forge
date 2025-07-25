#!/bin/bash

# First authenticate with GitHub if not already done
echo "Checking GitHub authentication..."
if ! gh auth status &>/dev/null; then
    echo "Please authenticate with GitHub:"
    gh auth login
fi

# Create the release
echo "Creating release v1.5.0..."
gh release create v1.5.0 \
    --title "RAG Context Injection Fix" \
    --notes-file RELEASE_NOTES_v1.5.0.md \
    --repo lhyphendixon/autonomite-agent-platform

echo "Release created successfully!"
echo "You can view it at: https://github.com/lhyphendixon/autonomite-agent-platform/releases/tag/v1.5.0"