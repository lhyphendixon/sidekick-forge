"""
Lore Social OAuth Service — LinkedIn, Twitter/X, Facebook OAuth flows.

Handles authorization, token exchange, profile/content fetching,
and extraction into Lore-compatible text for the import pipeline.
"""

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)


@dataclass
class OAuthTokenBundle:
    access_token: str
    refresh_token: Optional[str] = None
    token_type: Optional[str] = None
    expires_at: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# State encoding (HMAC-signed, same pattern as Asana OAuth)
# ---------------------------------------------------------------------------

def _get_signing_key() -> str:
    return os.getenv("JWT_SECRET_KEY", os.getenv("SECRET_KEY", "lore-oauth-fallback-key"))


def encode_state(provider: str, admin_user_id: str, extra: str = "") -> str:
    nonce = secrets.token_hex(8)
    timestamp = str(int(time.time()))
    raw = ":".join([provider, admin_user_id, timestamp, nonce, extra])
    sig = hmac.new(_get_signing_key().encode(), raw.encode(), hashlib.sha256).hexdigest()[:16]
    payload = f"{raw}:{sig}"
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_state(state: str, max_age: int = 900) -> Dict[str, str]:
    try:
        payload = base64.urlsafe_b64decode(state.encode()).decode()
        parts = payload.rsplit(":", 1)
        raw, sig = parts[0], parts[1]
        expected = hmac.new(_get_signing_key().encode(), raw.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected):
            raise ValueError("Invalid state signature")
        fields = raw.split(":")
        ts = int(fields[2])
        if time.time() - ts > max_age:
            raise ValueError("State expired")
        return {"provider": fields[0], "admin_user_id": fields[1], "extra": fields[4] if len(fields) > 4 else ""}
    except Exception as exc:
        raise ValueError(f"Invalid OAuth state: {exc}")


# ---------------------------------------------------------------------------
# LinkedIn
# ---------------------------------------------------------------------------

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_API_BASE = "https://api.linkedin.com/v2"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"


def linkedin_authorize_url(admin_user_id: str) -> str:
    client_id = os.getenv("LINKEDIN_OAUTH_CLIENT_ID", "")
    redirect_uri = os.getenv("LINKEDIN_OAUTH_REDIRECT_URI", "")
    if not client_id or not redirect_uri:
        raise ValueError("LinkedIn OAuth not configured. Set LINKEDIN_OAUTH_CLIENT_ID and LINKEDIN_OAUTH_REDIRECT_URI.")
    state = encode_state("linkedin", admin_user_id)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": "openid profile email w_member_social",
    }
    return f"{LINKEDIN_AUTH_URL}?{urlencode(params)}"


async def linkedin_exchange_code(code: str) -> OAuthTokenBundle:
    client_id = os.getenv("LINKEDIN_OAUTH_CLIENT_ID", "")
    client_secret = os.getenv("LINKEDIN_OAUTH_CLIENT_SECRET", "")
    redirect_uri = os.getenv("LINKEDIN_OAUTH_REDIRECT_URI", "")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(LINKEDIN_TOKEN_URL, data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        })
        resp.raise_for_status()
        data = resp.json()
    expires_at = None
    if data.get("expires_in"):
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])).isoformat()
    return OAuthTokenBundle(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        token_type=data.get("token_type"),
        expires_at=expires_at,
        extra=data,
    )


async def linkedin_fetch_profile(token: str) -> List[str]:
    """Fetch LinkedIn profile data and return as text blocks for Lore extraction."""
    texts = []
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
        # Basic profile via OpenID userinfo
        resp = await client.get(LINKEDIN_USERINFO_URL)
        if resp.status_code == 200:
            info = resp.json()
            name = info.get("name", "")
            email = info.get("email", "")
            if name:
                texts.append(f"LinkedIn profile: Name is {name}. Email is {email}.")

        # Try v2 profile endpoint for richer data
        resp = await client.get(f"{LINKEDIN_API_BASE}/me?projection=(id,firstName,lastName,headline,vanityName)")
        if resp.status_code == 200:
            me = resp.json()
            headline = ""
            if me.get("headline"):
                hl = me["headline"]
                if isinstance(hl, dict) and "localized" in hl:
                    headline = list(hl["localized"].values())[0] if hl["localized"] else ""
                elif isinstance(hl, str):
                    headline = hl
            if headline:
                texts.append(f"LinkedIn headline: {headline}")

        # Positions (work history)
        resp = await client.get(f"{LINKEDIN_API_BASE}/positions?q=members&projection=(elements*(title,company,description,startDate,endDate))")
        if resp.status_code == 200:
            positions = resp.json().get("elements", [])
            for pos in positions[:10]:
                title = pos.get("title", "")
                company = pos.get("company", {}).get("name", "") if isinstance(pos.get("company"), dict) else ""
                desc = pos.get("description", "")
                texts.append(f"LinkedIn position: {title} at {company}. {desc}".strip())

        # Posts/shares
        resp = await client.get(f"{LINKEDIN_API_BASE}/ugcPosts?q=authors&authors=List(urn:li:person:me)&count=20")
        if resp.status_code == 200:
            posts = resp.json().get("elements", [])
            for post in posts[:20]:
                body = post.get("specificContent", {}).get("com.linkedin.ugc.ShareContent", {})
                text = body.get("shareCommentary", {}).get("text", "")
                if text:
                    texts.append(f"LinkedIn post: {text}")

    logger.info(f"LinkedIn: fetched {len(texts)} text blocks")
    return texts


# ---------------------------------------------------------------------------
# Twitter / X
# ---------------------------------------------------------------------------

TWITTER_AUTH_URL = "https://twitter.com/i/oauth2/authorize"
TWITTER_TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
TWITTER_API_BASE = "https://api.twitter.com/2"

# PKCE challenge for Twitter OAuth 2.0
_twitter_verifiers: Dict[str, str] = {}


def twitter_authorize_url(admin_user_id: str) -> str:
    client_id = os.getenv("TWITTER_OAUTH_CLIENT_ID", "")
    redirect_uri = os.getenv("TWITTER_OAUTH_REDIRECT_URI", "")
    if not client_id or not redirect_uri:
        raise ValueError("Twitter/X OAuth not configured. Set TWITTER_OAUTH_CLIENT_ID and TWITTER_OAUTH_REDIRECT_URI.")
    state = encode_state("twitter", admin_user_id)
    # PKCE
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    _twitter_verifiers[state] = verifier
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": "tweet.read users.read offline.access",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{TWITTER_AUTH_URL}?{urlencode(params)}"


async def twitter_exchange_code(code: str, state: str) -> OAuthTokenBundle:
    client_id = os.getenv("TWITTER_OAUTH_CLIENT_ID", "")
    client_secret = os.getenv("TWITTER_OAUTH_CLIENT_SECRET", "")
    redirect_uri = os.getenv("TWITTER_OAUTH_REDIRECT_URI", "")
    verifier = _twitter_verifiers.pop(state, "")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(TWITTER_TOKEN_URL,
            auth=(client_id, client_secret),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    expires_at = None
    if data.get("expires_in"):
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])).isoformat()
    return OAuthTokenBundle(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=expires_at,
        extra=data,
    )


async def twitter_fetch_profile(token: str) -> List[str]:
    """Fetch Twitter/X profile and recent tweets for Lore extraction."""
    texts = []
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
        # User profile
        resp = await client.get(f"{TWITTER_API_BASE}/users/me?user.fields=name,username,description,location,public_metrics")
        if resp.status_code == 200:
            user = resp.json().get("data", {})
            name = user.get("name", "")
            bio = user.get("description", "")
            location = user.get("location", "")
            if name:
                texts.append(f"Twitter/X profile: {name} (@{user.get('username', '')}). Bio: {bio}. Location: {location}.")

            # Recent tweets
            user_id = user.get("id")
            if user_id:
                resp = await client.get(
                    f"{TWITTER_API_BASE}/users/{user_id}/tweets?max_results=100&tweet.fields=text,created_at"
                )
                if resp.status_code == 200:
                    tweets = resp.json().get("data", [])
                    for tweet in tweets:
                        text = tweet.get("text", "")
                        if text and not text.startswith("RT @"):
                            texts.append(f"Tweet: {text}")

    logger.info(f"Twitter: fetched {len(texts)} text blocks")
    return texts


# ---------------------------------------------------------------------------
# Facebook
# ---------------------------------------------------------------------------

FACEBOOK_AUTH_URL = "https://www.facebook.com/v18.0/dialog/oauth"
FACEBOOK_TOKEN_URL = "https://graph.facebook.com/v18.0/oauth/access_token"
FACEBOOK_API_BASE = "https://graph.facebook.com/v18.0"


def facebook_authorize_url(admin_user_id: str) -> str:
    client_id = os.getenv("FACEBOOK_OAUTH_CLIENT_ID", "")
    redirect_uri = os.getenv("FACEBOOK_OAUTH_REDIRECT_URI", "")
    if not client_id or not redirect_uri:
        raise ValueError("Facebook OAuth not configured. Set FACEBOOK_OAUTH_CLIENT_ID and FACEBOOK_OAUTH_REDIRECT_URI.")
    state = encode_state("facebook", admin_user_id)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": "public_profile,user_posts",
        "response_type": "code",
    }
    return f"{FACEBOOK_AUTH_URL}?{urlencode(params)}"


async def facebook_exchange_code(code: str) -> OAuthTokenBundle:
    client_id = os.getenv("FACEBOOK_OAUTH_CLIENT_ID", "")
    client_secret = os.getenv("FACEBOOK_OAUTH_CLIENT_SECRET", "")
    redirect_uri = os.getenv("FACEBOOK_OAUTH_REDIRECT_URI", "")
    async with httpx.AsyncClient(timeout=15.0) as client:
        # Short-lived token
        resp = await client.get(FACEBOOK_TOKEN_URL, params={
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        })
        resp.raise_for_status()
        data = resp.json()

        # Exchange for long-lived token
        short_token = data["access_token"]
        resp = await client.get(FACEBOOK_TOKEN_URL, params={
            "grant_type": "fb_exchange_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "fb_exchange_token": short_token,
        })
        if resp.status_code == 200:
            data = resp.json()

    expires_at = None
    if data.get("expires_in"):
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])).isoformat()
    return OAuthTokenBundle(
        access_token=data["access_token"],
        expires_at=expires_at,
        extra=data,
    )


async def facebook_fetch_profile(token: str) -> List[str]:
    """Fetch Facebook profile and posts for Lore extraction."""
    texts = []
    params = {"access_token": token}
    async with httpx.AsyncClient(timeout=15.0) as client:
        # Profile
        resp = await client.get(f"{FACEBOOK_API_BASE}/me?fields=name,email,location,hometown,about,work,education", params=params)
        if resp.status_code == 200:
            me = resp.json()
            parts = [f"Facebook profile: {me.get('name', '')}"]
            if me.get("about"):
                parts.append(f"About: {me['about']}")
            if me.get("hometown", {}).get("name"):
                parts.append(f"Hometown: {me['hometown']['name']}")
            if me.get("location", {}).get("name"):
                parts.append(f"Location: {me['location']['name']}")
            for job in (me.get("work") or [])[:5]:
                employer = job.get("employer", {}).get("name", "")
                position = job.get("position", {}).get("name", "")
                if employer:
                    parts.append(f"Worked at {employer} as {position}")
            for edu in (me.get("education") or [])[:5]:
                school = edu.get("school", {}).get("name", "")
                if school:
                    parts.append(f"Studied at {school}")
            texts.append(". ".join(parts))

        # Recent posts
        resp = await client.get(f"{FACEBOOK_API_BASE}/me/posts?fields=message,created_time&limit=50", params=params)
        if resp.status_code == 200:
            posts = resp.json().get("data", [])
            for post in posts:
                msg = post.get("message", "")
                if msg:
                    texts.append(f"Facebook post: {msg}")

    logger.info(f"Facebook: fetched {len(texts)} text blocks")
    return texts
