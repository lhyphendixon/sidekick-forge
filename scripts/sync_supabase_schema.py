#!/usr/bin/env python3
"""Sync Sidekick Forge tenant schemas via Supabase SQL API."""
from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Tuple

from app.config import settings
from app.services.schema_sync import (
    apply_schema,
    fetch_platform_clients,
    project_ref_from_url,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync Supabase schemas across tenants.")
    parser.add_argument(
        "--client",
        action="append",
        dest="client_ids",
        help="Limit sync to specific client IDs (defaults to all).",
    )
    parser.add_argument(
        "--skip-indexes",
        action="store_true",
        help="Skip IVFFLAT index rebuild (apply only column patch).",
    )
    parser.add_argument(
        "--include-platform",
        action="store_true",
        help="Also patch the platform database.",
    )
    return parser.parse_args()


def get_access_token() -> str:
    token = os.environ.get("SUPABASE_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("SUPABASE_ACCESS_TOKEN env var required")
    return token


def format_status(ok: bool) -> str:
    return "✅" if ok else "❌"


def run_patches(project_ref: str, name: str, token: str, run_indexes: bool) -> None:
    results = apply_schema(project_ref, token, include_indexes=run_indexes)
    messages = {
        "base_schema": "base tenant tables ensured",
        "vector_dimensions": "vector dimensions normalized",
        "conversation_patch": "conversation_transcripts columns aligned",
        "ivfflat_indexes": "IVFFLAT indexes ensured (lists=16)",
    }
    for step, ok, detail in results:
        message = messages.get(step, step)
        if ok:
            print(f"{format_status(True)} {name}: {message}")
        else:
            print(f"{format_status(False)} {name}: {message} failed -> {detail}")


def main() -> int:
    args = parse_args()
    target_ids = set(args.client_ids or [])
    run_indexes = not args.skip_indexes
    token = get_access_token()

    platform_ref = project_ref_from_url(settings.supabase_url)

    # Fetch clients using platform connection
    platform_clients = fetch_platform_clients(token)

    if args.include_platform:
        run_patches(platform_ref, "platform", token, run_indexes)

    for row in platform_clients:
        # Supabase SQL API returns dictionaries when selecting plain columns.
        client_id = row.get("id") if isinstance(row, dict) else row[0]
        if target_ids and client_id not in target_ids:
            continue

        status = (row.get("provisioning_status") if isinstance(row, dict) else row[4] if len(row) > 4 else "ready")
        status = (status or "ready").lower()

        supabase_url = row.get("supabase_url") if isinstance(row, dict) else row[2]
        if not supabase_url:
            print(
                f"⚠️  Skipping client {client_id}: missing supabase_url"
                + (f" (status={status})" if status else "")
            )
            continue
        if status not in {"ready", "schema_syncing"}:
            print(f"⏭️  Skipping client {client_id}: provisioning_status={status}")
            continue

        project_ref = project_ref_from_url(supabase_url)
        display_name = (row.get("name") if isinstance(row, dict) else row[1]) or client_id
        run_patches(project_ref, f"client {display_name}", token, run_indexes)

    return 0


if __name__ == "__main__":
    sys.exit(main())
