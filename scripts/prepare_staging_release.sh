#!/bin/bash
################################################################################
# Sidekick Forge - Prepare Staging Release
# Run on: STAGING server
# Purpose: Test, commit, tag, push to GitHub for production to pull
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

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
LATEST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0")

echo ""
echo "========================================"
echo "  Sidekick Forge - Prepare Release"
echo "  Branch:   $CURRENT_BRANCH"
echo "  Last tag: $LATEST_TAG"
echo "========================================"
echo ""

# ── Step 1: Pre-release tests ────────────────────────────────────────────────
info "Step 1/6: Running pre-release tests..."
TEST_FILE="$SCRIPT_DIR/test_mission_critical.py"
if [ -f "$TEST_FILE" ]; then
    python3 "$TEST_FILE" --quick || {
        fail "Tests failed. Fix issues before releasing."
        exit 1
    }
    success "Tests passed"
else
    warn "Test suite not found at $TEST_FILE"
    read -p "Continue without tests? (yes/no): " SKIP
    [[ "$SKIP" == "yes" ]] || { fail "Cancelled."; exit 1; }
fi

# ── Step 2: Capture Supabase schema (optional) ──────────────────────────────
info "Step 2/6: Checking for Supabase schema changes..."
if command -v supabase &>/dev/null; then
    source "$PROJECT_ROOT/.env" 2>/dev/null || true
    if [ -n "${SUPABASE_PROJECT_REF:-}" ]; then
        mkdir -p "$PROJECT_ROOT/supabase/migrations"
        MIGRATION_NAME="release_$(date +%Y%m%d_%H%M%S)"
        MIGRATION_FILE="$PROJECT_ROOT/supabase/migrations/${MIGRATION_NAME}.sql"

        supabase link --project-ref "$SUPABASE_PROJECT_REF" 2>/dev/null || true
        supabase db diff --linked > "$MIGRATION_FILE" 2>/dev/null || true

        if [ -s "$MIGRATION_FILE" ]; then
            info "Schema changes detected:"
            echo "----------------------------------------"
            head -20 "$MIGRATION_FILE"
            echo "----------------------------------------"
            read -p "Include this migration? (yes/no): " INCLUDE
            if [[ "$INCLUDE" == "yes" ]]; then
                success "Migration captured: ${MIGRATION_NAME}.sql"
            else
                rm -f "$MIGRATION_FILE"
                info "Migration excluded"
            fi
        else
            rm -f "$MIGRATION_FILE"
            info "No schema changes detected"
        fi
    else
        info "SUPABASE_PROJECT_REF not set -- skipping schema capture"
    fi
else
    info "Supabase CLI not installed -- skipping schema capture"
fi

# ── Step 3: Stage and commit ─────────────────────────────────────────────────
info "Step 3/6: Staging changes..."
echo ""
git status --short
echo ""

if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
    warn "No changes to commit. Proceeding to tag only."
    SKIP_COMMIT=true
else
    SKIP_COMMIT=false

    # Calculate suggested version for default commit message
    VERSION=${LATEST_TAG#v}
    MAJOR=$(echo "$VERSION" | cut -d. -f1)
    MINOR=$(echo "$VERSION" | cut -d. -f2)
    PATCH=$(echo "$VERSION" | cut -d. -f3)
    SUGGESTED="v${MAJOR}.${MINOR}.$((PATCH + 1))"

    read -p "Commit message (default: 'Release $SUGGESTED'): " COMMIT_MSG
    if [ -z "$COMMIT_MSG" ]; then
        COMMIT_MSG="Release $SUGGESTED"
    fi

    git add -A
    git commit -m "$COMMIT_MSG"
    success "Committed: $COMMIT_MSG"
fi

# ── Step 4: Tag ──────────────────────────────────────────────────────────────
info "Step 4/6: Creating release tag..."

# Recalculate from latest tag (may have changed if commit created a new one)
LATEST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0")
VERSION=${LATEST_TAG#v}
MAJOR=$(echo "$VERSION" | cut -d. -f1)
MINOR=$(echo "$VERSION" | cut -d. -f2)
PATCH=$(echo "$VERSION" | cut -d. -f3)
SUGGESTED="v${MAJOR}.${MINOR}.$((PATCH + 1))"

# If we skipped commit, HEAD might already be tagged
if [ "$SKIP_COMMIT" = true ] && git describe --exact-match HEAD 2>/dev/null; then
    EXISTING_TAG=$(git describe --exact-match HEAD 2>/dev/null)
    warn "HEAD is already tagged as $EXISTING_TAG"
    read -p "Create a new tag anyway? (yes/no): " RETAG
    [[ "$RETAG" == "yes" ]] || { info "Skipping tag. Pushing existing."; RELEASE_VERSION="$EXISTING_TAG"; }
fi

if [ -z "${RELEASE_VERSION:-}" ]; then
    read -p "Release version (default: $SUGGESTED): " RELEASE_VERSION
    RELEASE_VERSION="${RELEASE_VERSION:-$SUGGESTED}"
    [[ "$RELEASE_VERSION" =~ ^v ]] || RELEASE_VERSION="v${RELEASE_VERSION}"

    git tag -a "$RELEASE_VERSION" -m "Release $RELEASE_VERSION"
    success "Tagged: $RELEASE_VERSION"
fi

# ── Step 5: Push ─────────────────────────────────────────────────────────────
info "Step 5/6: Pushing to GitHub..."
git push origin "$CURRENT_BRANCH"
git push origin "$RELEASE_VERSION"
success "Pushed branch '$CURRENT_BRANCH' and tag '$RELEASE_VERSION' to origin"

# ── Step 6: Release summary ─────────────────────────────────────────────────
info "Step 6/6: Release summary"
echo ""
echo "========================================"
echo "  Release: $RELEASE_VERSION"
echo "  Date:    $(date +%Y-%m-%d)"
echo "  Commit:  $(git rev-parse --short HEAD)"
echo ""
echo "  Changes since $LATEST_TAG:"
git log "${LATEST_TAG}..HEAD" --pretty=format:"    - %s (%h)" --no-merges 2>/dev/null || echo "    (first release)"
echo ""
echo "========================================"
echo ""
info "Next: On production server, run:"
info "  cd /root/sidekick-forge && ./scripts/deploy_to_production.sh"
info ""
info "Or deploy a specific tag:"
info "  ./scripts/deploy_to_production.sh $RELEASE_VERSION"
echo ""
