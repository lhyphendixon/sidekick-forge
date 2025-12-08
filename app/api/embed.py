from fastapi import APIRouter, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
from typing import Optional, Dict, Any
import asyncio
import json
import logging
import uuid

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
from pydantic import BaseModel, EmailStr

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


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

    return templates.TemplateResponse(
        "embed/sidekick.html",
        {
            "request": request,
            "client_id": client_id,
            "agent_slug": agent_slug,
            "theme": theme,
            "supabase_url": settings.supabase_url,
            "supabase_anon_key": settings.supabase_anon_key,
            "client_supabase_url": client_supabase_url,
            "client_supabase_anon_key": client_supabase_anon_key,
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


@router.post("/api/embed/text/stream")
async def embed_text_stream(
    request: Request,
    client_id: str = Form(...),
    agent_slug: str = Form(...),
    message: str = Form(...),
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

            # TEMP: text embed requests use a deterministic user/session
            # Use a valid UUID so downstream queries against Supabase succeed.
            user_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "sidekick-forge/embed-user"))

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

            api_keys = await agent_service.get_client_api_keys(client_uuid)

            conversation_id = str(uuid.uuid4())
            session_id = str(uuid.uuid4())

            trigger_request = trigger_api.TriggerAgentRequest(
                agent_slug=agent_slug,
                client_id=client_id,
                mode=trigger_api.TriggerMode.TEXT,
                message=message,
                user_id=user_id,
                session_id=session_id,
                conversation_id=conversation_id,
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
                    conversation_id=conversation_id,
                    user_id=user_id,
                    session_id=session_id,
                    mode="text",
                    request_context=None,
                    client_conversation_id=conversation_id,
                )
                
                # Add tools and user message to context
                tools_payload = await trigger_api._get_agent_tools(tools_service, platform_client.id, agent.id)
                if tools_payload:
                    agent_context["tools"] = tools_payload
                trigger_api._apply_tool_prompt_sections(agent_context, tools_payload)
                agent_context["user_message"] = message

                # Create room and dispatch
                text_room_name = f"text-{conversation_id}-{uuid.uuid4().hex[:8]}"
                await trigger_api.ensure_livekit_room_exists(
                    backend_livekit,
                    text_room_name,
                    agent_name=settings.livekit_agent_name,
                    agent_slug=agent.slug,
                    user_id=user_id,
                    agent_config=agent_context,
                    enable_agent_dispatch=True,
                )

                await trigger_api.dispatch_agent_job(
                    livekit_manager=backend_livekit,
                    room_name=text_room_name,
                    agent=agent,
                    client=platform_client,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    session_id=session_id,
                    tools=agent_context.get("tools"),
                    tools_config=agent_context.get("tools_config"),
                    api_keys=agent_context.get("api_keys"),
                    agent_context=agent_context,
                )

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
                        final_payload = {
                            "done": True,
                            "full_text": update.get("full_text", ""),
                            "conversation_id": conversation_id,
                            "citations": update.get("citations", []),
                            "tools": {"results": update.get("tool_results", [])},
                        }
                        yield f"data: {json.dumps(final_payload)}\n\n"
                        return

            except Exception as livekit_err:
                logger.error(f"[embed-stream] livekit streaming failed: {livekit_err}; falling back to non-streaming")
                try:
                    final_result = await trigger_api.handle_text_trigger_via_livekit(
                        trigger_request,
                        agent,
                        platform_client,
                        tools_service,
                    )
                except Exception:
                    try:
                        final_result = await trigger_api.handle_text_trigger(
                            trigger_request,
                            agent,
                            platform_client,
                            tools_service,
                        )
                    except Exception:
                        yield f"data: {json.dumps({'error': 'stream failed'})}\n\n"
                        return
                        
                if not final_result:
                    yield f"data: {json.dumps({'error': 'stream failed'})}\n\n"
                    return

                response_text = (
                    final_result.get("response")
                    or final_result.get("agent_response")
                    or "(No response from the model.)"
                )
                tools_payload = final_result.get("tools") or {}
                citations = final_result.get("citations") or []

                final_payload = {
                    "done": True,
                    "full_text": response_text,
                    "conversation_id": conversation_id,
                    "citations": citations,
                    "tools": tools_payload,
                }
                yield f"data: {json.dumps(final_payload)}\n\n"

        except Exception as exc:
            logger.error("embed_text_stream error: %s", exc, exc_info=True)
            yield f"data: {json.dumps({'error': 'stream failed'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
