"""Campaign Scan API endpoints.

Provides status polling for the Campaign Scan widget and a manual
trigger endpoint.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query

from app.services.campaign_scan_service import campaign_scan_service

router = APIRouter(prefix="/campaign-scan", tags=["campaign-scan"])
logger = logging.getLogger(__name__)


@router.get("/status")
async def get_scan_status(
    agent_slug: str = Query(...),
    user_id: str = Query(...),
):
    """Poll the status of the latest campaign scan for a user + agent.

    The widget calls this every few seconds while waiting for an email
    to arrive and be processed.

    Returns:
        - status: "none" | "pending" | "processing" | "complete" | "failed"
        - results: structured feedback (only when complete)
        - error: error message (only when failed)
    """
    scan = await campaign_scan_service.get_latest_scan(agent_slug, user_id)

    if not scan:
        return {"status": "none"}

    response = {
        "status": scan["status"],
        "id": scan["id"],
        "email_subject": scan.get("email_subject"),
        "created_at": scan.get("created_at"),
    }

    if scan["status"] == "complete":
        response["results"] = scan.get("results")
        response["completed_at"] = scan.get("completed_at")
    elif scan["status"] == "failed":
        response["error"] = scan.get("error")

    return response


@router.post("/start")
async def start_scan(
    agent_slug: str = Query(...),
    user_id: str = Query(...),
    client_id: str = Query(...),
    sender_email: str = Query(default=""),
):
    """Create a pending scan record so the email webhook knows to route
    forwarded emails to the campaign scan pipeline.

    Called by the widget when it opens.
    """
    # Cancel any existing pending scan first
    existing = await campaign_scan_service.get_pending_scan(agent_slug, user_id)
    if existing:
        sb = await campaign_scan_service._get_platform_client()
        sb.table("campaign_scans").update(
            {"status": "failed", "error": "Replaced by new scan"}
        ).eq("id", existing["id"]).execute()

    scan_id = await campaign_scan_service.create_scan(
        client_id=client_id,
        agent_slug=agent_slug,
        user_id=user_id,
        sender_email=sender_email,
        subject="",
        body_plain="",
        body_html="",
    )
    return {"ok": True, "scan_id": scan_id}


@router.post("/reset")
async def reset_scan(
    agent_slug: str = Query(...),
    user_id: str = Query(...),
):
    """Reset (cancel) any pending/processing scan so the widget can restart.

    Called when the user wants to scan a different email.
    """
    scan = await campaign_scan_service.get_pending_scan(agent_slug, user_id)
    if scan:
        sb = await campaign_scan_service._get_platform_client()
        sb.table("campaign_scans").update(
            {"status": "failed", "error": "Cancelled by user"}
        ).eq("id", scan["id"]).execute()
        return {"ok": True, "cancelled": scan["id"]}
    return {"ok": True, "cancelled": None}
