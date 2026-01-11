#!/bin/bash
# ============================================================
# Sidekick Forge Environment-Based Deployment Script
# ============================================================
# Deploys staging or production using Supabase Branching
#
# Usage:
#   ./scripts/deploy-env.sh staging    - Deploy staging (uses Supabase branch)
#   ./scripts/deploy-env.sh production - Deploy production (uses main Supabase)
#   ./scripts/deploy-env.sh stop       - Stop all containers
#   ./scripts/deploy-env.sh status     - Show running containers
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_env_file() {
    local env_file=$1
    if [ ! -f "$env_file" ]; then
        log_error "Environment file not found: $env_file"
        log_info "Please copy the template and fill in credentials:"
        log_info "  cp ${env_file}.template $env_file"
        exit 1
    fi
}

deploy_staging() {
    log_info "Deploying STAGING environment..."
    log_info "This uses the Supabase STAGING BRANCH for database isolation."

    check_env_file ".env.staging"

    # Stop any existing containers
    log_info "Stopping existing containers..."
    docker compose -f docker-compose.yml -f docker-compose.staging.yml down 2>/dev/null || true

    # Build and start staging
    log_info "Building and starting staging containers..."
    docker compose -f docker-compose.yml -f docker-compose.staging.yml up -d --build

    log_success "Staging environment deployed!"
    echo ""
    echo "=================================================="
    echo "  STAGING DEPLOYMENT COMPLETE"
    echo "=================================================="
    echo "  API:        https://staging.sidekickforge.com"
    echo "  Supabase:   Staging Branch (isolated from production)"
    echo "  Agent:      sidekick-agent-staging"
    echo ""
    echo "  View logs:  docker compose -f docker-compose.yml -f docker-compose.staging.yml logs -f"
    echo "=================================================="
}

deploy_production() {
    log_info "Deploying PRODUCTION environment..."
    log_warning "This uses the MAIN Supabase project with real client data."

    check_env_file ".env.production"

    echo ""
    log_warning "=========================================="
    log_warning "  WARNING: PRODUCTION DEPLOYMENT"
    log_warning "=========================================="
    log_warning "  This will deploy to production with:"
    log_warning "  - Real client data"
    log_warning "  - Live payment processing"
    log_warning "  - app.sidekickforge.com domain"
    log_warning "=========================================="
    echo ""
    read -p "Type 'deploy production' to confirm: " confirm

    if [ "$confirm" != "deploy production" ]; then
        log_info "Deployment cancelled."
        exit 0
    fi

    # Stop any existing containers
    log_info "Stopping existing containers..."
    docker compose -f docker-compose.yml -f docker-compose.production.yml down 2>/dev/null || true

    # Build and start production
    log_info "Building and starting production containers..."
    docker compose -f docker-compose.yml -f docker-compose.production.yml up -d --build

    log_success "Production environment deployed!"
    echo ""
    echo "=================================================="
    echo "  PRODUCTION DEPLOYMENT COMPLETE"
    echo "=================================================="
    echo "  API:        https://app.sidekickforge.com"
    echo "  Supabase:   Main Project (production)"
    echo "  Agent:      sidekick-agent-production"
    echo ""
    echo "  View logs:  docker compose -f docker-compose.yml -f docker-compose.production.yml logs -f"
    echo "=================================================="
}

stop_all() {
    log_info "Stopping all containers..."

    docker compose -f docker-compose.yml -f docker-compose.staging.yml down 2>/dev/null || true
    docker compose -f docker-compose.yml -f docker-compose.production.yml down 2>/dev/null || true
    docker compose down 2>/dev/null || true

    log_success "All containers stopped."
}

show_status() {
    echo ""
    log_info "Container Status:"
    echo "=================================================="
    docker ps --filter "name=sidekick-forge" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" || echo "No containers running"
    echo ""

    log_info "Network Status:"
    echo "=================================================="
    docker network ls --filter "name=sidekick-forge" --format "table {{.Name}}\t{{.Driver}}" || echo "No networks found"
    echo ""
}

show_help() {
    echo ""
    echo "Sidekick Forge Environment Deployment"
    echo "======================================"
    echo ""
    echo "This script deploys Sidekick Forge using Supabase Branching"
    echo "to keep staging and production databases separate."
    echo ""
    echo "Usage: $0 {staging|production|stop|status}"
    echo ""
    echo "Commands:"
    echo "  staging    - Deploy to staging environment"
    echo "               Uses Supabase BRANCH for isolated testing"
    echo "               Domain: staging.sidekickforge.com"
    echo ""
    echo "  production - Deploy to production environment"
    echo "               Uses MAIN Supabase project with real data"
    echo "               Domain: app.sidekickforge.com"
    echo ""
    echo "  stop       - Stop all running containers"
    echo ""
    echo "  status     - Show status of running containers"
    echo ""
    echo "Setup Steps:"
    echo "  1. Create a staging branch in Supabase Dashboard"
    echo "  2. Copy .env.staging.template to .env.staging"
    echo "  3. Fill in staging branch credentials"
    echo "  4. Run: $0 staging"
    echo ""
    echo "For production:"
    echo "  1. Copy .env.production.template to .env.production"
    echo "  2. Fill in production credentials"
    echo "  3. Run: $0 production"
    echo ""
}

# Main command handler
case "$1" in
    staging)
        deploy_staging
        ;;
    production)
        deploy_production
        ;;
    stop)
        stop_all
        ;;
    status)
        show_status
        ;;
    -h|--help|help)
        show_help
        ;;
    *)
        show_help
        exit 1
        ;;
esac
