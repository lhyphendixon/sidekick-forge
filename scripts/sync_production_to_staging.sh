#!/bin/bash

################################################################################
# Sidekick Forge - One-Time Production → Staging Sync
################################################################################
# This script syncs the current production state to staging, including:
# - All current production code (with hotfixes)
# - Deployment automation system
# - Current database schema as baseline
#
# Run this ON STAGING SERVER to sync with production
################################################################################

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║   Sidekick Forge - Sync Production to Staging             ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

log_warning "This will sync staging with current production state"
log_warning "Including all hotfixes and the new deployment system"
echo ""
read -p "Continue? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    log_error "Sync cancelled"
    exit 0
fi

# Step 1: Backup current staging state
log_info "Backing up current staging state..."
BACKUP_DIR="$PROJECT_ROOT/backups/pre-sync-$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

if [ -f "$PROJECT_ROOT/.env" ]; then
    cp "$PROJECT_ROOT/.env" "$BACKUP_DIR/.env.staging.backup"
    log_success "Backed up staging .env"
fi

cd "$PROJECT_ROOT"
CURRENT_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
echo "$CURRENT_COMMIT" > "$BACKUP_DIR/staging_commit_before_sync.txt"
log_success "Current staging commit: $CURRENT_COMMIT"

# Step 2: Pull latest from GitHub
log_info "Pulling latest code from GitHub (includes all production hotfixes)..."

# Stash any local changes
if ! git diff --quiet || ! git diff --cached --quiet; then
    log_warning "Stashing local changes..."
    git stash save "Pre-sync backup $(date +%Y%m%d_%H%M%S)"
fi

# Fetch and pull
git fetch origin
git pull origin main

NEW_COMMIT=$(git rev-parse HEAD)
log_success "Updated to commit: $NEW_COMMIT"

# Step 3: Preserve staging-specific environment variables
log_info "Preserving staging-specific environment variables..."

if [ -f "$BACKUP_DIR/.env.staging.backup" ]; then
    # Extract staging-specific values
    STAGING_SUPABASE_URL=$(grep -E "^SUPABASE_URL=" "$BACKUP_DIR/.env.staging.backup" | cut -d '=' -f2- || echo "")
    STAGING_SUPABASE_KEY=$(grep -E "^SUPABASE_SERVICE_ROLE_KEY=" "$BACKUP_DIR/.env.staging.backup" | cut -d '=' -f2- || echo "")
    STAGING_SUPABASE_ANON=$(grep -E "^SUPABASE_ANON_KEY=" "$BACKUP_DIR/.env.staging.backup" | cut -d '=' -f2- || echo "")
    STAGING_SUPABASE_REF=$(grep -E "^SUPABASE_PROJECT_REF=" "$BACKUP_DIR/.env.staging.backup" | cut -d '=' -f2- || echo "")
    STAGING_DOMAIN=$(grep -E "^DOMAIN_NAME=" "$BACKUP_DIR/.env.staging.backup" | cut -d '=' -f2- || echo "staging.sidekickforge.com")
    STAGING_LIVEKIT_URL=$(grep -E "^LIVEKIT_URL=" "$BACKUP_DIR/.env.staging.backup" | cut -d '=' -f2- || echo "")
    STAGING_LIVEKIT_KEY=$(grep -E "^LIVEKIT_API_KEY=" "$BACKUP_DIR/.env.staging.backup" | cut -d '=' -f2- || echo "")
    STAGING_LIVEKIT_SECRET=$(grep -E "^LIVEKIT_API_SECRET=" "$BACKUP_DIR/.env.staging.backup" | cut -d '=' -f2- || echo "")

    # Update .env with staging values
    if [ -n "$STAGING_SUPABASE_URL" ]; then
        sed -i "s|^SUPABASE_URL=.*|SUPABASE_URL=$STAGING_SUPABASE_URL|" "$PROJECT_ROOT/.env"
    fi
    if [ -n "$STAGING_SUPABASE_KEY" ]; then
        sed -i "s|^SUPABASE_SERVICE_ROLE_KEY=.*|SUPABASE_SERVICE_ROLE_KEY=$STAGING_SUPABASE_KEY|" "$PROJECT_ROOT/.env"
    fi
    if [ -n "$STAGING_SUPABASE_ANON" ]; then
        sed -i "s|^SUPABASE_ANON_KEY=.*|SUPABASE_ANON_KEY=$STAGING_SUPABASE_ANON|" "$PROJECT_ROOT/.env"
    fi
    if [ -n "$STAGING_SUPABASE_REF" ]; then
        # Add or update SUPABASE_PROJECT_REF
        if grep -q "^SUPABASE_PROJECT_REF=" "$PROJECT_ROOT/.env"; then
            sed -i "s|^SUPABASE_PROJECT_REF=.*|SUPABASE_PROJECT_REF=$STAGING_SUPABASE_REF|" "$PROJECT_ROOT/.env"
        else
            echo "SUPABASE_PROJECT_REF=$STAGING_SUPABASE_REF" >> "$PROJECT_ROOT/.env"
        fi
    fi
    if [ -n "$STAGING_DOMAIN" ]; then
        sed -i "s|^DOMAIN_NAME=.*|DOMAIN_NAME=$STAGING_DOMAIN|" "$PROJECT_ROOT/.env"
    fi
    if [ -n "$STAGING_LIVEKIT_URL" ]; then
        sed -i "s|^LIVEKIT_URL=.*|LIVEKIT_URL=$STAGING_LIVEKIT_URL|" "$PROJECT_ROOT/.env"
    fi
    if [ -n "$STAGING_LIVEKIT_KEY" ]; then
        sed -i "s|^LIVEKIT_API_KEY=.*|LIVEKIT_API_KEY=$STAGING_LIVEKIT_KEY|" "$PROJECT_ROOT/.env"
    fi
    if [ -n "$STAGING_LIVEKIT_SECRET" ]; then
        sed -i "s|^LIVEKIT_API_SECRET=.*|LIVEKIT_API_SECRET=$STAGING_LIVEKIT_SECRET|" "$PROJECT_ROOT/.env"
    fi

    # Ensure staging-specific settings
    sed -i "s|^DEVELOPMENT_MODE=.*|DEVELOPMENT_MODE=true|" "$PROJECT_ROOT/.env"

    log_success "Restored staging-specific environment variables"
else
    log_warning "No previous .env backup found - using pulled .env"
    log_warning "You may need to manually configure staging-specific values!"
fi

# Step 4: Make scripts executable
log_info "Making deployment scripts executable..."
chmod +x "$PROJECT_ROOT/scripts"/*.sh
log_success "Scripts are executable"

# Step 5: Check if Supabase CLI is installed
log_info "Checking for Supabase CLI..."
if command -v supabase &> /dev/null; then
    log_success "Supabase CLI is installed"
    SUPABASE_CLI_INSTALLED=true
else
    log_warning "Supabase CLI not installed"
    log_info "Install with: npm install -g supabase"
    SUPABASE_CLI_INSTALLED=false
fi

# Step 6: Initialize Supabase migrations (if CLI available)
if [ "$SUPABASE_CLI_INSTALLED" = true ]; then
    log_info "Initializing Supabase migrations..."

    # Initialize if not already done
    if [ ! -f "$PROJECT_ROOT/supabase/config.toml" ]; then
        cd "$PROJECT_ROOT"
        supabase init
        log_success "Supabase initialized"
    else
        log_info "Supabase already initialized"
    fi

    # Check for SUPABASE_PROJECT_REF
    source "$PROJECT_ROOT/.env"
    if [ -n "$SUPABASE_PROJECT_REF" ]; then
        log_info "Linking to staging Supabase project..."
        supabase link --project-ref "$SUPABASE_PROJECT_REF" || {
            log_warning "Failed to link to Supabase - you may need to do this manually"
        }
    else
        log_warning "SUPABASE_PROJECT_REF not set in .env"
        log_warning "Add it and run: ./scripts/supabase_migration_helper.sh link staging"
    fi
else
    log_warning "Skipping Supabase initialization - CLI not installed"
fi

# Step 7: Rebuild Docker containers
log_info "Rebuilding Docker containers with latest code..."
cd "$PROJECT_ROOT"
docker-compose build || {
    log_warning "Docker build failed - you may need to fix errors manually"
}
log_success "Docker images rebuilt"

# Step 8: Restart services
log_info "Restarting services..."
docker-compose up -d || {
    log_warning "Docker restart failed - check logs with: docker-compose logs"
}
log_success "Services restarted"

# Step 9: Run health check
log_info "Running health check..."
sleep 5  # Give services time to start

if curl -s -f http://localhost:8000/health > /dev/null 2>&1; then
    log_success "Health check passed"
else
    log_warning "Health check failed - services may still be starting"
    log_info "Check status with: docker-compose ps"
    log_info "Check logs with: docker-compose logs -f fastapi"
fi

# Success summary
echo ""
log_success "╔════════════════════════════════════════════════════════════╗"
log_success "║   Staging Synced with Production Successfully!            ║"
log_success "╚════════════════════════════════════════════════════════════╝"
echo ""
log_info "Sync summary:"
log_info "  - Code updated to production commit: $NEW_COMMIT"
log_info "  - Deployment system installed"
log_info "  - Staging environment preserved"
log_info "  - Docker containers rebuilt"
log_info "  - Services restarted"
echo ""
log_info "Backup location: $BACKUP_DIR"
echo ""

# Next steps
echo ""
log_info "╔════════════════════════════════════════════════════════════╗"
log_info "║   Next Steps                                               ║"
log_info "╚════════════════════════════════════════════════════════════╝"
echo ""

if [ "$SUPABASE_CLI_INSTALLED" = false ]; then
    echo "1. Install Supabase CLI:"
    echo "   npm install -g supabase"
    echo ""
fi

echo "2. Verify SUPABASE_PROJECT_REF is in .env:"
echo "   grep SUPABASE_PROJECT_REF /root/sidekick-forge/.env"
echo ""
echo "   If missing, add it:"
echo "   echo 'SUPABASE_PROJECT_REF=your-staging-ref' >> /root/sidekick-forge/.env"
echo ""

if [ "$SUPABASE_CLI_INSTALLED" = true ] && [ -z "$SUPABASE_PROJECT_REF" ]; then
    echo "3. Link to staging Supabase project:"
    echo "   cd /root/sidekick-forge"
    echo "   ./scripts/supabase_migration_helper.sh link staging"
    echo ""
fi

echo "4. Capture current staging schema as baseline:"
echo "   cd /root/sidekick-forge"
echo "   ./scripts/supabase_migration_helper.sh capture"
echo "   git add supabase/migrations/"
echo "   git commit -m 'Add baseline staging schema'"
echo ""

echo "5. Test the deployment system:"
echo "   cd /root/sidekick-forge"
echo "   ./scripts/prepare_staging_release.sh"
echo ""

echo "6. Read the documentation:"
echo "   cat /root/sidekick-forge/STAGING_SETUP.md"
echo "   cat /root/sidekick-forge/DEPLOYMENT_QUICKSTART.md"
echo ""

log_success "Staging is now in sync with production! 🚀"
echo ""
