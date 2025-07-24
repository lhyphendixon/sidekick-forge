#!/bin/bash

# Push to GitHub Script for Autonomite Agent Platform
# This script pushes to the existing repository

echo "=== Autonomite Agent Platform - GitHub Push Script ==="
echo ""

# Check if we're in a git repository
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "Error: Not in a git repository!"
    exit 1
fi

# Show current remote
echo "Current remote configuration:"
git remote -v
echo ""

# Use the existing repository
REPO_URL="https://github.com/lhyphendixon/autonomite-agent-platform.git"

echo "Using existing repository: $REPO_URL"
echo ""

# Remove existing origin if it exists
if git remote | grep -q "^origin$"; then
    echo "Removing existing origin..."
    git remote remove origin
fi

# Add new origin
echo "Adding new origin..."
git remote add origin "$REPO_URL"

# Push main branch
echo ""
echo "Pushing main branch..."
git push -u origin main

# Push tags
echo ""
echo "Pushing tags..."
git push origin --tags

echo ""
echo "=== Push Complete! ==="
echo ""
echo "Next steps:"
echo "1. Visit: https://github.com/lhyphendixon/autonomite-agent-platform"
echo "2. Go to the Releases tab"
echo "3. Click 'Create a new release'"
echo "4. Select tag: v1.1.0"
echo "5. Copy contents of RELEASE_NOTES_v1.1.0.md"
echo ""
echo "Or use GitHub CLI:"
echo "gh release create v1.1.0 --title 'v1.1.0: Thin-Client Architecture & Infrastructure Improvements' --notes-file RELEASE_NOTES_v1.1.0.md"
echo ""