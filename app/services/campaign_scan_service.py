"""Campaign Scan Service

Analyses forwarded emails/newsletters for errors, typos, visual issues,
subject line effectiveness, CTA clarity, mobile responsiveness, and
deliverability flags using the zai-glm-4.7 multimodal model.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.integrations.supabase_client import supabase_manager

logger = logging.getLogger(__name__)

TABLE = "campaign_scans"

SCAN_SYSTEM_PROMPT = """\
You are an expert email marketing reviewer. Analyse the email campaign below \
and return a JSON object with your findings. Be thorough but concise.

Return ONLY valid JSON in this exact structure (no markdown fences):
{
  "summary": "One-sentence overall assessment",
  "score": <1-100 quality score>,
  "errors": [
    {"text": "exact problematic text or description", "issue": "what is wrong", "suggestion": "how to fix it"}
  ],
  "warnings": [
    {"text": "relevant excerpt", "issue": "potential problem", "suggestion": "recommendation"}
  ],
  "suggestions": [
    {"text": "relevant excerpt or area", "issue": "opportunity for improvement", "suggestion": "what to do"}
  ]
}

Categories to check:
- ERRORS: typos, misspellings, broken/malformed links, factual inaccuracies, HTML rendering issues
- WARNINGS: grammar issues, unclear CTAs, accessibility problems (missing alt text, low contrast), \
  subject line issues, sender name problems
- SUGGESTIONS: subject line effectiveness, CTA clarity and placement, mobile responsiveness tips, \
  content structure, spam trigger words, preheader text, personalization opportunities, \
  image-to-text ratio, unsubscribe link presence

When images are provided, also assess:
- Image quality and relevance
- Alt text presence/quality
- Layout and visual hierarchy
- Brand consistency
- Dark mode compatibility concerns
"""


class CampaignScanService:
    """Processes email campaigns through LLM analysis."""

    def __init__(self):
        self.model = "zai-glm-4.7"
        self.base_url = "https://api.cerebras.ai/v1"

    async def _get_api_key(self, client_id: str) -> str:
        """Get Cerebras API key for the client."""
        if not getattr(supabase_manager, "_initialized", False):
            await supabase_manager.initialize()
        sb = supabase_manager.admin_client

        # Try client-level key first
        try:
            result = sb.table("clients").select("cerebras_api_key").eq("id", client_id).execute()
            if result.data and result.data[0].get("cerebras_api_key"):
                return result.data[0]["cerebras_api_key"]
        except Exception:
            pass

        # Fall back to platform key from settings
        if settings.groq_api_key:
            # Check if there's a cerebras key in env
            import os
            cerebras_key = os.getenv("CEREBRAS_API_KEY")
            if cerebras_key:
                return cerebras_key

        raise ValueError(f"No Cerebras API key available for client {client_id}")

    async def _get_platform_client(self):
        if not getattr(supabase_manager, "_initialized", False):
            await supabase_manager.initialize()
        return supabase_manager.admin_client

    async def create_scan(
        self,
        client_id: str,
        agent_slug: str,
        user_id: str,
        sender_email: str,
        subject: str,
        body_plain: str,
        body_html: str,
        images: Optional[List[str]] = None,
    ) -> str:
        """Create a pending scan record and return its ID."""
        sb = await self._get_platform_client()
        result = sb.table(TABLE).insert({
            "client_id": client_id,
            "agent_slug": agent_slug,
            "user_id": user_id,
            "sender_email": sender_email,
            "email_subject": subject,
            "email_body_plain": body_plain,
            "email_body_html": body_html,
            "email_images": json.dumps(images or []),
            "status": "pending",
        }).execute()

        scan_id = result.data[0]["id"]
        logger.info("Created campaign scan %s for %s/%s", scan_id, client_id, agent_slug)
        return scan_id

    async def get_latest_scan(
        self, agent_slug: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get the most recent scan for a user + agent combo."""
        sb = await self._get_platform_client()
        try:
            result = (
                sb.table(TABLE)
                .select("*")
                .eq("agent_slug", agent_slug)
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as exc:
            logger.error("Failed to get latest scan: %s", exc)
            return None

    async def get_pending_scan(
        self, agent_slug: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get a pending/processing scan for a user + agent (active widget waiting)."""
        sb = await self._get_platform_client()
        try:
            result = (
                sb.table(TABLE)
                .select("*")
                .eq("agent_slug", agent_slug)
                .eq("user_id", user_id)
                .in_("status", ["pending", "processing"])
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as exc:
            logger.error("Failed to get pending scan: %s", exc)
            return None

    async def get_pending_scan_by_agent(
        self, agent_slug: str
    ) -> Optional[Dict[str, Any]]:
        """Get any pending scan for an agent (regardless of user).

        Used by the email webhook to route forwarded emails to campaign scan
        before sender verification, since the forwarding sender may differ
        from the user who started the scan.
        """
        sb = await self._get_platform_client()
        try:
            result = (
                sb.table(TABLE)
                .select("*")
                .eq("agent_slug", agent_slug)
                .eq("status", "pending")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as exc:
            logger.error("Failed to get pending scan by agent: %s", exc)
            return None

    async def process_scan(self, scan_id: str, client_id: str) -> Dict[str, Any]:
        """Run the LLM analysis on a scan record."""
        sb = await self._get_platform_client()

        # Mark as processing
        sb.table(TABLE).update({"status": "processing"}).eq("id", scan_id).execute()

        # Fetch the scan data
        scan_row = sb.table(TABLE).select("*").eq("id", scan_id).execute()
        if not scan_row.data:
            raise ValueError(f"Scan {scan_id} not found")
        scan = scan_row.data[0]

        try:
            api_key = await self._get_api_key(client_id)

            # Build the message content
            content_parts = []

            # Add the email content
            email_text = ""
            if scan.get("email_subject"):
                email_text += f"Subject: {scan['email_subject']}\n\n"
            if scan.get("email_body_html"):
                email_text += f"--- HTML Body ---\n{scan['email_body_html']}\n\n"
            if scan.get("email_body_plain"):
                email_text += f"--- Plain Text Body ---\n{scan['email_body_plain']}"

            content_parts.append({"type": "text", "text": email_text})

            # Add images if available (multimodal)
            images = scan.get("email_images") or []
            if isinstance(images, str):
                try:
                    images = json.loads(images)
                except Exception:
                    images = []

            for img in images[:5]:  # Limit to 5 images
                if isinstance(img, str) and img.startswith(("http://", "https://")):
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": img}
                    })
                elif isinstance(img, str) and img.startswith("data:"):
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": img}
                    })

            messages = [
                {"role": "system", "content": SCAN_SYSTEM_PROMPT},
                {"role": "user", "content": content_parts if len(content_parts) > 1 else email_text},
            ]

            # Call the LLM
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "max_tokens": 4096,
                    },
                    timeout=120.0,
                )
                response.raise_for_status()
                data = response.json()
                raw_content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

            # Parse JSON results
            results = self._parse_results(raw_content)

            # Update scan record
            sb.table(TABLE).update({
                "status": "complete",
                "results": results,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", scan_id).execute()

            logger.info("Campaign scan %s completed: score=%s", scan_id, results.get("score"))
            return results

        except Exception as exc:
            logger.error("Campaign scan %s failed: %s", scan_id, exc, exc_info=True)
            sb.table(TABLE).update({
                "status": "failed",
                "error": str(exc),
            }).eq("id", scan_id).execute()
            raise

    def _parse_results(self, raw: str) -> Dict[str, Any]:
        """Parse LLM response into structured results."""
        # Strip markdown fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        try:
            results = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Failed to parse scan results as JSON, wrapping as raw")
            results = {
                "summary": "Analysis complete but output could not be structured.",
                "score": 0,
                "errors": [],
                "warnings": [],
                "suggestions": [{"text": "", "issue": "Raw analysis", "suggestion": raw}],
            }

        # Ensure expected keys exist
        results.setdefault("summary", "Analysis complete.")
        results.setdefault("score", 0)
        results.setdefault("errors", [])
        results.setdefault("warnings", [])
        results.setdefault("suggestions", [])

        return results


campaign_scan_service = CampaignScanService()
