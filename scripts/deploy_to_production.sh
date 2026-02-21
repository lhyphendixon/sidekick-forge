#!/bin/bash

################################################################################
# Sidekick Forge - Production Deployment Script
################################################################################
# This script deploys changes from staging to production with:
# - Supabase schema migration support
# - Environment variable preservation
# - Zero-downtime service reload
# - Automatic rollback on failure
# - Health checks and validation
################################################################################

set -e  # Exit on error
set -o pipefail  # Catch errors in pipelines

# Script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Deployment configuration
BACKUP_DIR="$PROJECT_ROOT/backups/$(date +%Y%m%d_%H%M%S)"
DEPLOYMENT_LOG="$BACKUP_DIR/deployment.log"
ROLLBACK_NEEDED=false

# Function to create backup directory
create_backup_dir() {
    log_info "Creating backup directory..."
    mkdir -p "$BACKUP_DIR"
    log_success "Backup directory: $BACKUP_DIR"
}

# Function to backup current state
backup_current_state() {
    log_info "Backing up current state..."

    # Backup .env file
    if [ -f "$PROJECT_ROOT/.env" ]; then
        cp "$PROJECT_ROOT/.env" "$BACKUP_DIR/.env.backup"
        log_success "Backed up .env file"
    else
        log_error ".env file not found!"
        exit 1
    fi

    # Backup current code (git commit hash)
    cd "$PROJECT_ROOT"
    CURRENT_COMMIT=$(git rev-parse HEAD)
    echo "$CURRENT_COMMIT" > "$BACKUP_DIR/git_commit.txt"
    log_success "Current git commit: $CURRENT_COMMIT"

    # Backup docker-compose.yml
    if [ -f "$PROJECT_ROOT/docker-compose.yml" ]; then
        cp "$PROJECT_ROOT/docker-compose.yml" "$BACKUP_DIR/docker-compose.yml.backup"
        log_success "Backed up docker-compose.yml"
    fi
}

# Function to preserve production environment variables
preserve_production_env() {
    log_info "Preserving production environment variables..."

    # Extract critical production values
    if [ -f "$PROJECT_ROOT/.env" ]; then
        # Create a temporary file with production-specific values
        cat > "$BACKUP_DIR/production_env_vars.txt" <<EOF
# Production-specific environment variables
DOMAIN_NAME=$(grep -E "^DOMAIN_NAME=" "$PROJECT_ROOT/.env" | cut -d '=' -f2- || echo "")
DEVELOPMENT_MODE=$(grep -E "^DEVELOPMENT_MODE=" "$PROJECT_ROOT/.env" | cut -d '=' -f2- || echo "false")
SUPABASE_URL=$(grep -E "^SUPABASE_URL=" "$PROJECT_ROOT/.env" | cut -d '=' -f2- || echo "")
SUPABASE_SERVICE_ROLE_KEY=$(grep -E "^SUPABASE_SERVICE_ROLE_KEY=" "$PROJECT_ROOT/.env" | cut -d '=' -f2- || echo "")
SUPABASE_ANON_KEY=$(grep -E "^SUPABASE_ANON_KEY=" "$PROJECT_ROOT/.env" | cut -d '=' -f2- || echo "")
LIVEKIT_URL=$(grep -E "^LIVEKIT_URL=" "$PROJECT_ROOT/.env" | cut -d '=' -f2- || echo "")
LIVEKIT_API_KEY=$(grep -E "^LIVEKIT_API_KEY=" "$PROJECT_ROOT/.env" | cut -d '=' -f2- || echo "")
LIVEKIT_API_SECRET=$(grep -E "^LIVEKIT_API_SECRET=" "$PROJECT_ROOT/.env" | cut -d '=' -f2- || echo "")
EOF
        log_success "Preserved production environment variables"
    else
        log_error "Cannot preserve .env - file not found"
        exit 1
    fi
}

# Function to pull latest code from GitHub
pull_latest_code() {
    log_info "Pulling latest code from GitHub..."

    cd "$PROJECT_ROOT"

    # Fetch latest changes
    git fetch origin

    # Get current branch
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
    log_info "Current branch: $CURRENT_BRANCH"

    # Pull latest changes
    git pull origin "$CURRENT_BRANCH"

    NEW_COMMIT=$(git rev-parse HEAD)
    log_success "Updated to commit: $NEW_COMMIT"

    # Check if there were any changes
    if [ "$CURRENT_COMMIT" == "$NEW_COMMIT" ]; then
        log_warning "No new commits to deploy"
    else
        log_success "Pulled new changes from GitHub"
    fi
}

# Function to restore production environment variables
restore_production_env() {
    log_info "Restoring production environment variables..."

    if [ -f "$BACKUP_DIR/production_env_vars.txt" ]; then
        # Read production values
        source "$BACKUP_DIR/production_env_vars.txt"

        # Update .env file with production values
        # This preserves production secrets while allowing staging values to update other vars

        # Backup the pulled .env
        cp "$PROJECT_ROOT/.env" "$BACKUP_DIR/.env.pulled"

        # Update critical production values
        sed -i "s|^DOMAIN_NAME=.*|DOMAIN_NAME=$DOMAIN_NAME|" "$PROJECT_ROOT/.env"
        sed -i "s|^DEVELOPMENT_MODE=.*|DEVELOPMENT_MODE=$DEVELOPMENT_MODE|" "$PROJECT_ROOT/.env"
        sed -i "s|^SUPABASE_URL=.*|SUPABASE_URL=$SUPABASE_URL|" "$PROJECT_ROOT/.env"
        sed -i "s|^SUPABASE_SERVICE_ROLE_KEY=.*|SUPABASE_SERVICE_ROLE_KEY=$SUPABASE_SERVICE_ROLE_KEY|" "$PROJECT_ROOT/.env"
        sed -i "s|^SUPABASE_ANON_KEY=.*|SUPABASE_ANON_KEY=$SUPABASE_ANON_KEY|" "$PROJECT_ROOT/.env"
        sed -i "s|^LIVEKIT_URL=.*|LIVEKIT_URL=$LIVEKIT_URL|" "$PROJECT_ROOT/.env"
        sed -i "s|^LIVEKIT_API_KEY=.*|LIVEKIT_API_KEY=$LIVEKIT_API_KEY|" "$PROJECT_ROOT/.env"
        sed -i "s|^LIVEKIT_API_SECRET=.*|LIVEKIT_API_SECRET=$LIVEKIT_API_SECRET|" "$PROJECT_ROOT/.env"

        log_success "Restored production environment variables"
    else
        log_error "Cannot restore production env - backup not found"
        exit 1
    fi
}

# Function to apply Supabase migrations
apply_supabase_migrations() {
    log_info "Checking for Supabase migrations..."

    # Check if migrations directory exists
    if [ ! -d "$PROJECT_ROOT/supabase/migrations" ]; then
        log_warning "No migrations directory found - skipping schema changes"
        return 0
    fi

    # Count migration files
    MIGRATION_COUNT=$(ls -1 "$PROJECT_ROOT/supabase/migrations"/*.sql 2>/dev/null | wc -l)

    if [ "$MIGRATION_COUNT" -eq 0 ]; then
        log_info "No migration files found - skipping schema changes"
        return 0
    fi

    log_info "Found $MIGRATION_COUNT migration file(s)"

    # Check if Supabase CLI is installed
    if ! command -v supabase &> /dev/null; then
        log_warning "Supabase CLI not installed - cannot apply migrations automatically"
        log_warning "Please apply migrations manually or install Supabase CLI"
        read -p "Continue without applying migrations? (yes/no): " SKIP_MIGRATIONS
        if [ "$SKIP_MIGRATIONS" != "yes" ]; then
            log_error "Deployment cancelled - migrations required"
            exit 1
        fi
        return 0
    fi

    # Load Supabase configuration
    source "$PROJECT_ROOT/.env"

    if [ -z "$SUPABASE_PROJECT_REF" ]; then
        log_warning "SUPABASE_PROJECT_REF not set in .env"
        log_warning "Skipping automatic migration deployment"
        read -p "Continue without applying migrations? (yes/no): " SKIP_MIGRATIONS
        if [ "$SKIP_MIGRATIONS" != "yes" ]; then
            log_error "Deployment cancelled - migrations required"
            exit 1
        fi
        return 0
    fi

    # Preview migrations (dry-run)
    log_info "Previewing schema changes..."
    supabase link --project-ref "$SUPABASE_PROJECT_REF" || {
        log_error "Failed to link to Supabase project"
        exit 1
    }

    supabase db push --dry-run || {
        log_error "Migration dry-run failed"
        exit 1
    }

    # Confirm migration deployment
    echo ""
    log_warning "Ready to apply migrations to PRODUCTION database"
    read -p "Apply these migrations? (yes/no): " APPLY_MIGRATIONS

    if [ "$APPLY_MIGRATIONS" == "yes" ]; then
        log_info "Applying migrations to production..."
        supabase db push || {
            log_error "Failed to apply migrations"
            ROLLBACK_NEEDED=true
            exit 1
        }
        log_success "Migrations applied successfully"
    else
        log_error "Migrations cancelled by user"
        exit 1
    fi
}

# Function to rebuild Docker images
rebuild_docker_images() {
    log_info "Rebuilding Docker images..."

    cd "$PROJECT_ROOT"

    # Build FastAPI image
    log_info "Building FastAPI image..."
    docker-compose build fastapi || {
        log_error "Failed to build FastAPI image"
        ROLLBACK_NEEDED=true
        exit 1
    }

    # Build agent worker image
    log_info "Building agent worker image..."
    docker-compose build agent-worker || {
        log_error "Failed to build agent worker image"
        ROLLBACK_NEEDED=true
        exit 1
    }

    log_success "Docker images rebuilt successfully"
}

# Function to run pre-deployment health check
pre_deployment_health_check() {
    log_info "Running pre-deployment health check..."

    # Check if FastAPI is responding
    if curl -s -f http://localhost:8000/health > /dev/null 2>&1; then
        log_success "Current FastAPI is healthy"
    else
        log_warning "Current FastAPI health check failed (may be expected)"
    fi

    # Check running containers
    RUNNING_CONTAINERS=$(docker-compose ps --services --filter "status=running" | wc -l)
    log_info "Running containers: $RUNNING_CONTAINERS"
}

# Function to deploy new containers (zero-downtime)
deploy_new_containers() {
    log_info "Deploying new containers..."

    cd "$PROJECT_ROOT"

    # Restart services one at a time for zero-downtime
    log_info "Restarting FastAPI service..."
    docker-compose up -d --no-deps --build fastapi || {
        log_error "Failed to restart FastAPI"
        ROLLBACK_NEEDED=true
        exit 1
    }

    # Wait for FastAPI to be healthy
    log_info "Waiting for FastAPI to be ready..."
    for i in {1..30}; do
        if curl -s -f http://localhost:8000/health > /dev/null 2>&1; then
            log_success "FastAPI is healthy"
            break
        fi
        if [ $i -eq 30 ]; then
            log_error "FastAPI failed to become healthy"
            ROLLBACK_NEEDED=true
            exit 1
        fi
        sleep 2
    done

    # Restart agent workers
    log_info "Restarting agent workers..."
    docker-compose up -d --no-deps --scale agent-worker=2 agent-worker || {
        log_error "Failed to restart agent workers"
        ROLLBACK_NEEDED=true
        exit 1
    }

    log_success "New containers deployed successfully"
}

# Function to run post-deployment tests
run_post_deployment_tests() {
    log_info "Running post-deployment health checks..."

    # Run mission critical tests
    if [ -f "$SCRIPT_DIR/test_mission_critical.py" ]; then
        log_info "Running mission critical test suite..."
        python3 "$SCRIPT_DIR/test_mission_critical.py" --quick || {
            log_error "Mission critical tests failed!"
            ROLLBACK_NEEDED=true
            exit 1
        }
        log_success "Mission critical tests passed"
    else
        log_warning "Mission critical test suite not found - skipping"
    fi

    # Basic health check
    if curl -s -f http://localhost:8000/health > /dev/null 2>&1; then
        log_success "FastAPI health check passed"
    else
        log_error "FastAPI health check failed"
        ROLLBACK_NEEDED=true
        exit 1
    fi
}

# Function to rollback on failure
rollback_deployment() {
    log_error "Rolling back deployment..."

    cd "$PROJECT_ROOT"

    # Restore .env
    if [ -f "$BACKUP_DIR/.env.backup" ]; then
        cp "$BACKUP_DIR/.env.backup" "$PROJECT_ROOT/.env"
        log_success "Restored .env file"
    fi

    # Restore code to previous commit
    PREVIOUS_COMMIT=$(cat "$BACKUP_DIR/git_commit.txt")
    git reset --hard "$PREVIOUS_COMMIT"
    log_success "Restored code to commit: $PREVIOUS_COMMIT"

    # Rebuild and restart services
    docker-compose build
    docker-compose up -d --force-recreate

    log_success "Rollback completed"
    log_error "Deployment failed - system restored to previous state"
    exit 1
}

# Function to reload Nginx
reload_nginx() {
    log_info "Reloading Nginx configuration..."

    # Test nginx configuration
    if nginx -t 2>&1 | grep -q "successful"; then
        systemctl reload nginx
        log_success "Nginx reloaded successfully"
    else
        log_warning "Nginx configuration test failed - skipping reload"
    fi
}

# Function to cleanup old Docker images
cleanup_old_images() {
    log_info "Cleaning up old Docker images..."

    # Remove dangling images
    docker image prune -f > /dev/null 2>&1 || true

    log_success "Cleanup completed"
}

################################################################################
# Main Deployment Flow
################################################################################

main() {
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║   Sidekick Forge - Production Deployment                  ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""

    # Confirmation
    log_warning "This will deploy the latest changes from GitHub to PRODUCTION"
    read -p "Continue with production deployment? (yes/no): " CONFIRM

    if [ "$CONFIRM" != "yes" ]; then
        log_error "Deployment cancelled by user"
        exit 0
    fi

    # Start deployment
    log_info "Starting deployment at $(date)"

    # Step 1: Create backup directory
    create_backup_dir

    # Redirect all output to log file
    exec > >(tee -a "$DEPLOYMENT_LOG")
    exec 2>&1

    # Step 2: Backup current state
    backup_current_state

    # Step 3: Preserve production environment
    preserve_production_env

    # Step 4: Pre-deployment health check
    pre_deployment_health_check

    # Step 5: Pull latest code
    pull_latest_code

    # Step 6: Restore production environment
    restore_production_env

    # Step 7: Apply Supabase migrations
    apply_supabase_migrations

    # Step 8: Rebuild Docker images
    rebuild_docker_images

    # Step 9: Deploy new containers
    deploy_new_containers

    # Step 10: Run post-deployment tests
    run_post_deployment_tests

    # Step 11: Reload Nginx
    reload_nginx

    # Step 12: Cleanup
    cleanup_old_images

    # Success
    echo ""
    log_success "╔════════════════════════════════════════════════════════════╗"
    log_success "║   Deployment Completed Successfully!                      ║"
    log_success "╚════════════════════════════════════════════════════════════╝"
    echo ""
    log_info "Deployment log: $DEPLOYMENT_LOG"
    log_info "Backup location: $BACKUP_DIR"
    log_info "Deployment completed at $(date)"
    echo ""
}

# Trap errors and rollback if needed
trap 'if [ "$ROLLBACK_NEEDED" = true ]; then rollback_deployment; fi' EXIT

# Run main deployment
main "$@"
