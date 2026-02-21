# Sidekick Forge - Deployment Quick Start

**TL;DR**: Deploy from staging to production in 2 commands.

## Prerequisites

1. **On Staging Server**: Supabase CLI installed
   ```bash
   npm install -g supabase
   ```

2. **On Production Server**: Docker Compose running
   ```bash
   docker-compose ps
   ```

---

## Staging → Production Deployment

### Step 1: On Staging Server

```bash
cd /root/sidekick-forge
./scripts/prepare_staging_release.sh
```

**What it does:**
- ✅ Runs tests
- ✅ Captures Supabase schema changes
- ✅ Commits and tags release
- ✅ Pushes to GitHub

**Output:** Release version (e.g., `v2.3.0`)

---

### Step 2: On Production Server

```bash
cd /root/sidekick-forge
./scripts/deploy_to_production.sh
```

**What it does:**
- ✅ Backs up current state
- ✅ Pulls latest code from GitHub
- ✅ Preserves production `.env` secrets
- ✅ Applies Supabase migrations (with confirmation)
- ✅ Rebuilds Docker images
- ✅ Restarts services (zero-downtime)
- ✅ Runs health checks
- ✅ Auto-rollback on failure

**Time:** ~3-5 minutes

---

## That's It! 🎉

Your changes are now live in production.

---

## Verify Deployment

```bash
# Check health
curl http://localhost:8000/health

# View logs
docker-compose logs -f fastapi

# Run tests
python3 scripts/test_mission_critical.py --quick
```

---

## Troubleshooting

### Deployment Failed?
- Check logs: `tail -f backups/*/deployment.log`
- System auto-rolled back to previous state
- Fix the issue on staging and try again

### Need to Rollback Manually?
```bash
# Find backup
ls -lt backups/

# Use the most recent
BACKUP_DIR="backups/20260221_143022"
cp "$BACKUP_DIR/.env.backup" .env
git reset --hard $(cat "$BACKUP_DIR/git_commit.txt")
docker-compose up -d --force-recreate
```

---

## Common Commands

```bash
# Staging: Prepare release
./scripts/prepare_staging_release.sh

# Production: Deploy
./scripts/deploy_to_production.sh

# Check status
docker-compose ps

# View logs
docker-compose logs -f fastapi
docker-compose logs -f agent-worker

# Restart services
docker-compose restart fastapi

# Run tests
python3 scripts/test_mission_critical.py
```

---

## Supabase Migrations

### Capture Schema Changes
```bash
./scripts/supabase_migration_helper.sh capture
```

### Preview Migrations
```bash
./scripts/supabase_migration_helper.sh preview
```

### Apply Migrations Manually
```bash
./scripts/supabase_migration_helper.sh apply
```

---

## Full Documentation

For detailed documentation, see [DEPLOYMENT.md](./DEPLOYMENT.md).

---

## Emergency Contacts

- **Deployment Issues**: Check [DEPLOYMENT.md](./DEPLOYMENT.md) troubleshooting section
- **System Logs**: `docker-compose logs` or `backups/*/deployment.log`
- **Rollback**: Automatic on failure, or see manual rollback above
