#!/bin/bash
################################################################################
# Sidekick Forge - Sync Production to Staging
# Run on: STAGING server
# Purpose: Pull latest code from GitHub (including any production hotfixes)
#          into the staging environment. Does NOT touch .env.
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

echo ""
echo "========================================"
echo "  Sidekick Forge - Sync from Production"
echo "  Branch:  $(git rev-parse --abbrev-ref HEAD)"
echo "  Current: $(git rev-parse --short HEAD)"
echo "========================================"
echo ""

warn "This will pull the latest code from GitHub into staging."
warn "Your .env will NOT be modified (it is gitignored)."
read -p "Continue? (yes/no): " CONFIRM
[[ "$CONFIRM" == "yes" ]] || { info "Cancelled."; exit 0; }

# ── Step 1: Stash local changes if any ───────────────────────────────────────
if ! git diff --quiet || ! git diff --cached --quiet; then
    warn "Stashing uncommitted changes..."
    git stash save "pre-sync-$(date +%Y%m%d_%H%M%S)"
    success "Changes stashed"
fi

# ── Step 2: Pull latest ─────────────────────────────────────────────────────
info "Fetching and pulling from origin..."
BEFORE=$(git rev-parse HEAD)
git fetch origin
git pull origin main

AFTER=$(git rev-parse HEAD)
if [ "$BEFORE" = "$AFTER" ]; then
    info "Already up to date."
else
    success "Updated: $(git rev-parse --short $BEFORE) -> $(git rev-parse --short $AFTER)"
    echo ""
    info "Changes pulled:"
    git log "${BEFORE}..${AFTER}" --pretty=format:"  - %s (%h)" --no-merges
    echo ""
fi

# ── Step 3: Make scripts executable ──────────────────────────────────────────
chmod +x "$PROJECT_ROOT/scripts"/*.sh 2>/dev/null || true

# ── Step 4: Rebuild and restart ──────────────────────────────────────────────
info "Rebuilding Docker images..."
docker compose build || { fail "Build failed -- check errors above."; exit 1; }

info "Restarting services..."
docker compose up -d || { fail "Restart failed -- check docker compose logs."; exit 1; }

# ── Step 5: Health check ────────────────────────────────────────────────────
info "Waiting for services..."
sleep 5
if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    success "Health check passed"
else
    warn "Health check failed -- services may still be starting"
    info "Check with: docker compose logs -f fastapi"
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "  Staging synced successfully"
echo "  Commit: $(git rev-parse --short HEAD)"
echo "========================================"
echo ""
