from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx
from supabase import Client

# NOTE: settings import is deferred to avoid validation errors in agent container
# from app.config import settings
from app.services.client_service_supabase import ClientService


def _get_settings():
    """Lazy import of settings to avoid validation at module load time."""
    from app.config import settings
    return settings


ASANA_AUTH_URL = "https://app.asana.com/-/oauth_authorize"
ASANA_TOKEN_URL = "https://app.asana.com/-/oauth_token"


logger = logging.getLogger(__name__)


@dataclass
class AsanaTokenBundle:
    access_token: str
    refresh_token: Optional[str]
    token_type: Optional[str]
    expires_at: Optional[datetime]
    extra: Dict[str, Any]

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return datetime.now(timezone.utc) >= self.expires_at


class AsanaOAuthError(RuntimeError):
    """Raised when OAuth flow encounters an unrecoverable error."""

    def __init__(self, message: str, *, error_code: Optional[str] = None) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class _TokenStore:
    name: str
    client: Client


class AsanaOAuthService:
    """Manages Asana OAuth authorization, tokens, and persistence."""

    def __init__(
        self,
        client_service: ClientService,
        *,
        primary_supabase: Optional[Client] = None,
        platform_supabase: Optional[Client] = None,
    ) -> None:
        self.client_service = client_service
        # Lazy load settings to avoid validation errors in container environments
        settings = _get_settings()
        self._client_id = settings.asana_oauth_client_id
        self._client_secret = settings.asana_oauth_client_secret
        self._redirect_uri = settings.asana_oauth_redirect_uri
        self._scopes = settings.asana_oauth_scopes.split()
        self._preferred_store = settings.asana_token_preferred_store
        self._mirror_tokens = settings.asana_token_mirror_stores
        self._refresh_margin = timedelta(seconds=settings.asana_token_refresh_margin_seconds)

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
            raise AsanaOAuthError(
                "Asana OAuth is not fully configured. "
                "Set ASANA_OAUTH_CLIENT_ID, ASANA_OAUTH_CLIENT_SECRET, and ASANA_OAUTH_REDIRECT_URI."
            )

    def build_authorization_url(self, client_id: str, admin_user_id: str) -> str:
        """Generate the Asana authorization URL with encoded state."""
        self.ensure_configured()
        state = self._encode_state(client_id, admin_user_id)
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "state": state,
            "scope": " ".join(self._scopes),
        }
        return f"{ASANA_AUTH_URL}?{urlencode(params)}"

    def parse_state(self, state: str) -> Dict[str, str]:
        """Validate and decode the state payload."""
        try:
            decoded = base64.urlsafe_b64decode(state.encode()).decode()
            client_id, admin_user_id, timestamp_str, nonce, signature = decoded.split(":")
        except Exception as exc:
            raise AsanaOAuthError("Invalid state parameter received.") from exc

        raw = ":".join([client_id, admin_user_id, timestamp_str, nonce])
        expected_sig = self._sign_state(raw)
        if not hmac.compare_digest(expected_sig, signature):
            raise AsanaOAuthError("State signature mismatch.")

        try:
            timestamp = int(timestamp_str)
        except ValueError as exc:
            raise AsanaOAuthError("Invalid state timestamp.") from exc

        if time.time() - timestamp > 900:
            raise AsanaOAuthError("OAuth state has expired. Please retry the connection.")

        return {"client_id": client_id, "admin_user_id": admin_user_id}

    def get_connection(self, client_id: str) -> Optional[Dict[str, Any]]:
        """Fetch the stored Asana connection for a client."""
        record, store = self._fetch_record(client_id)
        if store:
            self._last_store_cache[client_id] = store
        return record

    def disconnect(self, client_id: str) -> None:
        """Remove the stored Asana connection for the client."""
        self._last_store_cache.pop(client_id, None)
        for store in self._stores:
            try:
                store.client.table("client_asana_connections").delete().eq("client_id", client_id).execute()
            except Exception as exc:
                logger.debug("Failed to delete Asana token from %s store: %s", store.name, exc)

    async def exchange_code(self, client_id: str, code: str) -> None:
        """Exchange an authorization code for tokens and store them."""
        self.ensure_configured()
        payload = {
            "grant_type": "authorization_code",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "redirect_uri": self._redirect_uri,
            "code": code,
        }
        tokens = await self._request_token(payload)
        self._upsert_connection(client_id, tokens)

    async def ensure_valid_token(self, client_id: str, *, force_refresh: bool = False) -> Optional[AsanaTokenBundle]:
        """Retrieve a valid token bundle, refreshing if needed."""
        record = self.get_connection(client_id)
        if not record:
            logger.debug("No Asana connection found for client %s", client_id)
            return None

        bundle = self._record_to_bundle(record)
        needs_refresh = self._should_refresh(bundle, force_refresh=force_refresh)

        logger.info(
            "Asana token check for %s: expires_at=%s, is_expired=%s, needs_refresh=%s, has_refresh_token=%s",
            client_id,
            bundle.expires_at.isoformat() if bundle.expires_at else "None",
            bundle.is_expired,
            needs_refresh,
            bool(bundle.refresh_token),
        )

        if not needs_refresh:
            return bundle

        if not bundle.refresh_token:
            logger.error(
                "Asana token for client %s has expired but NO refresh token is stored. "
                "This means Asana did not return a refresh_token during OAuth. "
                "User must reconnect Asana.",
                client_id,
            )
            raise AsanaOAuthError(
                "Stored Asana token has expired and no refresh token is available. Please reconnect Asana."
            )

        logger.info("Attempting to refresh Asana token for client %s", client_id)
        try:
            refreshed = await self._refresh_bundle(client_id, bundle.refresh_token)
            logger.info("Successfully refreshed Asana token for client %s", client_id)
            return refreshed
        except AsanaOAuthError as exc:
            if exc.error_code == "invalid_grant" or "invalid_grant" in str(exc).lower():
                logger.warning(
                    "Asana refresh token was rejected (invalid_grant) for client %s; removing connection. Error: %s",
                    client_id,
                    exc,
                )
                self.disconnect(client_id)
            else:
                logger.error("Asana token refresh failed for client %s: %s", client_id, exc)
            raise

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
        settings = _get_settings()
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
            raise AsanaOAuthError("No Supabase client available for Asana token storage.")
        return stores

    def _fetch_record(self, client_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[_TokenStore]]:
        for store in self._stores:
            try:
                res = (
                    store.client.table("client_asana_connections")
                    .select("*")
                    .eq("client_id", client_id)
                    .limit(1)
                    .execute()
                )
            except Exception as exc:
                logger.debug("Failed to query Asana token store %s: %s", store.name, exc)
                continue
            rows = res.data or []
            if rows:
                return rows[0], store
        return None, None

    async def _request_token(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(ASANA_TOKEN_URL, data=payload)
        except httpx.HTTPError as exc:
            raise AsanaOAuthError(f"Failed to reach Asana token endpoint: {exc}") from exc

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise AsanaOAuthError("Unexpected response format from Asana token endpoint.") from exc
        if response.status_code >= 400:
            error_code = payload.get("error") if isinstance(payload, dict) else None
            detail = None
            if isinstance(payload, dict):
                detail = payload.get("error_description") or payload.get("message")
            raise AsanaOAuthError(
                f"Asana token endpoint returned {response.status_code}: {detail or response.text}",
                error_code=error_code,
            )
        return payload

    def _upsert_connection(self, client_id: str, token_payload: Dict[str, Any]) -> None:
        access_token = token_payload.get("access_token")
        refresh_token = token_payload.get("refresh_token")
        token_type = token_payload.get("token_type")
        expires_in = token_payload.get("expires_in")
        extra_payload = token_payload if isinstance(token_payload, dict) else {}
        
        # Log what Asana actually returned
        logger.info(
            "Asana OAuth token response for client %s: has_refresh_token=%s, expires_in=%s, keys=%s",
            client_id,
            bool(refresh_token),
            expires_in,
            list(token_payload.keys())
        )

        expires_at: Optional[str] = None
        if isinstance(expires_in, (int, float)):
            expires_dt = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
            expires_at = expires_dt.isoformat()

        record = {
            "client_id": client_id,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": token_type,
            "expires_at": expires_at,
            "extra": extra_payload,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if not access_token:
            raise AsanaOAuthError("Token payload from Asana is missing access_token.")

        write_targets = self._determine_write_targets(client_id)
        write_failures: List[str] = []

        for target in write_targets:
            try:
                target.client.table("client_asana_connections").upsert(record, on_conflict="client_id").execute()
                self._last_store_cache[client_id] = target
            except Exception as exc:
                logger.error("Failed to persist Asana tokens in %s store: %s", target.name, exc)
                write_failures.append(target.name)

        if len(write_failures) == len(write_targets):
            raise AsanaOAuthError("Failed to persist Asana tokens.")

    def _record_to_bundle(self, record: Optional[Dict[str, Any]]) -> AsanaTokenBundle:
        if not record:
            raise AsanaOAuthError("No Asana connection record available.")
        expires_at = record.get("expires_at")
        expires_dt = None
        if expires_at:
            try:
                expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except ValueError:
                expires_dt = None
        return AsanaTokenBundle(
            access_token=record.get("access_token", ""),
            refresh_token=record.get("refresh_token"),
            token_type=record.get("token_type"),
            expires_at=expires_dt,
            extra=record.get("extra") or {},
        )

    def _should_refresh(self, bundle: AsanaTokenBundle, *, force_refresh: bool = False) -> bool:
        if force_refresh:
            return True
        if bundle.is_expired:
            return True
        if not bundle.expires_at or self._refresh_margin <= timedelta(0):
            return False
        now = datetime.now(timezone.utc)
        return bundle.expires_at - now <= self._refresh_margin

    async def _refresh_bundle(self, client_id: str, refresh_token: str) -> AsanaTokenBundle:
        self.ensure_configured()
        payload = {
            "grant_type": "refresh_token",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": refresh_token,
        }
        tokens = await self._request_token(payload)
        self._upsert_connection(client_id, tokens)
        refreshed = self.get_connection(client_id)
        if not refreshed:
            raise AsanaOAuthError("Failed to reload refreshed Asana token bundle.")
        return self._record_to_bundle(refreshed)

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
