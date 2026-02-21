# Transfer Deployment System to Staging Server

**Quick guide for transferring the deployment automation from production to staging.**

---

## What You Have (Production Server)

✅ Complete deployment automation system
✅ Ready-to-transfer package: `sidekick-forge-deployment-system.tar.gz` (24K)
✅ All scripts, documentation, and configuration templates

---

## What Staging Needs

To set up the deployment system on staging, you need to transfer:

### Files in the Package:
```
✓ scripts/deploy_to_production.sh
✓ scripts/prepare_staging_release.sh          ← Primary script for staging
✓ scripts/supabase_migration_helper.sh
✓ scripts/README_DEPLOYMENT.md
✓ DEPLOYMENT.md
✓ DEPLOYMENT_QUICKSTART.md
✓ STAGING_SETUP.md                            ← Setup instructions
✓ CLAUDE.md (updated)
✓ supabase/config.toml
✓ supabase/migrations/ (empty, ready for use)
```

---

## Transfer Options

### Option 1: Git (Recommended - Easiest)

**On Production (where you are now):**
```bash
cd /root/sidekick-forge

# Commit all deployment files
git add scripts/deploy_to_production.sh \
        scripts/prepare_staging_release.sh \
        scripts/supabase_migration_helper.sh \
        scripts/create_staging_package.sh \
        scripts/README_DEPLOYMENT.md \
        DEPLOYMENT.md \
        DEPLOYMENT_QUICKSTART.md \
        STAGING_SETUP.md \
        TRANSFER_TO_STAGING.md \
        CLAUDE.md

git commit -m "Add deployment automation with Supabase branching support"
git push origin main
```

**On Staging:**
```bash
cd /root/sidekick-forge
git pull origin main
chmod +x scripts/*.sh
```

**Done!** Now follow [STAGING_SETUP.md](./STAGING_SETUP.md)

---

### Option 2: SCP Transfer (If Git Not Available)

**On Production:**
```bash
# Package is already created at:
/root/sidekick-forge/sidekick-forge-deployment-system.tar.gz

# Transfer to staging
scp /root/sidekick-forge/sidekick-forge-deployment-system.tar.gz \
    user@staging-server:/root/
```

**On Staging:**
```bash
cd /root
tar -xzf sidekick-forge-deployment-system.tar.gz
cp -r sidekick-forge/* /root/sidekick-forge/
chmod +x /root/sidekick-forge/scripts/*.sh

# Read setup instructions
cat /root/sidekick-forge/STAGING_SETUP.md
```

---

### Option 3: Download Package (If Accessible via Web)

**On Production:**
```bash
# Move package to web-accessible location
cp /root/sidekick-forge/sidekick-forge-deployment-system.tar.gz \
   /root/sidekick-forge/static/

# Now accessible at: https://sidekickforge.com/static/sidekick-forge-deployment-system.tar.gz
```

**On Staging:**
```bash
cd /root
wget https://sidekickforge.com/static/sidekick-forge-deployment-system.tar.gz
tar -xzf sidekick-forge-deployment-system.tar.gz
cp -r sidekick-forge/* /root/sidekick-forge/
chmod +x /root/sidekick-forge/scripts/*.sh
```

---

## After Transfer: Staging Setup Steps

Once files are on staging, follow these steps:

### 1. Install Supabase CLI
```bash
npm install -g supabase
```

### 2. Configure Staging Environment

Edit `/root/sidekick-forge/.env` on staging:
```env
# CRITICAL: Add this line
SUPABASE_PROJECT_REF=your-staging-project-ref

# Other staging-specific values:
SUPABASE_URL=https://your-staging-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=staging-key
DOMAIN_NAME=staging.sidekickforge.com
DEVELOPMENT_MODE=true
```

Get `SUPABASE_PROJECT_REF` from:
- Supabase Dashboard → Your Staging Project → Settings → API → Project Ref

### 3. Initialize Migrations
```bash
cd /root/sidekick-forge
./scripts/supabase_migration_helper.sh init
./scripts/supabase_migration_helper.sh link staging
```

### 4. Test the Setup
```bash
# Run tests
python3 scripts/test_mission_critical.py

# Try capturing schema
./scripts/supabase_migration_helper.sh capture
```

### 5. Test Release Process
```bash
# Make a small test change
echo "# Test" >> README.md

# Run staging release
./scripts/prepare_staging_release.sh

# Follow prompts, use version like: v2.2.2-test
```

---

## Full Documentation

After transfer, read these files on staging:

1. **[STAGING_SETUP.md](./STAGING_SETUP.md)** - Complete staging setup guide
2. **[DEPLOYMENT_QUICKSTART.md](./DEPLOYMENT_QUICKSTART.md)** - Quick reference
3. **[DEPLOYMENT.md](./DEPLOYMENT.md)** - Full deployment documentation

---

## Verification Checklist

On staging server, verify:

- [ ] All scripts are executable (`ls -lh scripts/*.sh`)
- [ ] Supabase CLI installed (`supabase --version`)
- [ ] `.env` has `SUPABASE_PROJECT_REF`
- [ ] Migrations directory exists (`ls supabase/migrations/`)
- [ ] Can link to Supabase (`./scripts/supabase_migration_helper.sh link staging`)
- [ ] Tests run (`python3 scripts/test_mission_critical.py`)
- [ ] Git remote configured (`git remote -v`)

---

## The Deployment Flow (After Setup)

Once staging is configured:

**On Staging:**
```bash
./scripts/prepare_staging_release.sh
# → Tests, captures schema, commits, tags, pushes to GitHub
```

**On Production:**
```bash
./scripts/deploy_to_production.sh
# → Pulls code, applies migrations, deploys containers
```

---

## Quick Reference

### Package Location (Production)
```
/root/sidekick-forge/sidekick-forge-deployment-system.tar.gz
```

### Package Contents
- Deployment scripts (3 scripts)
- Documentation (5 files)
- Supabase config template
- Migration directory structure

### Package Size
24 KB

### Transfer Command (SCP Example)
```bash
scp /root/sidekick-forge/sidekick-forge-deployment-system.tar.gz \
    user@staging-server.com:/root/
```

---

## Support

- **Setup Issues**: See [STAGING_SETUP.md](./STAGING_SETUP.md)
- **Deployment Questions**: See [DEPLOYMENT.md](./DEPLOYMENT.md)
- **Quick Reference**: See [DEPLOYMENT_QUICKSTART.md](./DEPLOYMENT_QUICKSTART.md)

---

**You're on Production Server**
**Next Step**: Transfer package to staging and follow STAGING_SETUP.md

---

**Created**: 2026-02-21
**Package**: sidekick-forge-deployment-system.tar.gz
**Size**: 24K
**Ready**: ✅
