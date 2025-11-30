"""Deprecated voice transcript endpoints.

Transcript streaming now happens directly via Supabase Realtime subscriptions, so these
routes exist only to provide a clear error for older clients that still call them.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

router = APIRouter(prefix="/voice-transcripts", tags=["voice-transcripts"])

DEPRECATION_MESSAGE = (
    "SSE transcript streaming has been removed. "
    "Use Supabase Realtime channels on conversation_transcripts instead."
)


@router.get("/stream")
async def stream_voice_transcripts(
    conversation_id: str = Query(..., description="Conversation ID"),
    client_id: str = Query(..., description="Client ID"),
    agent_id: Optional[str] = Query(None, description="Agent ID"),
    last_timestamp: Optional[str] = Query(None, description="Deprecated SSE cursor"),
    include_citations: bool = Query(False, description="Unused – retained for compatibility"),
):
    """Return a deprecation error for legacy SSE consumers."""
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=DEPRECATION_MESSAGE)


@router.get("/history")
async def get_transcript_history(
    conversation_id: str = Query(..., description="Conversation ID"),
    client_id: str = Query(..., description="Client ID"),
    limit: int = Query(50, description="Legacy pagination parameter"),
    offset: int = Query(0, description="Legacy pagination parameter"),
    include_citations: bool = Query(False, description="Unused – retained for compatibility"),
):
    """Return a deprecation error for legacy history consumers."""
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=DEPRECATION_MESSAGE)
