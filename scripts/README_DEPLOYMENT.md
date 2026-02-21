# Deployment Scripts - README

This directory contains scripts for deploying Sidekick Forge from staging to production.

## Overview

The deployment system provides:
- ✅ Automated staging-to-production workflow
- ✅ Supabase schema migration support
- ✅ Production environment preservation
- ✅ Zero-downtime container updates
- ✅ Automatic rollback on failure
- ✅ Health check validation

## Scripts

### 1. `deploy_to_production.sh`
**Location**: Run on **PRODUCTION SERVER**

Main deployment script that pulls code from GitHub and deploys to production.

**Usage:**
```bash
cd /root/sidekick-forge
./scripts/deploy_to_production.sh
```

**What it does:**
1. Backs up current state (code, .env, config)
2. Preserves production environment variables
3. Pulls latest code from GitHub
4. Restores production credentials
5. Applies Supabase migrations (with confirmation)
6. Rebuilds Docker images
7. Deploys containers (zero-downtime)
8. Runs health checks and tests
9. Auto-rollback on failure

**Time:** ~3-5 minutes

---

### 2. `prepare_staging_release.sh`
**Location**: Run on **STAGING SERVER**

Prepares staging changes for production deployment.

**Usage:**
```bash
cd /root/sidekick-forge
./scripts/prepare_staging_release.sh
```

**What it does:**
1. Runs mission critical tests
2. Captures Supabase schema changes as migrations
3. Prompts for release version
4. Commits changes to Git
5. Creates Git tag
6. Pushes to GitHub
7. Displays release notes

**Output:** Release version tag (e.g., `v2.3.0`)

---

### 3. `supabase_migration_helper.sh`
**Location**: Run on **EITHER SERVER**

Utility for managing Supabase schema migrations.

**Commands:**

```bash
# Initialize migrations
./scripts/supabase_migration_helper.sh init

# Link to Supabase project
./scripts/supabase_migration_helper.sh link production
./scripts/supabase_migration_helper.sh link staging

# Create new migration
./scripts/supabase_migration_helper.sh create add_user_profiles

# Capture current schema changes
./scripts/supabase_migration_helper.sh capture

# Preview migrations (dry-run)
./scripts/supabase_migration_helper.sh preview

# Apply migrations
./scripts/supabase_migration_helper.sh apply

# List migrations
./scripts/supabase_migration_helper.sh list

# Check migration status
./scripts/supabase_migration_helper.sh status

# Help
./scripts/supabase_migration_helper.sh help
```

---

## Deployment Workflow

### Full Staging → Production Flow

**On Staging Server:**
```bash
# 1. Ensure tests pass
cd /root/sidekick-forge
python3 scripts/test_mission_critical.py

# 2. Prepare release
./scripts/prepare_staging_release.sh
# Enter version when prompted (e.g., v2.3.0)
```

**On Production Server:**
```bash
# 3. Deploy to production
cd /root/sidekick-forge
./scripts/deploy_to_production.sh
# Confirm Supabase migrations when prompted
```

**Verify:**
```bash
# Check health
curl http://localhost:8000/health

# Run tests
python3 scripts/test_mission_critical.py --quick

# View logs
docker-compose logs -f fastapi
```

---

## Environment Variables

### Required in `.env`

**For Deployment:**
```env
# Git repository (automatic from git remote)
# No configuration needed

# Supabase (for migrations)
SUPABASE_PROJECT_REF=your-project-ref
SUPABASE_ACCESS_TOKEN=your-access-token  # Optional
```

**Production-Specific (preserved during deployment):**
```env
DOMAIN_NAME=sidekickforge.com
DEVELOPMENT_MODE=false
SUPABASE_URL=https://production-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=production-key
SUPABASE_ANON_KEY=production-anon-key
LIVEKIT_URL=wss://production.livekit.cloud
LIVEKIT_API_KEY=production-key
LIVEKIT_API_SECRET=production-secret
```

---

## Backup and Rollback

### Automatic Backups

Every deployment creates a backup in:
```
/root/sidekick-forge/backups/YYYYMMDD_HHMMSS/
├── .env.backup              # Production .env
├── .env.pulled              # Pulled .env (before restore)
├── production_env_vars.txt  # Extracted production values
├── docker-compose.yml.backup
├── git_commit.txt           # Previous git commit hash
└── deployment.log           # Full deployment log
```

### Automatic Rollback

Deployment automatically rolls back if:
- Supabase migration fails
- Docker build fails
- Container startup fails
- Health check fails
- Mission critical tests fail

**Rollback actions:**
1. Restore `.env` from backup
2. Reset git to previous commit
3. Rebuild and restart containers

### Manual Rollback

```bash
# Find latest backup
ls -lt /root/sidekick-forge/backups/

# Restore from backup
BACKUP_DIR="/root/sidekick-forge/backups/20260221_143022"
cd /root/sidekick-forge

# Restore .env
cp "$BACKUP_DIR/.env.backup" .env

# Restore code
git reset --hard $(cat "$BACKUP_DIR/git_commit.txt")

# Rebuild and restart
docker-compose build
docker-compose up -d --force-recreate

# Verify
python3 scripts/test_mission_critical.py --quick
```

---

## Supabase Migrations

### Directory Structure

```
supabase/
├── config.toml                      # Supabase CLI config
└── migrations/                      # Version-controlled migrations
    ├── 20260221120000_initial_schema.sql
    ├── 20260221130000_add_user_profiles.sql
    └── 20260221140000_add_conversations.sql
```

### Migration Best Practices

1. **Keep migrations small** - One logical change per file
2. **Test on staging first** - Always apply to staging before production
3. **Use descriptive names** - `20260221_add_user_profiles.sql`
4. **Include IF NOT EXISTS** - Prevent errors on re-runs
5. **Never edit applied migrations** - Create new ones instead
6. **Preview before production** - Use `--dry-run`

### Example Migration

```sql
-- 20260221_add_user_preferences.sql

-- Add preferences column
ALTER TABLE users
ADD COLUMN IF NOT EXISTS preferences JSONB DEFAULT '{}'::jsonb;

-- Create index for faster queries
CREATE INDEX IF NOT EXISTS idx_users_preferences
ON users USING gin(preferences);

-- Update RLS policies
CREATE POLICY "Users can update their own preferences"
ON users FOR UPDATE
USING (auth.uid() = id)
WITH CHECK (auth.uid() = id);
```

---

## Troubleshooting

### Common Issues

**Deployment fails with "Cannot link to Supabase"**
- Ensure `SUPABASE_PROJECT_REF` is set in `.env`
- Get from: Supabase Dashboard → Settings → API

**Migration fails with "Column already exists"**
- Update migration to use `IF NOT EXISTS`
- Or create a new migration to handle the change

**Health check fails after deployment**
- Check logs: `docker-compose logs fastapi`
- System will auto-rollback
- Fix issue on staging and redeploy

**Git conflicts during pull**
- Deployment script stashes changes automatically
- Check deployment log for details

---

## Testing

### Mission Critical Tests

Run before and after deployment:

```bash
# Full test suite
python3 scripts/test_mission_critical.py

# Quick mode (faster, skips slow tests)
python3 scripts/test_mission_critical.py --quick

# Verbose output
python3 scripts/test_mission_critical.py --verbose
```

**Test Categories:**
- Health & Connectivity
- Client Management
- Agent Management
- LiveKit Integration
- Data Persistence
- API Key Synchronization
- RAG System

---

## Documentation

- **Quick Start**: [DEPLOYMENT_QUICKSTART.md](../DEPLOYMENT_QUICKSTART.md)
- **Full Guide**: [DEPLOYMENT.md](../DEPLOYMENT.md)
- **Project Guide**: [CLAUDE.md](../CLAUDE.md)

---

## Support

For issues:
1. Check deployment log: `backups/*/deployment.log`
2. Review application logs: `docker-compose logs`
3. Consult [DEPLOYMENT.md](../DEPLOYMENT.md) troubleshooting section
4. Contact development team

---

## Script Maintenance

### Adding New Environment Variables

If you add new production-specific environment variables that should be preserved:

1. Edit `deploy_to_production.sh`
2. Add to `preserve_production_env()` function
3. Add to `restore_production_env()` function

Example:
```bash
# In preserve_production_env()
NEW_VAR=$(grep -E "^NEW_VAR=" "$PROJECT_ROOT/.env" | cut -d '=' -f2- || echo "")

# In restore_production_env()
sed -i "s|^NEW_VAR=.*|NEW_VAR=$NEW_VAR|" "$PROJECT_ROOT/.env"
```

### Modifying Deployment Flow

The deployment flow is defined in `deploy_to_production.sh` `main()` function:

1. Backup
2. Preserve env
3. Pull code
4. Restore env
5. Migrations
6. Build
7. Deploy
8. Test
9. Cleanup

To add steps, insert into the `main()` function and create corresponding functions.

---

## Version History

### v1.0.0 (2026-02-21)
- Initial deployment automation
- Supabase migration support
- Zero-downtime deployment
- Automatic rollback
- Environment preservation
- Test integration

---

**Last Updated:** 2026-02-21
