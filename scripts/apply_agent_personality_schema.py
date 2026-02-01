#!/usr/bin/env python3
"""Apply agent_personality schema patch to tenant databases only."""
from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Tuple, Optional

import requests

from app.config import settings

SQL_ENDPOINT_TEMPLATE = "https://api.supabase.com/v1/projects/{project_ref}/database/query"

AGENT_PERSONALITY_SQL = """
do $$
begin
  if exists (
    select 1 from information_schema.tables
    where table_schema = 'public' and table_name = 'agents'
  ) then
    create table if not exists public.agent_personality (
      agent_id uuid primary key references public.agents(id) on delete cascade,
      openness int default 50,
      conscientiousness int default 50,
      extraversion int default 50,
      agreeableness int default 50,
      neuroticism int default 50,
      created_at timestamptz not null default now(),
      updated_at timestamptz not null default now()
    );
  end if;
end$$;
""".strip()


def project_ref_from_url(url: str) -> str:
    host = url.split("https://")[-1]
    return host.split(".supabase.co")[0]


def execute_sql(project_ref: str, token: str, sql: str) -> Tuple[bool, str]:
    url = SQL_ENDPOINT_TEMPLATE.format(project_ref=project_ref)
    headers = {
        "Authorization": f"Bearer {token}",
        "apikey": token,
        "Content-Type": "application/json",
    }
    response = requests.post(url, headers=headers, json={"query": sql}, timeout=30)
    if response.status_code in (200, 201):
        return True, ""
    try:
        detail = response.json().get("msg") or response.text
    except Exception:
        detail = response.text
    return False, detail


def fetch_platform_clients(token: str) -> List[Dict[str, str]]:
    project_ref = project_ref_from_url(settings.supabase_url)
    url = SQL_ENDPOINT_TEMPLATE.format(project_ref=project_ref)
    headers = {
        "Authorization": f"Bearer {token}",
        "apikey": token,
        "Content-Type": "application/json",
    }
    payload = {
        "query": (
            "select id, name, supabase_url, supabase_service_role_key, provisioning_status "
            "from clients"
        )
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict):
        return data.get("result") or data.get("data") or []
    if isinstance(data, list):
        return data
    return []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply agent_personality schema patch across tenant databases.",
    )
    parser.add_argument(
        "--client",
        action="append",
        dest="client_ids",
        help="Limit to specific client IDs (can repeat).",
    )
    parser.add_argument(
        "--include-platform",
        action="store_true",
        help="Also apply to the platform database.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print targets without applying SQL.",
    )
    return parser.parse_args()


def format_status(ok: bool) -> str:
    return "✅" if ok else "❌"


def run_patch(project_ref: str, label: str, token: str, dry_run: bool) -> None:
    if dry_run:
        print(f"📝 DRY RUN: would apply agent_personality to {label}")
        return
    ok, detail = execute_sql(project_ref, token, AGENT_PERSONALITY_SQL)
    if ok:
        print(f"{format_status(True)} {label}: agent_personality applied")
    else:
        print(f"{format_status(False)} {label}: failed -> {detail}")


def main() -> int:
    args = parse_args()
    token = os.environ.get("SUPABASE_ACCESS_TOKEN")
    if not token:
        print("SUPABASE_ACCESS_TOKEN env var required", file=sys.stderr)
        return 1

    target_ids = set(args.client_ids or [])
    platform_ref = project_ref_from_url(settings.supabase_url)

    if args.include_platform:
        run_patch(platform_ref, "platform", token, args.dry_run)

    clients = fetch_platform_clients(token)
    if not clients:
        print("No clients found.")
        return 0

    for row in clients:
        client_id = row.get("id")
        if target_ids and client_id not in target_ids:
            continue

        status = (row.get("provisioning_status") or "ready").lower()
        if status not in {"ready", "schema_syncing"}:
            print(f"⏭️  Skipping client {client_id}: provisioning_status={status}")
            continue

        supabase_url = row.get("supabase_url")
        if not supabase_url:
            print(f"⚠️  Skipping client {client_id}: missing supabase_url")
            continue

        project_ref = project_ref_from_url(supabase_url)
        name = row.get("name") or client_id
        run_patch(project_ref, f"client {name}", token, args.dry_run)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
