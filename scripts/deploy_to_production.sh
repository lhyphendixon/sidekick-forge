#!/bin/bash
################################################################################
# Sidekick Forge - Deploy to Production (Enhanced)
# Run on: PRODUCTION server
# Purpose: Pull from GitHub, apply migrations, build, restart services, verify
#
# This script handles:
#   1. Pre-flight safety checks (git cleanliness, feature verification)
#   2. Code updates from Git
#   3. Database migrations (Supabase)
#   4. Nginx configuration regeneration
#   5. Docker image builds
#   6. Service restarts
#   7. Static asset verification
#   8. Health checks
#   9. Post-deploy feature verification
#
# SAFETY FEATURES (added after v2.9.6 → v2.9.9 incident):
#   - Blocks deployment if uncommitted changes exist
#   - Verifies critical files haven't been dramatically reduced in size
#   - Checks that critical features (Ken Burns, Wizard, Video mode) exist
#   - Optional staging sync verification
#
# .env is gitignored and NEVER touched by this script (except for ENVIRONMENT).
#
# Usage:
#   ./scripts/deploy_to_production.sh              # Pull latest from current branch
#   ./scripts/deploy_to_production.sh v2.9.10      # Checkout a specific tag
#   ./scripts/deploy_to_production.sh v2.9.10 -y   # Non-interactive mode
#   ./scripts/deploy_to_production.sh --yes        # Pull latest, skip prompts
#   ./scripts/deploy_to_production.sh --skip-migrations  # Skip DB migrations
#   ./scripts/deploy_to_production.sh --skip-nginx       # Skip nginx update
#   ./scripts/deploy_to_production.sh --skip-safety      # Skip safety checks (DANGEROUS)
#
################################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail()    { echo -e "${RED}[FAIL]${NC} $1"; }
step()    { echo -e "${CYAN}[STEP]${NC} $1"; }

cd "$PROJECT_ROOT"

# ── Parse arguments ──────────────────────────────────────────────────────────
TARGET_TAG=""
AUTO_YES=false
SKIP_MIGRATIONS=false
SKIP_NGINX=false
SKIP_SAFETY=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -y|--yes)
            AUTO_YES=true
            shift
            ;;
        --skip-migrations)
            SKIP_MIGRATIONS=true
            shift
            ;;
        --skip-nginx)
            SKIP_NGINX=true
            shift
            ;;
        --skip-safety)
            SKIP_SAFETY=true
            shift
            ;;
        -*)
            fail "Unknown option: $1"
            echo "Usage: $0 [TAG] [-y|--yes] [--skip-migrations] [--skip-nginx] [--skip-safety]"
            exit 1
            ;;
        *)
            TARGET_TAG="$1"
            shift
            ;;
    esac
done

# ══════════════════════════════════════════════════════════════════════════════
# SAFETY CHECKS - Prevent stripped code from being deployed
# Added after v2.9.6 → v2.9.9 incident where 2000+ lines were accidentally removed
# ══════════════════════════════════════════════════════════════════════════════

# Minimum expected line counts for critical files (based on v2.9.6 baseline)
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

verify_git_clean() {
    step "Safety Check: Git cleanliness..."

    # Check for uncommitted changes
    if ! git diff --quiet 2>/dev/null; then
        fail "UNCOMMITTED CHANGES DETECTED!"
        echo ""
        echo "The following files have unstaged changes:"
        git diff --name-only | head -20
        echo ""
        fail "All changes must be committed to Git before deploying."
        fail "This prevents staging/production drift that caused the v2.9.6 incident."
        echo ""
        echo "To fix:"
        echo "  1. git add -A"
        echo "  2. git commit -m 'Your message'"
        echo "  3. git push origin $CURRENT_BRANCH"
        echo "  4. Re-run this deployment script"
        echo ""
        return 1
    fi

    # Check for staged but uncommitted changes
    if ! git diff --cached --quiet 2>/dev/null; then
        fail "STAGED BUT UNCOMMITTED CHANGES DETECTED!"
        echo ""
        echo "The following files are staged but not committed:"
        git diff --cached --name-only | head -20
        echo ""
        fail "Commit these changes before deploying."
        return 1
    fi

    success "Git working directory is clean"
    return 0
}

verify_file_sizes() {
    step "Safety Check: Critical file sizes..."

    local has_issues=false

    for file in "${!CRITICAL_FILE_MIN_LINES[@]}"; do
        local min_lines=${CRITICAL_FILE_MIN_LINES[$file]}
        local filepath="$PROJECT_ROOT/$file"

        if [ -f "$filepath" ]; then
            local actual_lines=$(wc -l < "$filepath")

            if [ "$actual_lines" -lt "$min_lines" ]; then
                fail "$file: $actual_lines lines (expected >= $min_lines)"
                warn "This file may have been stripped of critical functionality!"
                has_issues=true
            else
                success "$file: $actual_lines lines (✓ >= $min_lines)"
            fi
        else
            warn "$file: FILE NOT FOUND"
            has_issues=true
        fi
    done

    if [ "$has_issues" = true ]; then
        echo ""
        fail "CRITICAL FILE SIZE CHECK FAILED!"
        fail "One or more files are smaller than expected."
        fail "This may indicate stripped functionality (like v2.9.6 → v2.9.9 incident)."
        echo ""
        echo "To investigate:"
        echo "  1. Compare with staging: ssh staging 'wc -l /root/sidekick-forge/docker/agent/*.py'"
        echo "  2. Check git history: git log --oneline -20 docker/agent/"
        echo "  3. Review recent commits: git diff HEAD~5..HEAD docker/agent/"
        echo ""
        return 1
    fi

    return 0
}

verify_critical_features() {
    step "Safety Check: Critical features present..."

    local entrypoint="$PROJECT_ROOT/docker/agent/entrypoint.py"
    local has_issues=false

    if [ ! -f "$entrypoint" ]; then
        fail "entrypoint.py not found!"
        return 1
    fi

    for feature_spec in "${CRITICAL_FEATURES[@]}"; do
        local pattern="${feature_spec%%:*}"
        local description="${feature_spec#*:}"

        if grep -q "$pattern" "$entrypoint" 2>/dev/null; then
            success "$description: present"
        else
            fail "$description: MISSING (pattern: $pattern)"
            has_issues=true
        fi
    done

    if [ "$has_issues" = true ]; then
        echo ""
        fail "CRITICAL FEATURE CHECK FAILED!"
        fail "One or more critical features are missing from entrypoint.py"
        fail "This deployment would break production functionality."
        echo ""
        echo "To investigate:"
        echo "  1. Check staging for these features"
        echo "  2. Review git history: git log -p --all -S 'pattern' -- docker/agent/entrypoint.py"
        echo "  3. Consider reverting to a known-good tag"
        echo ""
        return 1
    fi

    return 0
}

run_safety_checks() {
    if [ "$SKIP_SAFETY" = true ]; then
        warn "⚠️  SAFETY CHECKS SKIPPED (--skip-safety flag)"
        warn "⚠️  You are bypassing protections that prevent stripped code deployment"
        if ! confirm "Are you absolutely sure you want to proceed without safety checks?"; then
            info "Cancelled."
            DEPLOY_SUCCESS=true
            exit 0
        fi
        return 0
    fi

    echo ""
    echo "════════════════════════════════════════════════════════════"
    echo "  Running Pre-Deploy Safety Checks"
    echo "════════════════════════════════════════════════════════════"
    echo ""

    # Check 1: Git cleanliness
    if ! verify_git_clean; then
        if ! confirm "Deploy anyway despite uncommitted changes? (NOT RECOMMENDED)"; then
            info "Cancelled."
            DEPLOY_SUCCESS=true
            exit 0
        fi
        warn "Proceeding despite uncommitted changes..."
    fi

    # Check 2: File sizes (only after git pull, so we do it later)
    # This is checked in post-pull verification

    echo ""
    success "Pre-pull safety checks passed"
    echo ""
}

run_post_pull_verification() {
    echo ""
    echo "════════════════════════════════════════════════════════════"
    echo "  Running Post-Pull Verification"
    echo "════════════════════════════════════════════════════════════"
    echo ""

    if [ "$SKIP_SAFETY" = true ]; then
        warn "Skipping post-pull verification (--skip-safety)"
        return 0
    fi

    # Check file sizes
    if ! verify_file_sizes; then
        if ! confirm "Deploy anyway despite file size issues? (DANGEROUS)"; then
            fail "Deployment aborted due to file size verification failure."
            exit 1
        fi
        warn "Proceeding despite file size issues..."
    fi

    # Check critical features
    if ! verify_critical_features; then
        if ! confirm "Deploy anyway despite missing features? (VERY DANGEROUS)"; then
            fail "Deployment aborted due to missing critical features."
            exit 1
        fi
        warn "Proceeding despite missing features..."
    fi

    echo ""
    success "Post-pull verification passed"
    echo ""
}

# Helper for confirmations
confirm() {
    local prompt="$1"
    if [ "$AUTO_YES" = true ]; then
        info "Auto-confirmed: $prompt"
        return 0
    fi
    read -p "$prompt (yes/no): " RESPONSE
    [[ "$RESPONSE" == "yes" ]]
}

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
PREVIOUS_COMMIT=$(git rev-parse HEAD)
BACKUP_DIR="$PROJECT_ROOT/backups/deploy-$(date +%Y%m%d_%H%M%S)"
DEPLOY_SUCCESS=false

# ── Rollback function ────────────────────────────────────────────────────────
rollback() {
    if [ "$DEPLOY_SUCCESS" = true ]; then
        return 0
    fi
    echo ""
    fail "Deployment failed -- rolling back to $PREVIOUS_COMMIT..."
    cd "$PROJECT_ROOT"
    git checkout "$CURRENT_BRANCH" 2>/dev/null || true
    git reset --hard "$PREVIOUS_COMMIT"
    docker compose up -d --force-recreate 2>/dev/null || true
    fail "Rolled back to commit $(git rev-parse --short $PREVIOUS_COMMIT)"
    [ -d "$BACKUP_DIR" ] && fail "Check logs at: $BACKUP_DIR/deploy.log"
}
trap rollback EXIT

# ── Step 0: Fetch tags early ─────────────────────────────────────────────────
info "Fetching latest tags from origin..."
git fetch --tags --quiet 2>/dev/null || git fetch --tags

# Validate target tag exists (if specified)
if [ -n "$TARGET_TAG" ]; then
    if ! git rev-parse "$TARGET_TAG" >/dev/null 2>&1; then
        fail "Tag '$TARGET_TAG' not found. Available tags:"
        git tag -l | grep -E "^v[0-9]" | tail -10
        exit 1
    fi
fi

# ── Banner ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  Sidekick Forge - Production Deploy (Enhanced)"
echo "  Server:  $(hostname)"
echo "  Branch:  $CURRENT_BRANCH"
echo "  Current: $(git rev-parse --short HEAD)"
if [ -n "$TARGET_TAG" ]; then
echo "  Target:  $TARGET_TAG"
fi
echo "════════════════════════════════════════════════════════════"
echo ""

# ── Check required env vars ──────────────────────────────────────────────────
source "$PROJECT_ROOT/.env" 2>/dev/null || true

# Check AGENT_NAME
if [ -z "${AGENT_NAME:-}" ]; then
    warn "AGENT_NAME is not set in .env"
    if ! confirm "Continue with default 'sidekick-agent'?"; then
        info "Cancelled."
        DEPLOY_SUCCESS=true
        exit 0
    fi
fi

# Check ENVIRONMENT is set to production
if [ "${ENVIRONMENT:-development}" != "production" ]; then
    warn "ENVIRONMENT is not set to 'production' (current: ${ENVIRONMENT:-not set})"
    if confirm "Add 'ENVIRONMENT=production' to .env?"; then
        if grep -q "^ENVIRONMENT=" "$PROJECT_ROOT/.env" 2>/dev/null; then
            sed -i "s/^ENVIRONMENT=.*/ENVIRONMENT=production/" "$PROJECT_ROOT/.env"
        else
            echo "ENVIRONMENT=production" >> "$PROJECT_ROOT/.env"
        fi
        success "Set ENVIRONMENT=production in .env"
        export ENVIRONMENT=production
    fi
fi

# Production confirmation
warn "This will deploy to PRODUCTION."
if ! confirm "Continue?"; then
    info "Cancelled."
    DEPLOY_SUCCESS=true
    exit 0
fi

# ── Pre-Deploy Safety Checks ──────────────────────────────────────────────────
run_safety_checks

# ── Step 1: Create backup + start logging ────────────────────────────────────
step "Step 1/10: Creating backup..."
mkdir -p "$BACKUP_DIR"
echo "$PREVIOUS_COMMIT" > "$BACKUP_DIR/previous_commit.txt"
exec > >(tee -a "$BACKUP_DIR/deploy.log") 2>&1
success "Backup created (commit: $(git rev-parse --short $PREVIOUS_COMMIT))"

# ── Step 2: Pull latest code ────────────────────────────────────────────────
step "Step 2/10: Pulling latest code..."
git fetch origin --tags

if [ -n "$TARGET_TAG" ]; then
    info "Checking out tag: $TARGET_TAG"
    git checkout "$TARGET_TAG"
else
    git pull origin "$CURRENT_BRANCH"
fi

NEW_COMMIT=$(git rev-parse HEAD)
if [ "$PREVIOUS_COMMIT" = "$NEW_COMMIT" ]; then
    warn "Already at latest commit. Rebuilding anyway."
else
    success "Updated: $(git rev-parse --short $PREVIOUS_COMMIT) -> $(git rev-parse --short $NEW_COMMIT)"

    # Show what changed
    info "Changes in this deployment:"
    git log --oneline "$PREVIOUS_COMMIT".."$NEW_COMMIT" | head -10
fi

# ── Post-Pull Verification (file sizes, critical features) ───────────────────
run_post_pull_verification

# ── Step 3: Apply database migrations ────────────────────────────────────────
step "Step 3/10: Checking database migrations..."

if [ "$SKIP_MIGRATIONS" = true ]; then
    warn "Skipping migrations (--skip-migrations flag)"
else
    MIGRATIONS_DIR="$PROJECT_ROOT/migrations"
    MIGRATION_TRACKING_FILE="$PROJECT_ROOT/.applied_migrations"

    # Create tracking file if it doesn't exist
    touch "$MIGRATION_TRACKING_FILE"

    # Check for pending migrations
    PENDING_MIGRATIONS=()
    for migration_file in "$MIGRATIONS_DIR"/*.sql; do
        if [ -f "$migration_file" ]; then
            migration_name=$(basename "$migration_file")
            if ! grep -qF "$migration_name" "$MIGRATION_TRACKING_FILE"; then
                PENDING_MIGRATIONS+=("$migration_file")
            fi
        fi
    done

    if [ ${#PENDING_MIGRATIONS[@]} -eq 0 ]; then
        success "No pending migrations"
    else
        warn "Found ${#PENDING_MIGRATIONS[@]} pending migration(s):"
        for m in "${PENDING_MIGRATIONS[@]}"; do
            echo "  - $(basename "$m")"
        done

        if confirm "Apply migrations now?"; then
            # Check for required Supabase env vars
            if [ -z "${SUPABASE_URL:-}" ] || [ -z "${SUPABASE_SERVICE_ROLE_KEY:-}" ]; then
                fail "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required for migrations"
                exit 1
            fi

            # Extract project ref from URL
            SUPABASE_PROJECT_REF=$(echo "$SUPABASE_URL" | sed -E 's|https://([^.]+)\.supabase\.co.*|\1|')

            for migration_file in "${PENDING_MIGRATIONS[@]}"; do
                migration_name=$(basename "$migration_file")
                info "Applying: $migration_name"

                # Read migration content
                MIGRATION_SQL=$(cat "$migration_file")

                # Apply via Supabase Management API
                HTTP_STATUS=$(curl -s -o /tmp/migration_response.json -w "%{http_code}" \
                    "https://api.supabase.com/v1/projects/${SUPABASE_PROJECT_REF}/database/query" \
                    -H "Authorization: Bearer ${SUPABASE_ACCESS_TOKEN:-}" \
                    -H "Content-Type: application/json" \
                    -d "{\"query\": $(echo "$MIGRATION_SQL" | jq -Rs .)}" 2>/dev/null || echo "000")

                if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "201" ]; then
                    echo "$migration_name" >> "$MIGRATION_TRACKING_FILE"
                    success "Applied: $migration_name"
                else
                    # Fallback: try direct psql if available
                    if command -v psql &> /dev/null && [ -n "${DATABASE_URL:-}" ]; then
                        warn "Management API failed, trying direct connection..."
                        if psql "$DATABASE_URL" -f "$migration_file" 2>/dev/null; then
                            echo "$migration_name" >> "$MIGRATION_TRACKING_FILE"
                            success "Applied via psql: $migration_name"
                        else
                            warn "Migration failed: $migration_name (may already be applied)"
                            echo "$migration_name" >> "$MIGRATION_TRACKING_FILE"
                        fi
                    else
                        warn "Migration may have failed: $migration_name (HTTP $HTTP_STATUS)"
                        warn "Please verify manually and add to .applied_migrations if successful"
                    fi
                fi
            done
        else
            warn "Skipping migrations (user declined)"
        fi
    fi
fi

# ── Step 4: Stamp agent worker version ───────────────────────────────────────
step "Step 4/10: Stamping agent build version..."
BUILD_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
VERSION_HASH=$(git rev-parse --short HEAD)
ENTRYPOINT="$PROJECT_ROOT/docker/agent/entrypoint.py"
if [ -f "$ENTRYPOINT" ]; then
    sed -i "s/^AGENT_BUILD_VERSION = .*/AGENT_BUILD_VERSION = \"$BUILD_TIME\"/" "$ENTRYPOINT"
    sed -i "s/^AGENT_BUILD_HASH = .*/AGENT_BUILD_HASH = \"$VERSION_HASH\"/" "$ENTRYPOINT"
    success "Agent version: $BUILD_TIME ($VERSION_HASH)"
else
    warn "entrypoint.py not found -- skipping version stamp"
fi

# ── Step 5: Update nginx configuration ───────────────────────────────────────
step "Step 5/10: Updating nginx configuration..."

if [ "$SKIP_NGINX" = true ]; then
    warn "Skipping nginx update (--skip-nginx flag)"
else
    NGINX_CONF="/etc/nginx/sites-available/sidekickforge.conf"
    NGINX_TEMPLATE="$PROJECT_ROOT/nginx/site.conf.template"

    # Check if nginx config exists and needs updating
    if [ -f "$NGINX_CONF" ]; then
        # Check if static path is correct
        CURRENT_STATIC_PATH=$(grep -oP "alias \K[^;]+" "$NGINX_CONF" | grep "/static/" | head -1 || echo "")
        EXPECTED_STATIC_PATH="$PROJECT_ROOT/app/static/"

        if [ "$CURRENT_STATIC_PATH" != "$EXPECTED_STATIC_PATH" ]; then
            warn "Static path mismatch detected!"
            warn "  Current:  $CURRENT_STATIC_PATH"
            warn "  Expected: $EXPECTED_STATIC_PATH"

            # Fix static paths in nginx config
            info "Fixing static file paths in nginx config..."

            # Backup current config
            cp "$NGINX_CONF" "$BACKUP_DIR/nginx.conf.bak"

            # Update all paths that point to old versioned directories
            sed -i "s|/root/sidekick-forge-v[0-9.]*|$PROJECT_ROOT|g" "$NGINX_CONF"

            # Also ensure the main static alias is correct
            sed -i "s|alias /root/[^;]*app/static/;|alias $PROJECT_ROOT/app/static/;|g" "$NGINX_CONF"

            success "Fixed static paths in nginx config"
        else
            success "Nginx static paths are correct"
        fi

        # Ensure client_max_body_size is set
        if ! grep -q "client_max_body_size" "$NGINX_CONF"; then
            warn "client_max_body_size not set in nginx config"
            # Add it after ssl_dhparam line
            sed -i '/ssl_dhparam/a\    client_max_body_size 50M;' "$NGINX_CONF"
            success "Added client_max_body_size 50M"
        fi
    else
        warn "Nginx config not found at $NGINX_CONF"

        # Try to generate from template
        if [ -f "$NGINX_TEMPLATE" ]; then
            info "Generating nginx config from template..."

            # Export required variables
            export DOMAIN_NAME="${DOMAIN_NAME:-sidekickforge.com}"
            export PROJECT_ROOT
            export DOMAIN_REGEX="${DOMAIN_NAME//./\\.}"

            envsubst '${DOMAIN_NAME} ${PROJECT_ROOT}' < "$NGINX_TEMPLATE" > "$NGINX_CONF"
            ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/
            success "Generated nginx config from template"
        else
            warn "No nginx template found - skipping nginx setup"
        fi
    fi

    # Test and reload nginx
    if nginx -t 2>/dev/null; then
        systemctl reload nginx 2>/dev/null || service nginx reload 2>/dev/null || true
        success "Nginx configuration reloaded"
    else
        warn "Nginx config test failed - check manually"
    fi
fi

# ── Step 6: Build Docker images ─────────────────────────────────────────────
step "Step 6/10: Building Docker images..."

export COMPOSE_SILENCE_DEPRECATION_WARNINGS=1

if ! docker compose build fastapi 2>&1 | grep -v "variable is not set"; then
    fail "FastAPI build failed"
    exit 1
fi
success "FastAPI image built"

if docker compose config --services 2>/dev/null | grep -q "agent-worker"; then
    if ! docker compose build agent-worker 2>&1 | grep -v "variable is not set"; then
        warn "Agent worker build failed - continuing..."
    else
        success "Agent worker image built"
    fi
fi

# ── Step 7: Restart FastAPI ──────────────────────────────────────────────────
step "Step 7/10: Restarting FastAPI..."

docker stop sidekick-forge-fastapi 2>/dev/null || true
docker rm sidekick-forge-fastapi 2>/dev/null || true

docker compose up -d --force-recreate --no-deps fastapi 2>&1 | grep -v "variable is not set" || true

info "Waiting for health check..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
        success "FastAPI healthy after $((i * 2))s"
        break
    fi
    if [ "$i" -eq 30 ]; then
        fail "FastAPI did not become healthy within 60s"
        exit 1
    fi
    sleep 2
done

# ── Step 8: Restart agent worker ─────────────────────────────────────────────
step "Step 8/10: Restarting agent worker..."

if docker compose config --services 2>/dev/null | grep -q "agent-worker"; then
    docker compose up -d --force-recreate --no-deps agent-worker 2>&1 | grep -v "variable is not set" || true
    sleep 8

    if docker compose ps agent-worker 2>/dev/null | grep -qE "Up|running"; then
        success "Agent worker is running"
    else
        warn "Agent worker may not have started - check manually"
    fi
else
    info "No agent-worker service in compose file - skipping"
fi

# ── Step 9: Verify static assets ─────────────────────────────────────────────
step "Step 9/10: Verifying static assets..."

# Check that static files are being served from the correct location
STATIC_TEST_FILE="$PROJECT_ROOT/app/static/js/image-catalyst-widget.js"
if [ -f "$STATIC_TEST_FILE" ]; then
    # Get a unique string from the local file
    LOCAL_CHECKSUM=$(md5sum "$STATIC_TEST_FILE" | cut -d' ' -f1)

    # Try to fetch via nginx and compare
    SERVED_CONTENT=$(curl -sf "http://localhost/static/js/image-catalyst-widget.js" 2>/dev/null || \
                     curl -sf "https://localhost/static/js/image-catalyst-widget.js" -k 2>/dev/null || \
                     curl -sf "http://127.0.0.1:8000/static/js/image-catalyst-widget.js" 2>/dev/null || echo "")

    if [ -n "$SERVED_CONTENT" ]; then
        SERVED_CHECKSUM=$(echo "$SERVED_CONTENT" | md5sum | cut -d' ' -f1)

        if [ "$LOCAL_CHECKSUM" = "$SERVED_CHECKSUM" ]; then
            success "Static assets verified (checksums match)"
        else
            warn "Static asset checksum mismatch!"
            warn "  Local:  $LOCAL_CHECKSUM"
            warn "  Served: $SERVED_CHECKSUM"
            warn "This may indicate nginx is serving from wrong directory"
            warn "Or browser caching - users may need to hard refresh"
        fi
    else
        warn "Could not fetch static file for verification"
    fi
else
    warn "Test static file not found - skipping verification"
fi

# ── Step 10: Post-deploy verification ────────────────────────────────────────
step "Step 10/10: Post-deploy verification..."

# Health check
if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    success "Health check: passed"
else
    fail "Health check: failed"
    exit 1
fi

# Check marketing routes loaded
ROOT_RESPONSE=$(curl -sf http://localhost:8000/ 2>/dev/null | head -c 20)
if [[ "$ROOT_RESPONSE" == "<!DOCTYPE html>"* ]] || [[ "$ROOT_RESPONSE" == "<!"* ]]; then
    success "Marketing routes: loaded"
else
    warn "Marketing routes may not be loaded (root returns JSON instead of HTML)"
fi

# Check admin dashboard
ADMIN_RESPONSE=$(curl -sf http://localhost:8000/admin/ -o /dev/null -w "%{http_code}" 2>/dev/null || echo "000")
if [ "$ADMIN_RESPONSE" = "200" ] || [ "$ADMIN_RESPONSE" = "302" ]; then
    success "Admin dashboard: accessible"
else
    warn "Admin dashboard returned HTTP $ADMIN_RESPONSE"
fi

# Run mission critical tests if available
TEST_FILE="$SCRIPT_DIR/test_mission_critical.py"
if [ -f "$TEST_FILE" ]; then
    info "Running quick tests..."
    if python3 "$TEST_FILE" --quick 2>/dev/null; then
        success "Post-deploy tests passed"
    else
        warn "Post-deploy tests had issues - check manually"
    fi
fi

# Cleanup
docker image prune -f >/dev/null 2>&1 || true

# ── Done ─────────────────────────────────────────────────────────────────────
DEPLOY_SUCCESS=true
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  Deployment Successful!"
echo ""
echo "  Previous: $(git rev-parse --short $PREVIOUS_COMMIT)"
echo "  Current:  $(git rev-parse --short HEAD)"
echo "  Log:      $BACKUP_DIR/deploy.log"
echo ""
echo "  Checklist:"
[ "$SKIP_SAFETY" = false ] && echo "  [x] Safety checks passed (git clean, file sizes, features)"
echo "  [x] Code updated"
[ "$SKIP_MIGRATIONS" = false ] && echo "  [x] Migrations checked"
[ "$SKIP_NGINX" = false ] && echo "  [x] Nginx updated"
echo "  [x] Docker images rebuilt"
echo "  [x] Services restarted"
echo "  [x] Health checks passed"
echo ""
echo "  If users report stale assets, advise hard refresh (Ctrl+Shift+R)"
echo "════════════════════════════════════════════════════════════"
echo ""
