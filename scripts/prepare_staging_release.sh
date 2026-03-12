#!/bin/bash
################################################################################
# Sidekick Forge - Prepare Staging Release
# Run on: STAGING server
# Purpose: Test, commit, tag, push to GitHub for production to pull
#
# Safety features:
#   - Creates backup tag of current state before any changes
#   - Detects destructive file shrinkage (>30% line loss in critical files)
#   - Shows file-level diff summary for review before committing
#   - Warns on deleted files
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
info "Step 1/8: Running pre-release tests..."
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

# ── Step 2: Backup tag ──────────────────────────────────────────────────────
info "Step 2/8: Creating backup tag of current state..."
BACKUP_TAG="backup/pre-release-$(date +%Y%m%d_%H%M%S)"
git tag "$BACKUP_TAG" -m "Pre-release backup before preparing next release"
success "Backup tag created: $BACKUP_TAG"
info "  To rollback if needed: git checkout $BACKUP_TAG"

# ── Step 3: Destructive change detection ────────────────────────────────────
info "Step 3/8: Scanning for destructive changes..."

# Critical files/directories to monitor for unexpected shrinkage
CRITICAL_PATHS=(
    "docker/agent/entrypoint.py"
    "docker/agent/tool_registry.py"
    "docker/agent/sidekick_agent.py"
    "app/agent_modules/"
    "app/services/"
    "app/api/"
    "app/templates/"
    "app/static/js/"
    "app/static/css/"
)

SHRINK_THRESHOLD=30  # Warn if a file loses more than 30% of its lines
WARNINGS_FOUND=false

# Check for deleted files (tracked files that no longer exist)
DELETED_FILES=$(git diff --name-status HEAD 2>/dev/null | grep "^D" | awk '{print $2}' || true)
if [ -n "$DELETED_FILES" ]; then
    echo ""
    warn "⚠️  FILES BEING DELETED:"
    echo "$DELETED_FILES" | while read -r f; do
        OLD_LINES=$(git show HEAD:"$f" 2>/dev/null | wc -l || echo "?")
        echo -e "  ${RED}✗${NC} $f ($OLD_LINES lines removed)"
    done
    echo ""
    WARNINGS_FOUND=true
fi

# Check for significant shrinkage in critical files
for CPATH in "${CRITICAL_PATHS[@]}"; do
    # Get modified files under this path
    MODIFIED=$(git diff --name-only HEAD -- "$CPATH" 2>/dev/null || true)
    [ -z "$MODIFIED" ] && continue

    while IFS= read -r FILE; do
        [ -z "$FILE" ] && continue
        # Skip deleted files (already handled above)
        [ ! -f "$FILE" ] && continue

        OLD_LINES=$(git show HEAD:"$FILE" 2>/dev/null | wc -l 2>/dev/null || echo "0")
        NEW_LINES=$(wc -l < "$FILE" 2>/dev/null || echo "0")

        if [ "$OLD_LINES" -gt 50 ] && [ "$NEW_LINES" -gt 0 ]; then
            LOST=$((OLD_LINES - NEW_LINES))
            if [ "$LOST" -gt 0 ]; then
                PCT=$((LOST * 100 / OLD_LINES))
                if [ "$PCT" -ge "$SHRINK_THRESHOLD" ]; then
                    warn "⚠️  $FILE shrank by ${PCT}% ($OLD_LINES → $NEW_LINES lines, -$LOST)"
                    WARNINGS_FOUND=true
                fi
            fi
        fi
    done <<< "$MODIFIED"
done

if [ "$WARNINGS_FOUND" = true ]; then
    echo ""
    echo -e "${RED}════════════════════════════════════════════════════════════${NC}"
    echo -e "${RED}  DESTRUCTIVE CHANGES DETECTED${NC}"
    echo -e "${RED}  Review the warnings above carefully.${NC}"
    echo -e "${RED}  Large file shrinkage often means code was accidentally${NC}"
    echo -e "${RED}  overwritten by a less complete version.${NC}"
    echo -e "${RED}════════════════════════════════════════════════════════════${NC}"
    echo ""
    read -p "Continue despite warnings? (yes/no): " CONTINUE_ANYWAY
    [[ "$CONTINUE_ANYWAY" == "yes" ]] || { fail "Cancelled. Backup tag: $BACKUP_TAG"; exit 1; }
else
    success "No destructive changes detected"
fi

# ── Step 4: Capture Supabase schema (optional) ──────────────────────────────
info "Step 4/8: Checking for Supabase schema changes..."
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

# ── Step 5: Diff summary ───────────────────────────────────────────────────
info "Step 5/8: Change summary..."
echo ""

# Show compact diff stats
STAT_OUTPUT=$(git diff --stat HEAD 2>/dev/null || true)
UNTRACKED=$(git ls-files --others --exclude-standard 2>/dev/null || true)

if [ -n "$STAT_OUTPUT" ] || [ -n "$UNTRACKED" ]; then
    if [ -n "$STAT_OUTPUT" ]; then
        echo "$STAT_OUTPUT"
    fi
    if [ -n "$UNTRACKED" ]; then
        echo ""
        info "New files:"
        echo "$UNTRACKED" | while read -r f; do
            LINES=$(wc -l < "$f" 2>/dev/null || echo "?")
            echo "  + $f ($LINES lines)"
        done
    fi
    echo ""
fi

# ── Step 6: Stage and commit ─────────────────────────────────────────────────
info "Step 6/8: Staging changes..."

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

# ── Step 7: Tag ──────────────────────────────────────────────────────────────
info "Step 7/8: Creating release tag..."

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

# ── Step 8: Push ─────────────────────────────────────────────────────────────
info "Step 8/8: Pushing to GitHub..."
git push origin "$CURRENT_BRANCH"
git push origin "$RELEASE_VERSION"
success "Pushed branch '$CURRENT_BRANCH' and tag '$RELEASE_VERSION' to origin"

# ── Release summary ─────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "  Release: $RELEASE_VERSION"
echo "  Date:    $(date +%Y-%m-%d)"
echo "  Commit:  $(git rev-parse --short HEAD)"
echo "  Backup:  $BACKUP_TAG"
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
info ""
info "To rollback staging if something is wrong:"
info "  git checkout $BACKUP_TAG"
echo ""
