# Sidekick Forge - Deployment Quick Start

**TL;DR**: Deploy from staging to production in 2 commands.

## Prerequisites

- **Both servers** have their own `.env` configured (gitignored, never touched by deploys)
- **Both servers** have `AGENT_NAME` set in `.env`:
  - Staging: `AGENT_NAME=sidekick-agent-staging-docker`
  - Production: `AGENT_NAME=sidekick-agent-production`

---

## Staging → Production Deployment

### Step 1: On Staging Server

```bash
cd /root/sidekick-forge
./scripts/prepare_staging_release.sh
```

**What it does:**
- Runs pre-release tests
- Captures Supabase schema changes (optional)
- Commits, tags, and pushes to GitHub

**Output:** Release version (e.g., `v2.10.0`)

---

### Step 2: On Production Server

```bash
cd /root/sidekick-forge
./scripts/deploy_to_production.sh
```

Or deploy a specific tag:
```bash
./scripts/deploy_to_production.sh v2.10.0
```

**What it does:**
- Pulls latest code from GitHub
- Stamps agent build version
- Rebuilds Docker images
- Restarts services (zero-downtime)
- Runs health checks
- Auto-rollback on failure

**Time:** ~3-5 minutes

---

## That's It!

Your changes are now live in production.

---

## Syncing Production Hotfixes to Staging

If hotfixes were applied directly on production:

```bash
# On staging server:
cd /root/sidekick-forge
./scripts/sync_production_to_staging.sh
```

---

## Verify Deployment

```bash
# Check health
curl http://localhost:8000/health

# View logs
docker compose logs -f fastapi
docker compose logs -f agent-worker

# Run tests
python3 scripts/test_mission_critical.py --quick
```

---

## Troubleshooting

### Deployment Failed?
- System auto-rolled back to previous state
- Check logs: `cat backups/deploy-*/deploy.log`
- Fix the issue on staging and try again

### Need to Rollback Manually?
```bash
# Find the backup
ls -lt backups/

# Restore to the previous commit
git reset --hard $(cat backups/deploy-YYYYMMDD_HHMMSS/previous_commit.txt)
docker compose build && docker compose up -d
```

---

## Common Commands

```bash
# Staging: Prepare release
./scripts/prepare_staging_release.sh

# Production: Deploy latest
./scripts/deploy_to_production.sh

# Production: Deploy specific tag
./scripts/deploy_to_production.sh v2.10.0

# Staging: Sync from production
./scripts/sync_production_to_staging.sh

# Check status
docker compose ps

# View logs
docker compose logs -f fastapi
docker compose logs -f agent-worker

# Restart a service
docker compose restart fastapi
```

---

## Full Documentation

For detailed documentation, see [DEPLOYMENT.md](./DEPLOYMENT.md).
