# Setting Up Deployment System on Staging Server

This guide is for setting up the deployment automation on your **staging server** after it was developed on production.

## Overview

You need to transfer these files from production to staging:
- Deployment scripts
- Documentation
- Supabase configuration template

## Files to Transfer

### 1. Deployment Scripts (from `/root/sidekick-forge/scripts/`)

```bash
scripts/
├── deploy_to_production.sh           # Not used on staging, but good to have
├── prepare_staging_release.sh        # ← PRIMARY SCRIPT for staging
├── supabase_migration_helper.sh      # ← Helper utilities
└── README_DEPLOYMENT.md              # Scripts documentation
```

### 2. Documentation (from `/root/sidekick-forge/`)

```bash
DEPLOYMENT.md                         # Complete deployment guide
DEPLOYMENT_QUICKSTART.md              # Quick reference
STAGING_SETUP.md                      # This file
```

### 3. Update CLAUDE.md

The updated `CLAUDE.md` file with deployment section added.

---

## Transfer Methods

### Option 1: Via Git (Recommended)

**On Production Server:**
```bash
cd /root/sidekick-forge

# Add all deployment files to git
git add scripts/deploy_to_production.sh
git add scripts/prepare_staging_release.sh
git add scripts/supabase_migration_helper.sh
git add scripts/README_DEPLOYMENT.md
git add DEPLOYMENT.md
git add DEPLOYMENT_QUICKSTART.md
git add STAGING_SETUP.md
git add CLAUDE.md

# Commit
git commit -m "Add deployment automation system with Supabase branching support"

# Push to GitHub
git push origin main
```

**On Staging Server:**
```bash
cd /root/sidekick-forge

# Pull latest changes
git pull origin main

# Make scripts executable
chmod +x scripts/deploy_to_production.sh
chmod +x scripts/prepare_staging_release.sh
chmod +x scripts/supabase_migration_helper.sh
```

---

### Option 2: Via SCP (Alternative)

**From Production to Staging:**
```bash
# Set variables
STAGING_SERVER="user@staging-server.com"
STAGING_PATH="/root/sidekick-forge"

# Transfer scripts
scp /root/sidekick-forge/scripts/deploy_to_production.sh \
    /root/sidekick-forge/scripts/prepare_staging_release.sh \
    /root/sidekick-forge/scripts/supabase_migration_helper.sh \
    /root/sidekick-forge/scripts/README_DEPLOYMENT.md \
    $STAGING_SERVER:$STAGING_PATH/scripts/

# Transfer documentation
scp /root/sidekick-forge/DEPLOYMENT*.md \
    /root/sidekick-forge/STAGING_SETUP.md \
    /root/sidekick-forge/CLAUDE.md \
    $STAGING_SERVER:$STAGING_PATH/

# SSH to staging and make executable
ssh $STAGING_SERVER "cd $STAGING_PATH && chmod +x scripts/*.sh"
```

---

### Option 3: Create Transfer Package

**On Production Server:**
```bash
cd /root/sidekick-forge

# Create package
tar -czf deployment-system.tar.gz \
    scripts/deploy_to_production.sh \
    scripts/prepare_staging_release.sh \
    scripts/supabase_migration_helper.sh \
    scripts/README_DEPLOYMENT.md \
    DEPLOYMENT.md \
    DEPLOYMENT_QUICKSTART.md \
    STAGING_SETUP.md \
    CLAUDE.md

# Transfer to staging (via scp, rsync, or download)
scp deployment-system.tar.gz user@staging-server.com:/root/
```

**On Staging Server:**
```bash
cd /root
tar -xzf deployment-system.tar.gz -C /root/sidekick-forge/

# Make scripts executable
chmod +x /root/sidekick-forge/scripts/*.sh
```

---

## Staging Server Setup

Once files are transferred, set up the staging environment:

### 1. Install Supabase CLI

```bash
# Install Node.js if not already installed
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Install Supabase CLI globally
npm install -g supabase

# Verify installation
supabase --version
```

### 2. Configure Staging Environment Variables

Edit `/root/sidekick-forge/.env` on staging:

```bash
# Staging Supabase Project (different from production!)
SUPABASE_URL=https://staging-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=staging-service-role-key
SUPABASE_ANON_KEY=staging-anon-key
SUPABASE_PROJECT_REF=staging-project-ref           # ← REQUIRED for migrations

# Staging LiveKit (different from production!)
LIVEKIT_URL=wss://staging.livekit.cloud
LIVEKIT_API_KEY=staging-api-key
LIVEKIT_API_SECRET=staging-api-secret

# Staging Domain
DOMAIN_NAME=staging.sidekickforge.com

# Development mode
DEVELOPMENT_MODE=true

# Staging agent names (different from production!)
AGENT_NAME=sidekick-agent-staging
LIVEKIT_AGENT_NAME=sidekick-agent-staging-docker
```

**CRITICAL**: Get `SUPABASE_PROJECT_REF` from:
- Supabase Dashboard → Your Staging Project → Settings → API
- Look for "Project Reference ID" or "Project Ref"

### 3. Initialize Supabase Migrations

```bash
cd /root/sidekick-forge

# Initialize Supabase (if not already done)
./scripts/supabase_migration_helper.sh init

# Link to staging Supabase project
./scripts/supabase_migration_helper.sh link staging

# Verify link
supabase status
```

### 4. Create Migrations Directory

```bash
# Create migrations directory if it doesn't exist
mkdir -p /root/sidekick-forge/supabase/migrations

# Add to git (if using git)
touch /root/sidekick-forge/supabase/migrations/.gitkeep
git add /root/sidekick-forge/supabase/migrations/.gitkeep
git commit -m "Initialize migrations directory"
```

### 5. Verify Test Suite Works

```bash
cd /root/sidekick-forge

# Run mission critical tests
python3 scripts/test_mission_critical.py --quick

# Should see tests pass (or at least run without errors)
```

---

## Staging Workflow

### First Time: Capture Current Schema

If your staging database already has a schema, capture it as the baseline:

```bash
cd /root/sidekick-forge

# Capture current schema as initial migration
./scripts/supabase_migration_helper.sh capture

# This creates: supabase/migrations/TIMESTAMP_schema_snapshot.sql

# Review the migration
ls -lh supabase/migrations/

# Commit to git
git add supabase/migrations/
git commit -m "Add initial schema migration"
git push origin main
```

### Regular Workflow: Making Schema Changes

**When you make schema changes on staging:**

1. Make changes in Supabase Dashboard or via SQL
2. Capture the changes:
   ```bash
   ./scripts/supabase_migration_helper.sh capture
   ```
3. Review the generated migration file
4. Test it locally (optional):
   ```bash
   ./scripts/supabase_migration_helper.sh preview
   ```

### Preparing a Release

When ready to deploy to production:

```bash
cd /root/sidekick-forge

# Run the staging release script
./scripts/prepare_staging_release.sh

# It will:
# 1. Run tests
# 2. Capture any schema changes
# 3. Prompt for version (e.g., v2.3.0)
# 4. Commit and tag
# 5. Push to GitHub
```

---

## Differences Between Staging and Production

| Aspect | Staging Server | Production Server |
|--------|----------------|-------------------|
| **Primary Script** | `prepare_staging_release.sh` | `deploy_to_production.sh` |
| **Git Flow** | Commits and pushes TO GitHub | Pulls FROM GitHub |
| **Supabase Project** | Staging Supabase project | Production Supabase project |
| **Domain** | `staging.sidekickforge.com` | `sidekickforge.com` |
| **DEVELOPMENT_MODE** | `true` | `false` |
| **Purpose** | Prepare and test changes | Deploy tested changes |
| **Schema Changes** | Created here first | Applied from migrations |

---

## Testing the Setup

### 1. Test Supabase Integration

```bash
# Test supabase CLI
./scripts/supabase_migration_helper.sh status

# Should show migration status for staging project
```

### 2. Test Staging Release Script (Dry Run)

```bash
# Make a small test change (e.g., edit a comment)
echo "# Test change" >> /root/sidekick-forge/README.md

# Run staging release script
./scripts/prepare_staging_release.sh

# Follow prompts:
# - Tests should pass
# - No schema changes (expected)
# - Enter version: v2.2.2-test
# - Commit message: "Test deployment system"

# Verify it pushed to GitHub
git log --oneline -n 5
git tag
```

### 3. Verify GitHub Integration

```bash
# Check remote
git remote -v

# Ensure it points to correct repository
# Should be: https://github.com/lhyphendixon/sidekick-forge.git
```

---

## Common Issues on Staging

### Issue: "Supabase CLI not found"

**Solution:**
```bash
# Install Supabase CLI
npm install -g supabase

# Verify
supabase --version
```

---

### Issue: "Cannot link to Supabase project"

**Solution:**
```bash
# Ensure SUPABASE_PROJECT_REF is in .env
grep SUPABASE_PROJECT_REF /root/sidekick-forge/.env

# If missing, add it:
echo "SUPABASE_PROJECT_REF=your-staging-project-ref" >> /root/sidekick-forge/.env

# Get project ref from: Supabase Dashboard → Settings → API
```

---

### Issue: "Tests fail on staging"

**Solution:**
```bash
# Check which tests fail
python3 scripts/test_mission_critical.py -v

# Common causes:
# - Staging services not running
# - Different Supabase credentials
# - Missing test data in staging database

# Ensure services are running
docker-compose ps

# Check logs
docker-compose logs
```

---

### Issue: "Git push fails - authentication"

**Solution:**
```bash
# Set up GitHub authentication (if not already done)

# Option 1: Use SSH key (recommended)
ssh-keygen -t ed25519 -C "your_email@example.com"
# Add key to GitHub: Settings → SSH and GPG keys

# Update git remote to use SSH
git remote set-url origin git@github.com:lhyphendixon/sidekick-forge.git

# Option 2: Use Personal Access Token
# GitHub → Settings → Developer settings → Personal access tokens
# Then: git push (enter token as password)
```

---

## Environment Variables Reference

### Staging `.env` Template

```env
# ========================================
# Staging Environment Configuration
# ========================================

# Staging Supabase Project
SUPABASE_URL=https://your-staging-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
SUPABASE_PROJECT_REF=your-staging-project-ref

# Staging LiveKit
LIVEKIT_URL=wss://your-staging-instance.livekit.cloud
LIVEKIT_API_KEY=APIxxxxxxxxxx
LIVEKIT_API_SECRET=secretxxxxxxxxxx

# Staging Domain
DOMAIN_NAME=staging.sidekickforge.com

# Development Settings
DEVELOPMENT_MODE=true
LOG_LEVEL=DEBUG

# Staging Agent Configuration
AGENT_NAME=sidekick-agent-staging
LIVEKIT_AGENT_NAME=sidekick-agent-staging-docker

# Redis (if used)
REDIS_HOST=localhost
REDIS_PORT=6380

# Other staging-specific settings...
```

---

## Checklist: Staging Setup Complete

- [ ] Files transferred from production
- [ ] Scripts made executable (`chmod +x scripts/*.sh`)
- [ ] Supabase CLI installed (`npm install -g supabase`)
- [ ] `.env` configured with staging credentials
- [ ] `SUPABASE_PROJECT_REF` added to `.env`
- [ ] Supabase migrations initialized
- [ ] Linked to staging Supabase project
- [ ] Test suite runs successfully
- [ ] Git remote configured correctly
- [ ] Test release created and pushed to GitHub
- [ ] Documentation reviewed

---

## Quick Command Reference

```bash
# On Staging Server

# Prepare a release for production
cd /root/sidekick-forge
./scripts/prepare_staging_release.sh

# Capture schema changes
./scripts/supabase_migration_helper.sh capture

# Preview migrations
./scripts/supabase_migration_helper.sh preview

# Run tests
python3 scripts/test_mission_critical.py

# Check Supabase status
./scripts/supabase_migration_helper.sh status

# List migrations
./scripts/supabase_migration_helper.sh list
```

---

## Next Steps After Setup

1. **Test the full workflow**:
   - Make a small change on staging
   - Run `prepare_staging_release.sh`
   - Verify it pushed to GitHub
   - On production, run `deploy_to_production.sh`
   - Verify deployment succeeded

2. **Document staging-specific configurations**:
   - Note any differences in `.env`
   - Document staging database setup
   - Record staging domain and SSL setup

3. **Set up CI/CD (optional)**:
   - GitHub Actions for automated testing
   - Automated schema validation
   - Deployment notifications

---

## Support

If you encounter issues:
1. Check this setup guide
2. Review [DEPLOYMENT.md](./DEPLOYMENT.md)
3. Check logs in staging environment
4. Verify `.env` configuration
5. Ensure Supabase CLI is properly installed

---

**Last Updated:** 2026-02-21
