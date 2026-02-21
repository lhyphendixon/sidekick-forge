# Sidekick Forge - Deployment Guide

Complete guide for deploying Sidekick Forge from staging to production.

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Deployment Architecture](#deployment-architecture)
4. [Staging Release Process](#staging-release-process)
5. [Production Deployment Process](#production-deployment-process)
6. [Supabase Schema Management](#supabase-schema-management)
7. [Rollback Procedures](#rollback-procedures)
8. [Troubleshooting](#troubleshooting)

---

## Overview

Sidekick Forge uses a streamlined deployment process that:
- ✅ Preserves production environment configuration
- ✅ Applies Supabase schema migrations safely
- ✅ Performs zero-downtime container updates
- ✅ Runs automated health checks
- ✅ Supports automatic rollback on failure

**Deployment Flow:**
```
Staging Server → GitHub → Production Server
```

---

## Prerequisites

### On Staging Server

1. **Git repository** configured and up-to-date
2. **Supabase CLI** installed (for schema migrations)
   ```bash
   npm install -g supabase
   ```
3. **Test suite** passing
4. **Environment variables** configured in `.env`

### On Production Server

1. **Git repository** cloned and configured
2. **Docker and Docker Compose** installed
3. **Nginx** configured with SSL certificates
4. **Production `.env`** file with production credentials
5. **Supabase CLI** installed (optional, for manual migrations)

### Required Environment Variables

**Staging `.env`:**
```env
# Staging Supabase Project
SUPABASE_URL=https://staging-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=staging-key
SUPABASE_ANON_KEY=staging-anon-key
SUPABASE_PROJECT_REF=staging-project-ref

# Staging LiveKit
LIVEKIT_URL=wss://staging.livekit.cloud
LIVEKIT_API_KEY=staging-key
LIVEKIT_API_SECRET=staging-secret

# Staging Domain
DOMAIN_NAME=staging.sidekickforge.com
DEVELOPMENT_MODE=true
```

**Production `.env`:**
```env
# Production Supabase Project
SUPABASE_URL=https://eukudpgfpihxsypulopm.supabase.co
SUPABASE_SERVICE_ROLE_KEY=production-key
SUPABASE_ANON_KEY=production-anon-key
SUPABASE_PROJECT_REF=production-project-ref

# Production LiveKit
LIVEKIT_URL=wss://litebridge-hw6srhvi.livekit.cloud
LIVEKIT_API_KEY=production-key
LIVEKIT_API_SECRET=production-secret

# Production Domain
DOMAIN_NAME=sidekickforge.com
DEVELOPMENT_MODE=false
```

---

## Deployment Architecture

### Directory Structure

```
/root/sidekick-forge/
├── app/                          # FastAPI application code
├── docker/                       # Docker configurations
│   └── agent/                   # Agent worker runtime
├── scripts/                      # Deployment scripts
│   ├── deploy_to_production.sh         # Production deployment (run on production)
│   ├── prepare_staging_release.sh      # Staging release prep (run on staging)
│   └── supabase_migration_helper.sh    # Supabase utilities
├── supabase/                     # Supabase configuration
│   ├── config.toml              # Supabase CLI config
│   └── migrations/              # Schema migrations (version controlled)
├── .env                          # Environment-specific configuration
└── docker-compose.yml            # Service definitions
```

### Services

1. **FastAPI** - Main API backend (port 8000)
2. **Agent Worker** - Stateless worker pool for AI agents
3. **Redis** - Caching and session storage
4. **Nginx** - Reverse proxy with SSL termination

---

## Staging Release Process

Run this process **on the staging server** to prepare changes for production.

### Step 1: Ensure Tests Pass

```bash
cd /root/sidekick-forge
python3 scripts/test_mission_critical.py
```

All tests must pass before proceeding.

### Step 2: Run Staging Release Script

```bash
cd /root/sidekick-forge
./scripts/prepare_staging_release.sh
```

This script will:
1. ✅ Run mission critical tests
2. ✅ Capture Supabase schema changes as migrations
3. ✅ Prompt for release version (e.g., `v2.3.0`)
4. ✅ Commit changes to Git
5. ✅ Create Git tag for the release
6. ✅ Push code and tag to GitHub
7. ✅ Display release notes

### Step 3: Review on GitHub

1. Go to your GitHub repository
2. Review the new tag and commits
3. Verify migration files in `supabase/migrations/`
4. Confirm everything looks correct

**You're now ready to deploy to production!**

---

## Production Deployment Process

Run this process **on the production server** to deploy the latest changes.

### Step 1: SSH to Production Server

```bash
ssh production
# or
ssh root@your-production-server.com
```

### Step 2: Navigate to Project Directory

```bash
cd /root/sidekick-forge
```

### Step 3: Run Production Deployment Script

```bash
./scripts/deploy_to_production.sh
```

### Step 4: Deployment Flow

The script will automatically:

1. **Backup Current State**
   - Creates timestamped backup in `backups/YYYYMMDD_HHMMSS/`
   - Backs up `.env`, `docker-compose.yml`, current git commit

2. **Preserve Production Environment**
   - Extracts production-specific values from `.env`
   - Stores: `DOMAIN_NAME`, `SUPABASE_URL`, `SUPABASE_*_KEY`, `LIVEKIT_*`, `DEVELOPMENT_MODE`

3. **Pull Latest Code**
   - Fetches from GitHub
   - Pulls latest commits for current branch

4. **Restore Production Environment**
   - Merges production-specific values back into `.env`
   - Ensures production credentials are never overwritten

5. **Apply Supabase Migrations** (if any)
   - Links to production Supabase project
   - Previews migrations with `--dry-run`
   - Prompts for confirmation
   - Applies migrations to production database

6. **Rebuild Docker Images**
   - Builds FastAPI image with latest code
   - Builds agent-worker image with latest code

7. **Deploy New Containers** (zero-downtime)
   - Restarts FastAPI service
   - Waits for health check
   - Restarts agent workers
   - Scales worker pool if needed

8. **Run Post-Deployment Tests**
   - Runs mission critical test suite (`--quick` mode)
   - Verifies all critical functionality works

9. **Reload Nginx**
   - Tests nginx configuration
   - Reloads nginx to pick up any changes

10. **Cleanup**
    - Removes dangling Docker images
    - Keeps system clean

### Step 5: Monitor Deployment

```bash
# Check container status
docker-compose ps

# View FastAPI logs
docker-compose logs -f fastapi

# View agent worker logs
docker-compose logs -f agent-worker

# Check health endpoint
curl http://localhost:8000/health
```

---

## Supabase Schema Management

Sidekick Forge uses **Supabase CLI migrations** for schema versioning.

### Migration Workflow

#### 1. Initialize Migrations (one-time setup)

```bash
cd /root/sidekick-forge
./scripts/supabase_migration_helper.sh init
```

#### 2. Link to Supabase Project

**On Staging:**
```bash
./scripts/supabase_migration_helper.sh link staging
```

**On Production:**
```bash
./scripts/supabase_migration_helper.sh link production
```

#### 3. Create a New Migration

**Option A: Manually create migration file**
```bash
./scripts/supabase_migration_helper.sh create add_user_profiles
```
Then edit the generated file in `supabase/migrations/`.

**Option B: Capture current schema changes**
```bash
./scripts/supabase_migration_helper.sh capture
```
This generates a migration from your current database schema.

#### 4. Preview Migrations (Dry-Run)

```bash
./scripts/supabase_migration_helper.sh preview
```

Shows exactly what SQL will be executed without applying changes.

#### 5. Apply Migrations

**On Staging (for testing):**
```bash
./scripts/supabase_migration_helper.sh apply
```

**On Production (via deployment script):**
Migrations are automatically applied during `deploy_to_production.sh`.

#### 6. List Migrations

```bash
./scripts/supabase_migration_helper.sh list
```

#### 7. Check Migration Status

```bash
./scripts/supabase_migration_helper.sh status
```

### Migration Best Practices

1. **Test migrations on staging first**
   - Always apply to staging Supabase project before production

2. **Keep migrations small and focused**
   - One logical change per migration
   - Easier to review and rollback

3. **Use descriptive names**
   - Good: `20260221_add_agent_context_table.sql`
   - Bad: `migration_1.sql`

4. **Never edit applied migrations**
   - Migrations are immutable once applied
   - Create a new migration to make changes

5. **Include rollback migrations for critical changes**
   - For destructive changes, create a reverse migration
   - Example: `20260221_drop_old_table.sql` and `20260221_restore_old_table.sql`

6. **Preview before production**
   - Always use `--dry-run` to preview migrations
   - Review SQL carefully for data loss risks

### Common Migration Operations

**Add a new table:**
```sql
-- 20260221_add_conversations_table.sql
CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES auth.users(id),
    agent_id UUID NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Add RLS policies
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view their own conversations"
    ON conversations FOR SELECT
    USING (auth.uid() = user_id);
```

**Add a column:**
```sql
-- 20260221_add_user_preferences.sql
ALTER TABLE users
ADD COLUMN preferences JSONB DEFAULT '{}'::jsonb;
```

**Create an index:**
```sql
-- 20260221_add_conversation_index.sql
CREATE INDEX IF NOT EXISTS idx_conversations_user_id
ON conversations(user_id);
```

---

## Rollback Procedures

If deployment fails, the script will **automatically rollback** using the backup.

### Automatic Rollback

The deployment script will rollback if:
- Supabase migration fails
- Docker build fails
- Health check fails after deployment
- Mission critical tests fail

**Rollback actions:**
1. Restore `.env` from backup
2. Reset git to previous commit
3. Rebuild and restart containers
4. Verify system is operational

### Manual Rollback

If you need to manually rollback:

#### 1. Identify Backup Directory

```bash
ls -lt /root/sidekick-forge/backups/
```

Look for the most recent deployment backup (e.g., `20260221_143022`).

#### 2. Restore Environment

```bash
BACKUP_DIR="/root/sidekick-forge/backups/20260221_143022"
cp "$BACKUP_DIR/.env.backup" /root/sidekick-forge/.env
```

#### 3. Restore Code

```bash
cd /root/sidekick-forge
PREVIOUS_COMMIT=$(cat "$BACKUP_DIR/git_commit.txt")
git reset --hard "$PREVIOUS_COMMIT"
```

#### 4. Rebuild and Restart

```bash
docker-compose build
docker-compose up -d --force-recreate
```

#### 5. Verify System

```bash
python3 scripts/test_mission_critical.py --quick
curl http://localhost:8000/health
```

### Rollback Supabase Migrations

**WARNING:** Supabase migrations cannot be automatically rolled back. You must create a reverse migration.

**Example:**

If this migration was applied:
```sql
-- 20260221_add_column.sql
ALTER TABLE users ADD COLUMN new_field TEXT;
```

Create a reverse migration:
```sql
-- 20260222_remove_column.sql
ALTER TABLE users DROP COLUMN new_field;
```

Then apply the reverse migration:
```bash
./scripts/supabase_migration_helper.sh apply
```

---

## Troubleshooting

### Deployment Script Errors

#### Error: "Failed to link to Supabase project"

**Cause:** Missing `SUPABASE_PROJECT_REF` in `.env`

**Solution:**
```bash
# Add to .env
SUPABASE_PROJECT_REF=your-project-ref
```

Get your project ref from Supabase Dashboard → Settings → API.

---

#### Error: "Migration dry-run failed"

**Cause:** Invalid SQL in migration file

**Solution:**
1. Review the migration file in `supabase/migrations/`
2. Test SQL manually in Supabase SQL Editor
3. Fix syntax errors
4. Commit and push fix
5. Re-run deployment

---

#### Error: "FastAPI failed to become healthy"

**Cause:** Application error preventing startup

**Solution:**
```bash
# Check FastAPI logs
docker-compose logs fastapi

# Common issues:
# - Missing environment variable
# - Database connection failure
# - Import error in Python code

# Fix the issue, then restart
docker-compose restart fastapi
```

---

#### Error: "Mission critical tests failed"

**Cause:** Deployment broke existing functionality

**Solution:**
1. Review test output for specific failures
2. Check logs: `docker-compose logs`
3. If critical, rollback deployment (automatic)
4. Fix issue on staging
5. Re-deploy when tests pass

---

### Supabase Migration Errors

#### Error: "Migration already applied"

**Cause:** Trying to apply a migration that already exists in the database

**Solution:**
```bash
# Check migration status
./scripts/supabase_migration_helper.sh status

# If migration is already applied, skip it
# Supabase tracks applied migrations in supabase_migrations table
```

---

#### Error: "Column already exists"

**Cause:** Migration tries to create a column that exists

**Solution:**
Update migration to use `IF NOT EXISTS`:
```sql
-- Before
ALTER TABLE users ADD COLUMN email TEXT;

-- After
ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT;
```

---

### Docker Errors

#### Error: "Port already in use"

**Cause:** Another service is using port 8000

**Solution:**
```bash
# Find process using port 8000
sudo lsof -i :8000

# Kill the process
sudo kill -9 <PID>

# Or change port in docker-compose.yml
```

---

#### Error: "Cannot connect to Docker daemon"

**Cause:** Docker service not running

**Solution:**
```bash
# Start Docker
sudo systemctl start docker

# Enable on boot
sudo systemctl enable docker
```

---

### Nginx Errors

#### Error: "nginx: configuration test failed"

**Cause:** Invalid nginx configuration

**Solution:**
```bash
# Test configuration
nginx -t

# Check error message
# Common issues:
# - Missing SSL certificate
# - Syntax error in site.conf
# - Invalid domain name

# Fix and regenerate
./scripts/generate-nginx-config.sh
nginx -t
```

---

### Git Errors

#### Error: "Cannot pull - uncommitted changes"

**Cause:** Local changes conflict with pull

**Solution:**
```bash
# Stash changes
git stash

# Pull latest
git pull

# Reapply changes (if needed)
git stash pop
```

---

## Environment-Specific Differences

### Staging vs Production

| Configuration | Staging | Production |
|--------------|---------|------------|
| Domain | `staging.sidekickforge.com` | `sidekickforge.com` |
| DEVELOPMENT_MODE | `true` | `false` |
| Supabase Project | Staging project | Production project |
| LiveKit | Staging instance | Production instance |
| SSL Certificates | Let's Encrypt (staging) | Let's Encrypt (production) |
| Logging Level | `DEBUG` | `INFO` |
| Error Reporting | Console only | Sentry + Console |

### Configuration Preservation

The deployment script **ALWAYS preserves** these production values:
- `DOMAIN_NAME`
- `DEVELOPMENT_MODE`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_ANON_KEY`
- `LIVEKIT_URL`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`

All other values can be updated from staging.

---

## Deployment Checklist

### Before Deployment

- [ ] All tests passing on staging
- [ ] Code committed and pushed to GitHub
- [ ] Migration files reviewed and tested
- [ ] Release notes documented
- [ ] Backup verified
- [ ] Production `.env` backed up
- [ ] Stakeholders notified

### During Deployment

- [ ] SSH to production server
- [ ] Run deployment script
- [ ] Review migration preview
- [ ] Confirm migration application
- [ ] Monitor container startup
- [ ] Watch health checks

### After Deployment

- [ ] Verify API health: `curl http://localhost:8000/health`
- [ ] Check container status: `docker-compose ps`
- [ ] Review logs: `docker-compose logs`
- [ ] Run smoke tests
- [ ] Monitor error rates
- [ ] Verify client functionality
- [ ] Update documentation if needed

---

## Quick Reference

### Common Commands

```bash
# Staging: Prepare release
cd /root/sidekick-forge
./scripts/prepare_staging_release.sh

# Production: Deploy
cd /root/sidekick-forge
./scripts/deploy_to_production.sh

# Check health
curl http://localhost:8000/health

# View logs
docker-compose logs -f fastapi
docker-compose logs -f agent-worker

# Restart services
docker-compose restart fastapi
docker-compose restart agent-worker

# Rebuild everything
docker-compose down
docker-compose build
docker-compose up -d

# Run tests
python3 scripts/test_mission_critical.py
python3 scripts/test_mission_critical.py --quick
```

### Supabase Commands

```bash
# Initialize
./scripts/supabase_migration_helper.sh init

# Capture schema
./scripts/supabase_migration_helper.sh capture

# Preview migrations
./scripts/supabase_migration_helper.sh preview

# Apply migrations
./scripts/supabase_migration_helper.sh apply

# List migrations
./scripts/supabase_migration_helper.sh list
```

---

## Support

For issues or questions:
1. Check this documentation
2. Review deployment logs in `backups/*/deployment.log`
3. Check application logs: `docker-compose logs`
4. Review GitHub issues
5. Contact the development team

---

## Changelog

### v1.0.0 (2026-02-21)
- Initial deployment automation
- Supabase migration support
- Zero-downtime deployment
- Automatic rollback on failure
- Environment preservation
- Mission critical test integration
