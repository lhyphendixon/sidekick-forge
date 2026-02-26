#!/bin/bash
################################################################################
# Sidekick Forge - Deploy to Production
# Run on: PRODUCTION server
# Purpose: Pull from GitHub, build, restart services, verify health
#
# .env is gitignored and NEVER touched by this script.
# Both staging and production keep their own .env files intact.
#
# Usage:
#   ./scripts/deploy_to_production.sh          # Pull latest from current branch
#   ./scripts/deploy_to_production.sh v2.9.9   # Checkout a specific tag
################################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail()    { echo -e "${RED}[FAIL]${NC} $1"; }

cd "$PROJECT_ROOT"

TARGET_TAG="${1:-}"
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
    docker compose build --quiet 2>/dev/null || true
    docker compose up -d 2>/dev/null || true
    fail "Rolled back to commit $(git rev-parse --short $PREVIOUS_COMMIT)"
    fail "Check logs at: $BACKUP_DIR/deploy.log"
}
trap rollback EXIT

# ── Banner ───────────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "  Sidekick Forge - Production Deploy"
echo "  Server:  $(hostname)"
echo "  Branch:  $CURRENT_BRANCH"
echo "  Current: $(git rev-parse --short HEAD)"
if [ -n "$TARGET_TAG" ]; then
echo "  Target:  $TARGET_TAG"
fi
echo "========================================"
echo ""

# Check AGENT_NAME is set
source "$PROJECT_ROOT/.env" 2>/dev/null || true
if [ -z "${AGENT_NAME:-}" ]; then
    warn "AGENT_NAME is not set in .env"
    warn "Add 'AGENT_NAME=sidekick-agent-production' to .env before deploying."
    read -p "Continue with default 'sidekick-agent'? (yes/no): " CONTINUE
    [[ "$CONTINUE" == "yes" ]] || { info "Cancelled."; DEPLOY_SUCCESS=true; exit 0; }
fi

warn "This will deploy to PRODUCTION."
read -p "Continue? (yes/no): " CONFIRM
[[ "$CONFIRM" == "yes" ]] || { info "Cancelled."; DEPLOY_SUCCESS=true; exit 0; }

# ── Step 1: Create backup + start logging ────────────────────────────────────
mkdir -p "$BACKUP_DIR"
echo "$PREVIOUS_COMMIT" > "$BACKUP_DIR/previous_commit.txt"
exec > >(tee -a "$BACKUP_DIR/deploy.log") 2>&1
info "Step 1/7: Backup created (commit: $(git rev-parse --short $PREVIOUS_COMMIT))"

# ── Step 2: Pull latest code ────────────────────────────────────────────────
info "Step 2/7: Pulling latest code..."
git fetch origin

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
fi

# ── Step 3: Stamp agent worker version ───────────────────────────────────────
info "Step 3/7: Stamping agent build version..."
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

# ── Step 4: Build Docker images ─────────────────────────────────────────────
info "Step 4/7: Building Docker images..."
docker compose build fastapi || { fail "FastAPI build failed"; exit 1; }
docker compose build agent-worker || { fail "Agent worker build failed"; exit 1; }
success "Images built"

# ── Step 5: Restart FastAPI + health check ───────────────────────────────────
info "Step 5/7: Restarting FastAPI..."
docker compose up -d --no-deps fastapi

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

# ── Step 6: Restart agent worker ─────────────────────────────────────────────
info "Step 6/7: Restarting agent worker..."
docker compose up -d --no-deps agent-worker

# Give agent worker time to register with LiveKit
sleep 8

# Verify agent worker is running
if docker compose ps agent-worker | grep -q "Up\|running"; then
    success "Agent worker is running"
    docker compose logs agent-worker --since 30s 2>&1 | grep -E "BUILD VERSION|registered worker" || true
else
    fail "Agent worker failed to start"
    exit 1
fi

# ── Step 7: Post-deploy verification ────────────────────────────────────────
info "Step 7/7: Post-deploy verification..."

if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    success "Health check: passed"
else
    fail "Health check: failed"
    exit 1
fi

# Run mission critical tests if available
TEST_FILE="$SCRIPT_DIR/test_mission_critical.py"
if [ -f "$TEST_FILE" ]; then
    info "Running quick tests..."
    python3 "$TEST_FILE" --quick || {
        fail "Post-deploy tests failed"
        exit 1
    }
    success "Post-deploy tests passed"
fi

# Cleanup dangling images
docker image prune -f >/dev/null 2>&1 || true

# ── Done ─────────────────────────────────────────────────────────────────────
DEPLOY_SUCCESS=true
echo ""
echo "========================================"
echo "  Deployment Successful"
echo "  Previous: $(git rev-parse --short $PREVIOUS_COMMIT)"
echo "  Current:  $(git rev-parse --short HEAD)"
echo "  Log:      $BACKUP_DIR/deploy.log"
echo "========================================"
echo ""
