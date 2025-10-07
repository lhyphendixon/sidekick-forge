"""Helper functions for interacting with the Supabase Management API."""
from __future__ import annotations

import os
import time
import secrets
import string
from typing import Any, Dict, Optional
import re

import requests

SUPABASE_API_BASE = "https://api.supabase.com/v1"


class SupabaseManagementError(RuntimeError):
    """Raised when management API calls fail."""


def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "apikey": token,
        "Content-Type": "application/json",
    }


def _generate_password(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _generate_unique_name(name: str, client_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        slug = "client"
    suffix = client_id.replace("-", "")[:8]
    max_len = max(1, 50 - len(suffix) - 1)
    slug = slug[:max_len].strip("-")
    if slug:
        return f"{slug}-{suffix}"
    return f"client-{suffix}"


def create_project(
    *,
    token: str,
    org_id: str,
    name: str,
    client_id: str,
    region: str,
    plan: str,
    db_password: Optional[str] = None,
    wait_timeout: int = 300,
    poll_interval: float = 5.0,
) -> Dict[str, Any]:
    """Create a Supabase project and return metadata including service keys."""
    if not db_password:
        db_password = _generate_password()

    unique_name = _generate_unique_name(name, client_id)

    payload = {
        "organization_id": org_id,
        "name": unique_name,
        "db_pass": db_password,
        "plan": plan,
        "region": region,
    }

    resp = requests.post(
        f"{SUPABASE_API_BASE}/projects",
        headers=_headers(token),
        json=payload,
        timeout=30,
    )
    if resp.status_code in (200, 201):
        data = resp.json()
        project_ref = data.get("project_ref") or data.get("ref") or data.get("id")
        if not project_ref:
            raise SupabaseManagementError("Supabase project creation response missing project_ref")
    else:
        error_text = resp.text
        if resp.status_code == 400 and "already exists" in error_text.lower():
            existing = _find_project_by_name(token, org_id, unique_name)
            if not existing:
                raise SupabaseManagementError(f"Failed to create project: {error_text}")
            project_ref = existing.get("id") or existing.get("project_ref") or existing.get("ref")
        else:
            raise SupabaseManagementError(f"Failed to create project: {error_text}")

    if not project_ref:
        raise SupabaseManagementError("Supabase project reference not determined")

    _wait_for_ready(token, project_ref, wait_timeout=wait_timeout, poll_interval=poll_interval)

    settings = _fetch_project_settings(token, project_ref)

    return {
        "project_ref": project_ref,
        "supabase_url": f"https://{project_ref}.supabase.co",
        "service_role_key": settings.get("service_role_key"),
        "anon_key": settings.get("anon_key"),
        "db_password": db_password,
    }


def _find_project_by_name(token: str, org_id: str, name: str) -> Optional[Dict[str, Any]]:
    resp = requests.get(
        f"{SUPABASE_API_BASE}/projects",
        headers=_headers(token),
        timeout=30,
    )
    if resp.status_code != 200:
        raise SupabaseManagementError(
            f"Failed to list projects while looking for existing '{name}': {resp.text}"
        )

    projects = resp.json() or []
    for project in projects:
        if project.get("organization_id") == org_id and project.get("name") == name:
            return project
    return None


def _wait_for_ready(token: str, project_ref: str, *, wait_timeout: int, poll_interval: float) -> None:
    deadline = time.time() + wait_timeout
    last_status = None

    while time.time() < deadline:
        resp = requests.get(
            f"{SUPABASE_API_BASE}/projects/{project_ref}",
            headers=_headers(token),
            timeout=30,
        )
        if resp.status_code != 200:
            raise SupabaseManagementError(
                f"Failed to fetch project status for {project_ref}: {resp.text}"
            )
        data = resp.json()
        status = data.get("status") or data.get("project_status")
        last_status = status

        if status in {"ACTIVE", "RUNNING", "ACTIVE_HEALTHY", "READY"}:
            return

        time.sleep(poll_interval)

    raise SupabaseManagementError(
        f"Timed out waiting for project {project_ref} to become ready (last status={last_status})"
    )


def _fetch_project_settings(
    token: str,
    project_ref: str,
    *,
    attempts: int = 60,
    interval: float = 5.0,
) -> Dict[str, Any]:
    """Return project API keys using the management API.

    Supabase recently deprecated the old ``/settings`` endpoint; if we receive a
    404 we retry using the new ``/api-keys`` route which returns a list of key
    metadata. We keep the legacy behaviour when the old endpoint is still
    available for backwards compatibility.
    """

    def _transform_keys(payload: Dict[str, Any]) -> Dict[str, Any]:
        # Legacy endpoint already includes the keys in the payload.
        if "anon_key" in payload and "service_role_key" in payload:
            return payload
        return payload

    for attempt in range(1, attempts + 1):
        resp = requests.get(
            f"{SUPABASE_API_BASE}/projects/{project_ref}/settings",
            headers=_headers(token),
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            transformed = _transform_keys(data)
            if transformed:
                return transformed
            return data

        if resp.status_code == 404:
            # Fall back to the new /api-keys endpoint.
            keys = _fetch_project_api_keys(token, project_ref)
            if keys:
                return keys
            time.sleep(interval)
            continue

        raise SupabaseManagementError(
            f"Failed to fetch project settings for {project_ref}: {resp.text}"
        )

    raise SupabaseManagementError(
        f"Timed out fetching project settings for {project_ref}"
    )


def _fetch_project_api_keys(token: str, project_ref: str) -> Optional[Dict[str, Any]]:
    resp = requests.get(
        f"{SUPABASE_API_BASE}/projects/{project_ref}/api-keys",
        headers=_headers(token),
        timeout=30,
    )

    if resp.status_code == 404:
        return None

    if resp.status_code != 200:
        raise SupabaseManagementError(
            f"Failed to fetch project api-keys for {project_ref}: {resp.text}"
        )

    data = resp.json() or []
    if isinstance(data, dict):
        # Unexpected format – return whatever we got.
        return data

    keys: Dict[str, Any] = {}
    for entry in data:
        name = entry.get("name") or entry.get("id")
        if not name:
            continue
        api_key = entry.get("api_key")
        if not api_key:
            continue
        keys[name] = api_key

    anon = keys.get("anon")
    service = keys.get("service_role")
    if not anon and not service:
        return None

    return {
        "anon_key": anon,
        "service_role_key": service,
    }
