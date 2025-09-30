#!/usr/bin/env python3
"""Sync Sidekick Forge tenant schemas via Supabase SQL API."""
from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Tuple

import requests

from app.config import settings

CONVERSATION_PATCH_SQL = """
alter table if exists public.conversation_transcripts
  add column if not exists role text,
  add column if not exists sequence int,
  add column if not exists user_message text,
  add column if not exists assistant_message text,
  add column if not exists citations jsonb default '[]'::jsonb,
  add column if not exists source text,
  add column if not exists turn_id uuid default gen_random_uuid();

update public.conversation_transcripts
  set turn_id = coalesce(turn_id, gen_random_uuid());
""".strip()

IVFFLAT_PATCH_SQL = """
create index if not exists documents_embeddings_ivfflat
  on public.documents using ivfflat (embeddings vector_cosine_ops) with (lists = 16);

create index if not exists documents_embedding_ivfflat
  on public.documents using ivfflat (embedding vector_cosine_ops) with (lists = 16);

create index if not exists documents_embedding_vec_ivfflat
  on public.documents using ivfflat (embedding_vec vector_cosine_ops) with (lists = 16);

create index if not exists document_chunks_embeddings_ivfflat
  on public.document_chunks using ivfflat (embeddings vector_cosine_ops) with (lists = 16);

create index if not exists document_chunks_embeddings_vec_ivfflat
  on public.document_chunks using ivfflat (embeddings_vec vector_cosine_ops) with (lists = 16);

create index if not exists conversation_transcripts_embeddings_ivfflat
  on public.conversation_transcripts using ivfflat (embeddings vector_cosine_ops) with (lists = 16);
""".strip()

SQL_ENDPOINT = "https://api.supabase.com/v1/projects/{project_ref}/sql"


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


def fetch_clients(token: str) -> List[Dict[str, str]]:
    url = SQL_ENDPOINT.format(project_ref=settings.supabase_url.split("https://")[1].split(".supabase.co")[0])
    headers = {
        "Authorization": f"Bearer {token}",
        "apikey": token,
        "Content-Type": "application/json",
    }
    payload = {"query": "select id, name, supabase_url, supabase_service_role_key from clients"}
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get("result", [])


def project_ref_from_url(url: str) -> str:
    host = url.split("https://")[-1]
    return host.split(".supabase.co")[0]


def execute_sql(project_ref: str, token: str, sql: str) -> Tuple[bool, str]:
    url = SQL_ENDPOINT.format(project_ref=project_ref)
    headers = {
        "Authorization": f"Bearer {token}",
        "apikey": token,
        "Content-Type": "application/json",
    }
    payload = {"query": sql}
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code == 200:
        return True, ""
    try:
        detail = resp.json().get("msg") or resp.text
    except Exception:
        detail = resp.text
    return False, detail


def format_status(ok: bool) -> str:
    return "✅" if ok else "❌"


def run_patches(project_ref: str, name: str, token: str, run_indexes: bool) -> None:
    ok, err = execute_sql(project_ref, token, CONVERSATION_PATCH_SQL)
    if ok:
        print(f"{format_status(True)} {name}: conversation_transcripts columns aligned")
    else:
        print(f"{format_status(False)} {name}: conversation patch failed -> {err}")

    if not run_indexes:
        return

    ok, err = execute_sql(project_ref, token, IVFFLAT_PATCH_SQL)
    if ok:
        print(f"{format_status(True)} {name}: IVFFLAT indexes ensured (lists=16)")
    else:
        print(f"{format_status(False)} {name}: IVFFLAT patch failed -> {err}")


def main() -> int:
    args = parse_args()
    target_ids = set(args.client_ids or [])
    run_indexes = not args.skip_indexes
    token = get_access_token()

    platform_ref = project_ref_from_url(settings.supabase_url)

    # Fetch clients using platform connection
    platform_clients = fetch_clients(token)

    if args.include_platform:
        run_patches(platform_ref, "platform", token, run_indexes)

    for row in platform_clients:
        client_id = row.get("id")
        if target_ids and client_id not in target_ids:
            continue
        supabase_url = row.get("supabase_url")
        if not supabase_url:
            print(f"⚠️  Skipping client {client_id}: missing supabase_url")
            continue
        project_ref = project_ref_from_url(supabase_url)
        display_name = row.get("name") or client_id
        run_patches(project_ref, f"client {display_name}", token, run_indexes)

    return 0


if __name__ == "__main__":
    sys.exit(main())
