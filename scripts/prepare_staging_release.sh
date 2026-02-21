#!/bin/bash

################################################################################
# Sidekick Forge - Staging Release Preparation Script
################################################################################
# This script prepares staging changes for production deployment by:
# - Running tests to ensure quality
# - Capturing Supabase schema changes as migrations
# - Committing code and migrations to Git
# - Pushing to GitHub
# - Tagging release version
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

# Function to run pre-release tests
run_pre_release_tests() {
    log_info "Running pre-release tests..."

    cd "$PROJECT_ROOT"

    # Run mission critical tests
    if [ -f "$SCRIPT_DIR/test_mission_critical.py" ]; then
        log_info "Running mission critical test suite..."
        python3 "$SCRIPT_DIR/test_mission_critical.py" || {
            log_error "Tests failed! Cannot prepare release."
            exit 1
        }
        log_success "All tests passed"
    else
        log_warning "Mission critical test suite not found - skipping tests"
        read -p "Continue without running tests? (yes/no): " SKIP_TESTS
        if [ "$SKIP_TESTS" != "yes" ]; then
            log_error "Release preparation cancelled"
            exit 1
        fi
    fi
}

# Function to capture Supabase schema changes
capture_supabase_schema() {
    log_info "Capturing Supabase schema changes..."

    cd "$PROJECT_ROOT"

    # Check if Supabase CLI is installed
    if ! command -v supabase &> /dev/null; then
        log_warning "Supabase CLI not installed"
        log_info "Install with: npm install -g supabase"
        log_warning "Skipping schema migration capture"
        read -p "Continue without capturing schema? (yes/no): " SKIP_SCHEMA
        if [ "$SKIP_SCHEMA" != "yes" ]; then
            log_error "Release preparation cancelled"
            exit 1
        fi
        return 0
    fi

    # Check if .env has Supabase configuration
    if [ ! -f "$PROJECT_ROOT/.env" ]; then
        log_error ".env file not found"
        exit 1
    fi

    source "$PROJECT_ROOT/.env"

    if [ -z "$SUPABASE_PROJECT_REF" ]; then
        log_warning "SUPABASE_PROJECT_REF not set in .env"
        log_warning "Cannot capture schema changes automatically"
        read -p "Continue without capturing schema? (yes/no): " SKIP_SCHEMA
        if [ "$SKIP_SCHEMA" != "yes" ]; then
            log_error "Release preparation cancelled"
            exit 1
        fi
        return 0
    fi

    # Create migrations directory if it doesn't exist
    mkdir -p "$PROJECT_ROOT/supabase/migrations"

    # Link to staging Supabase project
    log_info "Linking to staging Supabase project..."
    supabase link --project-ref "$SUPABASE_PROJECT_REF" || {
        log_error "Failed to link to Supabase project"
        exit 1
    }

    # Generate migration from current schema
    MIGRATION_NAME="staging_release_$(date +%Y%m%d_%H%M%S)"
    log_info "Generating migration: $MIGRATION_NAME"

    # Check if there are schema changes
    supabase db diff --linked > "$PROJECT_ROOT/supabase/migrations/${MIGRATION_NAME}.sql" 2>/dev/null || {
        # If diff command fails or produces empty file, no changes
        if [ ! -s "$PROJECT_ROOT/supabase/migrations/${MIGRATION_NAME}.sql" ]; then
            rm -f "$PROJECT_ROOT/supabase/migrations/${MIGRATION_NAME}.sql"
            log_info "No schema changes detected"
            return 0
        fi
    }

    # Check if migration file has actual changes
    if [ -s "$PROJECT_ROOT/supabase/migrations/${MIGRATION_NAME}.sql" ]; then
        log_success "Schema migration captured: ${MIGRATION_NAME}.sql"

        # Show migration preview
        log_info "Migration preview:"
        echo "----------------------------------------"
        head -n 20 "$PROJECT_ROOT/supabase/migrations/${MIGRATION_NAME}.sql"
        echo "----------------------------------------"

        read -p "Include this migration in the release? (yes/no): " INCLUDE_MIGRATION
        if [ "$INCLUDE_MIGRATION" != "yes" ]; then
            rm "$PROJECT_ROOT/supabase/migrations/${MIGRATION_NAME}.sql"
            log_warning "Migration excluded from release"
        fi
    else
        rm -f "$PROJECT_ROOT/supabase/migrations/${MIGRATION_NAME}.sql"
        log_info "No schema changes detected"
    fi
}

# Function to get release version
get_release_version() {
    log_info "Determining release version..."

    cd "$PROJECT_ROOT"

    # Get latest tag
    LATEST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0")
    log_info "Latest tag: $LATEST_TAG"

    # Extract version components
    VERSION=${LATEST_TAG#v}
    MAJOR=$(echo "$VERSION" | cut -d. -f1)
    MINOR=$(echo "$VERSION" | cut -d. -f2)
    PATCH=$(echo "$VERSION" | cut -d. -f3)

    # Increment patch version by default
    NEW_PATCH=$((PATCH + 1))
    SUGGESTED_VERSION="v${MAJOR}.${MINOR}.${NEW_PATCH}"

    log_info "Suggested version: $SUGGESTED_VERSION"
    read -p "Enter release version (default: $SUGGESTED_VERSION): " RELEASE_VERSION

    if [ -z "$RELEASE_VERSION" ]; then
        RELEASE_VERSION="$SUGGESTED_VERSION"
    fi

    # Ensure version starts with 'v'
    if [[ ! "$RELEASE_VERSION" =~ ^v ]]; then
        RELEASE_VERSION="v${RELEASE_VERSION}"
    fi

    log_success "Release version: $RELEASE_VERSION"
    echo "$RELEASE_VERSION"
}

# Function to commit changes
commit_changes() {
    local VERSION=$1
    log_info "Committing changes..."

    cd "$PROJECT_ROOT"

    # Check for uncommitted changes
    if git diff --quiet && git diff --cached --quiet; then
        log_warning "No changes to commit"
        return 0
    fi

    # Show status
    log_info "Git status:"
    git status --short

    # Get commit message
    echo ""
    log_info "Enter commit message for this release:"
    read -p "> " COMMIT_MESSAGE

    if [ -z "$COMMIT_MESSAGE" ]; then
        COMMIT_MESSAGE="Release $VERSION"
    fi

    # Stage all changes
    git add .

    # Commit
    git commit -m "$COMMIT_MESSAGE" || {
        log_error "Failed to commit changes"
        exit 1
    }

    log_success "Changes committed"
}

# Function to tag release
tag_release() {
    local VERSION=$1
    log_info "Tagging release $VERSION..."

    cd "$PROJECT_ROOT"

    # Create annotated tag
    git tag -a "$VERSION" -m "Release $VERSION - Prepared for production deployment" || {
        log_error "Failed to create tag"
        exit 1
    }

    log_success "Tagged as $VERSION"
}

# Function to push to GitHub
push_to_github() {
    local VERSION=$1
    log_info "Pushing to GitHub..."

    cd "$PROJECT_ROOT"

    # Get current branch
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
    log_info "Current branch: $CURRENT_BRANCH"

    # Push commits
    git push origin "$CURRENT_BRANCH" || {
        log_error "Failed to push commits"
        exit 1
    }

    # Push tags
    git push origin "$VERSION" || {
        log_error "Failed to push tag"
        exit 1
    }

    log_success "Pushed to GitHub"
}

# Function to generate release notes
generate_release_notes() {
    local VERSION=$1
    log_info "Generating release notes..."

    cd "$PROJECT_ROOT"

    # Get commits since last tag
    LATEST_TAG=$(git describe --tags --abbrev=0 HEAD^ 2>/dev/null || echo "")

    if [ -z "$LATEST_TAG" ]; then
        COMMIT_RANGE="HEAD"
    else
        COMMIT_RANGE="${LATEST_TAG}..HEAD"
    fi

    log_info "Release notes for $VERSION:"
    echo "========================================"
    echo "Version: $VERSION"
    echo "Date: $(date +%Y-%m-%d)"
    echo ""
    echo "Changes:"
    git log "$COMMIT_RANGE" --pretty=format:"- %s (%h)" --no-merges
    echo ""
    echo "========================================"
}

################################################################################
# Main Flow
################################################################################

main() {
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║   Sidekick Forge - Staging Release Preparation            ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""

    log_info "This script will prepare staging changes for production deployment"
    log_info "Location: $PROJECT_ROOT"
    echo ""

    # Step 1: Run pre-release tests
    run_pre_release_tests

    # Step 2: Capture Supabase schema changes
    capture_supabase_schema

    # Step 3: Get release version
    RELEASE_VERSION=$(get_release_version)

    # Step 4: Commit changes
    commit_changes "$RELEASE_VERSION"

    # Step 5: Tag release
    tag_release "$RELEASE_VERSION"

    # Step 6: Push to GitHub
    push_to_github "$RELEASE_VERSION"

    # Step 7: Generate release notes
    generate_release_notes "$RELEASE_VERSION"

    # Success
    echo ""
    log_success "╔════════════════════════════════════════════════════════════╗"
    log_success "║   Staging Release Prepared Successfully!                  ║"
    log_success "╚════════════════════════════════════════════════════════════╝"
    echo ""
    log_info "Release version: $RELEASE_VERSION"
    log_info "Changes pushed to GitHub"
    log_info ""
    log_info "Next steps:"
    log_info "1. Review changes on GitHub"
    log_info "2. Run deployment on production server:"
    log_info "   ssh production 'cd /root/sidekick-forge && ./scripts/deploy_to_production.sh'"
    echo ""
}

# Run main flow
main "$@"
