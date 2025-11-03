from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from typing import Optional
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

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/embed/{client_id}/{agent_slug}", response_class=HTMLResponse)
async def embed_sidekick(
    request: Request,
    client_id: str,
    agent_slug: str,
    theme: Optional[str] = "dark",
):
    import os
    dev_mode = os.getenv("DEVELOPMENT_MODE", "false").lower() == "true"
    return templates.TemplateResponse(
        "embed/sidekick.html",
        {
            "request": request,
            "client_id": client_id,
            "agent_slug": agent_slug,
            "theme": theme,
            "supabase_url": settings.supabase_url,
            "supabase_anon_key": settings.supabase_anon_key,
            "development_mode": dev_mode,
        },
    )


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

            # Use the handle_text_trigger that works with multitenant architecture
            result = await trigger_api.handle_text_trigger(
                trigger_request,
                agent,
                platform_client,
                None,  # tools_service is optional and handled internally
            )

            response_text = (
                result.get("response")
                or result.get("agent_response")
                or "(No response from the model.)"
            )
            tools_payload = result.get("tools") or {}
            citations = result.get("citations") or []

            for token in response_text.split():
                delta = token + " "
                yield f"data: {json.dumps({'delta': delta})}\n\n"
                await asyncio.sleep(0)

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
