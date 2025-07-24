#!/bin/bash

# Push to GitHub - Autonomite Agent Platform Public Release
# Run this script after creating the repository on GitHub

echo "🚀 Pushing Autonomite Agent Platform to GitHub..."

# Push main branch
git push -u origin main

# Push tags
git push origin --tags

echo "✅ Successfully pushed to GitHub!"
echo ""
echo "📦 Repository: https://github.com/autonomite-ai/autonomite-agent-platform"
echo "🏷️  Release: v1.0.0"
echo ""
echo "Next steps:"
echo "1. Visit the repository on GitHub"
echo "2. Create a release from tag v1.0.0"
echo "3. Share the repository URL with users"