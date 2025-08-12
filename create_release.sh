#!/bin/bash

# First authenticate with GitHub if not already done
echo "Checking GitHub authentication..."
if ! gh auth status &>/dev/null; then
    echo "Please authenticate with GitHub:"
    gh auth login
fi

# Create the release
echo "Creating release v2.0.0..."
gh release create v2.0.0 \
    --title "Sidekick Forge Public Release" \
    --notes-file RELEASE_NOTES_v2.0.0.md \
    --repo lhyphendixon/sidekick-forge

echo "Release created successfully!"
echo "You can view it at: https://github.com/lhyphendixon/sidekick-forge/releases/tag/v2.0.0"