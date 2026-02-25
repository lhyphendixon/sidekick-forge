"""
Utilities for issuing and validating short-lived embed tokens for public embeds.

Tokens are JWTs signed with the platform's JWT secret and carry tightly-scoped
permissions, including the client_id and agent_slug they are valid for.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Any
import uuid
import jwt

from app.config import settings


def _embed_subject(client_id: str, agent_slug: str) -> str:
    """Deterministic UUID5 subject for embeds (satisfies AuthContext UUID type)."""
    base = f"embed:{client_id}:{agent_slug}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, base))


def create_embed_token(
    *,
    client_id: str,
    agent_slug: str,
    ttl_seconds: int = 600,
    extra: Dict[str, Any] | None = None,
) -> str:
    """Create a short-lived JWT for public embeds.

    Payload includes:
    - sub: deterministic UUID so middleware treats it as authenticated
    - token_type: "embed"
    - permissions: ["embed", f"client:{client_id}", f"agent:{agent_slug}"]
    - client_id, agent_slug
    - iat/exp
    """
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "sub": _embed_subject(client_id, agent_slug),
        "token_type": "embed",
        "permissions": [
            "embed",
            f"client:{client_id}",
            f"agent:{agent_slug}",
        ],
        "client_id": client_id,
        "agent_slug": agent_slug,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    if extra:
        payload.update(extra)

    token = jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    # PyJWT returns str for recent versions
    return token

