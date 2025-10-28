from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.services.client_service_supabase import ClientService


ASANA_AUTH_URL = "https://app.asana.com/-/oauth_authorize"
ASANA_TOKEN_URL = "https://app.asana.com/-/oauth_token"


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


class AsanaOAuthService:
    """Manages Asana OAuth authorization, tokens, and persistence."""

    def __init__(self, client_service: ClientService) -> None:
        self.client_service = client_service
        self.supabase = client_service.supabase
        self._client_id = settings.asana_oauth_client_id
        self._client_secret = settings.asana_oauth_client_secret
        self._redirect_uri = settings.asana_oauth_redirect_uri
        self._scopes = settings.asana_oauth_scopes.split()

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
        try:
            res = (
                self.supabase.table("client_asana_connections")
                .select("*")
                .eq("client_id", client_id)
                .limit(1)
                .execute()
            )
            rows = res.data or []
            return rows[0] if rows else None
        except Exception:
            return None

    def disconnect(self, client_id: str) -> None:
        """Remove the stored Asana connection for the client."""
        try:
            self.supabase.table("client_asana_connections").delete().eq("client_id", client_id).execute()
        except Exception:
            # Non-fatal
            pass

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

    async def ensure_valid_token(self, client_id: str) -> Optional[AsanaTokenBundle]:
        """Retrieve a valid token bundle, refreshing if needed."""
        record = self.get_connection(client_id)
        if not record:
            return None

        bundle = self._record_to_bundle(record)
        if not bundle.is_expired:
            return bundle

        if not bundle.refresh_token:
            raise AsanaOAuthError(
                "Stored Asana token has expired and no refresh token is available. Please reconnect Asana."
            )
        self.ensure_configured()
        payload = {
            "grant_type": "refresh_token",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": bundle.refresh_token,
        }
        tokens = await self._request_token(payload)
        self._upsert_connection(client_id, tokens)
        return self._record_to_bundle(self.get_connection(client_id))

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

    async def _request_token(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(ASANA_TOKEN_URL, data=payload)
        except httpx.HTTPError as exc:
            raise AsanaOAuthError(f"Failed to reach Asana token endpoint: {exc}") from exc

        if response.status_code >= 400:
            detail = response.text
            raise AsanaOAuthError(f"Asana token endpoint returned {response.status_code}: {detail}")
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise AsanaOAuthError("Unexpected response format from Asana token endpoint.") from exc

    def _upsert_connection(self, client_id: str, token_payload: Dict[str, Any]) -> None:
        access_token = token_payload.get("access_token")
        refresh_token = token_payload.get("refresh_token")
        token_type = token_payload.get("token_type")
        expires_in = token_payload.get("expires_in")
        data = token_payload.get("data") or {}

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
            "extra": data,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if data and isinstance(data, dict):
            record["extra"] = data

        if not access_token:
            raise AsanaOAuthError("Token payload from Asana is missing access_token.")

        try:
            self.supabase.table("client_asana_connections").upsert(record, on_conflict="client_id").execute()
        except Exception as exc:
            raise AsanaOAuthError(f"Failed to persist Asana tokens: {exc}") from exc

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
