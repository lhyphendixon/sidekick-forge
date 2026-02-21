#!/bin/bash

################################################################################
# Sidekick Forge - Supabase Migration Helper
################################################################################
# Utilities for managing Supabase schema migrations across environments
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

# Function to check Supabase CLI installation
check_supabase_cli() {
    if ! command -v supabase &> /dev/null; then
        log_error "Supabase CLI is not installed"
        log_info "Install with: npm install -g supabase"
        exit 1
    fi
    log_success "Supabase CLI is installed"
}

# Function to initialize Supabase migrations
init_migrations() {
    log_info "Initializing Supabase migrations..."

    cd "$PROJECT_ROOT"

    # Check if already initialized
    if [ -f "$PROJECT_ROOT/supabase/config.toml" ]; then
        log_info "Supabase already initialized"
    else
        supabase init
        log_success "Supabase initialized"
    fi

    # Create migrations directory
    mkdir -p "$PROJECT_ROOT/supabase/migrations"
    log_success "Migrations directory ready"
}

# Function to link to Supabase project
link_to_project() {
    local ENV=$1  # staging or production

    log_info "Linking to $ENV Supabase project..."

    # Load environment variables
    if [ -f "$PROJECT_ROOT/.env" ]; then
        source "$PROJECT_ROOT/.env"
    else
        log_error ".env file not found"
        exit 1
    fi

    if [ -z "$SUPABASE_PROJECT_REF" ]; then
        log_error "SUPABASE_PROJECT_REF not set in .env"
        log_info "Add SUPABASE_PROJECT_REF=your-project-ref to .env"
        exit 1
    fi

    # Link to project
    supabase link --project-ref "$SUPABASE_PROJECT_REF" || {
        log_error "Failed to link to Supabase project"
        exit 1
    }

    log_success "Linked to $ENV project: $SUPABASE_PROJECT_REF"
}

# Function to create a new migration
create_migration() {
    local MIGRATION_NAME=$1

    if [ -z "$MIGRATION_NAME" ]; then
        log_error "Migration name required"
        log_info "Usage: $0 create <migration_name>"
        exit 1
    fi

    log_info "Creating new migration: $MIGRATION_NAME"

    cd "$PROJECT_ROOT"

    # Create migration file
    supabase migration new "$MIGRATION_NAME" || {
        log_error "Failed to create migration"
        exit 1
    }

    log_success "Migration created"
    log_info "Edit the migration file in supabase/migrations/"
}

# Function to capture current schema as migration
capture_schema() {
    log_info "Capturing current schema as migration..."

    cd "$PROJECT_ROOT"

    # Generate timestamp
    TIMESTAMP=$(date +%Y%m%d%H%M%S)
    MIGRATION_FILE="$PROJECT_ROOT/supabase/migrations/${TIMESTAMP}_schema_snapshot.sql"

    # Generate diff from linked database
    supabase db diff --linked > "$MIGRATION_FILE" || {
        log_error "Failed to capture schema"
        exit 1
    }

    # Check if migration has content
    if [ -s "$MIGRATION_FILE" ]; then
        log_success "Schema captured: ${TIMESTAMP}_schema_snapshot.sql"
        log_info "Preview:"
        echo "========================================"
        head -n 30 "$MIGRATION_FILE"
        echo "========================================"
    else
        rm "$MIGRATION_FILE"
        log_info "No schema changes detected"
    fi
}

# Function to preview migrations (dry-run)
preview_migrations() {
    log_info "Previewing migrations..."

    cd "$PROJECT_ROOT"

    # Run dry-run
    supabase db push --dry-run || {
        log_error "Preview failed"
        exit 1
    }

    log_success "Preview completed"
}

# Function to apply migrations
apply_migrations() {
    local CONFIRM=${1:-"prompt"}

    log_info "Applying migrations to database..."

    cd "$PROJECT_ROOT"

    # Show what will be applied
    log_info "Migrations to apply:"
    ls -1 "$PROJECT_ROOT/supabase/migrations"/*.sql 2>/dev/null || {
        log_info "No migration files found"
        return 0
    }

    # Preview first
    log_info "Previewing changes..."
    supabase db push --dry-run

    # Confirm
    if [ "$CONFIRM" == "prompt" ]; then
        echo ""
        log_warning "Ready to apply migrations to database"
        read -p "Continue? (yes/no): " APPLY
        if [ "$APPLY" != "yes" ]; then
            log_error "Migration cancelled"
            exit 1
        fi
    fi

    # Apply migrations
    supabase db push || {
        log_error "Failed to apply migrations"
        exit 1
    }

    log_success "Migrations applied successfully"
}

# Function to pull schema from remote
pull_schema() {
    log_info "Pulling schema from remote database..."

    cd "$PROJECT_ROOT"

    # Generate timestamp
    TIMESTAMP=$(date +%Y%m%d%H%M%S)

    # Pull schema
    supabase db pull || {
        log_error "Failed to pull schema"
        exit 1
    }

    log_success "Schema pulled from remote database"
}

# Function to list migrations
list_migrations() {
    log_info "Listing migrations..."

    cd "$PROJECT_ROOT"

    if [ -d "$PROJECT_ROOT/supabase/migrations" ]; then
        ls -lh "$PROJECT_ROOT/supabase/migrations"/*.sql 2>/dev/null || {
            log_info "No migration files found"
        }
    else
        log_info "Migrations directory does not exist"
    fi
}

# Function to show migration status
migration_status() {
    log_info "Checking migration status..."

    cd "$PROJECT_ROOT"

    # List migrations
    supabase migration list || {
        log_error "Failed to get migration status"
        exit 1
    }
}

# Function to reset local database
reset_local() {
    log_warning "This will reset your local database and apply all migrations"
    read -p "Continue? (yes/no): " CONFIRM

    if [ "$CONFIRM" != "yes" ]; then
        log_error "Reset cancelled"
        exit 1
    fi

    cd "$PROJECT_ROOT"

    # Reset database
    supabase db reset || {
        log_error "Failed to reset database"
        exit 1
    }

    log_success "Local database reset and migrations applied"
}

# Function to show help
show_help() {
    cat <<EOF
Sidekick Forge - Supabase Migration Helper

Usage: $0 <command> [options]

Commands:
    init                    Initialize Supabase migrations
    link <env>             Link to Supabase project (staging|production)
    create <name>          Create a new migration
    capture                Capture current schema as migration
    preview                Preview pending migrations (dry-run)
    apply                  Apply migrations to database
    pull                   Pull schema from remote database
    list                   List all migration files
    status                 Show migration status on linked database
    reset                  Reset local database and apply all migrations
    help                   Show this help message

Examples:
    $0 init
    $0 link staging
    $0 create add_user_profile_table
    $0 capture
    $0 preview
    $0 apply
    $0 list
    $0 status

Environment Variables:
    SUPABASE_PROJECT_REF   - Your Supabase project reference ID
    SUPABASE_ACCESS_TOKEN  - Your Supabase access token (optional)

EOF
}

################################################################################
# Main
################################################################################

main() {
    local COMMAND=${1:-"help"}

    case "$COMMAND" in
        init)
            check_supabase_cli
            init_migrations
            ;;
        link)
            check_supabase_cli
            link_to_project "${2:-staging}"
            ;;
        create)
            check_supabase_cli
            create_migration "$2"
            ;;
        capture)
            check_supabase_cli
            capture_schema
            ;;
        preview)
            check_supabase_cli
            preview_migrations
            ;;
        apply)
            check_supabase_cli
            apply_migrations "prompt"
            ;;
        pull)
            check_supabase_cli
            pull_schema
            ;;
        list)
            list_migrations
            ;;
        status)
            check_supabase_cli
            migration_status
            ;;
        reset)
            check_supabase_cli
            reset_local
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            log_error "Unknown command: $COMMAND"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
