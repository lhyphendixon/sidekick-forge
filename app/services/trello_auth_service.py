"""
Trello auth service — manages authorize redirect, token storage, and retrieval.

Trello uses API Key + User Token auth. The user authorizes via a redirect to
trello.com/1/authorize, then we capture the token via a return_url callback.
Tokens can be set to never expire.
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

from supabase import Client

from app.config import settings
from app.services.client_service_supabase import ClientService


TRELLO_AUTHORIZE_URL = "https://trello.com/1/authorize"

logger = logging.getLogger(__name__)


@dataclass
class TrelloTokenBundle:
    api_key: str
    token: str
    member_name: Optional[str]
    extra: Dict[str, Any]


class TrelloAuthError(RuntimeError):
    """Raised when the Trello auth flow encounters an error."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass(frozen=True)
class _TokenStore:
    name: str
    client: Client


class TrelloAuthService:
    """Manages Trello authorization, tokens, and persistence."""

    def __init__(
        self,
        client_service: ClientService,
        *,
        primary_supabase: Optional[Client] = None,
        platform_supabase: Optional[Client] = None,
    ) -> None:
        self.client_service = client_service
        self._api_key = settings.trello_api_key
        self._app_name = settings.trello_app_name
        self._return_url = settings.trello_return_url
        self._preferred_store = settings.trello_token_preferred_store
        self._mirror_tokens = settings.trello_token_mirror_stores

        self._stores: List[_TokenStore] = self._build_store_order(
            primary_supabase,
            platform_supabase or getattr(client_service, "supabase", None),
        )
        self._last_store_cache: Dict[str, _TokenStore] = {}

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def ensure_configured(self) -> None:
        if not self._api_key or not self._return_url:
            raise TrelloAuthError(
                "Trello integration is not fully configured. "
                "Set TRELLO_API_KEY and TRELLO_RETURN_URL."
            )

    @property
    def api_key(self) -> str:
        return self._api_key or ""

    def build_authorization_url(self, client_id: str, admin_user_id: str) -> str:
        """Generate the Trello authorization URL with encoded state in return_url."""
        self.ensure_configured()
        state = self._encode_state(client_id, admin_user_id)
        # Trello's authorize endpoint returns the token via fragment or return_url
        # We append the state to the return_url so we can identify the client on callback
        callback = f"{self._return_url}?state={state}"
        params = {
            "expiration": "never",
            "name": self._app_name or "Sidekick Forge",
            "scope": "read,write",
            "response_type": "token",
            "key": self._api_key,
            "return_url": callback,
        }
        return f"{TRELLO_AUTHORIZE_URL}?{urlencode(params)}"

    def parse_state(self, state: str) -> Dict[str, str]:
        """Validate and decode the state payload."""
        try:
            decoded = base64.urlsafe_b64decode(state.encode()).decode()
            client_id, admin_user_id, timestamp_str, nonce, signature = decoded.split(":")
        except Exception as exc:
            raise TrelloAuthError("Invalid state parameter received.") from exc

        raw = ":".join([client_id, admin_user_id, timestamp_str, nonce])
        expected_sig = self._sign_state(raw)
        if not hmac.compare_digest(expected_sig, signature):
            raise TrelloAuthError("State signature mismatch.")

        try:
            timestamp = int(timestamp_str)
        except ValueError as exc:
            raise TrelloAuthError("Invalid state timestamp.") from exc

        if time.time() - timestamp > 900:
            raise TrelloAuthError("Authorization state has expired. Please retry the connection.")

        return {"client_id": client_id, "admin_user_id": admin_user_id}

    def get_connection(self, client_id: str) -> Optional[Dict[str, Any]]:
        """Fetch the stored Trello connection for a client."""
        record, store = self._fetch_record(client_id)
        if store:
            self._last_store_cache[client_id] = store
        return record

    def disconnect(self, client_id: str) -> None:
        """Remove the stored Trello connection for the client."""
        self._last_store_cache.pop(client_id, None)
        for store in self._stores:
            try:
                store.client.table("client_trello_connections").delete().eq("client_id", client_id).execute()
            except Exception as exc:
                logger.debug("Failed to delete Trello token from %s store: %s", store.name, exc)

    def store_token(self, client_id: str, token: str, *, member_name: Optional[str] = None) -> None:
        """Persist a Trello user token for a client."""
        if not token:
            raise TrelloAuthError("Token is empty.")

        record = {
            "client_id": client_id,
            "api_key": self._api_key,
            "token": token,
            "member_name": member_name,
            "extra": {},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        write_targets = self._determine_write_targets(client_id)
        write_failures: List[str] = []

        for target in write_targets:
            try:
                target.client.table("client_trello_connections").upsert(
                    record, on_conflict="client_id"
                ).execute()
                self._last_store_cache[client_id] = target
            except Exception as exc:
                logger.error("Failed to persist Trello token in %s store: %s", target.name, exc)
                write_failures.append(target.name)

        if len(write_failures) == len(write_targets):
            raise TrelloAuthError("Failed to persist Trello token.")

    def get_token_bundle(self, client_id: str) -> Optional[TrelloTokenBundle]:
        """Retrieve a valid token bundle for a client."""
        record = self.get_connection(client_id)
        if not record:
            return None
        token = record.get("token", "")
        api_key = record.get("api_key") or self._api_key or ""
        if not token or not api_key:
            return None
        return TrelloTokenBundle(
            api_key=api_key,
            token=token,
            member_name=record.get("member_name"),
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
            raise TrelloAuthError("No Supabase client available for Trello token storage.")
        return stores

    def _fetch_record(self, client_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[_TokenStore]]:
        for store in self._stores:
            try:
                res = (
                    store.client.table("client_trello_connections")
                    .select("*")
                    .eq("client_id", client_id)
                    .limit(1)
                    .execute()
                )
            except Exception as exc:
                logger.debug("Failed to query Trello token store %s: %s", store.name, exc)
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
        for store in self._stores:
            if store is primary_store:
                continue
            if self._mirror_tokens:
                targets.append(store)
        if not targets and self._stores:
            targets.append(self._stores[0])
        return targets
