from fastapi import APIRouter, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
from typing import Optional, Dict, Any, List
import asyncio
import json
import logging
import uuid
from io import BytesIO

from fastapi.templating import Jinja2Templates

from app.core.dependencies import get_agent_service, get_client_service
from app.config import settings
from app.services.tools_service_supabase import ToolsService
from app.api.v1 import trigger as trigger_api
from app.services.agent_service_multitenant import AgentService as MultitentAgentService
from app.services.client_service_multitenant import ClientService as MultitenantClientService
from app.services.client_service_supabase_enhanced import ClientService as SupabaseClientService
from app.utils.supabase_credentials import SupabaseCredentialManager
from app.services.client_supabase_auth import ensure_client_user_credentials
from app.integrations.supabase_client import supabase_manager
from app.middleware.auth import require_user_auth
from app.models.user import AuthContext
from app.agent_modules.transcript_store import store_turn
from app.services.usage_tracking import usage_tracking_service
from app.services.tier_features import get_tier_features
from pydantic import BaseModel, EmailStr

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing", "past_due"}


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return bool(value)


def _effective_client_tier(client_record: Dict[str, Any]) -> str:
    tier = str(client_record.get("tier") or "adventurer").lower()
    if tier not in {"adventurer", "champion", "paragon"}:
        tier = "adventurer"

    subscription_id = client_record.get("stripe_subscription_id")
    subscription_status = str(client_record.get("subscription_status") or "").lower()

    # If a paid plan exists but subscription is no longer active, fail closed to base tier features.
    if tier in {"champion", "paragon"} and subscription_id:
        if subscription_status and subscription_status not in ACTIVE_SUBSCRIPTION_STATUSES:
            return "adventurer"

    return tier


def _compute_effective_mode_access(agent_record: Dict[str, Any], client_record: Dict[str, Any]) -> Dict[str, bool]:
    tier = _effective_client_tier(client_record)
    tier_features = get_tier_features(tier)

    voice_enabled = _as_bool(agent_record.get("voice_chat_enabled"), True) and bool(
        tier_features.get("voice_chat_enabled", True)
    )
    text_enabled = _as_bool(agent_record.get("text_chat_enabled"), True) and bool(
        tier_features.get("text_chat_enabled", True)
    )
    video_enabled = _as_bool(agent_record.get("video_chat_enabled"), False) and bool(
        tier_features.get("video_chat_enabled", False)
    )

    return {
        "voice_chat_enabled": voice_enabled,
        "text_chat_enabled": text_enabled,
        "video_chat_enabled": video_enabled,
    }


async def _resolve_effective_user_id(user_id: str, client_id: str) -> str:
    """
    Resolve the effective user_id for querying a client's database.

    For platform admins previewing external clients, this looks up the
    platform_client_user_mappings table to find the shadow user_id that
    was created in the client's Supabase instance.

    For regular client users, returns the original user_id unchanged.
    """
    try:
        from supabase import create_client
        platform_sb = create_client(settings.supabase_url, settings.supabase_service_role_key)
        mapping_result = platform_sb.table("platform_client_user_mappings").select("client_user_id").eq(
            "platform_user_id", user_id
        ).eq("client_id", client_id).maybe_single().execute()

        if mapping_result.data and mapping_result.data.get("client_user_id"):
            effective_user_id = mapping_result.data["client_user_id"]
            logger.info(f"[embed] Resolved platform user {user_id[:8]}... -> client user {effective_user_id[:8]}...")
            return effective_user_id
    except Exception as mapping_err:
        # Non-fatal - continue with original user_id (regular client user case)
        logger.debug(f"[embed] No platform-to-client user mapping found: {mapping_err}")

    return user_id


class ClientUserSyncRequest(BaseModel):
    client_id: str
    email: EmailStr
    password: str


@router.get("/embed/{client_id}/{agent_slug}", response_class=HTMLResponse)
async def embed_sidekick(
    request: Request,
    client_id: str,
    agent_slug: str,
    theme: Optional[str] = "dark",
):
    try:
        client_supabase_url, client_supabase_anon_key = await SupabaseCredentialManager.get_frontend_credentials(
            client_id,
            allow_platform_ids={"global"},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Fetch Supertab config, chat mode settings, and agent display info
    supertab_config = None
    voice_chat_enabled = True  # Default to enabled
    text_chat_enabled = True   # Default to enabled
    video_chat_enabled = False # Default to disabled (requires avatar setup)
    agent_name = agent_slug.replace("-", " ").title()  # Default: convert slug to title case
    agent_image = None  # Default: no image
    agent_id = None  # Will be populated from database lookup
    agent_description = ""  # Agent description for info modal
    agent_tools = []  # List of assigned tools for info modal
    try:
        from supabase import create_client

        # Get client billing/plan context and Supertab config from platform database
        platform_sb = create_client(settings.supabase_url, settings.supabase_service_role_key)
        try:
            client_result = platform_sb.table("clients").select(
                "supertab_client_id, supabase_service_role_key, tier, stripe_subscription_id, subscription_status"
            ).eq("id", client_id).maybe_single().execute()
        except Exception:
            # Backward-compatible fallback if optional columns are unavailable.
            client_result = platform_sb.table("clients").select(
                "supertab_client_id, supabase_service_role_key"
            ).eq("id", client_id).maybe_single().execute()
        client_record = client_result.data or {}
        client_supertab_id = client_result.data.get("supertab_client_id") if client_result.data else None
        client_service_key = client_result.data.get("supabase_service_role_key") if client_result.data else None

        # Get agent settings from client database (chat mode settings, Supertab, and display info)
        if client_supabase_url and client_service_key:
            client_sb = create_client(client_supabase_url, client_service_key)
            try:
                agent_result = client_sb.table("agents").select(
                    "id, name, description, agent_image, supertab_enabled, supertab_voice_enabled, "
                    "supertab_text_enabled, supertab_video_enabled, supertab_experience_id, supertab_price, "
                    "supertab_cta, supertab_subscription_experience_id, supertab_subscription_price, "
                    "voice_chat_enabled, text_chat_enabled, video_chat_enabled"
                ).eq("slug", agent_slug).maybe_single().execute()
            except Exception:
                # Fallback for tenant schemas that haven't received new supertab per-mode columns yet.
                agent_result = client_sb.table("agents").select(
                    "id, name, description, agent_image, supertab_enabled, supertab_experience_id, "
                    "supertab_price, supertab_cta, supertab_subscription_experience_id, "
                    "supertab_subscription_price, voice_chat_enabled, text_chat_enabled, video_chat_enabled"
                ).eq("slug", agent_slug).maybe_single().execute()

            if agent_result.data:
                agent_record = agent_result.data
                # Get agent ID
                if agent_record.get("id"):
                    agent_id = str(agent_record.get("id"))
                # Get agent display info
                if agent_record.get("name"):
                    agent_name = agent_record.get("name")
                if agent_record.get("agent_image"):
                    agent_image = agent_record.get("agent_image")
                if agent_record.get("description"):
                    agent_description = agent_record.get("description")

                # Fetch assigned tools for this agent
                if agent_id:
                    try:
                        # Get tool IDs from agent_tools table (platform DB)
                        agent_tools_result = platform_sb.table("agent_tools").select("tool_id").eq("agent_id", agent_id).execute()
                        tool_ids = [r["tool_id"] for r in (agent_tools_result.data or [])]

                        if tool_ids:
                            # Get tool details from tools table (platform DB) - only active phase, non-admin-only tools
                            tools_result = platform_sb.table("tools").select("name, slug, description, icon_url, execution_phase, admin_only").in_("id", tool_ids).eq("enabled", True).execute()
                            if tools_result.data:
                                # Filter to only show active (conversation) abilities, not ambient or admin-only
                                agent_tools = [
                                    {
                                        "name": t.get("name", ""),
                                        "slug": t.get("slug", ""),
                                        "description": t.get("description", ""),
                                        "icon_url": t.get("icon_url", "")
                                    }
                                    for t in tools_result.data
                                    if t.get("execution_phase") == "active" and not t.get("admin_only")
                                ]
                    except Exception as tools_err:
                        logger.warning(f"[embed] Failed to fetch agent tools: {tools_err}")

                # Get chat mode settings (default to True if not set)
                mode_access = _compute_effective_mode_access(agent_record, client_record)
                voice_chat_enabled = mode_access["voice_chat_enabled"]
                text_chat_enabled = mode_access["text_chat_enabled"]
                video_chat_enabled = mode_access["video_chat_enabled"]

                # Get Supertab settings
                agent_supertab_enabled = _as_bool(agent_record.get("supertab_enabled"), False)
                supertab_voice_enabled = _as_bool(
                    agent_record.get("supertab_voice_enabled"),
                    agent_supertab_enabled,
                )
                supertab_text_enabled = _as_bool(agent_record.get("supertab_text_enabled"), False)
                supertab_video_enabled = _as_bool(agent_record.get("supertab_video_enabled"), False)

                # Fail closed: only allow paywall modes that are currently accessible.
                supertab_voice_enabled = supertab_voice_enabled and voice_chat_enabled
                supertab_text_enabled = supertab_text_enabled and text_chat_enabled
                supertab_video_enabled = supertab_video_enabled and video_chat_enabled

                agent_supertab_experience_id = agent_record.get("supertab_experience_id")
                agent_supertab_price = agent_record.get("supertab_price")
                agent_supertab_cta = agent_record.get("supertab_cta")

                # Subscription settings
                agent_supertab_sub_experience_id = agent_record.get("supertab_subscription_experience_id")
                agent_supertab_sub_price = agent_record.get("supertab_subscription_price")

                # Only create Supertab config if both client_id and agent is enabled with at least one experience_id
                has_session = bool(agent_supertab_experience_id)
                has_subscription = bool(agent_supertab_sub_experience_id)
                supertab_any_mode_enabled = supertab_voice_enabled or supertab_text_enabled or supertab_video_enabled
                if client_supertab_id and (agent_supertab_enabled or supertab_any_mode_enabled) and (has_session or has_subscription):
                    supertab_config = {
                        "enabled": True,
                        "client_id": client_supertab_id,
                        "voice_enabled": supertab_voice_enabled,
                        "text_enabled": supertab_text_enabled,
                        "video_enabled": supertab_video_enabled,
                        "experience_id": agent_supertab_experience_id or "",
                        "price": agent_supertab_price or "per session",
                        "cta": agent_supertab_cta or "Start a voice conversation",
                        "subscription_experience_id": agent_supertab_sub_experience_id or "",
                        "subscription_price": agent_supertab_sub_price or "$20/mo",
                    }
                    logger.info(f"[embed] Supertab enabled for {client_id}/{agent_slug} (session={has_session}, subscription={has_subscription})")
    except Exception as e:
        # Fail open - if we can't get config, just continue with defaults
        logger.warning(f"[embed] Failed to fetch agent config: {e}")

    return templates.TemplateResponse(
        "embed/sidekick.html",
        {
            "request": request,
            "client_id": client_id,
            "agent_id": agent_id,
            "agent_slug": agent_slug,
            "agent_name": agent_name,
            "agent_image": agent_image,
            "agent_description": agent_description,
            "agent_tools": agent_tools,
            "theme": theme,
            "supabase_url": settings.supabase_url,
            "supabase_anon_key": settings.supabase_anon_key,
            "client_supabase_url": client_supabase_url,
            "client_supabase_anon_key": client_supabase_anon_key,
            "supertab_config": supertab_config,
            "voice_chat_enabled": voice_chat_enabled,
            "text_chat_enabled": text_chat_enabled,
            "video_chat_enabled": video_chat_enabled,
        },
    )


@router.post("/api/embed/client-users/sync")
async def sync_client_user_credentials(
    payload: ClientUserSyncRequest,
    auth: AuthContext = Depends(require_user_auth),
):
    await supabase_manager.initialize()

    def _fetch_user():
        return supabase_manager.admin_client.auth.admin.get_user_by_id(str(auth.user_id))

    user_record = await asyncio.to_thread(_fetch_user)
    user_email = getattr(user_record.user, "email", None) if user_record else None

    if not user_email or user_email.lower() != payload.email.lower():
        raise HTTPException(status_code=403, detail="Email mismatch")

    try:
        await ensure_client_user_credentials(payload.client_id, payload.email, payload.password)
    except Exception as exc:
        logger.error(f"Failed to sync client user credentials: {exc}")
        raise HTTPException(status_code=500, detail="Unable to synchronize client credentials")

    return {"success": True}


class SupertabUserCreateRequest(BaseModel):
    client_id: str
    agent_slug: str
    email: EmailStr
    supertab_user_id: Optional[str] = None
    payment_status: str
    offering_id: Optional[str] = None


class PrintReadyPdfRequest(BaseModel):
    client_id: str
    user_id: str
    conversation_ids: List[str]
    filename: Optional[str] = None
    user_label: Optional[str] = None
    assistant_label: Optional[str] = None


@router.post("/api/embed/supertab/create-user")
async def create_supertab_user(payload: SupertabUserCreateRequest):
    """
    Create or link a user in the client's Supabase after a successful Supertab payment.
    This allows users who pay via Supertab to have their conversations tracked.
    """
    try:
        import secrets
        from supabase import create_client

        logger.info(f"[supertab] Creating user for {payload.email} in client {payload.client_id}")

        # Get client's Supabase credentials
        client_supabase_url, _, client_service_key = await SupabaseCredentialManager.get_client_supabase_credentials(
            payload.client_id
        )

        if not client_supabase_url or not client_service_key:
            raise HTTPException(status_code=400, detail="Client Supabase not configured")

        client_sb = create_client(client_supabase_url, client_service_key)

        # Check if user already exists by email
        existing_user = None
        try:
            # Try to find user by email in auth.users via admin API
            users_response = client_sb.auth.admin.list_users()
            for user in users_response:
                if hasattr(user, 'email') and user.email and user.email.lower() == payload.email.lower():
                    existing_user = user
                    break
        except Exception as e:
            logger.debug(f"[supertab] Could not list users: {e}")

        user_id = None

        if existing_user:
            # User already exists
            user_id = str(existing_user.id)
            logger.info(f"[supertab] Found existing user {user_id} for {payload.email}")

            # Update user metadata with Supertab info
            try:
                client_sb.auth.admin.update_user_by_id(
                    user_id,
                    {"user_metadata": {
                        "supertab_user_id": payload.supertab_user_id,
                        "supertab_payment_status": payload.payment_status,
                        "supertab_offering_id": payload.offering_id,
                    }}
                )
            except Exception as e:
                logger.warning(f"[supertab] Could not update user metadata: {e}")
        else:
            # Create new user with random password (they'll use Supertab for auth)
            temp_password = secrets.token_urlsafe(32)

            try:
                new_user = client_sb.auth.admin.create_user({
                    "email": payload.email,
                    "password": temp_password,
                    "email_confirm": True,  # Auto-confirm since they paid
                    "user_metadata": {
                        "source": "supertab_payment",
                        "supertab_user_id": payload.supertab_user_id,
                        "supertab_payment_status": payload.payment_status,
                        "supertab_offering_id": payload.offering_id,
                        "agent_slug": payload.agent_slug,
                    }
                })
                user_id = str(new_user.user.id)
                logger.info(f"[supertab] Created new user {user_id} for {payload.email}")
            except Exception as e:
                logger.error(f"[supertab] Failed to create user: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to create user: {str(e)}")

        # Record the payment/entitlement in a supertab_entitlements table if it exists
        try:
            client_sb.table("supertab_entitlements").upsert({
                "user_id": user_id,
                "email": payload.email,
                "supertab_user_id": payload.supertab_user_id,
                "offering_id": payload.offering_id,
                "payment_status": payload.payment_status,
                "agent_slug": payload.agent_slug,
                "created_at": "now()",
            }, on_conflict="user_id,offering_id").execute()
        except Exception as e:
            # Table might not exist - that's OK
            logger.debug(f"[supertab] Could not record entitlement (table may not exist): {e}")

        return {
            "success": True,
            "user_id": user_id,
            "email": payload.email,
            "is_new_user": existing_user is None,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[supertab] Error creating user: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/embed/text/stream")
async def embed_text_stream(
    request: Request,
    client_id: str = Form(...),
    agent_slug: str = Form(...),
    message: str = Form(...),
    conversation_id: Optional[str] = Form(None),
    user_id: Optional[str] = Form(None),
):
    async def generate():
        try:
            try:
                yield ":stream-open\n\n"
            except Exception:
                pass

            logger.info(
                "[embed-stream] start client_id=%s agent=%s", client_id, agent_slug
            )

            # Use provided user_id or generate a deterministic one
            # Use a valid UUID so downstream queries against Supabase succeed.
            effective_user_id = user_id if user_id else str(uuid.uuid5(uuid.NAMESPACE_URL, "sidekick-forge/embed-user"))

            # Use multitenant services for proper architecture
            agent_service = MultitentAgentService()
            client_service = MultitenantClientService()

            # Get agent from client database
            from uuid import UUID
            client_uuid = UUID(client_id)
            agent = await agent_service.get_agent(client_uuid, agent_slug)
            if not agent or not agent.enabled:
                yield f"data: {json.dumps({'error': 'Agent not available'})}\n\n"
                return

            # Get client info and API keys
            platform_client = await client_service.get_client(client_id)
            if not platform_client:
                yield f"data: {json.dumps({'error': 'Client not found'})}\n\n"
                return

            # Enforce text mode access in backend (agent + product tier gating)
            platform_client_data = (
                platform_client.model_dump()
                if hasattr(platform_client, "model_dump")
                else (platform_client.dict() if hasattr(platform_client, "dict") else {})
            )
            agent_data_for_access = {
                "voice_chat_enabled": getattr(agent, "voice_chat_enabled", True),
                "text_chat_enabled": getattr(agent, "text_chat_enabled", True),
                "video_chat_enabled": getattr(agent, "video_chat_enabled", False),
            }
            mode_access = _compute_effective_mode_access(agent_data_for_access, platform_client_data)
            if not mode_access["text_chat_enabled"]:
                yield f"data: {json.dumps({'error': 'Text chat is disabled for this sidekick'})}\n\n"
                return

            api_keys = await agent_service.get_client_api_keys(client_uuid)

            # Use provided conversation_id or generate a new one
            effective_conversation_id = conversation_id if conversation_id else str(uuid.uuid4())
            session_id = str(uuid.uuid4())
            is_new_conversation = not conversation_id

            trigger_request = trigger_api.TriggerAgentRequest(
                agent_slug=agent_slug,
                client_id=client_id,
                mode=trigger_api.TriggerMode.TEXT,
                message=message,
                user_id=effective_user_id,
                session_id=session_id,
                conversation_id=effective_conversation_id,
            )

            # Initialize ToolsService with Supabase-based ClientService for platform access
            import os
            platform_supabase_url = os.getenv('SUPABASE_URL')
            platform_supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
            supabase_client_service = SupabaseClientService(
                supabase_url=platform_supabase_url,
                supabase_key=platform_supabase_key
            )
            tools_service = ToolsService(client_service=supabase_client_service)

            # Set up the LiveKit room and dispatch the agent job
            try:
                from app.integrations.livekit_client import livekit_manager
                from app.config import settings
                
                backend_livekit = livekit_manager
                if not backend_livekit._initialized:
                    await backend_livekit.initialize()

                # Build agent context and room
                agent_context, _, _, _ = await trigger_api._build_agent_context_for_dispatch(
                    agent=agent,
                    client=platform_client,
                    conversation_id=effective_conversation_id,
                    user_id=effective_user_id,
                    session_id=session_id,
                    mode="text",
                    request_context=None,
                    client_conversation_id=effective_conversation_id,
                )
                
                # Add tools and user message to context
                tools_payload = await trigger_api._get_agent_tools(tools_service, platform_client.id, agent.id)
                logger.info(f"[embed-stream] tools_payload count: {len(tools_payload) if tools_payload else 0}")
                if tools_payload:
                    logger.info(f"[embed-stream] tool slugs: {[t.get('slug') for t in tools_payload]}")
                    agent_context["tools"] = tools_payload
                tools_config = trigger_api._extract_agent_tools_config(agent)
                trigger_api._apply_tool_prompt_sections(agent_context, tools_payload, tools_config)
                agent_context["user_message"] = message

                # Create room and dispatch
                # NOTE: enable_agent_dispatch=False because we explicitly call dispatch_agent_job below
                # Setting it to True causes DOUBLE dispatch (one from room creation, one from explicit call)
                text_room_name = f"text-{effective_conversation_id}-{uuid.uuid4().hex[:8]}"
                await trigger_api.ensure_livekit_room_exists(
                    backend_livekit,
                    text_room_name,
                    agent_name=settings.livekit_agent_name,
                    agent_slug=agent.slug,
                    user_id=effective_user_id,
                    agent_config=agent_context,
                    enable_agent_dispatch=False,  # Don't dispatch here - we do it explicitly below
                )

                await trigger_api.dispatch_agent_job(
                    livekit_manager=backend_livekit,
                    room_name=text_room_name,
                    agent=agent,
                    client=platform_client,
                    user_id=effective_user_id,
                    conversation_id=effective_conversation_id,
                    session_id=session_id,
                    tools=agent_context.get("tools"),
                    tools_config=agent_context.get("tools_config"),
                    api_keys=agent_context.get("api_keys"),
                    agent_context=agent_context,
                )

                # Track text usage for quota metering (per-agent)
                try:
                    await usage_tracking_service.initialize()
                    is_within_quota, quota_status = await usage_tracking_service.increment_agent_text_usage(
                        client_id=str(platform_client.id),
                        agent_id=str(agent.id),
                        count=1,
                    )
                    if not is_within_quota:
                        logger.warning(
                            "Text quota exceeded for agent %s (client %s): %d/%d messages",
                            agent.slug, platform_client.id, quota_status.used, quota_status.limit
                        )
                except Exception as usage_err:
                    logger.warning("Failed to track text usage in embed stream: %s", usage_err)

                # Stream responses from the worker
                async for update in trigger_api.poll_for_text_response_streaming(
                    backend_livekit,
                    text_room_name,
                ):
                    if "error" in update:
                        yield f"data: {json.dumps({'error': update['error']})}\n\n"
                        return
                    elif "delta" in update:
                        yield f"data: {json.dumps({'delta': update['delta']})}\n\n"
                    elif update.get("done"):
                        full_text = update.get("full_text", "")
                        citations = update.get("citations", [])

                        # Persist the conversation turn to the client's Supabase
                        try:
                            client_supabase_url, _, client_service_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
                            from supabase import create_client
                            client_sb = create_client(client_supabase_url, client_service_key)

                            # Build metadata, including widget data if present
                            turn_metadata = {"channel": "text", "agent_slug": agent_slug}
                            if update.get("widget"):
                                turn_metadata["widget"] = update["widget"]

                            await store_turn({
                                "conversation_id": effective_conversation_id,
                                "session_id": session_id,
                                "agent_id": str(agent.id) if agent.id else None,
                                "user_id": effective_user_id,
                                "client_id": client_id,  # Required for multi-tenant schemas with RLS
                                "user_text": message,
                                "assistant_text": full_text,
                                "citations": citations,
                                "metadata": turn_metadata
                            }, client_sb)
                            logger.info(f"[embed-stream] Persisted conversation turn for {effective_conversation_id}")
                        except Exception as store_err:
                            logger.warning(f"[embed-stream] Failed to persist conversation turn: {store_err}")

                        final_payload = {
                            "done": True,
                            "full_text": full_text,
                            "conversation_id": effective_conversation_id,
                            "is_new_conversation": is_new_conversation,
                            "citations": citations,
                            "tools": {"results": update.get("tool_results", [])},
                        }
                        # Include widget trigger if present
                        if update.get("widget"):
                            final_payload["widget"] = update["widget"]
                            logger.info(f"[embed-stream] Widget trigger included in response: {update['widget'].get('type')}")
                        yield f"data: {json.dumps(final_payload)}\n\n"
                        return

            except Exception as livekit_err:
                # NO FALLBACK POLICY: If LiveKit streaming fails, return an error rather than
                # falling back to non-RAG paths that would produce hallucinated responses
                import traceback
                logger.error(f"[embed-stream] ❌ NO FALLBACK POLICY: LiveKit streaming failed: {type(livekit_err).__name__}: {livekit_err}")
                logger.error(f"[embed-stream] Traceback: {traceback.format_exc()}")
                error_msg = f"RAG processing failed: {str(livekit_err)}"
                yield f"data: {json.dumps({'error': error_msg, 'no_fallback': True})}\n\n"
                return

        except Exception as exc:
            # NO FALLBACK POLICY: Any error in the streaming path should return an error
            logger.error("❌ NO FALLBACK POLICY - embed_text_stream error: %s", exc, exc_info=True)
            yield f"data: {json.dumps({'error': f'Processing failed: {str(exc)}', 'no_fallback': True})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


class GenerateTitleRequest(BaseModel):
    first_message: str


@router.post("/api/embed/conversations/{conversation_id}/generate-title")
async def generate_conversation_title(
    conversation_id: str,
    request: GenerateTitleRequest,
    client_id: str = None,
):
    """
    Generate an AI title for a conversation based on the first message.
    Uses the agent's configured LLM to generate a short, descriptive title.
    """
    try:
        if not client_id:
            raise HTTPException(status_code=400, detail="client_id is required")

        # Get client's Supabase credentials
        client_supabase_url, _, client_service_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
        from supabase import create_client
        client_sb = create_client(client_supabase_url, client_service_key)

        # Get conversation to find the agent
        conversation_result = client_sb.table("conversations").select("*").eq("id", conversation_id).limit(1).execute()
        if not conversation_result.data:
            raise HTTPException(status_code=404, detail="Conversation not found")

        conversation = conversation_result.data[0]
        agent_id = conversation.get("agent_id")

        # Get agent's LLM settings
        agent_service = MultitentAgentService()
        from uuid import UUID
        client_uuid = UUID(client_id)

        # Get API keys for title generation
        api_keys = await agent_service.get_client_api_keys(client_uuid)
        openai_key = api_keys.get("openai_api_key") if api_keys else None

        if not openai_key:
            # Fallback: generate a simple title from the message
            words = request.first_message.split()[:5]
            title = " ".join(words) + ("..." if len(request.first_message.split()) > 5 else "")
        else:
            # Use OpenAI to generate a title
            import openai
            openai_client = openai.OpenAI(api_key=openai_key)

            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "Generate a very short title (3-6 words) for a conversation that starts with the following message. Return only the title, no quotes or punctuation."
                    },
                    {
                        "role": "user",
                        "content": request.first_message
                    }
                ],
                max_tokens=20,
                temperature=0.7
            )
            title = response.choices[0].message.content.strip()

        # Update the conversation with the generated title
        # Handle schemas that may not have conversation_title column
        try:
            client_sb.table("conversations").update({
                "conversation_title": title
            }).eq("id", conversation_id).execute()
        except Exception as update_err:
            # If conversation_title column doesn't exist, log and continue
            # The title was still generated successfully, just can't persist it
            if "conversation_title" in str(update_err).lower() or "column" in str(update_err).lower():
                logger.info(f"Conversations table missing conversation_title column, skipping title persistence")
            else:
                raise

        return {"success": True, "title": title, "conversation_id": conversation_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to generate conversation title: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/embed/conversations")
async def list_embed_conversations(
    client_id: str,
    user_id: str,
    agent_slug: Optional[str] = None,
    agent_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
):
    """
    List conversations for a user, optionally filtered by agent.
    Returns conversations ordered by last interaction time.
    """
    try:
        # Get client's Supabase credentials
        client_supabase_url, _, client_service_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
        from supabase import create_client
        client_sb = create_client(client_supabase_url, client_service_key)

        # Resolve effective user_id (handles platform admin -> client shadow user mapping)
        effective_user_id = await _resolve_effective_user_id(user_id, client_id)

        # If agent_slug provided but no agent_id, look up the agent_id
        effective_agent_id = agent_id
        if agent_slug and not agent_id:
            try:
                agent_service = MultitentAgentService()
                from uuid import UUID
                client_uuid = UUID(client_id)
                agent = await agent_service.get_agent(client_uuid, agent_slug)
                if agent and agent.id:
                    effective_agent_id = str(agent.id)
            except Exception as e:
                logger.warning(f"Failed to look up agent_id for slug {agent_slug}: {e}")

        # Try to exclude deleted conversations, but handle schemas that don't have status column
        # Build queries fresh each time since Supabase query builder mutates the object
        def build_base_query():
            q = client_sb.table("conversations").select("*").eq("user_id", effective_user_id)
            if effective_agent_id:
                q = q.eq("agent_id", effective_agent_id)
            return q

        try:
            query_with_status = build_base_query().or_("status.is.null,status.neq.deleted")
            query_with_status = query_with_status.order("updated_at", desc=True).order("created_at", desc=True)
            query_with_status = query_with_status.limit(limit).offset(offset)
            result = query_with_status.execute()
            conversations = result.data or []
        except Exception as status_err:
            # If status column doesn't exist, query without it
            if "status" in str(status_err).lower() or "column" in str(status_err).lower():
                logger.info("Conversations table missing status column, querying without status filter")
                query_no_status = build_base_query()
                query_no_status = query_no_status.order("updated_at", desc=True).order("created_at", desc=True)
                query_no_status = query_no_status.limit(limit).offset(offset)
                result = query_no_status.execute()
                conversations = result.data or []
            else:
                raise

        # Batch fetch message counts and last messages to avoid N+1 queries
        if conversations:
            conv_ids = [conv["id"] for conv in conversations]

            # Create lookup dicts for quick access
            message_counts = {}
            last_messages = {}

            # Batch query: Get all transcripts for these conversations in one query
            # We'll process them in Python to get counts and last messages
            try:
                # Get last message for each conversation using a single query
                # Order by created_at desc to get most recent first
                all_transcripts = client_sb.table("conversation_transcripts").select(
                    "conversation_id", "content", "role", "created_at"
                ).in_("conversation_id", conv_ids).order("created_at", desc=True).execute()

                # Process transcripts to get counts and last messages
                seen_convs = set()
                for transcript in (all_transcripts.data or []):
                    conv_id = transcript.get("conversation_id")
                    if not conv_id:
                        continue

                    # Count messages per conversation
                    message_counts[conv_id] = message_counts.get(conv_id, 0) + 1

                    # Store first (most recent) message for each conversation
                    if conv_id not in seen_convs:
                        content = transcript.get("content", "")
                        last_messages[conv_id] = {
                            "content": content[:100] + ("..." if len(content) > 100 else ""),
                            "role": transcript.get("role"),
                            "created_at": transcript.get("created_at")
                        }
                        seen_convs.add(conv_id)

            except Exception as e:
                logger.warning(f"Failed to batch fetch transcript data: {e}")

            # Apply counts and last messages to conversations
            for conv in conversations:
                conv["message_count"] = message_counts.get(conv["id"], 0)
                conv["last_message"] = last_messages.get(conv["id"])

        return {
            "success": True,
            "conversations": conversations,
            "total": len(conversations),
            "limit": limit,
            "offset": offset
        }

    except Exception as e:
        logger.error(f"Failed to list conversations: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/embed/conversations/{conversation_id}/messages")
async def get_embed_conversation_messages(
    conversation_id: str,
    client_id: str,
    limit: int = 200,
    offset: int = 0,
):
    """
    Get messages for a specific conversation.
    Returns messages ordered by creation time (oldest first).
    """
    try:
        # Get client's Supabase credentials
        client_supabase_url, _, client_service_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
        from supabase import create_client
        client_sb = create_client(client_supabase_url, client_service_key)

        # Get messages
        result = client_sb.table("conversation_transcripts").select("*").eq("conversation_id", conversation_id).order("created_at", desc=False).limit(limit).offset(offset).execute()

        messages = result.data or []

        return {
            "success": True,
            "messages": messages,
            "conversation_id": conversation_id,
            "total": len(messages),
            "limit": limit,
            "offset": offset
        }

    except Exception as e:
        logger.error(f"Failed to get conversation messages: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/embed/print-ready/pdf")
async def generate_print_ready_pdf(payload: PrintReadyPdfRequest):
    """
    Generate a server-side PDF containing one combined, text-only transcript
    across selected conversations that belong to the requesting user.
    """
    try:
        if not payload.conversation_ids:
            raise HTTPException(status_code=400, detail="conversation_ids is required")

        # Keep payload bounded so one request cannot generate massive PDFs.
        if len(payload.conversation_ids) > 100:
            raise HTTPException(status_code=400, detail="Maximum 100 conversations per export")

        from supabase import create_client
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
        import html

        client_supabase_url, _, client_service_key = await SupabaseCredentialManager.get_client_supabase_credentials(
            payload.client_id
        )
        client_sb = create_client(client_supabase_url, client_service_key)

        effective_user_id = await _resolve_effective_user_id(payload.user_id, payload.client_id)

        def _pick_name(record: Dict[str, Any]) -> Optional[str]:
            if not isinstance(record, dict):
                return None
            full_name = (record.get("full_name") or "").strip()
            if full_name:
                return full_name
            display_name = (record.get("display_name") or "").strip()
            if display_name:
                return display_name
            name = (record.get("name") or "").strip()
            if name:
                return name
            first_name = (record.get("first_name") or "").strip()
            last_name = (record.get("last_name") or "").strip()
            joined = f"{first_name} {last_name}".strip()
            return joined or None

        def _resolve_user_label() -> str:
            explicit = (payload.user_label or "").strip()
            if explicit:
                return explicit
            for table in ("profiles", "users"):
                try:
                    result = (
                        client_sb.table(table)
                        .select("*")
                        .eq("id", effective_user_id)
                        .limit(1)
                        .execute()
                    )
                    rows = result.data or []
                    if rows:
                        picked = _pick_name(rows[0])
                        if picked:
                            return picked
                except Exception:
                    continue
            return "User"

        # Enforce ownership: only export conversations tied to this effective user.
        try:
            conv_result = (
                client_sb.table("conversations")
                .select("id, agent_id, conversation_title, title, created_at, user_id")
                .in_("id", payload.conversation_ids)
                .eq("user_id", effective_user_id)
                .execute()
            )
        except Exception as conv_err:
            # Backward-compatible fallback for tenant schemas without `title`.
            if "title" in str(conv_err).lower() or "column" in str(conv_err).lower():
                conv_result = (
                    client_sb.table("conversations")
                    .select("id, agent_id, conversation_title, created_at, user_id")
                    .in_("id", payload.conversation_ids)
                    .eq("user_id", effective_user_id)
                    .execute()
                )
            else:
                raise
        conversation_rows = conv_result.data or []
        conversation_by_id = {str(row.get("id")): row for row in conversation_rows if row.get("id")}

        selected_ids = [cid for cid in payload.conversation_ids if cid in conversation_by_id]
        if not selected_ids:
            raise HTTPException(status_code=403, detail="No accessible conversations found for export")

        # Resolve sidekick labels by agent_id for diarized-style role labels.
        agent_ids = list(
            {
                str(conversation_by_id[cid].get("agent_id"))
                for cid in selected_ids
                if conversation_by_id[cid].get("agent_id")
            }
        )
        agent_name_by_id: Dict[str, str] = {}
        if agent_ids:
            try:
                agents_result = (
                    client_sb.table("agents")
                    .select("id, name")
                    .in_("id", agent_ids)
                    .execute()
                )
                for row in (agents_result.data or []):
                    aid = str(row.get("id") or "")
                    aname = str(row.get("name") or "").strip()
                    if aid and aname:
                        agent_name_by_id[aid] = aname
            except Exception:
                pass

        user_label = _resolve_user_label()
        default_assistant_label = (payload.assistant_label or "").strip() or "Assistant"

        def _format_timestamp(value: Any) -> str:
            if not value:
                return ""
            try:
                return str(value).replace("T", " ").replace("Z", "")
            except Exception:
                return str(value)

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=LETTER,
            leftMargin=54,
            rightMargin=54,
            topMargin=54,
            bottomMargin=54,
            title="PrintReady Export",
            author="Sidekick Forge",
        )

        styles = getSampleStyleSheet()
        title_style = styles["Title"]
        heading_style = styles["Heading2"]
        meta_style = ParagraphStyle(
            "MetaStyle",
            parent=styles["Normal"],
            fontSize=9,
            textColor=colors.HexColor("#666666"),
            leading=12,
            spaceAfter=8,
        )
        role_style = ParagraphStyle(
            "RoleStyle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=colors.HexColor("#333333"),
            leading=11,
            spaceAfter=2,
        )
        message_style = ParagraphStyle(
            "MessageStyle",
            parent=styles["Normal"],
            fontSize=11,
            leading=15,
            spaceAfter=10,
        )

        story = []
        story.append(Paragraph("Conversation Transcript", title_style))
        story.append(
            Paragraph(
                f"Generated for {html.escape(str(payload.user_id))} | "
                f"Conversations: {len(selected_ids)}",
                meta_style,
            )
        )
        story.append(Spacer(1, 12))

        for index, conversation_id in enumerate(selected_ids):
            conversation = conversation_by_id[conversation_id]
            title = (
                conversation.get("conversation_title")
                or conversation.get("title")
                or f"Conversation {index + 1}"
            )
            created_at = _format_timestamp(conversation.get("created_at"))

            story.append(Paragraph(html.escape(str(title)), heading_style))
            if created_at:
                story.append(Paragraph(f"Created: {html.escape(created_at)}", meta_style))

            msg_result = (
                client_sb.table("conversation_transcripts")
                .select("role, content, created_at")
                .eq("conversation_id", conversation_id)
                .in_("role", ["user", "assistant"])
                .order("created_at", desc=False)
                .execute()
            )
            messages = msg_result.data or []

            if not messages:
                story.append(Paragraph("No text messages found.", meta_style))
            else:
                agent_id = str(conversation.get("agent_id") or "")
                assistant_label = agent_name_by_id.get(agent_id) or default_assistant_label
                for msg in messages:
                    role = user_label if (msg.get("role") == "user") else assistant_label
                    content = str(msg.get("content") or "").strip()
                    if not content:
                        continue
                    safe_content = html.escape(content).replace("\n", "<br/>")
                    story.append(Paragraph(role, role_style))
                    story.append(Paragraph(safe_content, message_style))

            if index < len(selected_ids) - 1:
                story.append(PageBreak())

        doc.build(story)
        pdf_bytes = buffer.getvalue()
        buffer.close()

        base_filename = (payload.filename or "print-ready-export").strip() or "print-ready-export"
        safe_filename = "".join(ch for ch in base_filename if ch.isalnum() or ch in ("-", "_", " ")).strip()
        if not safe_filename:
            safe_filename = "print-ready-export"
        if not safe_filename.lower().endswith(".pdf"):
            safe_filename = f"{safe_filename}.pdf"

        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to generate print-ready PDF: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate print-ready PDF")


@router.get("/api/embed/conversations/recent")
async def get_most_recent_conversation(
    client_id: str,
    user_id: str,
    agent_slug: Optional[str] = None,
    agent_id: Optional[str] = None,
):
    """
    Get the most recent conversation for a user with a specific agent.
    Used to auto-restore the last conversation on page load.
    """
    try:
        # Get client's Supabase credentials
        client_supabase_url, _, client_service_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
        from supabase import create_client
        client_sb = create_client(client_supabase_url, client_service_key)

        # Resolve effective user_id (handles platform admin -> client shadow user mapping)
        effective_user_id = await _resolve_effective_user_id(user_id, client_id)

        # If agent_slug provided but no agent_id, look up the agent_id
        effective_agent_id = agent_id
        if agent_slug and not agent_id:
            try:
                agent_service = MultitentAgentService()
                from uuid import UUID
                client_uuid = UUID(client_id)
                agent = await agent_service.get_agent(client_uuid, agent_slug)
                if agent and agent.id:
                    effective_agent_id = str(agent.id)
            except Exception as e:
                logger.warning(f"Failed to look up agent_id for slug {agent_slug}: {e}")

        # Try to exclude deleted conversations, but handle schemas that don't have status column
        # Build queries fresh each time since Supabase query builder mutates the object
        def build_base_query():
            q = client_sb.table("conversations").select("*").eq("user_id", effective_user_id)
            if effective_agent_id:
                q = q.eq("agent_id", effective_agent_id)
            return q

        try:
            query_with_status = build_base_query().or_("status.is.null,status.neq.deleted")
            query_with_status = query_with_status.order("updated_at", desc=True).order("created_at", desc=True).limit(1)
            result = query_with_status.execute()
        except Exception as status_err:
            # If status column doesn't exist, query without it
            if "status" in str(status_err).lower() or "column" in str(status_err).lower():
                logger.info("Conversations table missing status column, querying without status filter")
                query_no_status = build_base_query()
                query_no_status = query_no_status.order("updated_at", desc=True).order("created_at", desc=True).limit(1)
                result = query_no_status.execute()
            else:
                raise

        if not result.data:
            return {"success": True, "conversation": None}

        conversation = result.data[0]

        # Get messages for this conversation (ascending order for chronological display)
        messages_result = client_sb.table("conversation_transcripts").select("*").eq("conversation_id", conversation["id"]).order("created_at", desc=False).limit(200).execute()

        return {
            "success": True,
            "conversation": conversation,
            "messages": messages_result.data or []
        }

    except Exception as e:
        logger.error(f"Failed to get recent conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Session End & Ambient Abilities Endpoints
# =============================================================================

@router.post("/api/embed/session-end")
async def notify_session_end(
    client_id: str = Form(...),
    user_id: str = Form(...),
    conversation_id: str = Form(...),
    session_id: Optional[str] = Form(None),
    message_count: int = Form(0),
    agent_slug: Optional[str] = Form(None),
):
    """
    Notify the system that a user session has ended.
    Triggers post-session ambient abilities like UserSense.
    """
    try:
        from app.services.ambient_ability_service import ambient_ability_service

        # Resolve effective user_id for platform admins
        effective_user_id = await _resolve_effective_user_id(user_id, client_id)

        logger.info(
            f"[session-end] client={client_id[:8]}..., user={effective_user_id[:8]}..., "
            f"conversation={conversation_id[:8]}..., messages={message_count}"
        )

        # Fetch transcript for context
        transcript = None
        user_overview = None
        try:
            client_supabase_url, _, client_service_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
            from supabase import create_client
            client_sb = create_client(client_supabase_url, client_service_key)

            # Get transcript
            transcript_result = client_sb.table("conversation_transcripts").select(
                "role", "content", "created_at"
            ).eq("conversation_id", conversation_id).order(
                "created_at", desc=False
            ).execute()
            transcript = transcript_result.data or []

            # Get user overview
            try:
                overview_result = client_sb.rpc(
                    "get_user_overview",
                    {"p_user_id": effective_user_id, "p_client_id": client_id}
                ).execute()
                if overview_result.data:
                    user_overview = overview_result.data.get("overview", {})
            except Exception as ov_err:
                logger.debug(f"Could not fetch user overview: {ov_err}")

        except Exception as ctx_err:
            logger.warning(f"Could not fetch context for session end: {ctx_err}")

        # Queue post-session ambient abilities
        queued_runs = await ambient_ability_service.queue_post_session_abilities(
            client_id=client_id,
            user_id=effective_user_id,
            conversation_id=conversation_id,
            session_id=session_id,
            message_count=message_count,
            agent_slug=agent_slug,
            transcript=transcript,
            user_overview=user_overview
        )

        return {
            "success": True,
            "queued_abilities": len(queued_runs),
            "run_ids": queued_runs
        }

    except Exception as e:
        logger.error(f"Failed to process session end: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/embed/notifications")
async def get_embed_notifications(
    client_id: str,
    user_id: str,
):
    """
    Get pending ambient ability notifications for a user.
    These are shown as subtle toasts in the embed (e.g., "User Understanding Expanded").
    """
    try:
        from app.services.ambient_ability_service import ambient_ability_service

        # Resolve effective user_id for platform admins
        effective_user_id = await _resolve_effective_user_id(user_id, client_id)

        notifications = await ambient_ability_service.get_user_notifications(
            user_id=effective_user_id,
            client_id=client_id
        )

        return {
            "success": True,
            "notifications": [
                {
                    "id": str(n.id),
                    "ability_slug": n.ability_slug,
                    "message": n.notification_message,
                    "completed_at": n.completed_at.isoformat() if n.completed_at else None
                }
                for n in notifications
            ]
        }

    except Exception as e:
        logger.error(f"Failed to get notifications: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/embed/notifications/{notification_id}/shown")
async def mark_notification_shown(notification_id: str):
    """Mark a notification as shown to the user."""
    try:
        from app.services.ambient_ability_service import ambient_ability_service

        success = await ambient_ability_service.mark_notification_shown(notification_id)

        return {"success": success}

    except Exception as e:
        logger.error(f"Failed to mark notification shown: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user-overview/{client_id}", response_class=HTMLResponse)
async def view_user_overview(
    request: Request,
    client_id: str,
    user_id: Optional[str] = None,
    auth_context: AuthContext = Depends(require_user_auth),
):
    """
    View your User Overview - the understanding sidekicks have built about you.
    Users can see what information has been learned through their conversations.
    """
    try:
        # Use authenticated user_id if not explicitly provided
        effective_user_id = user_id or auth_context.user_id
        if not effective_user_id:
            raise HTTPException(status_code=401, detail="Authentication required")

        # Resolve effective user_id for platform admins previewing
        resolved_user_id = await _resolve_effective_user_id(effective_user_id, client_id)

        # Get client credentials
        client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
        from supabase import create_client
        client_sb = create_client(client_url, client_key)

        # Fetch user overview
        user_overview = {}
        sidekick_insights = {}
        learning_status = "unknown"

        try:
            result = client_sb.table("user_overviews").select(
                "overview", "sidekick_insights", "learning_status", "updated_at"
            ).eq("user_id", resolved_user_id).eq("client_id", client_id).limit(1).execute()

            if result.data and len(result.data) > 0:
                data = result.data[0]
                user_overview = data.get("overview", {})
                sidekick_insights = data.get("sidekick_insights", {})
                learning_status = data.get("learning_status", "completed")
        except Exception as fetch_err:
            logger.warning(f"Could not fetch user overview: {fetch_err}")

        # Get client name for display
        try:
            from supabase import create_client as create_platform_sb
            platform_sb = create_platform_sb(settings.supabase_url, settings.supabase_service_role_key)
            client_result = platform_sb.table("clients").select("name").eq("id", client_id).limit(1).execute()
            client_name = client_result.data[0]["name"] if client_result.data else "Your Sidekicks"
        except Exception:
            client_name = "Your Sidekicks"

        return templates.TemplateResponse(
            "embed/user_overview.html",
            {
                "request": request,
                "client_id": client_id,
                "client_name": client_name,
                "user_id": resolved_user_id,
                "user_overview": user_overview,
                "sidekick_insights": sidekick_insights,
                "learning_status": learning_status,
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to render user overview page: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/embed/user-overview/{client_id}")
async def get_user_overview_api(
    client_id: str,
    user_id: Optional[str] = None,
    auth_context: AuthContext = Depends(require_user_auth),
):
    """
    API endpoint to get user overview data as JSON.
    Used by the chat widget to show a link to view overview after learning completes.
    """
    try:
        effective_user_id = user_id or auth_context.user_id
        if not effective_user_id:
            raise HTTPException(status_code=401, detail="Authentication required")

        resolved_user_id = await _resolve_effective_user_id(effective_user_id, client_id)

        client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
        from supabase import create_client
        client_sb = create_client(client_url, client_key)

        try:
            result = client_sb.table("user_overviews").select(
                "overview", "sidekick_insights", "learning_status", "conversations_analyzed", "updated_at"
            ).eq("user_id", resolved_user_id).eq("client_id", client_id).limit(1).execute()

            if result.data and len(result.data) > 0:
                data = result.data[0]
                return {
                    "success": True,
                    "exists": True,
                    "overview": data.get("overview", {}),
                    "sidekick_insights": data.get("sidekick_insights", {}),
                    "learning_status": data.get("learning_status", "unknown"),
                    "conversations_analyzed": data.get("conversations_analyzed", 0),
                    "updated_at": data.get("updated_at"),
                    "view_url": f"/user-overview/{client_id}"
                }
        except Exception as fetch_err:
            logger.warning(f"Could not fetch user overview: {fetch_err}")

        return {
            "success": True,
            "exists": False,
            "overview": {},
            "sidekick_insights": {},
            "learning_status": "not_started",
            "conversations_analyzed": 0,
            "view_url": None
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get user overview: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/embed/connection-details/{client_id}/{agent_slug}")
async def get_react_embed_connection_details(
    client_id: str,
    agent_slug: str,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
):
    """
    Get connection details for the React embed popup.
    This endpoint triggers a voice session and returns the LiveKit connection info
    along with Supabase credentials needed for citation display.

    This is designed to be called by the React embed's useConnectionDetails hook
    when NEXT_PUBLIC_CONN_DETAILS_ENDPOINT is configured to point here.
    """
    try:
        import uuid as uuid_module
        from app.integrations.livekit_client import livekit_manager

        # Generate IDs if not provided
        effective_user_id = user_id or str(uuid_module.uuid4())
        effective_conversation_id = conversation_id or str(uuid_module.uuid4())
        room_name = f"react-embed-{effective_conversation_id[:8]}-{uuid_module.uuid4().hex[:8]}"

        # Get client Supabase credentials for the frontend
        client_supabase_url, client_supabase_anon_key = await SupabaseCredentialManager.get_frontend_credentials(
            client_id,
            allow_platform_ids={"global"},
        )

        # Get full client credentials for agent context
        client_creds = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
        if not client_creds:
            raise HTTPException(status_code=400, detail="Client Supabase not configured")
        _, _, client_service_key = client_creds

        # Get agent from client database
        from supabase import create_client
        client_sb = create_client(client_supabase_url, client_service_key)
        try:
            agent_result = client_sb.table("agents").select(
                "id, name, slug, enabled, voice_settings, system_prompt, "
                "voice_chat_enabled, text_chat_enabled, video_chat_enabled"
            ).eq("slug", agent_slug).maybe_single().execute()
        except Exception:
            agent_result = client_sb.table("agents").select(
                "id, name, slug, enabled, voice_settings, system_prompt"
            ).eq("slug", agent_slug).maybe_single().execute()

        if not agent_result.data or not agent_result.data.get("enabled", True):
            raise HTTPException(status_code=404, detail="Agent not found or disabled")

        agent_data = agent_result.data

        # Get client info
        client_service = MultitenantClientService()
        client = await client_service.get_client(client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")

        # Enforce voice mode access in backend for this voice connection path.
        client_data_for_access = (
            client.model_dump() if hasattr(client, "model_dump") else (client.dict() if hasattr(client, "dict") else {})
        )
        mode_access = _compute_effective_mode_access(agent_data, client_data_for_access)
        if not mode_access["voice_chat_enabled"]:
            raise HTTPException(status_code=403, detail="Voice chat is disabled for this sidekick")

        # Initialize LiveKit
        if not livekit_manager._initialized:
            await livekit_manager.initialize()

        # Create room and get user token
        from livekit import api

        # Ensure room exists
        await trigger_api.ensure_livekit_room_exists(
            livekit_manager,
            room_name,
            agent_name=settings.livekit_agent_name,
            agent_slug=agent_slug,
            user_id=effective_user_id,
            agent_config={
                "agent_slug": agent_slug,
                "client_id": client_id,
                "conversation_id": effective_conversation_id,
            },
            enable_agent_dispatch=False,  # We'll dispatch explicitly
        )

        # Create user token
        user_token = api.AccessToken(
            api_key=livekit_manager.api_key,
            api_secret=livekit_manager.api_secret,
        ).with_identity(effective_user_id).with_name("user").with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
            )
        ).to_jwt()

        # Build agent context for dispatch
        agent_service = MultitentAgentService()
        from uuid import UUID
        client_uuid = UUID(client_id)
        api_keys = await agent_service.get_client_api_keys(client_uuid)

        # Create a minimal agent object for dispatch
        class MinimalAgent:
            def __init__(self, data):
                self.id = data.get("id")
                self.slug = data.get("slug")
                self.name = data.get("name")
                self.system_prompt = data.get("system_prompt", "")
                self.voice_settings = data.get("voice_settings") or {}
                self.tools_config = {}

        agent = MinimalAgent(agent_data)

        # Dispatch agent job
        await trigger_api.dispatch_agent_job(
            livekit_manager=livekit_manager,
            room_name=room_name,
            agent=agent,
            client=client,
            user_id=effective_user_id,
            conversation_id=effective_conversation_id,
            session_id=str(uuid_module.uuid4()),
            api_keys=api_keys,
            agent_context={
                "agent_slug": agent_slug,
                "client_id": client_id,
                "conversation_id": effective_conversation_id,
                "supabase_url": client_supabase_url,
                "supabase_anon_key": client_supabase_anon_key,
                "supabase_service_role_key": client_service_key,
            },
        )

        # Return connection details in format expected by React embed
        return {
            "serverUrl": livekit_manager.url.replace("https://", "wss://").replace("http://", "ws://"),
            "roomName": room_name,
            "participantName": "user",
            "participantToken": user_token,
            "conversationId": effective_conversation_id,
            "supabaseUrl": client_supabase_url,
            "supabaseAnonKey": client_supabase_anon_key,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get React embed connection details: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
