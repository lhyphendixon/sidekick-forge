"""
Helpers for synchronizing Supabase Auth records across client projects.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Dict, Optional

from supabase import Client as SupabaseClient, create_client

from app.utils.supabase_credentials import SupabaseCredentialManager


class ClientSupabaseAuthError(Exception):
    """Raised when Supabase Auth synchronization fails."""


async def _run_in_thread(func, *args, **kwargs):
    """Helper to execute synchronous Supabase SDK calls off the main loop."""
    return await asyncio.to_thread(func, *args, **kwargs)


def _get_admin_api(url: str, service_role_key: str):
    client = create_client(url, service_role_key)
    return client, client.auth.admin


def _find_user_by_email(admin_api, email: str) -> Optional[str]:
    """Return the user_id for a given email if it exists."""
    try:
        response = admin_api._request("GET", "admin/users", query={"email": email})
        if isinstance(response, dict):
            users = response.get("users") or []
            if users:
                return users[0].get("id")
        elif isinstance(response, list):
            for user in response:
                if (user.get("email") or "").lower() == email.lower():
                    return user.get("id")
    except Exception:
        pass

    # Fallback: bounded pagination through list_users to find email
    try:
        page = 1
        max_pages = 10
        while page <= max_pages:
            users = admin_api.list_users(page=page, per_page=100)
            if not users:
                break
            for user in users:
                if getattr(user, "email", "").lower() == email.lower():
                    return getattr(user, "id")
            page += 1
    except Exception:
        pass

    return None


def _shadow_email(admin_email: str, client_id: str) -> str:
    """Produce a deterministic shadow email per admin/client combo."""
    local_part, _, domain = admin_email.partition("@")
    safe_local = (
        local_part.replace("+", "-plus-")
        .replace("@", "-at-")
        .replace(".", "-dot-")
    )
    return f"{safe_local}-{client_id[:8]}@preview.sidekick"


async def ensure_client_user_credentials(client_id: str, email: str, password: str) -> None:
    """
    Ensure the given email exists inside the client's Supabase project with the provided password.

    If the user already exists the password is updated in place (email confirmed).
    """
    credentials = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
    if not credentials:
        raise ClientSupabaseAuthError(f"Client {client_id} is missing Supabase configuration")

    url, _anon_key, service_role_key = credentials
    if not url or not service_role_key:
        raise ClientSupabaseAuthError(f"Client {client_id} Supabase config incomplete")

    shadow_email = _shadow_email(email, client_id)

    def _sync():
        admin_client, admin_api = _get_admin_api(url, service_role_key)
        user_id = _find_user_by_email(admin_api, shadow_email)

        if user_id:
            logging.getLogger(__name__).info(
                "Updating existing shadow user %s for client %s", shadow_email, client_id
            )
            admin_api.update_user_by_id(
                user_id,
                {"password": password, "email_confirm": True},
            )
        else:
            try:
                admin_api.create_user(
                    {
                        "email": shadow_email,
                        "email_confirm": True,
                        "password": password,
                        "user_metadata": {
                            "shadow_for": email,
                            "client_id": client_id,
                        },
                    }
                )
            except Exception as exc:
                if "already been registered" not in str(exc):
                    raise
                logging.getLogger(__name__).info(
                    "Shadow user %s already exists for client %s", shadow_email, client_id
                )

    await _run_in_thread(_sync)


async def generate_client_session_tokens(client_id: str, email: str) -> Dict[str, str]:
    """
    Create (or update) a client Supabase Auth user and return a fresh access/refresh token pair.

    A temporary password is generated for the user to establish the session.
    """
    credentials = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
    if not credentials:
        raise ClientSupabaseAuthError(f"Client {client_id} is missing Supabase configuration")

    url, anon_key, service_role_key = credentials
    if not url or not service_role_key or not anon_key:
        raise ClientSupabaseAuthError(f"Client {client_id} Supabase config incomplete")

    temp_password = secrets.token_urlsafe(24)
    shadow_email = _shadow_email(email, client_id)

    def _sync() -> Dict[str, str]:
        admin_client, admin_api = _get_admin_api(url, service_role_key)
        user_id = _find_user_by_email(admin_api, shadow_email)

        if user_id:
            logging.getLogger(__name__).info(
                "Generating session for existing shadow user %s (client %s)",
                shadow_email,
                client_id,
            )
            admin_api.update_user_by_id(
                user_id,
                {"password": temp_password, "email_confirm": True},
            )
        else:
            try:
                created = admin_api.create_user(
                    {
                        "email": shadow_email,
                        "email_confirm": True,
                        "password": temp_password,
                        "user_metadata": {
                            "shadow_for": email,
                            "client_id": client_id,
                        },
                    }
                )
                user_id = str(created.user.id) if getattr(created, "user", None) else None
            except Exception as exc:
                # Handle duplicate email race: lookup existing user instead of failing
                if "already been registered" in str(exc):
                    user_id = _find_user_by_email(admin_api, shadow_email)
                    logging.getLogger(__name__).info(
                        "Shadow user %s already exists when creating session (client %s)",
                        shadow_email,
                        client_id,
                    )
                else:
                    raise

            if not user_id:
                raise ClientSupabaseAuthError("Failed to create Supabase user for client")

        anon_client: SupabaseClient = create_client(url, anon_key)
        auth_response = anon_client.auth.sign_in_with_password(
            {"email": shadow_email, "password": temp_password}
        )
        session = auth_response.session
        if not session:
            raise ClientSupabaseAuthError("Supabase session response did not include a session")

        return {
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
            "token_type": getattr(session, "token_type", "bearer"),
            "expires_in": getattr(session, "expires_in", None),
            "user_id": user_id,
        }

    return await _run_in_thread(_sync)
