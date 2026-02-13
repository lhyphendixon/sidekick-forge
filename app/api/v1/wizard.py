"""
Wizard API endpoints for the sidekick onboarding wizard.
Handles wizard sessions, avatar generation, document uploads, and sidekick creation.
"""

import os
import uuid
import json
import logging
import tempfile
import random
import time
import httpx
from typing import List, Optional
from pathlib import Path
from datetime import datetime, timezone

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, BackgroundTasks, Query
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, Response
from pydantic import BaseModel, Field

from app.services.wizard_session_service import wizard_session_service
from app.services.wizard_completion_service import wizard_completion_service
from app.services.document_processor import DocumentProcessor
from app.services.firecrawl_scraper import FirecrawlScraper
# Use admin auth instead of middleware auth - same system admin pages use
from app.admin.auth import get_admin_user
from app.integrations.supabase_client import supabase_manager
from app.constants import (
    DOCUMENT_MAX_UPLOAD_BYTES,
    DOCUMENT_MAX_UPLOAD_MB,
    KNOWLEDGE_BASE_ALLOWED_EXTENSIONS,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wizard", tags=["wizard"])


# ============================================================
# Request/Response Models
# ============================================================

class SessionResponse(BaseModel):
    id: str
    user_id: str
    client_id: str
    current_step: int
    completed_steps: List[int]
    status: str
    step_data: dict
    created_at: str
    updated_at: str


class StepUpdateRequest(BaseModel):
    step_data: dict = Field(default_factory=dict)
    advance: bool = False


class StepUpdateResponse(BaseModel):
    success: bool
    current_step: int
    step_data: dict
    completed_steps: List[int]


class AvatarGenerateRequest(BaseModel):
    prompt: Optional[str] = None
    style: Optional[str] = None


class AvatarResponse(BaseModel):
    id: str
    prompt: str
    image_url: str
    selected: bool
    created_at: str


class KnowledgeStatusResponse(BaseModel):
    items: List[dict]
    pending_count: int
    processing_count: int
    ready_count: int
    error_count: int
    total_count: int
    all_complete: bool


class ClientSettingsResponse(BaseModel):
    bring_your_own_keys: bool
    uses_platform_keys: bool
    tier: str
    can_bring_own_keys: bool


class RandomNameResponse(BaseModel):
    name: str
    slug: str


class RandomPersonalityResponse(BaseModel):
    description: str
    traits: dict


class VoiceOption(BaseModel):
    id: str
    name: str
    description: str
    sample_url: Optional[str] = None
    tags: List[str] = []
    provider: str = "cartesia"


class VoicesResponse(BaseModel):
    voices: List[VoiceOption]


class AbilityOption(BaseModel):
    slug: str
    name: str
    description: str
    icon_url: Optional[str] = None
    requires_api_key: bool = False
    api_key_name: Optional[str] = None


class AbilitiesResponse(BaseModel):
    abilities: List[AbilityOption]


class ClonedVoiceResponse(BaseModel):
    id: str
    name: str
    description: str
    provider: str = "cartesia"
    is_cloned: bool = True
    sample_url: Optional[str] = None


# Empowering text for voice cloning recording
VOICE_CLONE_RECORDING_TEXT = """I am the voice of innovation, ready to guide and inspire.
Together, we will achieve greatness and transform ideas into reality."""


# ============================================================
# Helper Functions
# ============================================================

async def get_user_client_id(admin_user: dict) -> str:
    """Get the client_id associated with the authenticated user."""
    user_id = admin_user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID not found in auth context")

    # Check if admin_user already has visible_client_ids (from tenant_assignments)
    visible_client_ids = admin_user.get("visible_client_ids", [])
    if visible_client_ids:
        return visible_client_ids[0]  # Return first assigned client

    # Check for primary_client_id
    primary_client_id = admin_user.get("primary_client_id")
    if primary_client_id:
        return primary_client_id

    if not supabase_manager._initialized:
        await supabase_manager.initialize()

    # Query the user_clients or clients table to find the user's client
    try:
        result = supabase_manager.admin_client.table("user_clients").select(
            "client_id"
        ).eq("user_id", user_id).limit(1).execute()

        if result.data:
            return result.data[0]["client_id"]

        # Fallback: check if user is a client owner
        result = supabase_manager.admin_client.table("clients").select(
            "id"
        ).eq("owner_user_id", user_id).limit(1).execute()

        if result.data:
            return result.data[0]["id"]

        raise HTTPException(
            status_code=403,
            detail="User is not associated with any client"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user client_id: {e}")
        raise HTTPException(status_code=500, detail="Failed to determine client")


def generate_slug(name: str) -> str:
    """Generate a URL-safe slug from a name."""
    import re
    slug = name.lower()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    slug = slug.strip('-')
    return slug or f"sidekick-{uuid.uuid4().hex[:8]}"


# ============================================================
# Background Processing Functions
# ============================================================

async def process_wizard_document(
    pending_doc_id: str,
    file_content: bytes,
    filename: str,
    client_id: str
):
    """
    Background task to process an uploaded document.

    Saves the file to a temp location, processes it through the document
    pipeline, and updates the pending document status.
    """
    staged_path = None
    try:
        # Create a temp file for the document
        import aiofiles
        import aiofiles.os

        # Create staging directory
        staging_dir = Path(f"/data/uploads/{client_id}/wizard")
        staging_dir.mkdir(parents=True, exist_ok=True)

        # Write content to temp file
        staged_path = staging_dir / f"{pending_doc_id}_{filename}"
        async with aiofiles.open(staged_path, "wb") as f:
            await f.write(file_content)

        # Update status to processing
        await wizard_session_service.update_pending_document(
            pending_doc_id,
            {"status": "processing", "staged_path": str(staged_path)}
        )

        # Process through document pipeline
        processor = DocumentProcessor()
        result = await processor.process_uploaded_file(
            file_path=str(staged_path),
            title=filename,
            description=f"Uploaded via wizard for new sidekick",
            client_id=client_id,
            replace_existing=False
        )

        if result.get("success"):
            # Update pending document with the created document ID
            doc_id = result.get("document_id")
            update_data = {"status": "ready"}

            # Store document_id regardless of type (UUID or bigint integer)
            if doc_id is not None:
                update_data["document_id"] = str(doc_id)

            await wizard_session_service.update_pending_document(
                pending_doc_id,
                update_data
            )
            logger.info(f"Wizard document processed: {pending_doc_id} -> {doc_id}")
        else:
            await wizard_session_service.update_pending_document(
                pending_doc_id,
                {
                    "status": "error",
                    "error_message": result.get("error", "Processing failed")
                }
            )
            logger.error(f"Wizard document processing failed: {pending_doc_id} - {result.get('error')}")

    except Exception as e:
        logger.error(f"Error processing wizard document {pending_doc_id}: {e}")
        await wizard_session_service.update_pending_document(
            pending_doc_id,
            {"status": "error", "error_message": str(e)}
        )
    finally:
        # Clean up staged file if it exists
        if staged_path and staged_path.exists():
            try:
                staged_path.unlink()
            except Exception:
                pass


async def process_wizard_website(
    pending_doc_id: str,
    url: str,
    client_id: str,
    max_pages: int = 10
):
    """
    Background task to crawl and process a website.

    Uses Firecrawl to scrape the website, then processes the content
    through the document pipeline.
    """
    try:
        # Update status to processing
        await wizard_session_service.update_pending_document(
            pending_doc_id,
            {"status": "processing"}
        )

        # Scrape website using Firecrawl
        scraper = FirecrawlScraper()
        scrape_result = await scraper.scrape_and_extract(
            url=url,
            crawl=True,
            limit=max_pages
        )

        if not scrape_result.get("success"):
            await wizard_session_service.update_pending_document(
                pending_doc_id,
                {
                    "status": "error",
                    "error_message": scrape_result.get("error", "Crawl failed")
                }
            )
            return

        # Get the content
        content = scrape_result.get("content", "")
        title = scrape_result.get("title", url)
        pages_crawled = scrape_result.get("pages_crawled", 1)

        if not content:
            await wizard_session_service.update_pending_document(
                pending_doc_id,
                {"status": "error", "error_message": "No content extracted from website"}
            )
            return

        # Update pages crawled count
        await wizard_session_service.update_pending_document(
            pending_doc_id,
            {"pages_crawled": pages_crawled}
        )

        # Process through document pipeline
        processor = DocumentProcessor()
        result = await processor.process_web_content(
            content=content,
            title=title,
            source_url=url,
            description=f"Crawled via wizard ({pages_crawled} pages)",
            client_id=client_id,
            metadata={"source": "wizard", "pages_crawled": pages_crawled}
        )

        if result.get("success"):
            doc_id = result.get("document_id")
            update_data = {
                "status": "ready",
                "pages_crawled": pages_crawled
            }

            # Store document_id regardless of type (UUID or bigint integer)
            if doc_id is not None:
                update_data["document_id"] = str(doc_id)

            await wizard_session_service.update_pending_document(
                pending_doc_id,
                update_data
            )
            logger.info(f"Wizard website processed: {pending_doc_id} -> {doc_id}")
        else:
            await wizard_session_service.update_pending_document(
                pending_doc_id,
                {
                    "status": "error",
                    "error_message": result.get("error", "Processing failed")
                }
            )
            logger.error(f"Wizard website processing failed: {pending_doc_id} - {result.get('error')}")

    except Exception as e:
        logger.error(f"Error processing wizard website {pending_doc_id}: {e}")
        await wizard_session_service.update_pending_document(
            pending_doc_id,
            {"status": "error", "error_message": str(e)}
        )


# ============================================================
# Session Management Endpoints
# ============================================================

@router.post("/sessions", response_model=SessionResponse)
async def create_wizard_session(
    admin_user: dict = Depends(get_admin_user)
):
    """Create a new wizard session or get existing active session."""
    client_id = await get_user_client_id(admin_user)

    session = await wizard_session_service.get_or_create_session(
        user_id=admin_user["user_id"],
        client_id=client_id
    )

    return SessionResponse(
        id=session["id"],
        user_id=session["user_id"],
        client_id=session["client_id"],
        current_step=session["current_step"],
        completed_steps=session.get("completed_steps", []),
        status=session["status"],
        step_data=session.get("step_data", {}),
        created_at=session["created_at"],
        updated_at=session["updated_at"]
    )


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_wizard_session(
    session_id: str,
    admin_user: dict = Depends(get_admin_user)
):
    """Get a wizard session by ID."""
    session = await wizard_session_service.get_session(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Verify ownership
    if session["user_id"] != admin_user["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    return SessionResponse(
        id=session["id"],
        user_id=session["user_id"],
        client_id=session["client_id"],
        current_step=session["current_step"],
        completed_steps=session.get("completed_steps", []),
        status=session["status"],
        step_data=session.get("step_data", {}),
        created_at=session["created_at"],
        updated_at=session["updated_at"]
    )


@router.put("/sessions/{session_id}/step/{step_number}", response_model=StepUpdateResponse)
async def update_wizard_step(
    session_id: str,
    step_number: int,
    request: StepUpdateRequest,
    admin_user: dict = Depends(get_admin_user)
):
    """Update step data and optionally advance to the next step."""
    # Verify session ownership
    session = await wizard_session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["user_id"] != admin_user["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Validate step number
    if step_number < 1 or step_number > wizard_session_service.TOTAL_STEPS:
        raise HTTPException(status_code=400, detail="Invalid step number")

    # Auto-generate slug if name is provided
    step_data = request.step_data.copy()
    if "name" in step_data and "slug" not in step_data:
        step_data["slug"] = generate_slug(step_data["name"])

    result = await wizard_session_service.update_step(
        session_id=session_id,
        step_number=step_number,
        step_data=step_data,
        advance=request.advance
    )

    return StepUpdateResponse(**result)


@router.delete("/sessions/{session_id}")
async def delete_wizard_session(
    session_id: str,
    admin_user: dict = Depends(get_admin_user)
):
    """Abandon/delete a wizard session."""
    session = await wizard_session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["user_id"] != admin_user["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    success = await wizard_session_service.delete_session(session_id)

    return {"success": success, "message": "Session deleted" if success else "Failed to delete session"}


@router.get("/sessions/active/check")
async def check_active_session(
    admin_user: dict = Depends(get_admin_user)
):
    """Check if user has an active wizard session."""
    client_id = await get_user_client_id(admin_user)

    session = await wizard_session_service.get_active_session(
        user_id=admin_user["user_id"],
        client_id=client_id
    )

    if session:
        return {
            "has_session": True,
            "session_id": session["id"],
            "current_step": session["current_step"],
            "sidekick_name": session.get("step_data", {}).get("name", "your sidekick"),
            "last_updated": session["updated_at"]
        }

    return {"has_session": False}


@router.get("/sessions/{session_id}/client-settings", response_model=ClientSettingsResponse)
async def get_wizard_client_settings(
    session_id: str,
    admin_user: dict = Depends(get_admin_user)
):
    """
    Get client settings for the wizard, including BYOK (Bring Your Own Keys) status.

    This determines whether the user should see API key input fields or use platform keys.

    Returns:
        - bring_your_own_keys: True if user should provide their own keys
        - uses_platform_keys: True if client uses platform-managed keys
        - tier: The client's tier (adventurer, champion, paragon)
        - can_bring_own_keys: True if the tier allows using own keys
    """
    # Verify session ownership
    session = await wizard_session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["user_id"] != admin_user["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    client_id = session["client_id"]

    # Ensure Supabase is initialized
    if not supabase_manager._initialized:
        await supabase_manager.initialize()

    try:
        # Get client's tier and uses_platform_keys flag
        result = supabase_manager.admin_client.table("clients").select(
            "tier, uses_platform_keys"
        ).eq("id", client_id).single().execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Client not found")

        tier = result.data.get("tier", "champion")
        uses_platform_keys = result.data.get("uses_platform_keys")

        # If not explicitly set, default based on tier
        # Adventurer tier defaults to platform keys
        if uses_platform_keys is None:
            uses_platform_keys = (tier == "adventurer")

        # Determine if tier allows BYOK
        # Adventurers must use platform keys, Champion/Paragon can choose
        can_bring_own_keys = tier in ("champion", "paragon")

        # bring_your_own_keys is the inverse of uses_platform_keys
        bring_your_own_keys = not uses_platform_keys

        return ClientSettingsResponse(
            bring_your_own_keys=bring_your_own_keys,
            uses_platform_keys=uses_platform_keys,
            tier=tier,
            can_bring_own_keys=can_bring_own_keys
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting client settings for session {session_id}: {e}")
        # Default to BYOK enabled (show API key fields) if there's an error
        return ClientSettingsResponse(
            bring_your_own_keys=True,
            uses_platform_keys=False,
            tier="champion",
            can_bring_own_keys=True
        )


# ============================================================
# Voice Selection Endpoints
# ============================================================

@router.get("/voices", response_model=VoicesResponse)
async def list_voices(
    admin_user: dict = Depends(get_admin_user)
):
    """List available Cartesia voices for selection."""
    # Expanded curated list of Cartesia voices with sample URLs
    base_sample_url = "/api/v1/wizard/voice-sample"

    voices = [
        # Professional Female Voices
        VoiceOption(
            id="79a125e8-cd45-4c13-8a67-188112f4dd22",
            name="British Lady",
            description="Elegant, refined British accent",
            sample_url=f"{base_sample_url}/79a125e8-cd45-4c13-8a67-188112f4dd22",
            tags=["female", "british", "professional"],
            provider="cartesia"
        ),
        VoiceOption(
            id="c45bc5ec-dc68-4feb-8829-6e6b2748095d",
            name="Confident Customer Service",
            description="Professional, helpful customer service voice",
            sample_url=f"{base_sample_url}/c45bc5ec-dc68-4feb-8829-6e6b2748095d",
            tags=["female", "american", "professional"],
            provider="cartesia"
        ),
        VoiceOption(
            id="fb26447f-308b-471e-8b00-8e9f04284eb5",
            name="Teacher Lady",
            description="Clear, patient teaching voice",
            sample_url=f"{base_sample_url}/fb26447f-308b-471e-8b00-8e9f04284eb5",
            tags=["female", "american", "educational"],
            provider="cartesia"
        ),
        VoiceOption(
            id="248be419-c632-4f23-adf1-5324ed7dbf1d",
            name="Hannah",
            description="Young, articulate American woman",
            sample_url=f"{base_sample_url}/248be419-c632-4f23-adf1-5324ed7dbf1d",
            tags=["female", "american", "young"],
            provider="cartesia"
        ),
        VoiceOption(
            id="bf991597-6c13-47e4-8411-91ec2de5c466",
            name="Lily",
            description="Bright, cheerful voice",
            sample_url=f"{base_sample_url}/bf991597-6c13-47e4-8411-91ec2de5c466",
            tags=["female", "american", "cheerful"],
            provider="cartesia"
        ),

        # Calm/Soothing Female Voices
        VoiceOption(
            id="00a77add-48d5-4ef6-8157-71e5437b282d",
            name="Soothing Lady",
            description="Calm, relaxing female voice",
            sample_url=f"{base_sample_url}/00a77add-48d5-4ef6-8157-71e5437b282d",
            tags=["female", "american", "calm"],
            provider="cartesia"
        ),
        VoiceOption(
            id="b7d50908-b17c-442d-ad8d-810c63997ed9",
            name="Yogini",
            description="Peaceful, meditative female voice",
            sample_url=f"{base_sample_url}/b7d50908-b17c-442d-ad8d-810c63997ed9",
            tags=["female", "american", "calm"],
            provider="cartesia"
        ),
        VoiceOption(
            id="421b3369-f63f-4b03-8980-37a44df1d4e8",
            name="Sweet Lady",
            description="Friendly, warm female voice",
            sample_url=f"{base_sample_url}/421b3369-f63f-4b03-8980-37a44df1d4e8",
            tags=["female", "american", "friendly"],
            provider="cartesia"
        ),

        # Professional Male Voices
        VoiceOption(
            id="a0e99841-438c-4a64-b679-ae501e7d6091",
            name="Barbershop Man",
            description="Warm, friendly male voice",
            sample_url=f"{base_sample_url}/a0e99841-438c-4a64-b679-ae501e7d6091",
            tags=["male", "american", "casual"],
            provider="cartesia"
        ),
        VoiceOption(
            id="41534e16-2966-4c6b-9670-111411def906",
            name="Newsman",
            description="Clear, authoritative news anchor voice",
            sample_url=f"{base_sample_url}/41534e16-2966-4c6b-9670-111411def906",
            tags=["male", "american", "professional"],
            provider="cartesia"
        ),
        VoiceOption(
            id="f9836c6e-a0bd-460e-9d3c-f7299fa60f94",
            name="Pilot Captain",
            description="Calm, professional pilot voice",
            sample_url=f"{base_sample_url}/f9836c6e-a0bd-460e-9d3c-f7299fa60f94",
            tags=["male", "american", "professional"],
            provider="cartesia"
        ),
        VoiceOption(
            id="a167e0f3-df7e-4d52-a9c3-f949145efdab",
            name="Customer Support Man",
            description="Helpful, patient support agent",
            sample_url=f"{base_sample_url}/a167e0f3-df7e-4d52-a9c3-f949145efdab",
            tags=["male", "american", "professional"],
            provider="cartesia"
        ),
        VoiceOption(
            id="ee7ea9f8-c0c1-498c-9f62-dc2571edea11",
            name="Marcus",
            description="Deep, resonant male voice",
            sample_url=f"{base_sample_url}/ee7ea9f8-c0c1-498c-9f62-dc2571edea11",
            tags=["male", "american", "deep"],
            provider="cartesia"
        ),

        # Energetic/Casual Male Voices
        VoiceOption(
            id="694f9389-aac1-45b6-b726-9d9369183238",
            name="Sportsman",
            description="Energetic, enthusiastic male voice",
            sample_url=f"{base_sample_url}/694f9389-aac1-45b6-b726-9d9369183238",
            tags=["male", "american", "energetic"],
            provider="cartesia"
        ),
        VoiceOption(
            id="95856005-0332-41b0-935f-352e296aa0df",
            name="Classy British Man",
            description="Sophisticated British gentleman",
            sample_url=f"{base_sample_url}/95856005-0332-41b0-935f-352e296aa0df",
            tags=["male", "british", "professional"],
            provider="cartesia"
        ),
        VoiceOption(
            id="63ff761f-c1e8-414b-b969-d1833d1c870c",
            name="Friendly Australian",
            description="Warm, approachable Australian accent",
            sample_url=f"{base_sample_url}/63ff761f-c1e8-414b-b969-d1833d1c870c",
            tags=["male", "australian", "casual"],
            provider="cartesia"
        ),

        # Character/Unique Voices
        VoiceOption(
            id="5345cf08-6f37-424d-a5d9-8ae1c79f4c90",
            name="Wise Grandpa",
            description="Warm, grandfatherly storytelling voice",
            sample_url=f"{base_sample_url}/5345cf08-6f37-424d-a5d9-8ae1c79f4c90",
            tags=["male", "american", "mature"],
            provider="cartesia"
        ),
        VoiceOption(
            id="726d5ae5-055f-4c3d-8355-d9677de68937",
            name="Friendly Narrator",
            description="Engaging audiobook narrator",
            sample_url=f"{base_sample_url}/726d5ae5-055f-4c3d-8355-d9677de68937",
            tags=["male", "american", "narrator"],
            provider="cartesia"
        ),
        VoiceOption(
            id="36b42fcb-60c5-4bec-b077-cb1a00a92ec6",
            name="Nonfiction Man",
            description="Authoritative documentary voice",
            sample_url=f"{base_sample_url}/36b42fcb-60c5-4bec-b077-cb1a00a92ec6",
            tags=["male", "american", "authoritative"],
            provider="cartesia"
        ),
        VoiceOption(
            id="573e3144-a684-4e72-ac2b-9b2063a50b53",
            name="Reflective Woman",
            description="Thoughtful, introspective voice",
            sample_url=f"{base_sample_url}/573e3144-a684-4e72-ac2b-9b2063a50b53",
            tags=["female", "american", "thoughtful"],
            provider="cartesia"
        ),
    ]

    return VoicesResponse(voices=voices)


# ============================================================
# Abilities Selection Endpoints
# ============================================================

@router.get("/abilities", response_model=AbilitiesResponse)
async def list_abilities(
    admin_user: dict = Depends(get_admin_user)
):
    """List available built-in abilities for sidekick selection."""
    abilities = [
        AbilityOption(
            slug="web_search",
            name="Web Search (Perplexity)",
            description="Research and find current information from the web using Perplexity AI.",
            icon_url="/static/images/abilities/web-search.svg",
            requires_api_key=False,
            api_key_name=""
        ),
        AbilityOption(
            slug="usersense",
            name="UserSense",
            description="Remember user preferences and context across conversations for personalized interactions.",
            icon_url="/static/images/abilities/usersense.svg",
            requires_api_key=False
        ),
        AbilityOption(
            slug="documentsense",
            name="DocumentSense",
            description="Query and analyze uploaded documents to answer questions and provide insights.",
            icon_url="/static/images/abilities/documentsense.svg",
            requires_api_key=False
        ),
        AbilityOption(
            slug="content_catalyst",
            name="Content Catalyst",
            description="Generate articles, summaries, and content from various sources.",
            icon_url="/static/images/abilities/content-catalyst.svg",
            requires_api_key=False
        ),
        AbilityOption(
            slug="image_catalyst",
            name="Image Catalyst",
            description="Generate AI images. Thumbnail mode for polished marketing images, General mode for creative imagery. Supports reference images.",
            icon_url="/static/images/abilities/image-catalyst.svg",
            requires_api_key=False
        ),
    ]

    return AbilitiesResponse(abilities=abilities)


# Voice sample cache directory
VOICE_SAMPLE_CACHE_DIR = Path(__file__).parent.parent.parent / "static" / "audio" / "voice_samples"
VOICE_SAMPLE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Sample text for voice previews
VOICE_SAMPLE_TEXT = "Hi there! I'm excited to be your AI assistant. How can I help you today?"


@router.get("/voice-sample/{voice_id}")
async def get_voice_sample(
    voice_id: str,
):
    """
    Generate or retrieve a cached voice sample for the given voice ID.
    Returns audio/wav file.
    """
    import httpx

    # Check if sample is already cached
    cache_file = VOICE_SAMPLE_CACHE_DIR / f"{voice_id}.wav"
    if cache_file.exists():
        return FileResponse(
            cache_file,
            media_type="audio/wav",
            filename=f"voice_sample_{voice_id}.wav"
        )

    # Get Platform Cartesia API key from environment
    cartesia_api_key = os.getenv("CARTESIA_API_KEY")
    if not cartesia_api_key:
        raise HTTPException(
            status_code=503,
            detail="Platform Cartesia API key not configured."
        )

    # Generate sample using Cartesia TTS API
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.cartesia.ai/tts/bytes",
                headers={
                    "X-API-Key": cartesia_api_key,
                    "Cartesia-Version": "2024-06-10",
                    "Content-Type": "application/json",
                },
                json={
                    "model_id": "sonic-english",
                    "transcript": VOICE_SAMPLE_TEXT,
                    "voice": {
                        "mode": "id",
                        "id": voice_id,
                    },
                    "output_format": {
                        "container": "wav",
                        "encoding": "pcm_s16le",
                        "sample_rate": 24000,
                    },
                },
            )

            if response.status_code != 200:
                logger.error(f"Cartesia TTS error: {response.status_code} - {response.text}")
                raise HTTPException(
                    status_code=502,
                    detail=f"Failed to generate voice sample: {response.status_code}"
                )

            # Cache the audio file
            audio_data = response.content
            cache_file.write_bytes(audio_data)
            logger.info(f"Cached voice sample for {voice_id} ({len(audio_data)} bytes)")

            return Response(
                content=audio_data,
                media_type="audio/wav",
                headers={
                    "Content-Disposition": f'inline; filename="voice_sample_{voice_id}.wav"',
                    "Cache-Control": "public, max-age=86400",  # Cache for 24 hours
                }
            )

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Voice sample generation timed out")
    except Exception as e:
        logger.error(f"Error generating voice sample: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate voice sample: {str(e)}")


@router.get("/voice-clone-text")
async def get_voice_clone_text(admin_user: dict = Depends(get_admin_user)):
    """Get the suggested text for voice clone recording."""
    return {"text": VOICE_CLONE_RECORDING_TEXT}


@router.post("/clone-voice", response_model=ClonedVoiceResponse)
async def clone_voice(
    audio: UploadFile = File(...),
    name: str = Form(default="My Cloned Voice"),
    admin_user: dict = Depends(get_admin_user)
):
    """
    Clone a voice from an uploaded audio sample using Cartesia API.

    Accepts audio files (wav, mp3, m4a, webm) of 5-10 seconds.
    Returns the cloned voice ID that can be used for TTS.
    """
    import httpx

    # Validate file type
    allowed_types = {"audio/wav", "audio/mpeg", "audio/mp3", "audio/m4a", "audio/webm", "audio/x-wav", "audio/wave"}
    content_type = audio.content_type or ""

    # Also check by extension if content_type is generic
    filename = audio.filename or ""
    extension = filename.lower().split(".")[-1] if "." in filename else ""
    allowed_extensions = {"wav", "mp3", "m4a", "webm", "ogg"}

    if content_type not in allowed_types and extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format. Please upload wav, mp3, m4a, or webm files."
        )

    # Read audio content
    audio_content = await audio.read()

    # Validate size (max 10MB)
    if len(audio_content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Audio file too large. Maximum size is 10MB.")

    # Minimum size check (at least 10KB for a reasonable sample)
    if len(audio_content) < 10 * 1024:
        raise HTTPException(status_code=400, detail="Audio file too small. Please provide at least 5 seconds of audio.")

    # Get Platform Cartesia API key
    cartesia_api_key = os.getenv("CARTESIA_API_KEY")
    if not cartesia_api_key:
        raise HTTPException(status_code=503, detail="Platform Cartesia API key not configured.")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Prepare multipart form data for Cartesia clone API
            files = {
                "clip": (filename or "recording.wav", audio_content, content_type or "audio/wav")
            }
            data = {
                "name": name,
                "description": f"Custom cloned voice for sidekick wizard",
                "language": "en"
            }

            response = await client.post(
                "https://api.cartesia.ai/voices/clone",
                headers={
                    "X-API-Key": cartesia_api_key,
                    "Cartesia-Version": "2024-06-10"
                },
                files=files,
                data=data
            )

            if response.status_code != 200:
                error_detail = response.text
                logger.error(f"Cartesia clone API error: {response.status_code} - {error_detail}")
                raise HTTPException(
                    status_code=502,
                    detail=f"Voice cloning failed: {response.status_code}"
                )

            result = response.json()
            voice_id = result.get("id")

            if not voice_id:
                raise HTTPException(status_code=502, detail="Voice cloning failed: no voice ID returned")

            logger.info(f"Successfully cloned voice: {voice_id} for user {admin_user.get('user_id')}")

            return ClonedVoiceResponse(
                id=voice_id,
                name=name,
                description="Custom cloned voice",
                provider="cartesia",
                is_cloned=True,
                sample_url=f"/api/v1/wizard/voice-sample/{voice_id}"
            )

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Voice cloning timed out. Please try again.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cloning voice: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clone voice: {str(e)}")


# ============================================================
# Avatar Generation Endpoints
# ============================================================

@router.post("/sessions/{session_id}/generate-avatar", response_model=AvatarResponse)
async def generate_avatar(
    session_id: str,
    request: AvatarGenerateRequest,
    background_tasks: BackgroundTasks,
):
    """Generate an avatar image for the sidekick using Silicon Flow Z-Image-Turbo."""
    import httpx
    import base64

    # Verify session exists
    session = await wizard_session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get sidekick details from session
    step_data = session.get("step_data", {})
    name = step_data.get("name", "Assistant")
    personality = step_data.get("personality_description", "friendly and helpful")

    # Build prompt optimized for avatar generation
    if request.prompt:
        base_prompt = request.prompt
    else:
        base_prompt = f"An AI assistant named {name} who is {personality}"

    # Enhance prompt for better avatar results
    prompt = (
        f"Professional portrait avatar of {base_prompt}. "
        "Photorealistic digital art style, friendly welcoming expression, "
        "clean minimal background, soft studio lighting, "
        "high quality detailed face, modern tech aesthetic, "
        "suitable for profile picture, centered composition"
    )

    # Get Silicon Flow API key
    siliconflow_api_key = os.getenv("SILICONFLOW_API_KEY")
    if not siliconflow_api_key:
        raise HTTPException(status_code=503, detail="Platform Silicon Flow API key not configured.")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://api.siliconflow.com/v1/images/generations",
                headers={
                    "Authorization": f"Bearer {siliconflow_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "Tongyi-MAI/Z-Image-Turbo",
                    "prompt": prompt,
                    "image_size": "1024x1024",
                    "num_inference_steps": 8,
                    "batch_size": 1
                }
            )

            if response.status_code != 200:
                error_detail = response.text
                logger.error(f"Silicon Flow API error: {response.status_code} - {error_detail}")
                raise HTTPException(
                    status_code=502,
                    detail=f"Avatar generation failed: {response.status_code}"
                )

            result = response.json()
            logger.info(f"Silicon Flow response: {result}")

            # Extract image URL from response
            images = result.get("images", []) or result.get("data", [])
            if not images:
                raise HTTPException(status_code=502, detail="No image returned from generation")

            # Silicon Flow returns either URL or base64 - always persist locally
            image_data = images[0]
            avatar_dir = Path(__file__).parent.parent.parent / "static" / "images" / "avatars"
            avatar_dir.mkdir(parents=True, exist_ok=True)

            if isinstance(image_data, dict):
                remote_url = image_data.get("url")
                b64_data = image_data.get("b64_json")
            else:
                remote_url = image_data  # Direct URL string
                b64_data = None

            if b64_data:
                img_bytes = base64.b64decode(b64_data)
            elif remote_url:
                # Download the image so we don't depend on a temporary external URL
                img_response = await client.get(remote_url, timeout=30.0)
                if img_response.status_code != 200:
                    logger.warning(f"Failed to download avatar from {remote_url}, using URL directly")
                    image_url = remote_url
                    img_bytes = None
                else:
                    img_bytes = img_response.content
            else:
                img_bytes = None

            if img_bytes:
                filename = f"avatar_{uuid.uuid4().hex[:12]}.png"
                filepath = avatar_dir / filename
                filepath.write_bytes(img_bytes)
                image_url = f"/static/images/avatars/{filename}"
            elif not remote_url:
                raise HTTPException(status_code=502, detail="No image data in response")

            if not image_url:
                raise HTTPException(status_code=502, detail="Could not extract image URL from response")

            logger.info(f"Generated avatar: {image_url} for session {session_id}")

            # Create avatar record
            avatar = await wizard_session_service.create_avatar(
                session_id=session_id,
                prompt=prompt,
                image_url=image_url,
                provider="siliconflow",
                model="Tongyi-MAI/Z-Image-Turbo",
                params={"style": request.style}
            )

            return AvatarResponse(
                id=avatar["id"],
                prompt=avatar["prompt"],
                image_url=avatar["image_url"],
                selected=avatar["selected"],
                created_at=avatar["created_at"]
            )

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Avatar generation timed out. Please try again.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating avatar: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate avatar: {str(e)}")


@router.get("/sessions/{session_id}/avatars", response_model=List[AvatarResponse])
async def list_avatars(
    session_id: str,
    admin_user: dict = Depends(get_admin_user)
):
    """Get all generated avatars for a session."""
    session = await wizard_session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["user_id"] != admin_user["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    avatars = await wizard_session_service.get_avatars(session_id)

    return [
        AvatarResponse(
            id=a["id"],
            prompt=a["prompt"],
            image_url=a["image_url"],
            selected=a["selected"],
            created_at=a["created_at"]
        )
        for a in avatars
    ]


@router.post("/sessions/{session_id}/avatars/{avatar_id}/select")
async def select_avatar(
    session_id: str,
    avatar_id: str,
    admin_user: dict = Depends(get_admin_user)
):
    """Select an avatar as the final choice."""
    session = await wizard_session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["user_id"] != admin_user["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    success = await wizard_session_service.select_avatar(session_id, avatar_id)

    return {"success": success}


AVATAR_UPLOAD_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
AVATAR_UPLOAD_ALLOWED_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
}
AVATAR_UPLOAD_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


@router.post("/sessions/{session_id}/upload-avatar", response_model=AvatarResponse)
async def upload_avatar(
    session_id: str,
    file: UploadFile = File(...),
    admin_user: dict = Depends(get_admin_user),
):
    """Upload a custom avatar image for the sidekick."""
    session = await wizard_session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["user_id"] != admin_user["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Determine file extension from filename or content type
    import mimetypes

    suffix = ""
    if file.filename:
        original_suffix = Path(file.filename).suffix.lower()
        if original_suffix in AVATAR_UPLOAD_ALLOWED_EXTENSIONS:
            suffix = original_suffix

    content_type = (file.content_type or "").lower()
    if not suffix and content_type in AVATAR_UPLOAD_ALLOWED_TYPES:
        suffix = AVATAR_UPLOAD_ALLOWED_TYPES[content_type]

    if not suffix:
        raise HTTPException(
            status_code=400,
            detail="Unsupported image type. Please upload PNG, JPG, WEBP, GIF, or SVG.",
        )

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(contents) > AVATAR_UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds 5 MB limit")

    avatar_dir = Path("/app/static/images/avatars")
    avatar_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    unique = uuid.uuid4().hex[:8]
    filename = f"upload_{session_id[:8]}_{timestamp}_{unique}{suffix}"
    filepath = avatar_dir / filename
    filepath.write_bytes(contents)

    image_url = f"/static/images/avatars/{filename}"
    logger.info(f"Uploaded avatar: {image_url} for session {session_id}")

    avatar = await wizard_session_service.create_avatar(
        session_id=session_id,
        prompt="Uploaded",
        image_url=image_url,
        provider="upload",
        model="user_upload",
        params={},
    )

    return AvatarResponse(
        id=avatar["id"],
        prompt=avatar["prompt"],
        image_url=avatar["image_url"],
        selected=avatar["selected"],
        created_at=avatar["created_at"],
    )


# ============================================================
# Knowledge Base Endpoints
# ============================================================

@router.post("/sessions/{session_id}/documents")
async def upload_wizard_document(
    session_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    admin_user: dict = Depends(get_admin_user)
):
    """Upload a document for the sidekick's knowledge base."""
    # Verify session ownership
    session = await wizard_session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["user_id"] != admin_user["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Validate file
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    file_extension = Path(file.filename).suffix.lower().lstrip('.')
    if file_extension not in KNOWLEDGE_BASE_ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {', '.join(KNOWLEDGE_BASE_ALLOWED_EXTENSIONS)}"
        )

    # Read file and check size
    content = await file.read()
    file_size = len(content)

    if file_size > DOCUMENT_MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {DOCUMENT_MAX_UPLOAD_MB}MB"
        )

    # Create pending document record
    pending_doc = await wizard_session_service.create_pending_document(
        session_id=session_id,
        source_type="file",
        source_name=file.filename,
        file_size=file_size,
        file_type=file_extension
    )

    # Spawn background task for processing
    background_tasks.add_task(
        process_wizard_document,
        pending_doc_id=pending_doc["id"],
        file_content=content,
        filename=file.filename,
        client_id=session["client_id"]
    )

    return {
        "success": True,
        "pending_doc_id": pending_doc["id"],
        "status": "pending",
        "message": "Document uploaded and processing started"
    }


@router.post("/sessions/{session_id}/websites")
async def add_wizard_website(
    session_id: str,
    url: str = Form(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    admin_user: dict = Depends(get_admin_user)
):
    """Add a website URL for the sidekick's knowledge base."""
    # Verify session ownership
    session = await wizard_session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["user_id"] != admin_user["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Validate URL
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL format")

    # Create pending document record
    pending_doc = await wizard_session_service.create_pending_document(
        session_id=session_id,
        source_type="website",
        source_name=url
    )

    # Spawn background task for Firecrawl processing
    background_tasks.add_task(
        process_wizard_website,
        pending_doc_id=pending_doc["id"],
        url=url,
        client_id=session["client_id"]
    )

    return {
        "success": True,
        "pending_doc_id": pending_doc["id"],
        "status": "pending",
        "message": "Website crawl started"
    }


@router.get("/sessions/{session_id}/knowledge-status", response_model=KnowledgeStatusResponse)
async def get_knowledge_status(
    session_id: str,
    admin_user: dict = Depends(get_admin_user)
):
    """Get the processing status of all knowledge items."""
    session = await wizard_session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["user_id"] != admin_user["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    status = await wizard_session_service.get_knowledge_status(session_id)

    return KnowledgeStatusResponse(**status)


@router.delete("/sessions/{session_id}/documents/{doc_id}")
async def delete_wizard_document(
    session_id: str,
    doc_id: str,
    admin_user: dict = Depends(get_admin_user)
):
    """Remove a pending document."""
    session = await wizard_session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["user_id"] != admin_user["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    success = await wizard_session_service.delete_pending_document(doc_id)

    return {"success": success}


# ============================================================
# Randomization Endpoints
# ============================================================

SIDEKICK_NAMES = [
    "Aria", "Nova", "Echo", "Luna", "Sage", "Atlas", "Orion", "Phoenix",
    "Zephyr", "Iris", "Astra", "Cleo", "Jasper", "Ember", "Sterling",
    "Aurora", "Rex", "Stella", "Cosmo", "Fern", "Blaze", "Coral",
    "Dash", "Ivy", "Kai", "Lyra", "Max", "Nyx", "Quinn", "River"
]

PERSONALITY_TEMPLATES = [
    {
        "description": "Friendly and approachable, always ready to help with a warm tone and patient explanations.",
        "traits": {"openness": 70, "conscientiousness": 65, "extraversion": 80, "agreeableness": 85, "neuroticism": 25}
    },
    {
        "description": "Professional and efficient, providing clear and concise responses with expert knowledge.",
        "traits": {"openness": 60, "conscientiousness": 90, "extraversion": 50, "agreeableness": 70, "neuroticism": 20}
    },
    {
        "description": "Curious and creative, exploring ideas with enthusiasm and offering unique perspectives.",
        "traits": {"openness": 95, "conscientiousness": 55, "extraversion": 70, "agreeableness": 75, "neuroticism": 35}
    },
    {
        "description": "Calm and thoughtful, taking time to consider responses carefully and mindfully.",
        "traits": {"openness": 65, "conscientiousness": 75, "extraversion": 40, "agreeableness": 80, "neuroticism": 15}
    },
    {
        "description": "Energetic and enthusiastic, bringing positivity and motivation to every interaction.",
        "traits": {"openness": 80, "conscientiousness": 60, "extraversion": 95, "agreeableness": 85, "neuroticism": 30}
    },
    {
        "description": "Wise and knowledgeable, sharing insights and guidance with a mentor-like approach.",
        "traits": {"openness": 75, "conscientiousness": 85, "extraversion": 55, "agreeableness": 70, "neuroticism": 20}
    },
]


@router.get("/randomize/name", response_model=RandomNameResponse)
async def randomize_name(
    admin_user: dict = Depends(get_admin_user)
):
    """Generate a random sidekick name."""
    name = random.choice(SIDEKICK_NAMES)
    slug = generate_slug(name)

    return RandomNameResponse(name=name, slug=slug)


@router.get("/randomize/personality", response_model=RandomPersonalityResponse)
async def randomize_personality(
    admin_user: dict = Depends(get_admin_user)
):
    """Generate a random personality description and traits."""
    personality = random.choice(PERSONALITY_TEMPLATES)

    return RandomPersonalityResponse(
        description=personality["description"],
        traits=personality["traits"]
    )


# ============================================================
# Wizard Completion Endpoint
# ============================================================

@router.post("/sessions/{session_id}/complete")
async def complete_wizard(
    session_id: str,
    admin_user: dict = Depends(get_admin_user)
):
    """Complete the wizard and create the sidekick."""
    result = await wizard_completion_service.complete_wizard(
        session_id=session_id,
        user_id=admin_user["user_id"]
    )

    if not result.get("success"):
        error = result.get("error", "Failed to create sidekick")
        raise HTTPException(status_code=400, detail=error)

    return result


# ============================================================
# Text-to-Speech Endpoints
# ============================================================

# Default voice ID for the wizard assistant (Farah Qubit's voice)
# Farah Qubit's actual Cartesia voice ID from her agent configuration
WIZARD_VOICE_ID = "1013a0b6-8ce7-44dd-8bce-aadf7ac495a0"
WIZARD_TTS_MODEL = "sonic-3"  # Cartesia model (Farah uses sonic-3)
WIZARD_LLM_MODEL = "llama-3.3-70b-versatile"  # Groq Llama 3.3 70B (verified tool calling support)

# Step prompts for the wizard
WIZARD_STEP_PROMPTS = {
    1: {
        "title": "What would you like to name your sidekick?",
        "instruction": "Type or speak your answer."
    },
    2: {
        "title": "Describe your sidekick's personality.",
        "instruction": "Tell me how your sidekick should communicate and behave."
    },
    3: {
        "title": "Choose a voice for your sidekick.",
        "instruction": "Listen to the samples and select a voice that fits your sidekick."
    },
    4: {
        "title": "Let's create an avatar for your sidekick.",
        "instruction": "Describe how you'd like your sidekick to look, or let me generate one for you."
    },
    5: {
        "title": "Give your sidekick superpowers.",
        "instruction": "Choose which built-in abilities to enable for your sidekick."
    },
    6: {
        "title": "Give your sidekick some knowledge.",
        "instruction": "Upload documents or add websites for your sidekick to learn from."
    },
    7: {
        "title": "Choose your configuration.",
        "instruction": "Select default settings or customize the providers."
    },
    8: {
        "title": "Enter your API keys.",
        "instruction": "These keys connect your sidekick to the services it needs."
    },
    9: {
        "title": "Your sidekick is ready!",
        "instruction": "Review the summary and launch your sidekick."
    }
}


class TTSRequest(BaseModel):
    """Request model for TTS synthesis."""
    text: str = Field(..., description="Text to synthesize")
    voice_id: Optional[str] = Field(None, description="Cartesia voice ID (defaults to wizard voice)")
    model: Optional[str] = Field(None, description="TTS model (defaults to sonic-2)")


class StepPromptResponse(BaseModel):
    """Response model for step prompts."""
    step: int
    title: str
    instruction: str


@router.get("/step-prompts/{step_number}", response_model=StepPromptResponse)
async def get_step_prompt(step_number: int):
    """Get the text prompts for a wizard step."""
    if step_number < 1 or step_number > len(WIZARD_STEP_PROMPTS):
        raise HTTPException(status_code=400, detail="Invalid step number")

    prompts = WIZARD_STEP_PROMPTS[step_number]
    return StepPromptResponse(
        step=step_number,
        title=prompts["title"],
        instruction=prompts["instruction"]
    )


@router.post("/tts")
async def synthesize_speech(request: TTSRequest):
    """
    Synthesize speech using Cartesia TTS.

    Uses Farah Qubit's voice configuration to generate audio for the wizard.
    Returns audio as a streaming response.
    """
    # Get Cartesia API key from environment
    cartesia_api_key = os.getenv("CARTESIA_API_KEY")
    if not cartesia_api_key:
        # Try to get from a default client
        try:
            if not supabase_manager._initialized:
                await supabase_manager.initialize()

            # Get Autonomite client's API key (default client)
            result = supabase_manager.admin_client.table("clients").select(
                "cartesia_api_key"
            ).eq("name", "Autonomite").limit(1).execute()

            if result.data:
                cartesia_api_key = result.data[0].get("cartesia_api_key")
        except Exception as e:
            logger.error(f"Error getting Cartesia API key: {e}")

    if not cartesia_api_key:
        raise HTTPException(
            status_code=500,
            detail="Cartesia API key not configured. Please contact support."
        )

    voice_id = request.voice_id or WIZARD_VOICE_ID
    model = request.model or WIZARD_TTS_MODEL

    # Cartesia TTS API endpoint
    cartesia_url = "https://api.cartesia.ai/tts/bytes"

    headers = {
        "X-API-Key": cartesia_api_key,
        "Cartesia-Version": "2024-06-10",
        "Content-Type": "application/json"
    }

    payload = {
        "model_id": model,
        "transcript": request.text,
        "voice": {
            "mode": "id",
            "id": voice_id
        },
        "output_format": {
            "container": "mp3",
            "encoding": "mp3",
            "sample_rate": 44100
        }
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                cartesia_url,
                headers=headers,
                json=payload
            )

            if response.status_code != 200:
                logger.error(f"Cartesia TTS error: {response.status_code} - {response.text}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"TTS synthesis failed: {response.text}"
                )

            # Return audio as streaming response
            return StreamingResponse(
                iter([response.content]),
                media_type="audio/mpeg",
                headers={
                    "Content-Disposition": "inline",
                    "Cache-Control": "public, max-age=3600"
                }
            )

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="TTS request timed out")
    except httpx.RequestError as e:
        logger.error(f"Cartesia TTS request error: {e}")
        raise HTTPException(status_code=502, detail="Failed to connect to TTS service")


@router.post("/tts/step/{step_number}")
async def synthesize_step_prompt(step_number: int):
    """
    Synthesize the title and instruction for a wizard step.

    Returns cached audio if available, otherwise generates dynamically.
    """
    if step_number < 1 or step_number > len(WIZARD_STEP_PROMPTS):
        raise HTTPException(status_code=400, detail="Invalid step number")

    # Check for cached audio file first (try container path first, then host path)
    static_dirs = [
        Path("/app/static/audio/wizard"),
        Path("/root/sidekick-forge/app/static/audio/wizard"),
        Path(__file__).parent.parent.parent / "static" / "audio" / "wizard"
    ]
    cached_path = None
    for static_dir in static_dirs:
        potential_path = static_dir / f"step_{step_number}.mp3"
        if potential_path.exists():
            cached_path = potential_path
            break
    if cached_path:
        def file_iterator():
            with open(cached_path, "rb") as f:
                yield f.read()
        return StreamingResponse(
            file_iterator(),
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "inline",
                "Cache-Control": "public, max-age=86400"
            }
        )

    # Generate dynamically if not cached
    prompts = WIZARD_STEP_PROMPTS[step_number]
    text = f"{prompts['title']} {prompts['instruction']}"
    request = TTSRequest(text=text)
    return await synthesize_speech(request)


@router.post("/tts/generate-cache")
async def generate_tts_cache():
    """
    Pre-generate and cache all wizard step TTS audio files.

    Admin endpoint to populate the audio cache.
    """
    # Use container path first, fallback to host path
    for cache_dir_candidate in [
        Path("/app/static/audio/wizard"),
        Path("/root/sidekick-forge/app/static/audio/wizard"),
        Path(__file__).parent.parent.parent / "static" / "audio" / "wizard"
    ]:
        try:
            cache_dir_candidate.mkdir(parents=True, exist_ok=True)
            cache_dir = cache_dir_candidate
            break
        except Exception:
            continue
    else:
        raise HTTPException(status_code=500, detail="Cannot find writable static directory")

    # Get Cartesia API key
    cartesia_api_key = os.getenv("CARTESIA_API_KEY")
    if not cartesia_api_key:
        try:
            if not supabase_manager._initialized:
                await supabase_manager.initialize()
            result = supabase_manager.admin_client.table("clients").select(
                "cartesia_api_key"
            ).eq("name", "Autonomite").limit(1).execute()
            if result.data:
                cartesia_api_key = result.data[0].get("cartesia_api_key")
        except Exception as e:
            logger.error(f"Error getting Cartesia API key: {e}")

    if not cartesia_api_key:
        raise HTTPException(status_code=500, detail="Cartesia API key not configured")

    results = []
    headers = {
        "X-API-Key": cartesia_api_key,
        "Cartesia-Version": "2024-06-10",
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        for step_num, prompts in WIZARD_STEP_PROMPTS.items():
            text = f"{prompts['title']} {prompts['instruction']}"
            cache_path = cache_dir / f"step_{step_num}.mp3"

            try:
                payload = {
                    "model_id": WIZARD_TTS_MODEL,
                    "transcript": text,
                    "voice": {"mode": "id", "id": WIZARD_VOICE_ID},
                    "output_format": {"container": "mp3", "encoding": "mp3", "sample_rate": 44100}
                }

                response = await client.post(
                    "https://api.cartesia.ai/tts/bytes",
                    headers=headers,
                    json=payload
                )

                if response.status_code == 200:
                    with open(cache_path, "wb") as f:
                        f.write(response.content)
                    results.append({"step": step_num, "status": "success", "path": str(cache_path)})
                    logger.info(f"Cached TTS for step {step_num}: {cache_path}")
                else:
                    results.append({"step": step_num, "status": "error", "error": response.text})

            except Exception as e:
                results.append({"step": step_num, "status": "error", "error": str(e)})

    return {"results": results, "cache_dir": str(cache_dir)}


# ============================================================
# Voice-Guided Wizard Session Endpoints
# ============================================================

class VoiceSessionResponse(BaseModel):
    """Response for starting a voice-guided wizard session."""
    room_name: str
    token: str
    ws_url: str
    session_id: str
    guide_name: str


# System prompt for the wizard guide agent
WIZARD_GUIDE_SYSTEM_PROMPT = """You are Farah Qubit, guiding the user through creating their AI sidekick.

## CRITICAL: Be Concise
- Keep responses to 1-2 SHORT sentences max
- Ask ONE question, then STOP and LISTEN
- Don't repeat back what they said - just confirm briefly and move on
- Never ramble or over-explain

## Workflow
1. Ask a question
2. WAIT for their answer (stop talking!)
3. Extract the key info and call set_wizard_field
4. Brief confirmation ("Got it, [name].")
5. Ask the next question OR call wizard_next_step

## Steps
1. Name - "What would you like to name your sidekick?"
2. Personality - "How should they communicate? Professional, casual, friendly?"
3. Voice - "Browse the voices on screen and pick one you like."
4. Avatar - "Want to describe an avatar, or skip for a default?"
5. Knowledge - "You can upload docs on screen. Ready to continue?"
6. Config - "Default settings, or want to customize?"
7. API Keys - "Add any API keys you have on screen."
8. Launch - "Ready to create [name]?"

## Extracting Info
- "I want to call my sidekick Herman" → extract "Herman", call set_wizard_field(name, "Herman")
- "Make it professional but friendly" → call set_wizard_field(personality_description, "professional but friendly")
- "Let's go with default" → call set_wizard_field(config_mode, "default")

## IMPORTANT: Validate Input
- For the NAME step (step 1): the name should be a proper name, not random words
- If user says "Hi", "Hello", "Hey" etc - these are greetings, NOT names. Just say "Hi! What name would you like for your sidekick?"
- If input sounds unclear, garbled, or like noise ("Whatever", random words) - ask for clarification: "I didn't catch that clearly. What would you like to name your sidekick?"
- Only accept a name when user clearly states a name (e.g., "Call it Max", "Name it Sarah", "Let's name them Buddy")

## What NOT to do
- Don't say "That's a great choice!" or similar filler
- Don't explain what you're doing ("Let me save that...")
- Don't ask multiple questions at once
- Don't describe UI elements they can already see
- Don't repeat their answer back to them verbatim

## UI Steps (5, 7)
These are handled by clicking on screen. Just ask "Ready to continue?" and call wizard_next_step when they say yes.

## Tools
- set_wizard_field: Save a value (ALWAYS call this when you get info)
- wizard_next_step: Move to next step
- wizard_previous_step: Go back
- get_wizard_state: Check progress
- complete_wizard: Finish (only on step 8 when confirmed)"""


@router.post("/sessions/{session_id}/voice", response_model=VoiceSessionResponse)
async def start_wizard_voice_session(
    session_id: str,
    admin_user: dict = Depends(get_admin_user)
):
    """
    Start a voice-guided wizard session with Farah Qubit (or configured guide).

    Creates a LiveKit room with the wizard guide agent and returns connection details.
    The agent will have special wizard tools to fill form fields based on voice input.
    """
    from app.integrations.livekit_client import livekit_manager
    from app.services.agent_service_multitenant import AgentService
    from app.services.client_service_multitenant import ClientService
    from app.config import settings
    from app.utils.livekit_credentials import LiveKitCredentialManager

    # Verify session ownership
    session = await wizard_session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["user_id"] != admin_user["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    client_id = session["client_id"]

    # Initialize LiveKit manager if needed
    if not livekit_manager._initialized:
        await livekit_manager.initialize()

    # Farah Qubit - Platform Wizard Guide (hardcoded configuration)
    # These are platform-level defaults, NOT loaded from any client database
    guide_agent_slug = "farah-qubit"
    guide_agent_name = "Farah Qubit"

    # Farah's voice settings (platform defaults)
    farah_voice_settings = {
        "voice_id": "1013a0b6-8ce7-44dd-8bce-aadf7ac495a0",  # Farah's Cartesia voice
        "tts_provider": "cartesia",
        "stt_provider": "cartesia",
        "llm_provider": "cerebras",
        "llm_model": "zai-glm-4.7",
        "model": "sonic-3",
        "temperature": 0.8,
    }

    # Farah's sound settings (platform defaults)
    farah_sound_settings = {
        "thinking_sound": "beta1",
        "thinking_volume": 0.5,
        "ambient_sound": "office",
        "ambient_volume": 0.65,
    }

    agent_service = AgentService()
    client_service = ClientService()

    # Get client API keys for the wizard session
    try:
        api_keys = await agent_service.get_client_api_keys(uuid.UUID(client_id))
    except Exception as e:
        logger.warning(f"Could not get client API keys: {e}")
        api_keys = {}

    # Get client info for embedding config
    platform_client = await client_service.get_client(client_id)

    # Build wizard steps for the agent
    wizard_steps = [
        {"step": k, "title": v["title"], "instruction": v["instruction"]}
        for k, v in WIZARD_STEP_PROMPTS.items()
    ]

    # Build wizard configuration for the agent
    wizard_config = {
        "session_id": session_id,
        "wizard_type": "sidekick_onboarding",
        "steps": wizard_steps,
        "current_step": session.get("current_step", 1),
        "form_data": session.get("step_data", {}),
        "guide_system_prompt": WIZARD_GUIDE_SYSTEM_PROMPT,
    }

    # Generate a conversation_id for this wizard session (required by agent)
    conversation_id = str(uuid.uuid4())

    # Get Supabase credentials (required by agent for context management)
    # For wizard (a platform-level feature), we use platform credentials
    # Client-specific credentials are only used if the client has their own Supabase
    from app.config import settings as app_settings

    client_supabase_url = None
    client_supabase_anon_key = None
    client_supabase_service_role_key = None

    if platform_client:
        # Try direct fields first (new structure)
        if hasattr(platform_client, 'supabase_url') and platform_client.supabase_url:
            client_supabase_url = platform_client.supabase_url
            client_supabase_anon_key = getattr(platform_client, 'supabase_anon_key', None)
            client_supabase_service_role_key = getattr(platform_client, 'supabase_service_role_key', None)
        # Then check settings.supabase (old structure)
        elif hasattr(platform_client, 'settings') and platform_client.settings:
            if hasattr(platform_client.settings, 'supabase') and platform_client.settings.supabase:
                client_supabase_url = platform_client.settings.supabase.url
                client_supabase_anon_key = platform_client.settings.supabase.anon_key
                client_supabase_service_role_key = platform_client.settings.supabase.service_role_key

    # Fall back to platform Supabase credentials for wizard (platform-level feature)
    if not client_supabase_service_role_key:
        logger.info("Using platform Supabase credentials for wizard (client has no own Supabase)")
        client_supabase_url = app_settings.supabase_url
        client_supabase_anon_key = app_settings.supabase_anon_key
        client_supabase_service_role_key = app_settings.supabase_service_role_key

    logger.info(f"Wizard Supabase credentials: url={bool(client_supabase_url)}, anon={bool(client_supabase_anon_key)}, service={bool(client_supabase_service_role_key)}")

    # Build room metadata with wizard mode flag
    room_metadata = {
        "type": "wizard_guide",  # This flag tells the agent worker to load wizard tools
        "wizard_config": wizard_config,
        "client_id": client_id,
        "user_id": admin_user["user_id"],
        "agent_slug": guide_agent_slug,
        "agent_name": guide_agent_name,
        "system_prompt": WIZARD_GUIDE_SYSTEM_PROMPT,
        "conversation_id": conversation_id,  # Required by agent
        "voice_settings": farah_voice_settings,  # Platform hardcoded defaults
        "sound_settings": farah_sound_settings,  # Platform hardcoded defaults
        "api_keys": {k: v for k, v in api_keys.items() if v},
        # Client Supabase credentials (required by agent for context management)
        "supabase_url": client_supabase_url,
        "supabase_anon_key": client_supabase_anon_key,
        "supabase_service_role_key": client_supabase_service_role_key,
        # Platform Supabase credentials (for wizard session persistence — wizard tables live here)
        "platform_supabase_url": app_settings.supabase_url,
        "platform_supabase_service_role_key": app_settings.supabase_service_role_key,
    }

    # Add embedding config if available, with fallback default
    embedding_added = False
    if platform_client and hasattr(platform_client, 'settings') and platform_client.settings:
        if hasattr(platform_client.settings, 'embedding') and platform_client.settings.embedding:
            emb = platform_client.settings.embedding
            if hasattr(emb, 'model_dump'):
                room_metadata["embedding"] = emb.model_dump()
                embedding_added = True
            elif hasattr(emb, 'dict'):
                room_metadata["embedding"] = emb.dict()
                embedding_added = True

    # Fallback: provide default embedding config if not set (required by agent context manager)
    if not embedding_added:
        room_metadata["embedding"] = {
            "provider": "siliconflow",
            "document_model": "Qwen/Qwen3-Embedding-4B",
            "conversation_model": "Qwen/Qwen3-Embedding-4B",
            "dimension": 1024,
        }
        logger.info("Using fallback embedding config for wizard voice session")

    # Create room name with unique suffix so agent doesn't skip greeting due to deduplication
    # Using timestamp ensures each voice session attempt is a completely fresh room
    unique_suffix = int(time.time() * 1000) % 100000  # Last 5 digits of milliseconds
    room_name = f"wizard-{session_id}-{unique_suffix}"

    # Delete any existing room with the same name to prevent duplicate agents
    try:
        await livekit_manager.delete_room(room_name)
        logger.info(f"Deleted existing wizard room: {room_name}")
    except Exception as e:
        logger.debug(f"No existing room to delete or delete failed: {e}")

    # Create the LiveKit room WITHOUT auto-dispatch (we'll dispatch explicitly below)
    # Using only explicit dispatch prevents duplicate agents
    try:
        room = await livekit_manager.create_room(
            name=room_name,
            empty_timeout=600,  # 10 minutes
            max_participants=2,  # User + Agent
            metadata=room_metadata,
            enable_agent_dispatch=False,  # Disabled - we dispatch explicitly below
            agent_name=None  # Not needed when using explicit dispatch
        )
        logger.info(f"Created wizard voice room: {room_name}")
    except Exception as e:
        logger.error(f"Failed to create wizard room: {e}")
        raise HTTPException(status_code=500, detail="Failed to create voice session")

    # EXPLICITLY DISPATCH THE AGENT via LiveKit API (single dispatch to prevent duplicates)
    try:
        from livekit import api as livekit_api_module
        livekit_api = livekit_api_module.LiveKitAPI(
            url=livekit_manager.url,
            api_key=livekit_manager.api_key,
            api_secret=livekit_manager.api_secret
        )

        # Use AGENT_NAME env var if set, otherwise default
        wizard_agent_name = os.environ.get("WIZARD_AGENT_NAME") or os.environ.get("AGENT_NAME") or "sidekick-agent-staging-local"
        dispatch_request = livekit_api_module.CreateAgentDispatchRequest(
            room=room_name,
            metadata=json.dumps(room_metadata),
            agent_name=wizard_agent_name
        )

        dispatch_response = await livekit_api.agent_dispatch.create_dispatch(dispatch_request)
        dispatch_id = getattr(dispatch_response, 'dispatch_id', None) or getattr(dispatch_response, 'id', None)
        logger.info(f"✅ Wizard agent dispatched with dispatch_id: {dispatch_id}")
        await livekit_api.aclose()
    except Exception as e:
        logger.error(f"❌ Failed to dispatch wizard agent: {e}")
        # Continue anyway - the room dispatch might still work

    # Create participant token for the user
    wizard_agent_name = os.environ.get("WIZARD_AGENT_NAME") or os.environ.get("AGENT_NAME") or "sidekick-agent-staging-local"
    token = livekit_manager.create_token(
        identity=f"user-{admin_user['user_id']}",
        room_name=room_name,
        metadata={"user_id": admin_user["user_id"], "wizard_session_id": session_id},
        ttl=3600,  # 1 hour
        dispatch_agent_name=wizard_agent_name,
        dispatch_metadata=room_metadata
    )

    # Get the WebSocket URL for LiveKit
    livekit_url, _, _ = await LiveKitCredentialManager.get_backend_credentials()
    # Convert HTTP URL to WebSocket URL
    ws_url = livekit_url.replace("https://", "wss://").replace("http://", "ws://")

    # Update session to track voice session
    try:
        await wizard_session_service.update_step(
            session_id=session_id,
            step_number=session.get("current_step", 1),
            step_data={"voice_session_active": True, "livekit_room_name": room_name},
            advance=False
        )
    except Exception as e:
        logger.warning(f"Could not update session with voice room info: {e}")

    return VoiceSessionResponse(
        room_name=room_name,
        token=token,
        ws_url=ws_url,
        session_id=session_id,
        guide_name=guide_agent_name
    )


@router.delete("/sessions/{session_id}/voice")
async def end_wizard_voice_session(
    session_id: str,
    admin_user: dict = Depends(get_admin_user)
):
    """
    End a voice-guided wizard session.

    Cleans up the LiveKit room and updates session state.
    """
    from app.integrations.livekit_client import livekit_manager

    # Verify session ownership
    session = await wizard_session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["user_id"] != admin_user["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get room name from session step_data (stored when voice session started)
    step_data = session.get("step_data", {})
    room_name = step_data.get("livekit_room_name") or f"wizard-{session_id}"

    # Try to delete the room
    try:
        if livekit_manager._initialized:
            await livekit_manager.delete_room(room_name)
            logger.info(f"Deleted wizard voice room: {room_name}")
    except Exception as e:
        logger.warning(f"Could not delete wizard room {room_name}: {e}")

    # Update session to clear voice state
    try:
        await wizard_session_service.update_step(
            session_id=session_id,
            step_number=session.get("current_step", 1),
            step_data={"voice_session_active": False, "livekit_room_name": None},
            advance=False
        )
    except Exception as e:
        logger.warning(f"Could not update session: {e}")

    return {"success": True, "message": "Voice session ended"}
