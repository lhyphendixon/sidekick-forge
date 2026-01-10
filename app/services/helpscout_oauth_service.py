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

from app.config import settings
from app.services.client_service_supabase import ClientService


HELPSCOUT_AUTH_URL = "https://secure.helpscout.net/authentication/authorizeClientApplication"
HELPSCOUT_TOKEN_URL = "https://api.helpscout.net/v2/oauth2/token"


logger = logging.getLogger(__name__)


@dataclass
class HelpScoutTokenBundle:
    """Container for HelpScout OAuth tokens."""
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


class HelpScoutOAuthError(RuntimeError):
    """Raised when OAuth flow encounters an unrecoverable error."""

    def __init__(self, message: str, *, error_code: Optional[str] = None) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class _TokenStore:
    """Internal representation of a Supabase store for tokens."""
    name: str
    client: Client


class HelpScoutOAuthService:
    """Manages HelpScout OAuth authorization, tokens, and persistence."""

    TABLE_NAME = "client_helpscout_connections"

    def __init__(
        self,
        client_service: ClientService,
        *,
        primary_supabase: Optional[Client] = None,
        platform_supabase: Optional[Client] = None,
    ) -> None:
        self.client_service = client_service
        self._client_id = settings.helpscout_oauth_client_id
        self._client_secret = settings.helpscout_oauth_client_secret
        self._redirect_uri = settings.helpscout_oauth_redirect_uri
        self._preferred_store = settings.helpscout_token_preferred_store
        self._mirror_tokens = settings.helpscout_token_mirror_stores
        self._refresh_margin = timedelta(seconds=settings.helpscout_token_refresh_margin_seconds)

        self._stores: List[_TokenStore] = self._build_store_order(
            primary_supabase,
            platform_supabase or getattr(client_service, "supabase", None),
        )
        self._last_store_cache: Dict[str, _TokenStore] = {}

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def ensure_configured(self) -> None:
        """Raise if HelpScout OAuth is not fully configured."""
        if not self._client_id or not self._client_secret or not self._redirect_uri:
            raise HelpScoutOAuthError(
                "HelpScout OAuth is not fully configured. "
                "Set HELPSCOUT_OAUTH_CLIENT_ID, HELPSCOUT_OAUTH_CLIENT_SECRET, and HELPSCOUT_OAUTH_REDIRECT_URI."
            )

    def build_authorization_url(self, client_id: str, admin_user_id: str) -> str:
        """Generate the HelpScout authorization URL with encoded state."""
        self.ensure_configured()
        state = self._encode_state(client_id, admin_user_id)
        params = {
            "client_id": self._client_id,
            "state": state,
        }
        return f"{HELPSCOUT_AUTH_URL}?{urlencode(params)}"

    def parse_state(self, state: str) -> Dict[str, str]:
        """Validate and decode the state payload."""
        try:
            decoded = base64.urlsafe_b64decode(state.encode()).decode()
            client_id, admin_user_id, timestamp_str, nonce, signature = decoded.split(":")
        except Exception as exc:
            raise HelpScoutOAuthError("Invalid state parameter received.") from exc

        raw = ":".join([client_id, admin_user_id, timestamp_str, nonce])
        expected_sig = self._sign_state(raw)
        if not hmac.compare_digest(expected_sig, signature):
            raise HelpScoutOAuthError("State signature mismatch.")

        try:
            timestamp = int(timestamp_str)
        except ValueError as exc:
            raise HelpScoutOAuthError("Invalid state timestamp.") from exc

        if time.time() - timestamp > 900:
            raise HelpScoutOAuthError("OAuth state has expired. Please retry the connection.")

        return {"client_id": client_id, "admin_user_id": admin_user_id}

    def get_connection(self, client_id: str) -> Optional[Dict[str, Any]]:
        """Fetch the stored HelpScout connection for a client."""
        record, store = self._fetch_record(client_id)
        if store:
            self._last_store_cache[client_id] = store
        return record

    def disconnect(self, client_id: str) -> None:
        """Remove the stored HelpScout connection for the client."""
        self._last_store_cache.pop(client_id, None)
        for store in self._stores:
            try:
                store.client.table(self.TABLE_NAME).delete().eq("client_id", client_id).execute()
            except Exception as exc:
                logger.debug("Failed to delete HelpScout token from %s store: %s", store.name, exc)

    async def exchange_code(self, client_id: str, code: str) -> None:
        """Exchange an authorization code for tokens and store them."""
        self.ensure_configured()
        payload = {
            "grant_type": "authorization_code",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "code": code,
        }
        tokens = await self._request_token(payload)
        self._upsert_connection(client_id, tokens)

    async def ensure_valid_token(self, client_id: str, *, force_refresh: bool = False) -> Optional[HelpScoutTokenBundle]:
        """Retrieve a valid token bundle, refreshing if needed."""
        record = self.get_connection(client_id)
        if not record:
            return None

        bundle = self._record_to_bundle(record)
        if not self._should_refresh(bundle, force_refresh=force_refresh):
            return bundle

        if not bundle.refresh_token:
            raise HelpScoutOAuthError(
                "Stored HelpScout token has expired and no refresh token is available. Please reconnect HelpScout."
            )
        try:
            return await self._refresh_bundle(client_id, bundle.refresh_token)
        except HelpScoutOAuthError as exc:
            if exc.error_code == "invalid_grant" or "invalid_grant" in str(exc).lower():
                logger.warning("HelpScout refresh token was rejected; removing stored connection for %s", client_id)
                self.disconnect(client_id)
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
            raise HelpScoutOAuthError("No Supabase client available for HelpScout token storage.")
        return stores

    def _fetch_record(self, client_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[_TokenStore]]:
        for store in self._stores:
            try:
                res = (
                    store.client.table(self.TABLE_NAME)
                    .select("*")
                    .eq("client_id", client_id)
                    .limit(1)
                    .execute()
                )
            except Exception as exc:
                logger.debug("Failed to query HelpScout token store %s: %s", store.name, exc)
                continue
            rows = res.data or []
            if rows:
                return rows[0], store
        return None, None

    async def _request_token(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(HELPSCOUT_TOKEN_URL, data=payload)
        except httpx.HTTPError as exc:
            raise HelpScoutOAuthError(f"Failed to reach HelpScout token endpoint: {exc}") from exc

        try:
            response_payload = response.json()
        except json.JSONDecodeError as exc:
            raise HelpScoutOAuthError("Unexpected response format from HelpScout token endpoint.") from exc

        if response.status_code >= 400:
            error_code = response_payload.get("error") if isinstance(response_payload, dict) else None
            detail = None
            if isinstance(response_payload, dict):
                detail = response_payload.get("error_description") or response_payload.get("message")
            raise HelpScoutOAuthError(
                f"HelpScout token endpoint returned {response.status_code}: {detail or response.text}",
                error_code=error_code,
            )
        return response_payload

    def _upsert_connection(self, client_id: str, token_payload: Dict[str, Any]) -> None:
        access_token = token_payload.get("access_token")
        refresh_token = token_payload.get("refresh_token")
        token_type = token_payload.get("token_type")
        expires_in = token_payload.get("expires_in")
        extra_payload = token_payload if isinstance(token_payload, dict) else {}

        # Log what HelpScout actually returned
        logger.info(
            "HelpScout OAuth token response for client %s: has_refresh_token=%s, expires_in=%s, keys=%s",
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
            raise HelpScoutOAuthError("Token payload from HelpScout is missing access_token.")

        write_targets = self._determine_write_targets(client_id)
        write_failures: List[str] = []

        for target in write_targets:
            try:
                target.client.table(self.TABLE_NAME).upsert(record, on_conflict="client_id").execute()
                self._last_store_cache[client_id] = target
            except Exception as exc:
                logger.error("Failed to persist HelpScout tokens in %s store: %s", target.name, exc)
                write_failures.append(target.name)

        if len(write_failures) == len(write_targets):
            raise HelpScoutOAuthError("Failed to persist HelpScout tokens.")

    def _record_to_bundle(self, record: Optional[Dict[str, Any]]) -> HelpScoutTokenBundle:
        if not record:
            raise HelpScoutOAuthError("No HelpScout connection record available.")
        expires_at = record.get("expires_at")
        expires_dt = None
        if expires_at:
            try:
                expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except ValueError:
                expires_dt = None
        return HelpScoutTokenBundle(
            access_token=record.get("access_token", ""),
            refresh_token=record.get("refresh_token"),
            token_type=record.get("token_type"),
            expires_at=expires_dt,
            extra=record.get("extra") or {},
        )

    def _should_refresh(self, bundle: HelpScoutTokenBundle, *, force_refresh: bool = False) -> bool:
        if force_refresh:
            return True
        if bundle.is_expired:
            return True
        if not bundle.expires_at or self._refresh_margin <= timedelta(0):
            return False
        now = datetime.now(timezone.utc)
        return bundle.expires_at - now <= self._refresh_margin

    async def _refresh_bundle(self, client_id: str, refresh_token: str) -> HelpScoutTokenBundle:
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
            raise HelpScoutOAuthError("Failed to reload refreshed HelpScout token bundle.")
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
