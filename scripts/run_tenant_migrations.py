#!/usr/bin/env python3
"""
Run pending migrations for all active client Supabase projects.

Usage:
  python scripts/run_tenant_migrations.py --platform-url <platform_supabase_url> --platform-key <service_role_key> [--dry-run] [--only <client_id>] [--concurrency 3]

Notes:
- Applies migrations in sidekick-forge/migrations ordered by filename.
- Tracks applied migrations per tenant in public.migration_history (created if missing).
- Uses client Supabase service_role_key to execute SQL.
"""
import argparse
import asyncio
import hashlib
import os
from pathlib import Path
from typing import List, Dict, Optional
import logging
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tenant_migrations")


def load_migrations(migrations_dir: Path) -> List[Dict[str, str]]:
    migrations = []
    for path in sorted(migrations_dir.glob("*.sql")):
        content = path.read_text()
        sha = hashlib.sha256(content.encode()).hexdigest()
        migrations.append({"name": path.name, "sql": content, "hash": sha})
    return migrations


async def ensure_history_table(client_url: str, service_key: str, sql: str) -> None:
    await execute_sql(client_url, service_key, sql)


async def fetch_applied(client_url: str, service_key: str) -> Dict[str, str]:
    sql = "select name, hash from migration_history"
    try:
        data = await execute_sql(client_url, service_key, sql)
        return {row["name"]: row["hash"] for row in data or []}
    except Exception:
        return {}


async def execute_sql(client_url: str, service_key: str, sql: str) -> Optional[List[Dict]]:
    # Use Supabase SQL REST: postgrest not ideal; use rpc sql? Supabase exposes /rest/v1/rpc/sql when enabled.
    # For simplicity here, use the /rest/v1/rpc/sql function name "sql" if present.
    endpoint = f"{client_url}/rest/v1/rpc/sql"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=20.0) as http:
        resp = await http.post(endpoint, headers=headers, json={"q": sql})
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return None


async def apply_migration(client_url: str, service_key: str, migration: Dict[str, str]) -> None:
    await execute_sql(client_url, service_key, migration["sql"])
    await execute_sql(
        client_url,
        service_key,
        f"insert into migration_history (name, hash) values ('{migration['name']}', '{migration['hash']}') on conflict (name) do update set hash=excluded.hash, applied_at=now()",
    )


async def migrate_client(client: Dict[str, str], migrations: List[Dict[str, str]], dry_run: bool) -> None:
    client_id = client["id"]
    client_url = client["supabase_url"]
    service_key = client["service_role_key"]
    logger.info(f"[{client_id}] starting")
    await ensure_history_table(client_url, service_key, Path(__file__).resolve().parent.parent / "migrations" / "0000_migration_history.sql").read_text()
    applied = await fetch_applied(client_url, service_key)
    pending = [m for m in migrations if m["name"] not in applied]
    if not pending:
        logger.info(f"[{client_id}] no pending migrations")
        return
    for mig in pending:
        if dry_run:
            logger.info(f"[{client_id}] DRY RUN would apply {mig['name']}")
        else:
            logger.info(f"[{client_id}] applying {mig['name']}")
            await apply_migration(client_url, service_key, mig)
    logger.info(f"[{client_id}] done")


async def get_clients(platform_url: str, platform_key: str, only: Optional[str]) -> List[Dict[str, str]]:
    sql = "select id, supabase_url, supabase_service_role_key as service_role_key from clients where active = true"
    if only:
        sql += f" and id = '{only}'"
    data = await execute_sql(platform_url, platform_key, sql)
    clients = []
    for row in data or []:
        if row.get("supabase_url") and row.get("service_role_key"):
            clients.append({
                "id": row["id"],
                "supabase_url": row["supabase_url"],
                "service_role_key": row["service_role_key"],
            })
    return clients


async def main():
    parser = argparse.ArgumentParser(description="Run tenant migrations across Supabase clients.")
    parser.add_argument("--platform-url", required=False, default=os.getenv("PLATFORM_SUPABASE_URL"))
    parser.add_argument("--platform-key", required=False, default=os.getenv("PLATFORM_SUPABASE_SERVICE_ROLE_KEY"))
    parser.add_argument("--only", help="Single client id to run", default=None)
    parser.add_argument("--dry-run", action="store_true", help="List without applying")
    args = parser.parse_args()

    if not args.platform_url or not args.platform_key:
        raise SystemExit("platform url/key required")

    migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
    migrations = load_migrations(migrations_dir)
    platform_clients = await get_clients(args.platform_url, args.platform_key, args.only)
    if not platform_clients:
        logger.info("No clients found to migrate")
        return

    for client in platform_clients:
        await migrate_client(client, migrations, args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
