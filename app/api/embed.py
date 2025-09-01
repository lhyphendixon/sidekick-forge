from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from typing import Optional
import asyncio, json, logging
import os
import sys

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
            # TEMPORARY: Skip auth for RAG testing
            user = {'id': 'test-user-123', 'email': 'test@example.com'}
            logger.info("[embed-stream] TEMP: bypassing auth for RAG testing")
            
            # Commented out auth for testing RAG functionality
            # try:
            #     auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
            #     if not auth_header or not auth_header.startswith("Bearer "):
            #         logger.warning("[embed-stream] missing bearer token")
            #         yield f"data: {json.dumps({'error': 'unauthorized'})}\n\n"; return
            #     token = auth_header[7:]
            #     from app.integrations.supabase_client import supabase_manager
            #     if not supabase_manager._initialized:
            #         await supabase_manager.initialize()
            #     user = await supabase_manager.verify_jwt_token(token)
            #     if not user:
            #         logger.warning("[embed-stream] invalid bearer token")
            #         yield f"data: {json.dumps({'error': 'unauthorized'})}\n\n"; return
            # except Exception as e:
            #     logger.error(f"embed_text_stream auth error: {e}")
            #     yield f"data: {json.dumps({'error': 'unauthorized'})}\n\n"; return

            from app.shared.llm_factory import get_llm
            from livekit.agents import llm as lk_llm
            agent_svc = get_agent_service()
            client_svc = get_client_service()

            agent = await agent_svc.get_agent(client_id, agent_slug)
            if not agent or not agent.enabled:
                yield f"data: {json.dumps({'error':'Agent not available'})}\n\n"; return
            client = await client_svc.get_client(client_id)
            
            # Initialize simple RAG search for this agent  
            rag_context = None
            try:
                # Get client's Supabase configuration
                client_supabase_url = None
                client_service_key = None
                
                if client.settings and hasattr(client.settings, 'supabase'):
                    client_supabase_url = str(client.settings.supabase.url) if client.settings.supabase.url else None
                    client_service_key = client.settings.supabase.service_role_key
                
                if client_supabase_url and client_service_key:
                    # Create client Supabase connection
                    from supabase import create_client
                    client_supabase = create_client(client_supabase_url, client_service_key)
                    
                    # Verify RAG functions exist
                    try:
                        # Test if match_documents function exists
                        test_result = client_supabase.rpc("match_documents", {
                            "p_query_embedding": [0.0] * 1024,  # Test embedding (1024 dims to match schema)
                            "p_agent_slug": agent_slug,
                            "p_match_threshold": 0.5,
                            "p_match_count": 1
                        }).execute()
                        logger.info(f"[embed-stream] match_documents function verified for {agent_slug}")
                        
                        # Simple RAG search function
                        async def simple_rag_search(query_text: str) -> str:
                            try:
                                logger.info(f"[embed-stream] performing RAG search for: {query_text[:50]}...")
                                
                                # Use AIProcessor for dynamic embedding generation
                                from app.services.ai_processor import AIProcessor
                                ai_processor = AIProcessor()
                                
                                # Convert client settings to expected format
                                client_settings_dict = {
                                    'embedding': {
                                        'provider': getattr(client.settings.embedding, 'provider', 'openai') if client.settings.embedding else 'openai',
                                        'document_model': getattr(client.settings.embedding, 'document_model', 'text-embedding-3-small') if client.settings.embedding else 'text-embedding-3-small',
                                        'conversation_model': getattr(client.settings.embedding, 'conversation_model', 'text-embedding-3-small') if client.settings.embedding else 'text-embedding-3-small'
                                    },
                                    'api_keys': client.settings.api_keys.dict() if client.settings and client.settings.api_keys else {}
                                }
                                
                                logger.info(f"[embed-stream] using embedding provider: {client_settings_dict['embedding']['provider']}")
                                
                                # Generate embedding using client's configured provider
                                query_embedding = await ai_processor.generate_embeddings(
                                    text=query_text,
                                    context='conversation',
                                    client_settings=client_settings_dict
                                )
                                
                                if not query_embedding:
                                    logger.error("[embed-stream] failed to generate embedding")
                                    return ""
                                    
                                logger.info(f"[embed-stream] generated embedding (dim: {len(query_embedding)})")
                                
                                # Search documents using RPC
                                search_result = client_supabase.rpc("match_documents", {
                                    "p_query_embedding": query_embedding,
                                    "p_agent_slug": agent_slug,
                                    "p_match_threshold": 0.5,
                                    "p_match_count": 5
                                }).execute()
                                
                                if search_result.data:
                                    logger.info(f"[embed-stream] RAG found {len(search_result.data)} relevant documents")
                                    
                                    # Log first document similarity for debugging
                                    if search_result.data:
                                        first_similarity = search_result.data[0].get('similarity', 0)
                                        logger.info(f"[embed-stream] Top result similarity: {first_similarity:.3f}")
                                    
                                    # Build context from results and capture citations
                                    context_parts = []
                                    citations = []
                                    for i, doc in enumerate(search_result.data[:3], 1):  # Top 3 results
                                        title = doc.get('title', 'Document')
                                        content = doc.get('content', '').strip()
                                        source_url = doc.get('source_url', doc.get('url', ''))
                                        similarity = doc.get('similarity', 0.0)
                                        chunk_index = doc.get('chunk_index', i - 1)
                                        
                                        context_parts.append(f"[Source {i}: {title}]\n{content}")
                                        
                                        # Extract URL from content if possible
                                        extracted_url = ""
                                        if 'url:' in content:
                                            try:
                                                lines = content.split('\n')
                                                for line in lines[:5]:  # Check first few lines
                                                    if 'url:' in line:
                                                        extracted_url = line.split('url:')[1].strip().strip('"')
                                                        break
                                            except:
                                                pass
                                        
                                        # Build citation object with proper structure for frontend
                                        citation = {
                                            'id': str(doc.get('id', i)),
                                            'title': title,
                                            'source_url': extracted_url,
                                            'content': content,  # Full content for frontend
                                            'content_preview': content[:200] + "..." if len(content) > 200 else content,
                                            'similarity': float(similarity) if similarity and similarity > 0 else 0.0,
                                            'chunk_index': i - 1,  # Use position in results as chunk index
                                            'page_number': None  # Not available in this RPC
                                        }
                                        citations.append(citation)
                                    
                                    rag_context = "\n\n".join(context_parts)
                                    logger.info(f"[embed-stream] built RAG context ({len(rag_context)} chars) with {len(citations)} citations")
                                    
                                    # Store citations for later use
                                    return rag_context, citations
                                else:
                                    logger.info("[embed-stream] no RAG results found")
                                    return "", []
                                    
                            except Exception as e:
                                logger.error(f"[embed-stream] RAG search failed: {e}")
                                return "", []
                        
                        # Store the search function for later use
                        rag_search_fn = simple_rag_search
                        logger.info(f"[embed-stream] RAG search function ready for {agent_slug}")
                        
                    except Exception as e:
                        logger.warning(f"[embed-stream] match_documents function not available: {e}")
                        rag_search_fn = None
                        
                else:
                    logger.warning(f"[embed-stream] missing client Supabase config, skipping RAG")
                    rag_search_fn = None
                    
            except Exception as e:
                logger.warning(f"[embed-stream] RAG setup failed: {e}, proceeding without RAG")
                rag_search_fn = None

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
                
            # Build RAG context if RAG search is available
            system_prompt_to_use = agent.system_prompt
            rag_citations = []
            if 'rag_search_fn' in locals() and rag_search_fn:
                try:
                    logger.info(f"[embed-stream] building RAG context for message: {message[:50]}...")
                    rag_context, rag_citations = await rag_search_fn(message)
                    if rag_context:
                        # Enhance the system prompt with RAG context
                        enhanced_prompt = f"""{agent.system_prompt}

---

# Agent Context

## Relevant Knowledge Base

{rag_context}

---

Remember to use this context appropriately in your responses while maintaining your core personality and instructions."""
                        system_prompt_to_use = enhanced_prompt
                        logger.info(f"[embed-stream] RAG context integrated into system prompt with {len(rag_citations)} citations")
                    else:
                        logger.info(f"[embed-stream] no RAG context generated")
                except Exception as e:
                    logger.error(f"[embed-stream] RAG context building failed: {e}")
                    # Continue with original system prompt
                    rag_citations = []
            
            chat_ctx = lk_llm.ChatContext()
            if system_prompt_to_use:
                chat_ctx.add_message(role="system", content=system_prompt_to_use)
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
            # Include citations in the final response if available
            final_payload = {'done': True, 'full_text': full_text}
            
            # Include citations from RAG search results
            if rag_citations:
                final_payload['citations'] = rag_citations
                logger.info(f"[embed-stream] including {len(rag_citations)} citations in response")
            
            yield f"data: {json.dumps(final_payload)}\n\n"
        except Exception as e:
            logger.error(f"embed_text_stream error: {e}")
            yield f"data: {json.dumps({'error': 'stream failed'})}\n\n"
        finally:
            # Cleanup is handled automatically
            pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        }
    )


