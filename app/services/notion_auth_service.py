"""
Notion auth service — manages OAuth 2.0 Authorization Code Grant flow.

Notion uses standard OAuth with server-side code exchange.
Access tokens never expire (no refresh logic needed).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx
from supabase import Client

from app.config import settings
from app.services.client_service_supabase import ClientService

NOTION_AUTH_URL = "https://api.notion.com/v1/oauth/authorize"
NOTION_TOKEN_URL = "https://api.notion.com/v1/oauth/token"

logger = logging.getLogger(__name__)

TABLE_NAME = "client_notion_connections"


@dataclass
class NotionTokenBundle:
    access_token: str
    workspace_name: Optional[str]
    workspace_id: Optional[str]
    bot_id: Optional[str]
    extra: Dict[str, Any]


class NotionAuthError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass(frozen=True)
class _TokenStore:
    name: str
    client: Client


class NotionAuthService:
    """Manages Notion OAuth authorization, token storage, and retrieval."""

    def __init__(
        self,
        client_service: ClientService,
        *,
        primary_supabase: Optional[Client] = None,
        platform_supabase: Optional[Client] = None,
    ) -> None:
        self.client_service = client_service
        self._client_id = settings.notion_oauth_client_id
        self._client_secret = settings.notion_oauth_client_secret
        self._redirect_uri = settings.notion_oauth_redirect_uri
        self._preferred_store = settings.notion_token_preferred_store

        self._stores: List[_TokenStore] = self._build_store_order(
            primary_supabase,
            platform_supabase or getattr(client_service, "supabase", None),
        )
        self._last_store_cache: Dict[str, _TokenStore] = {}

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def ensure_configured(self) -> None:
        if not self._client_id or not self._client_secret or not self._redirect_uri:
            raise NotionAuthError(
                "Notion integration is not fully configured. "
                "Set NOTION_OAUTH_CLIENT_ID, NOTION_OAUTH_CLIENT_SECRET, and NOTION_OAUTH_REDIRECT_URI."
            )

    def build_authorization_url(self, client_id: str, admin_user_id: str) -> str:
        """Generate the Notion OAuth authorization URL."""
        self.ensure_configured()
        state = self._encode_state(client_id, admin_user_id)
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "owner": "user",
            "redirect_uri": self._redirect_uri,
            "state": state,
        }
        return f"{NOTION_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        """Exchange authorization code for access token.
        Notion uses HTTP Basic Auth (client_id:client_secret) for token exchange.
        Returns: {"access_token", "workspace_name", "workspace_id", "bot_id", ...}
        """
        self.ensure_configured()
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(
                NOTION_TOKEN_URL,
                auth=(self._client_id, self._client_secret),
                json={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self._redirect_uri,
                },
            )
            if resp.status_code >= 400:
                raise NotionAuthError(f"Notion token exchange failed: {resp.status_code} {resp.text[:300]}")
            return resp.json()

    def parse_state(self, state: str) -> Dict[str, str]:
        """Validate and decode the state payload."""
        try:
            decoded = base64.urlsafe_b64decode(state.encode()).decode()
            client_id, admin_user_id, timestamp_str, nonce, signature = decoded.split(":")
        except Exception as exc:
            raise NotionAuthError("Invalid state parameter received.") from exc

        raw = ":".join([client_id, admin_user_id, timestamp_str, nonce])
        expected_sig = self._sign_state(raw)
        if not hmac.compare_digest(expected_sig, signature):
            raise NotionAuthError("State signature mismatch.")

        try:
            timestamp = int(timestamp_str)
        except ValueError as exc:
            raise NotionAuthError("Invalid state timestamp.") from exc

        if time.time() - timestamp > 900:
            raise NotionAuthError("Authorization state has expired. Please retry the connection.")

        return {"client_id": client_id, "admin_user_id": admin_user_id}

    def get_connection(self, client_id: str) -> Optional[Dict[str, Any]]:
        record, store = self._fetch_record(client_id)
        if store:
            self._last_store_cache[client_id] = store
        return record

    def disconnect(self, client_id: str) -> None:
        self._last_store_cache.pop(client_id, None)
        for store in self._stores:
            try:
                store.client.table(TABLE_NAME).delete().eq("client_id", client_id).execute()
            except Exception as exc:
                logger.debug("Failed to delete Notion token from %s store: %s", store.name, exc)

    def store_connection(
        self,
        client_id: str,
        access_token: str,
        *,
        workspace_name: Optional[str] = None,
        workspace_id: Optional[str] = None,
        bot_id: Optional[str] = None,
    ) -> None:
        if not access_token:
            raise NotionAuthError("Access token is empty.")

        record = {
            "client_id": client_id,
            "access_token": access_token,
            "workspace_name": workspace_name,
            "workspace_id": workspace_id,
            "bot_id": bot_id,
            "extra": {},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        write_targets = self._determine_write_targets(client_id)
        write_failures: List[str] = []

        for target in write_targets:
            try:
                target.client.table(TABLE_NAME).upsert(
                    record, on_conflict="client_id"
                ).execute()
                self._last_store_cache[client_id] = target
            except Exception as exc:
                logger.error("Failed to persist Notion token in %s store: %s", target.name, exc)
                write_failures.append(target.name)

        if len(write_failures) == len(write_targets):
            raise NotionAuthError("Failed to persist Notion token.")

    def get_token_bundle(self, client_id: str) -> Optional[NotionTokenBundle]:
        record = self.get_connection(client_id)
        if not record:
            return None
        access_token = record.get("access_token", "")
        if not access_token:
            return None
        return NotionTokenBundle(
            access_token=access_token,
            workspace_name=record.get("workspace_name"),
            workspace_id=record.get("workspace_id"),
            bot_id=record.get("bot_id"),
            extra=record.get("extra") or {},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _encode_state(self, client_id: str, admin_user_id: str) -> str:
        nonce = secrets.token_hex(8)
        timestamp = str(int(time.time()))
        raw = ":".join([client_id, admin_user_id, timestamp, nonce])
        signature = self._sign_state(raw)
        payload = ":".join([raw, signature])
        return base64.urlsafe_b64encode(payload.encode()).decode()

    def _sign_state(self, value: str) -> str:
        return hmac.new(settings.secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()

    def _build_store_order(
        self,
        primary_supabase: Optional[Client],
        platform_supabase: Optional[Client],
    ) -> List[_TokenStore]:
        stores: List[_TokenStore] = []

        def add_store(name: str, supabase_client: Optional[Client]) -> None:
            if not supabase_client:
                return
            for existing in stores:
                if existing.client is supabase_client:
                    return
            stores.append(_TokenStore(name=name, client=supabase_client))

        if self._preferred_store == "primary" and primary_supabase:
            add_store("primary", primary_supabase)
            add_store("platform", platform_supabase)
        else:
            add_store("platform", platform_supabase)
            add_store("primary", primary_supabase)

        if not stores:
            raise NotionAuthError("No Supabase client available for Notion token storage.")
        return stores

    def _fetch_record(self, client_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[_TokenStore]]:
        for store in self._stores:
            try:
                res = (
                    store.client.table(TABLE_NAME)
                    .select("*")
                    .eq("client_id", client_id)
                    .limit(1)
                    .execute()
                )
            except Exception as exc:
                logger.debug("Failed to query Notion token store %s: %s", store.name, exc)
                continue
            rows = res.data or []
            if rows:
                return rows[0], store
        return None, None

    def _determine_write_targets(self, client_id: str) -> List[_TokenStore]:
        primary_store = self._last_store_cache.get(client_id) or (self._stores[0] if self._stores else None)
        targets: List[_TokenStore] = []
        if primary_store:
            targets.append(primary_store)
        if not targets and self._stores:
            targets.append(self._stores[0])
        return targets
