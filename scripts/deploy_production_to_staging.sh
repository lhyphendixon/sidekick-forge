#!/bin/bash
################################################################################
# Sidekick Forge - Deploy Production Code to Staging
# Run on: STAGING server
# Purpose: Safely pull production-tested code from GitHub to staging
#          with database isolation verification to prevent data loss
################################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail()    { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }
header()  { echo -e "\n${CYAN}════════════════════════════════════════${NC}"; echo -e "${CYAN}  $1${NC}"; echo -e "${CYAN}════════════════════════════════════════${NC}\n"; }

# Command line flags
SKIP_SAFETY=${SKIP_SAFETY:-false}
SHOW_CREDS=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-safety)
            SKIP_SAFETY=true
            shift
            ;;
        --show-credentials|--creds)
            SHOW_CREDS=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Deploy production code to staging environment safely."
            echo ""
            echo "Options:"
            echo "  --show-credentials  Show staging Supabase credentials for .env configuration"
            echo "  --skip-safety       Skip safety checks (DANGEROUS - use with caution)"
            echo "  -h, --help          Show this help message"
            echo ""
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# If --show-credentials was passed, just show them and exit
if [ "$SHOW_CREDS" = true ]; then
    show_staging_credentials() {
        echo ""
        echo "════════════════════════════════════════════════════════════════════"
        echo "  STAGING SUPABASE BRANCH CREDENTIALS"
        echo "════════════════════════════════════════════════════════════════════"
        echo ""
        echo "Add these to your staging .env file:"
        echo ""
        echo "SUPABASE_URL=https://senzircaknleviasihav.supabase.co"
        echo "SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNlbnppcmNha25sZXZpYXNpaGF2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjkxMDY3NzksImV4cCI6MjA4NDY4Mjc3OX0.vD-l1caDv5Gv6zCD1-sgGeR5HYPumFXz1RYUFb7QKUU"
        echo "SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNlbnppcmNha25sZXZpYXNpaGF2Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2OTEwNjc3OSwiZXhwIjoyMDg0NjgyNzc5fQ.kIWw3LXbznZLk0dMUA4_i4s4R2y5GQbnqmpHIUDSMJk"
        echo ""
        echo "════════════════════════════════════════════════════════════════════"
        echo ""
    }
    show_staging_credentials
    exit 0
fi

# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════
# Production database (MUST NEVER be used on staging)
PRODUCTION_SUPABASE_REF="eukudpgfpihxsypulopm"

# Staging database (Supabase branch of production project)
# Branch: "staging" - Created 2026-01-22, Status: FUNCTIONS_DEPLOYED
STAGING_SUPABASE_REF="senzircaknleviasihav"
STAGING_SUPABASE_URL="https://senzircaknleviasihav.supabase.co"

# ═══════════════════════════════════════════════════════════════════════════════
# STAGING SUPABASE CREDENTIALS (for reference/auto-configuration)
# These are the API keys for the staging branch - can be used to auto-configure
# ═══════════════════════════════════════════════════════════════════════════════
STAGING_ANON_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNlbnppcmNha25sZXZpYXNpaGF2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjkxMDY3NzksImV4cCI6MjA4NDY4Mjc3OX0.vD-l1caDv5Gv6zCD1-sgGeR5HYPumFXz1RYUFb7QKUU"
STAGING_SERVICE_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNlbnppcmNha25sZXZpYXNpaGF2Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2OTEwNjc3OSwiZXhwIjoyMDg0NjgyNzc5fQ.kIWw3LXbznZLk0dMUA4_i4s4R2y5GQbnqmpHIUDSMJk"

# ═══════════════════════════════════════════════════════════════════════════════
# Critical file minimum line counts (from v2.9.6 baseline)
# ═══════════════════════════════════════════════════════════════════════════════
declare -A CRITICAL_FILE_MIN_LINES=(
    ["docker/agent/entrypoint.py"]=4000
    ["docker/agent/tool_registry.py"]=2000
    ["docker/agent/sidekick_agent.py"]=1100
    ["docker/agent/context.py"]=1000
)

# Critical features that must exist in entrypoint.py
CRITICAL_FEATURES=(
    "kenburns:Ken Burns image generation"
    "WizardGuideAgent:Wizard onboarding mode"
    "bithuman:Video/Avatar mode"
    "is_glm_model:GLM reasoning toggle"
)

# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY CHECK: Verify staging uses isolated database (staging Supabase branch)
# ═══════════════════════════════════════════════════════════════════════════════
verify_database_isolation() {
    header "Database Isolation Check"

    local env_file="$PROJECT_ROOT/.env"

    if [ ! -f "$env_file" ]; then
        fail "CRITICAL: .env file not found at $env_file"
    fi

    # Extract SUPABASE_URL from .env
    local current_supabase_url
    current_supabase_url=$(grep -E "^SUPABASE_URL=" "$env_file" | cut -d'=' -f2- | tr -d '"' | tr -d "'" || echo "")

    if [ -z "$current_supabase_url" ]; then
        fail "CRITICAL: SUPABASE_URL not found in .env"
    fi

    info "Current Supabase URL: $current_supabase_url"
    info "Expected Staging URL: $STAGING_SUPABASE_URL"
    info "Production URL (FORBIDDEN): https://${PRODUCTION_SUPABASE_REF}.supabase.co"

    # Check if using production database (FATAL)
    if [[ "$current_supabase_url" == *"$PRODUCTION_SUPABASE_REF"* ]]; then
        echo ""
        echo -e "${RED}╔══════════════════════════════════════════════════════════════════╗${NC}"
        echo -e "${RED}║  CRITICAL ERROR: STAGING IS USING PRODUCTION DATABASE!           ║${NC}"
        echo -e "${RED}╠══════════════════════════════════════════════════════════════════╣${NC}"
        echo -e "${RED}║  Current .env contains production Supabase URL.                  ║${NC}"
        echo -e "${RED}║  Deploying would risk corrupting production data.                ║${NC}"
        echo -e "${RED}║                                                                  ║${NC}"
        echo -e "${RED}║  REQUIRED ACTION:                                                ║${NC}"
        echo -e "${RED}║  Update .env with staging Supabase branch credentials:           ║${NC}"
        echo -e "${RED}║                                                                  ║${NC}"
        echo -e "${RED}║  SUPABASE_URL=$STAGING_SUPABASE_URL${NC}"
        echo -e "${RED}║                                                                  ║${NC}"
        echo -e "${RED}║  Then re-run this deployment script.                             ║${NC}"
        echo -e "${RED}╚══════════════════════════════════════════════════════════════════╝${NC}"
        echo ""
        fail "Database isolation check failed. Deployment aborted."
    fi

    # Check if using correct staging branch
    if [[ "$current_supabase_url" == *"$STAGING_SUPABASE_REF"* ]]; then
        success "Database isolation verified - using staging Supabase branch"
    else
        warn "Supabase URL doesn't match expected staging branch ($STAGING_SUPABASE_REF)"
        warn "Current URL: $current_supabase_url"
        echo ""
        read -p "Continue with this database? (yes/no): " DB_CONFIRM
        [[ "$DB_CONFIRM" == "yes" ]] || fail "Deployment cancelled - verify database configuration."
    fi
}

# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY CHECK: Verify file sizes after pull
# ═══════════════════════════════════════════════════════════════════════════════
verify_file_sizes() {
    header "File Size Verification"

    local all_passed=true

    for file in "${!CRITICAL_FILE_MIN_LINES[@]}"; do
        local min_lines=${CRITICAL_FILE_MIN_LINES[$file]}
        local full_path="$PROJECT_ROOT/$file"

        if [ -f "$full_path" ]; then
            local actual_lines
            actual_lines=$(wc -l < "$full_path")

            if [ "$actual_lines" -lt "$min_lines" ]; then
                fail "STRIPPED FILE DETECTED: $file has $actual_lines lines (minimum: $min_lines)"
                all_passed=false
            else
                success "$file: $actual_lines lines (minimum: $min_lines)"
            fi
        else
            warn "File not found: $file"
        fi
    done

    if [ "$all_passed" = false ]; then
        fail "File size verification failed. Code may have been stripped."
    fi

    success "All critical files have expected line counts"
}

# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY CHECK: Verify critical features exist
# ═══════════════════════════════════════════════════════════════════════════════
verify_critical_features() {
    header "Critical Feature Verification"

    local entrypoint="$PROJECT_ROOT/docker/agent/entrypoint.py"
    local all_passed=true

    if [ ! -f "$entrypoint" ]; then
        fail "entrypoint.py not found!"
    fi

    for feature_entry in "${CRITICAL_FEATURES[@]}"; do
        local pattern="${feature_entry%%:*}"
        local description="${feature_entry#*:}"

        if grep -q "$pattern" "$entrypoint"; then
            success "Found: $description ($pattern)"
        else
            echo -e "${RED}[MISSING]${NC} $description ($pattern)"
            all_passed=false
        fi
    done

    if [ "$all_passed" = false ]; then
        fail "Critical feature verification failed. Features may have been stripped."
    fi

    success "All critical features present"
}

# ═══════════════════════════════════════════════════════════════════════════════
# Helper: Show staging credentials for manual configuration
# ═══════════════════════════════════════════════════════════════════════════════
show_staging_credentials() {
    echo ""
    echo -e "${CYAN}════════════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  STAGING SUPABASE BRANCH CREDENTIALS                               ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo "Add these to your staging .env file:"
    echo ""
    echo "SUPABASE_URL=$STAGING_SUPABASE_URL"
    echo "SUPABASE_ANON_KEY=$STAGING_ANON_KEY"
    echo "SUPABASE_SERVICE_ROLE_KEY=$STAGING_SERVICE_KEY"
    echo ""
    echo -e "${CYAN}════════════════════════════════════════════════════════════════════${NC}"
    echo ""
}

# ═══════════════════════════════════════════════════════════════════════════════
# Create backup before deployment
# ═══════════════════════════════════════════════════════════════════════════════
create_backup() {
    header "Creating Pre-Deployment Backup"

    local backup_dir="$PROJECT_ROOT/backups/pre-staging-deploy-$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$backup_dir"

    # Backup critical files
    local files_to_backup=(
        "docker/agent/entrypoint.py"
        "docker/agent/sidekick_agent.py"
        "docker/agent/context.py"
        "docker/agent/tool_registry.py"
        "app/main.py"
    )

    for file in "${files_to_backup[@]}"; do
        if [ -f "$PROJECT_ROOT/$file" ]; then
            local target_dir="$backup_dir/$(dirname "$file")"
            mkdir -p "$target_dir"
            cp "$PROJECT_ROOT/$file" "$target_dir/"
        fi
    done

    # Record current state
    echo "Backup created: $(date)" > "$backup_dir/backup_info.txt"
    echo "Git commit: $(git rev-parse HEAD)" >> "$backup_dir/backup_info.txt"
    echo "Git branch: $(git rev-parse --abbrev-ref HEAD)" >> "$backup_dir/backup_info.txt"

    success "Backup created at: $backup_dir"
    echo "$backup_dir"
}

# ═══════════════════════════════════════════════════════════════════════════════
# Main deployment logic
# ═══════════════════════════════════════════════════════════════════════════════
main() {
    cd "$PROJECT_ROOT"

    echo ""
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║     Sidekick Forge - Deploy Production Code to Staging          ║"
    echo "╠══════════════════════════════════════════════════════════════════╣"
    echo "║  Branch:  $(git rev-parse --abbrev-ref HEAD | head -c 50)$(printf '%*s' $((50 - $(git rev-parse --abbrev-ref HEAD | wc -c))) '')  ║"
    echo "║  Current: $(git rev-parse --short HEAD)                                              ║"
    echo "║  Date:    $(date +%Y-%m-%d\ %H:%M:%S)                                    ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo ""

    # ── Pre-deployment safety checks ──────────────────────────────────────────
    if [ "$SKIP_SAFETY" = true ]; then
        warn "SKIP_SAFETY=true - Bypassing safety checks (USE WITH EXTREME CAUTION)"
    else
        # CRITICAL: Database isolation check
        verify_database_isolation
    fi

    # ── Confirmation ──────────────────────────────────────────────────────────
    echo ""
    warn "This will pull the latest production code from GitHub."
    warn "Your staging .env will NOT be modified (it is gitignored)."
    echo ""
    read -p "Continue with deployment? (yes/no): " CONFIRM
    [[ "$CONFIRM" == "yes" ]] || { info "Deployment cancelled."; exit 0; }

    # ── Create backup ─────────────────────────────────────────────────────────
    BACKUP_DIR=$(create_backup)

    # ── Handle uncommitted changes ────────────────────────────────────────────
    header "Git Status Check"

    if ! git diff --quiet || ! git diff --cached --quiet; then
        warn "Uncommitted changes detected:"
        git status --short
        echo ""
        read -p "Stash these changes? (yes/no): " STASH_CONFIRM
        if [[ "$STASH_CONFIRM" == "yes" ]]; then
            git stash save "pre-staging-deploy-$(date +%Y%m%d_%H%M%S)"
            success "Changes stashed"
        else
            fail "Cannot proceed with uncommitted changes. Commit or stash them first."
        fi
    else
        success "Working directory clean"
    fi

    # ── Pull latest code ──────────────────────────────────────────────────────
    header "Pulling Production Code"

    BEFORE=$(git rev-parse HEAD)
    info "Fetching from origin..."
    git fetch origin

    info "Pulling main branch..."
    git pull origin main

    AFTER=$(git rev-parse HEAD)

    if [ "$BEFORE" = "$AFTER" ]; then
        info "Already up to date with production"
    else
        success "Updated: $(git rev-parse --short $BEFORE) -> $(git rev-parse --short $AFTER)"
        echo ""
        info "Changes pulled:"
        git log "${BEFORE}..${AFTER}" --pretty=format:"  - %s (%h)" --no-merges | head -20
        echo ""
    fi

    # ── Post-pull verification ────────────────────────────────────────────────
    if [ "$SKIP_SAFETY" != true ]; then
        verify_file_sizes
        verify_critical_features
    fi

    # ── Make scripts executable ───────────────────────────────────────────────
    chmod +x "$PROJECT_ROOT/scripts"/*.sh 2>/dev/null || true

    # ── Rebuild Docker images ─────────────────────────────────────────────────
    header "Rebuilding Docker Images"

    info "Building Docker images..."
    docker compose build || {
        fail "Docker build failed. Check errors above. Backup at: $BACKUP_DIR"
    }

    # ── Restart services ──────────────────────────────────────────────────────
    header "Restarting Services"

    info "Stopping existing services..."
    docker compose down

    info "Starting services..."
    docker compose up -d || {
        fail "Docker startup failed. Backup at: $BACKUP_DIR"
    }

    # ── Health check ──────────────────────────────────────────────────────────
    header "Health Verification"

    info "Waiting for services to initialize..."
    sleep 8

    if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
        success "FastAPI health check passed"
    else
        warn "FastAPI health check failed - services may still be starting"
        info "Check logs: docker compose logs -f fastapi"
    fi

    # Check agent worker registration
    if docker compose logs agent-worker 2>&1 | tail -20 | grep -q "registered"; then
        success "Agent worker registered successfully"
    else
        warn "Agent worker may still be starting"
        info "Check logs: docker compose logs -f agent-worker"
    fi

    # ── Summary ───────────────────────────────────────────────────────────────
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║         Staging Deployment Complete                              ║"
    echo "╠══════════════════════════════════════════════════════════════════╣"
    echo "║  Commit:  $(git rev-parse --short HEAD)                                              ║"
    echo "║  Branch:  $(git rev-parse --abbrev-ref HEAD | head -c 50)$(printf '%*s' $((50 - $(git rev-parse --abbrev-ref HEAD | wc -c))) '')  ║"
    echo "║  Backup:  $(basename "$BACKUP_DIR")      ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo ""
    info "Staging is now running production code with staging database."
    info ""
    info "Useful commands:"
    info "  docker compose logs -f fastapi      # View API logs"
    info "  docker compose logs -f agent-worker # View agent logs"
    info "  curl http://localhost:8000/health   # Health check"
    echo ""
}

# Run main
main "$@"
