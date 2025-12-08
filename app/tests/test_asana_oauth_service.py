from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List

import pytest

from app.services.asana_oauth_service import (
    AsanaOAuthError,
    AsanaOAuthService,
    AsanaTokenBundle,
)


def _make_service(refresh_margin_seconds: int = 300):
    service = AsanaOAuthService.__new__(AsanaOAuthService)
    service.client_service = None
    service._client_id = "asana-client-id"
    service._client_secret = "asana-client-secret"
    service._redirect_uri = "https://example.com/callback"
    service._scopes = ["default"]
    service._preferred_store = "platform"
    service._mirror_tokens = False
    service._stores = []
    service._last_store_cache = {}
    service._refresh_margin = timedelta(seconds=refresh_margin_seconds)
    service.ensure_configured = lambda: None  # type: ignore[assignment]
    return service


def _setup_records(service: AsanaOAuthService):
    records: Dict[str, Dict[str, AsanaTokenBundle]] = {}
    requests: List[Dict[str, str]] = []

    def get_connection(client_id: str):
        return records.get(client_id)

    def record_to_bundle(record: Dict[str, AsanaTokenBundle]) -> AsanaTokenBundle:
        return record["bundle"]

    async def request_token(payload: Dict[str, str]):
        requests.append(payload)
        return {
            "access_token": "new-access-token",
            "refresh_token": "next-refresh-token",
            "token_type": "bearer",
            "expires_in": 3600,
        }

    def upsert(client_id: str, token_payload: Dict[str, str]):
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(token_payload.get("expires_in", 0)))
        records[client_id] = {
            "bundle": AsanaTokenBundle(
                access_token=token_payload.get("access_token", ""),
                refresh_token=token_payload.get("refresh_token"),
                token_type=token_payload.get("token_type"),
                expires_at=expires_at,
                extra=token_payload,
            )
        }

    service.get_connection = get_connection  # type: ignore[assignment]
    service._record_to_bundle = record_to_bundle  # type: ignore[assignment]
    service._request_token = request_token  # type: ignore[assignment]
    service._upsert_connection = upsert  # type: ignore[assignment]
    service.disconnect = lambda client_id: records.pop(client_id, None)  # type: ignore[assignment]
    return records, requests


@pytest.mark.asyncio
async def test_ensure_valid_token_refreshes_when_margin_reached():
    service = _make_service(refresh_margin_seconds=300)
    records, requests = _setup_records(service)
    records["client-1"] = {
        "bundle": AsanaTokenBundle(
            access_token="stale",
            refresh_token="refresh-token",
            token_type="bearer",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
            extra={},
        )
    }

    bundle = await service.ensure_valid_token("client-1")

    assert bundle.access_token == "new-access-token"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_ensure_valid_token_force_refresh_honors_flag():
    service = _make_service(refresh_margin_seconds=0)
    records, requests = _setup_records(service)
    records["client-2"] = {
        "bundle": AsanaTokenBundle(
            access_token="fresh",
            refresh_token="refresh-token",
            token_type="bearer",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            extra={},
        )
    }

    bundle = await service.ensure_valid_token("client-2", force_refresh=True)

    assert bundle.access_token == "new-access-token"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_ensure_valid_token_drops_connection_on_invalid_grant():
    service = _make_service(refresh_margin_seconds=0)
    records, _ = _setup_records(service)
    records["client-3"] = {
        "bundle": AsanaTokenBundle(
            access_token="expired",
            refresh_token="refresh-token",
            token_type="bearer",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            extra={},
        )
    }

    async def failing_request(_: Dict[str, str]):
        raise AsanaOAuthError("invalid grant", error_code="invalid_grant")

    service._request_token = failing_request  # type: ignore[assignment]

    with pytest.raises(AsanaOAuthError):
        await service.ensure_valid_token("client-3", force_refresh=True)

    assert "client-3" not in records
