from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from typing import Optional
import asyncio, json, logging

from app.core.dependencies import get_agent_service, get_client_service
from app.middleware.auth import get_current_auth
from app.models.user import AuthContext
from app.config import settings
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/embed/{client_id}/{agent_slug}", response_class=HTMLResponse)
async def embed_sidekick(request: Request, client_id: str, agent_slug: str, theme: Optional[str] = "dark"):
    return templates.TemplateResponse(
        "embed/sidekick.html",
        {
            "request": request,
            "client_id": client_id,
            "agent_slug": agent_slug,
            "theme": theme,
            "supabase_url": settings.supabase_url,
            "supabase_anon_key": settings.supabase_anon_key,
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
            # Open stream early
            try:
                yield ":stream-open\n\n"
            except Exception:
                pass
            logger.info(f"[embed-stream] start client_id={client_id} agent={agent_slug}")
            # Manual auth for embed: verify Supabase JWT from Authorization header
            try:
                auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
                if not auth_header or not auth_header.startswith("Bearer "):
                    logger.warning("[embed-stream] missing bearer token")
                    yield f"data: {json.dumps({'error': 'unauthorized'})}\n\n"; return
                token = auth_header[7:]
                from app.integrations.supabase_client import supabase_manager
                if not supabase_manager._initialized:
                    await supabase_manager.initialize()
                user = await supabase_manager.verify_jwt_token(token)
                if not user:
                    logger.warning("[embed-stream] invalid bearer token")
                    yield f"data: {json.dumps({'error': 'unauthorized'})}\n\n"; return
            except Exception as e:
                logger.error(f"embed_text_stream auth error: {e}")
                yield f"data: {json.dumps({'error': 'unauthorized'})}\n\n"; return

            from app.shared.llm_factory import get_llm
            from livekit.agents import llm as lk_llm
            agent_svc = get_agent_service()
            client_svc = get_client_service()

            agent = await agent_svc.get_agent(client_id, agent_slug)
            if not agent or not agent.enabled:
                yield f"data: {json.dumps({'error':'Agent not available'})}\n\n"; return
            client = await client_svc.get_client(client_id)

            vs = getattr(agent, 'voice_settings', None)
            llm_provider = getattr(vs, 'llm_provider', None) or 'openai'
            llm_model = getattr(vs, 'llm_model', None) or 'gpt-4'
            api_keys = (getattr(client, 'settings', None) and getattr(client.settings, 'api_keys', None)) or {}
            api_keys = api_keys.dict() if hasattr(api_keys, 'dict') else {}

            # Validate provider API key exists
            provider_key_map = {
                'openai': 'openai_api_key',
                'groq': 'groq_api_key',
                'cerebras': 'cerebras_api_key',
                'deepinfra': 'deepinfra_api_key'
            }
            required_key_name = provider_key_map.get((llm_provider or '').lower())
            if required_key_name:
                key_val = api_keys.get(required_key_name)
                if not key_val or key_val in ['test', 'test_key', '<needs-actual-key>']:
                    logger.warning(f"[embed-stream] missing api key for provider={llm_provider}")
                    yield f"data: {json.dumps({'error': 'missing_api_key', 'provider': llm_provider})}\n\n"; return

            try:
                model = get_llm(llm_provider, llm_model, api_keys)
            except Exception as e:
                logger.error(f"[embed-stream] get_llm failed: {e}")
                yield f"data: {json.dumps({'error': 'stream failed'})}\n\n"; return
            chat_ctx = lk_llm.ChatContext()
            if getattr(agent, 'system_prompt', None):
                chat_ctx.add_message(role="system", content=agent.system_prompt)
            chat_ctx.add_message(role="user", content=message)

            full_text = ""
            stream = None
            try:
                stream = model.chat(chat_ctx=chat_ctx)
            except Exception as e:
                logger.error(f"[embed-stream] model.chat init failed: {e}")
                yield f"data: {json.dumps({'error': 'stream failed'})}\n\n"; return
            try:
                # Bound the streaming time to prevent indefinite hang
                async with asyncio.timeout(5):
                    async for chunk in stream:
                        delta = None
                        try:
                            if hasattr(chunk, 'choices') and chunk.choices:
                                part = getattr(chunk.choices[0], 'delta', None) or getattr(chunk.choices[0], 'message', None)
                                if part and hasattr(part, 'content') and part.content: delta = part.content
                            if not delta and hasattr(chunk, 'content') and chunk.content: delta = chunk.content
                            if not delta and hasattr(chunk, 'text') and getattr(chunk, 'text'): delta = getattr(chunk, 'text')
                            if not delta and isinstance(chunk, str): delta = chunk if chunk.strip() else None
                            if not delta:
                                # Regex fallback like admin preview to extract content='...'
                                import re
                                s = str(chunk)
                                matches = re.findall(r"content=\'([^\']*)\'", s)
                                if not matches:
                                    matches = re.findall(r'content=\"([^\"]*)\"', s)
                                if matches:
                                    delta = ''.join(matches)
                        except Exception:
                            delta = None
                        if delta:
                            full_text += delta
                            yield f"data: {json.dumps({'delta': delta})}\n\n"
                            await asyncio.sleep(0)
            except asyncio.TimeoutError:
                logger.warning("[embed-stream] timeout waiting for model chunks")
                if not full_text:
                    full_text = "(No response from the model.)"
            yield f"data: {json.dumps({'done': True, 'full_text': full_text})}\n\n"
        except Exception as e:
            logger.error(f"embed_text_stream error: {e}")
            yield f"data: {json.dumps({'error': 'stream failed'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        }
    )


