#!/bin/bash

################################################################################
# Create Staging Deployment Package
################################################################################
# This script creates a package of all deployment files needed for the staging
# server. Run this on the PRODUCTION server.
################################################################################

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PACKAGE_NAME="sidekick-forge-deployment-system.tar.gz"
TEMP_DIR=$(mktemp -d)

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}Creating deployment package for staging server...${NC}"
echo ""

# Create directory structure in temp
mkdir -p "$TEMP_DIR/sidekick-forge/scripts"
mkdir -p "$TEMP_DIR/sidekick-forge/supabase/migrations"

# Copy deployment scripts
echo -e "${BLUE}Copying deployment scripts...${NC}"
cp "$PROJECT_ROOT/scripts/deploy_to_production.sh" "$TEMP_DIR/sidekick-forge/scripts/"
cp "$PROJECT_ROOT/scripts/prepare_staging_release.sh" "$TEMP_DIR/sidekick-forge/scripts/"
cp "$PROJECT_ROOT/scripts/supabase_migration_helper.sh" "$TEMP_DIR/sidekick-forge/scripts/"
cp "$PROJECT_ROOT/scripts/README_DEPLOYMENT.md" "$TEMP_DIR/sidekick-forge/scripts/"

# Copy documentation
echo -e "${BLUE}Copying documentation...${NC}"
cp "$PROJECT_ROOT/DEPLOYMENT.md" "$TEMP_DIR/sidekick-forge/"
cp "$PROJECT_ROOT/DEPLOYMENT_QUICKSTART.md" "$TEMP_DIR/sidekick-forge/"
cp "$PROJECT_ROOT/STAGING_SETUP.md" "$TEMP_DIR/sidekick-forge/"
cp "$PROJECT_ROOT/CLAUDE.md" "$TEMP_DIR/sidekick-forge/"

# Copy supabase config template (if exists)
if [ -f "$PROJECT_ROOT/supabase/config.toml" ]; then
    echo -e "${BLUE}Copying Supabase config template...${NC}"
    cp "$PROJECT_ROOT/supabase/config.toml" "$TEMP_DIR/sidekick-forge/supabase/"
fi

# Create .gitkeep for migrations directory
touch "$TEMP_DIR/sidekick-forge/supabase/migrations/.gitkeep"

# Create a README for the package
cat > "$TEMP_DIR/README.txt" <<'EOF'
Sidekick Forge - Deployment System Package
===========================================

This package contains the deployment automation system for Sidekick Forge.

INSTALLATION ON STAGING SERVER:
--------------------------------

1. Extract this package:
   tar -xzf sidekick-forge-deployment-system.tar.gz

2. Copy files to your sidekick-forge directory:
   cp -r sidekick-forge/* /root/sidekick-forge/

3. Make scripts executable:
   chmod +x /root/sidekick-forge/scripts/*.sh

4. Follow the setup guide:
   cat /root/sidekick-forge/STAGING_SETUP.md

PREREQUISITES:
--------------
- Node.js installed
- Supabase CLI: npm install -g supabase
- Git configured with GitHub access
- Staging .env file with SUPABASE_PROJECT_REF

QUICK START:
------------
After installation:

1. Install Supabase CLI:
   npm install -g supabase

2. Configure .env with staging credentials including:
   SUPABASE_PROJECT_REF=your-staging-project-ref

3. Initialize migrations:
   cd /root/sidekick-forge
   ./scripts/supabase_migration_helper.sh init
   ./scripts/supabase_migration_helper.sh link staging

4. Test the setup:
   python3 scripts/test_mission_critical.py

5. Make a test release:
   ./scripts/prepare_staging_release.sh

For detailed instructions, see STAGING_SETUP.md

DOCUMENTATION:
--------------
- STAGING_SETUP.md          - Complete setup guide for staging
- DEPLOYMENT.md             - Full deployment documentation
- DEPLOYMENT_QUICKSTART.md  - Quick reference guide
- scripts/README_DEPLOYMENT.md - Scripts documentation

SUPPORT:
--------
Review the documentation files included in this package.

Last Updated: 2026-02-21
EOF

# Create the package
echo ""
echo -e "${BLUE}Creating tar.gz package...${NC}"
cd "$TEMP_DIR"
tar -czf "$PACKAGE_NAME" README.txt sidekick-forge/

# Move package to project root
mv "$PACKAGE_NAME" "$PROJECT_ROOT/"

# Cleanup
rm -rf "$TEMP_DIR"

# Success message
echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Package created successfully!                           ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BLUE}Package location:${NC} $PROJECT_ROOT/$PACKAGE_NAME"
echo -e "${BLUE}Package size:${NC} $(du -h "$PROJECT_ROOT/$PACKAGE_NAME" | cut -f1)"
echo ""
echo -e "${YELLOW}Transfer to staging server:${NC}"
echo ""
echo "  # Option 1: SCP"
echo "  scp $PROJECT_ROOT/$PACKAGE_NAME user@staging-server:/root/"
echo ""
echo "  # Option 2: Download (if you have web server)"
echo "  # Make it downloadable and download on staging"
echo ""
echo -e "${YELLOW}On staging server:${NC}"
echo ""
echo "  tar -xzf $PACKAGE_NAME"
echo "  cp -r sidekick-forge/* /root/sidekick-forge/"
echo "  chmod +x /root/sidekick-forge/scripts/*.sh"
echo "  cat sidekick-forge/STAGING_SETUP.md"
echo ""
echo -e "${GREEN}Package contents:${NC}"
tar -tzf "$PROJECT_ROOT/$PACKAGE_NAME" | head -20
echo "  ... (see full list with: tar -tzf $PACKAGE_NAME)"
echo ""
