"""Shared helpers for applying Sidekick Forge schema patches to Supabase projects."""
from __future__ import annotations

from typing import Iterable, List, Tuple

import requests

from app.config import settings

# SQL statements maintained as canonical schema patches
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

SQL_ENDPOINT_TEMPLATE = "https://api.supabase.com/v1/projects/{project_ref}/database/query"


class SchemaSyncError(RuntimeError):
    """Raised when schema sync fails for a Supabase project."""


def project_ref_from_url(url: str) -> str:
    """Extract the Supabase project ref from the provided URL."""
    host = url.split("https://")[-1]
    return host.split(".supabase.co")[0]


def execute_sql(project_ref: str, token: str, sql: str) -> Tuple[bool, str]:
    """Execute raw SQL against a Supabase project via Management API."""
    url = SQL_ENDPOINT_TEMPLATE.format(project_ref=project_ref)
    headers = {
        "Authorization": f"Bearer {token}",
        "apikey": token,
        "Content-Type": "application/json",
    }
    payload = {"query": sql}
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    if response.status_code in (200, 201):
        return True, ""
    try:
        detail = response.json().get("msg") or response.text
    except Exception:
        detail = response.text
    return False, detail


def apply_schema(project_ref: str, token: str, include_indexes: bool = True) -> List[Tuple[str, bool, str]]:
    """Apply canonical schema patches to the given Supabase project.

    Returns a list of (step, success, detail) tuples for logging/telemetry.
    """
    results: List[Tuple[str, bool, str]] = []

    ok, detail = execute_sql(project_ref, token, CONVERSATION_PATCH_SQL)
    results.append(("conversation_patch", ok, detail))

    if include_indexes:
        ok_indexes, detail_indexes = execute_sql(project_ref, token, IVFFLAT_PATCH_SQL)
        results.append(("ivfflat_indexes", ok_indexes, detail_indexes))

    return results


def fetch_platform_clients(token: str) -> List[dict]:
    """Fetch clients from the platform database using the Management API."""
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
    return response.json().get("result", [])


__all__ = [
    "SchemaSyncError",
    "apply_schema",
    "project_ref_from_url",
    "execute_sql",
    "fetch_platform_clients",
]
