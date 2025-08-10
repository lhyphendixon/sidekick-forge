#!/usr/bin/env python3
"""
LiveKit Agent Worker Entrypoint
Implements proper worker registration and job handling for the Autonomite agent
"""

import asyncio
import os
import json
import logging
import inspect
import time
from typing import Optional, Dict, Any
from datetime import datetime

from livekit import agents, rtc
from livekit.agents import JobContext, JobRequest, WorkerOptions, cli, llm, voice
from livekit.plugins import deepgram, elevenlabs, openai, groq, silero, cartesia
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from api_key_loader import APIKeyLoader
from config_validator import ConfigValidator, ConfigurationError
from context import AgentContextManager
from sidekick_agent import SidekickAgent

# Enable SDK debug logging for better diagnostics
os.environ["LIVEKIT_LOG_LEVEL"] = "debug"

# Configure logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Agent logic handled via AgentSession and SidekickAgent


# --- Performance Logging Helper ---
def log_perf(event: str, room_name: str, details: Dict[str, Any]):
    log_entry = {
        "event": event,
        "room_name": room_name,
        "details": details
    }
    logger.info(f"PERF: {json.dumps(log_entry)}")


async def _store_voice_turn(
    supabase_client,
    user_id: str,
    agent_id: str,
    conversation_id: str,
    user_message: str,
    agent_response: str
):
    """
    Store a voice conversation turn as TWO rows (user + assistant) in the client's Supabase,
    matching the text-mode schema for unified analytics.
    """
    try:
        ts = datetime.utcnow().isoformat()
        # User row
        user_row = {
            "conversation_id": conversation_id,
            "agent_id": agent_id,
            "user_id": user_id,
            "role": "user",
            "content": user_message,
            "transcript": user_message,
            "created_at": ts,
            "source": "voice",
        }
        # Assistant row
        assistant_row = {
            "conversation_id": conversation_id,
            "agent_id": agent_id,
            "user_id": user_id,
            "role": "assistant",
            "content": agent_response,
            "transcript": agent_response,
            "created_at": ts,
            "source": "voice",
        }
        await supabase_client.table("conversation_transcripts").insert(user_row).execute()
        await supabase_client.table("conversation_transcripts").insert(assistant_row).execute()
        logger.info(
            f"✅ Stored voice turn as two rows for conversation_id={conversation_id}"
        )
    except Exception as e:
        logger.error(f"❌ Failed to store voice turn rows: {e}")
        logger.error(
            f"Turn data that failed: user='{(user_message or '')[:50]}...', agent='{(agent_response or '')[:50]}...'"
        )


async def agent_job_handler(ctx: JobContext):
    """
    This function is called for each new agent job.
    It creates an agent, connects to the room, and handles the session.
    """
    logger.info(f"Received job for room: {ctx.room.name}")
    
    # The context provides a connected room object
    room = ctx.room

    job_received_time = time.perf_counter()
    perf_summary = {}

    try:
        # In automatic mode, we need to fetch room info to get metadata
        metadata = {}
        
        # Log room information for debugging
        logger.info(f"Room name: {ctx.room.name}")
        logger.info(f"Room sid: {getattr(ctx.room, 'sid', 'No SID')}")
        logger.info(f"Job ID: {getattr(ctx, 'job_id', 'No Job ID')}")
        logger.info(f"Room object type: {type(ctx.room)}")
        
        # Try to get room info from LiveKit API to access metadata
        try:
            # The room object from context might have limited info
            # We need to fetch full room details from LiveKit
            from livekit import api
            
            # Get LiveKit credentials from environment
            livekit_url = os.getenv("LIVEKIT_URL")
            livekit_api_key = os.getenv("LIVEKIT_API_KEY")
            livekit_api_secret = os.getenv("LIVEKIT_API_SECRET")
            
            logger.info(f"LiveKit API credentials available: URL={bool(livekit_url)}, Key={bool(livekit_api_key)}, Secret={bool(livekit_api_secret)}")
            
            if all([livekit_url, livekit_api_key, livekit_api_secret]):
                # Create LiveKit API client
                livekit_api = api.LiveKitAPI(
                    url=livekit_url,
                    api_key=livekit_api_key,
                    api_secret=livekit_api_secret
                )
                
                logger.info(f"Fetching room details from LiveKit API for room: {ctx.room.name}")
                
                # Fetch room details
                rooms = await livekit_api.room.list_rooms(
                    api.ListRoomsRequest(names=[ctx.room.name])
                )
                
                logger.info(f"API response - found {len(rooms.rooms) if rooms.rooms else 0} rooms")
                
                if rooms.rooms:
                    room_info = rooms.rooms[0]
                    logger.info(f"Room found - SID: {room_info.sid}, Has metadata: {bool(room_info.metadata)}")
                    
                    if room_info.metadata:
                        logger.info(f"Raw room metadata length: {len(room_info.metadata)} chars")
                        logger.info(f"Room metadata from API: {room_info.metadata[:200]}..." if len(room_info.metadata) > 200 else f"Room metadata from API: {room_info.metadata}")
                        try:
                            metadata = json.loads(room_info.metadata)
                            logger.info(f"Successfully parsed room metadata with {len(metadata)} keys")
                            logger.info(f"Metadata keys: {list(metadata.keys())}")
                            
                            # Log more details about the metadata content
                            if 'voice_settings' in metadata:
                                logger.info(f"Voice settings found: {metadata['voice_settings']}")
                            if 'system_prompt' in metadata:
                                logger.info(f"System prompt length: {len(metadata['system_prompt'])} chars")
                            if 'client_id' in metadata:
                                logger.info(f"Client ID: {metadata['client_id']}")
                            
                            # Log if API keys are present
                            if 'api_keys' in metadata:
                                available_keys = [k for k, v in metadata['api_keys'].items() if v and str(v).lower() not in ['test_key', 'test', 'dummy']]
                                logger.info(f"API keys in metadata: {len(available_keys)} real keys found")
                        except json.JSONDecodeError as e:
                            # Strict JSON parsing - fail loudly with detailed error info
                            logger.error(f"Failed to parse room metadata as JSON: {e}")
                            logger.error(f"JSON error position: line {e.lineno}, column {e.colno}")
                            logger.error(f"Raw metadata (first 500 chars): {room_info.metadata[:500]}")
                            logger.error(f"Metadata type: {type(room_info.metadata)}, length: {len(room_info.metadata)}")
                            # Re-raise to ensure the error is visible
                            raise ValueError(f"Room metadata must be valid JSON. Parse error: {e}")
                    else:
                        logger.warning("Room has no metadata in API response")
                else:
                    logger.warning(f"Room {ctx.room.name} not found in API response")
            else:
                missing = []
                if not livekit_url: missing.append("LIVEKIT_URL")
                if not livekit_api_key: missing.append("LIVEKIT_API_KEY")
                if not livekit_api_secret: missing.append("LIVEKIT_API_SECRET")
                logger.error(f"LiveKit credentials missing: {', '.join(missing)}")
                
        except Exception as e:
            logger.error(f"Failed to fetch room info from API: {e}", exc_info=True)
            
        # Check if room object has metadata attribute (backward compatibility)
        if not metadata and hasattr(ctx.room, 'metadata'):
            logger.info(f"Checking room.metadata attribute: {getattr(ctx.room, 'metadata', None)}")
            room_metadata = getattr(ctx.room, 'metadata', None)
            if room_metadata:
                if isinstance(room_metadata, str):
                    try:
                        metadata = json.loads(room_metadata)
                        logger.info(f"Room metadata from attribute: {metadata}")
                    except Exception as e:
                        logger.warning(f"Failed to parse room.metadata: {e}")
                elif isinstance(room_metadata, dict):
                    metadata = room_metadata
                    logger.info(f"Room metadata dict from attribute: {metadata}")
        
        # Check job metadata first (explicit dispatch) or as fallback
        if hasattr(ctx, 'job') and ctx.job and hasattr(ctx.job, 'metadata'):
            logger.info(f"Checking job metadata: {ctx.job.metadata}")
            if isinstance(ctx.job.metadata, str) and ctx.job.metadata:
                try:
                    job_metadata = json.loads(ctx.job.metadata)
                    logger.info(f"Successfully parsed job metadata with {len(job_metadata)} keys")
                    # Prefer job metadata if it has more complete data (explicit dispatch)
                    if not metadata or len(job_metadata.get('api_keys', {})) > len(metadata.get('api_keys', {})):
                        metadata = job_metadata
                        logger.info(f"Using job metadata (explicit dispatch)")
                except json.JSONDecodeError as e:
                    # Strict JSON parsing for job metadata too
                    logger.error(f"Failed to parse job metadata as JSON: {e}")
                    logger.error(f"Raw job metadata (first 500 chars): {ctx.job.metadata[:500]}")
                    logger.error(f"Job metadata type: {type(ctx.job.metadata)}, length: {len(ctx.job.metadata)}")
                    # Don't re-raise here since we might have room metadata as fallback
                    logger.warning("Continuing with room metadata if available")
            elif isinstance(ctx.job.metadata, dict):
                job_metadata = ctx.job.metadata
                if not metadata or len(job_metadata.get('api_keys', {})) > len(metadata.get('api_keys', {})):
                    metadata = job_metadata
                    logger.info(f"Using job metadata dict (explicit dispatch)")
        
        if not metadata:
            logger.warning("No metadata found in room or job, using defaults")
            # For rooms created by frontend without metadata, use environment variables
            metadata = {
                "system_prompt": "You are a helpful AI assistant.",
                "voice_settings": {
                    "llm_provider": "openai",
                    "stt_provider": "deepgram",
                    "tts_provider": "cartesia",
                    "voice_id": "248be419-c632-4f23-adf1-5324ed7dbf1d"
                }
            }
            
        # Load API keys using the loader (follows dynamic loading policy)
        api_keys = APIKeyLoader.load_api_keys(metadata)
        metadata['api_keys'] = api_keys
        
        # CRITICAL: Validate configuration before proceeding
        start_config = time.perf_counter()
        try:
            ConfigValidator.validate_configuration(metadata, api_keys)
        except ConfigurationError as e:
            logger.error(f"❌ Configuration validation failed: {e}")
            # Fail fast - don't try to start with invalid configuration
            raise
        perf_summary['config_validation'] = time.perf_counter() - start_config
        
        # CRITICAL: Store user_id on the context for later use in event handlers
        ctx.user_id = metadata.get("user_id", "unknown")
        logger.info(f"Stored user_id on context: {ctx.user_id}")

        # --- Migrated Agent Logic ---
        try:
            # Extract configuration from metadata
            system_prompt = metadata.get("system_prompt", "You are a helpful AI assistant.")
            voice_settings = metadata.get("voice_settings", {})
            llm_provider = voice_settings.get("llm_provider", metadata.get("llm_provider", "openai"))
            
            # Configure LLM based on provider - NO FALLBACK to environment variables
            if not llm_provider:
                raise ConfigurationError("LLM provider required but not found (llm_provider)")
            if llm_provider == "groq":
                groq_key = api_keys.get("groq_api_key")
                if not groq_key:
                    raise ConfigurationError("Groq API key required but not found")
                # Get model from voice_settings or metadata, with updated default
                model = voice_settings.get("llm_model", metadata.get("model", "llama-3.3-70b-versatile"))
                # Map old model names to new ones
                if model == "llama3-70b-8192" or model == "llama-3.1-70b-versatile":
                    model = "llama-3.3-70b-versatile"
                llm_plugin = groq.LLM(
                    model=model,
                    api_key=groq_key
                )
            elif llm_provider == "cerebras":
                cerebras_key = api_keys.get("cerebras_api_key")
                if not cerebras_key:
                    raise ConfigurationError("Cerebras API key required but not found")
                # LiveKit uses openai plugin shim for Cerebras per docs
                # from livekit.plugins import openai as lk_openai  (already imported as openai)
                os.environ["CEREBRAS_API_KEY"] = cerebras_key
                # Align with Cerebras documented chat models
                # https://inference-docs.cerebras.ai/api-reference/chat-completions
                model = voice_settings.get("llm_model", metadata.get("model", "llama3.1-8b"))
                llm_plugin = openai.LLM.with_cerebras(
                    model=model
                )
            else:
                openai_key = api_keys.get("openai_api_key")
                if not openai_key:
                    raise ConfigurationError("OpenAI API key required but not found")
                llm_plugin = openai.LLM(
                    model=metadata.get("model", "gpt-4"),
                    api_key=openai_key
                )
            
            # Validate LLM initialization
            ConfigValidator.validate_provider_initialization(f"{llm_provider} LLM", llm_plugin)
            
            # Configure STT - NO FALLBACK to environment variables
            stt_provider = voice_settings.get("stt_provider")
            if not stt_provider:
                raise ConfigurationError("STT provider required but not found (stt_provider)")
            if stt_provider == "cartesia":
                cartesia_key = api_keys.get("cartesia_api_key")
                if not cartesia_key:
                    raise ConfigurationError("Cartesia API key required for STT but not found")
                stt_plugin = cartesia.STT(
                    api_key=cartesia_key
                )
            else:
                deepgram_key = api_keys.get("deepgram_api_key")
                if not deepgram_key:
                    raise ConfigurationError("Deepgram API key required for STT but not found")
                stt_plugin = deepgram.STT(
                    api_key=deepgram_key,
                    model="nova-3",
                    language="en-US",
                    endpointing_ms=500       # Lower endpointing for faster turn commit
                )
                logger.info("📊 DIAGNOSTIC: Deepgram configured with model=nova-3, language=en-US, endpointing_ms=500")
            
            # Validate STT initialization
            ConfigValidator.validate_provider_initialization(f"{stt_provider} STT", stt_plugin)
            
            # Configure TTS - NO FALLBACK to environment variables
            tts_provider = voice_settings.get("tts_provider") or voice_settings.get("provider")
            if not tts_provider:
                raise ConfigurationError("TTS provider required but not found (tts_provider or provider)")
            if tts_provider == "elevenlabs":
                elevenlabs_key = api_keys.get("elevenlabs_api_key")
                if not elevenlabs_key:
                    raise ConfigurationError("ElevenLabs API key required for TTS but not found")
                tts_plugin = elevenlabs.TTS(
                    voice_id=voice_settings.get("voice_id", "Xb7hH8MSUJpSbSDYk0k2"),
                    api_key=elevenlabs_key
                )
            else:
                cartesia_key = api_keys.get("cartesia_api_key")
                if not cartesia_key:
                    raise ConfigurationError("Cartesia API key required for TTS but not found")
                
                # Check for test keys and fail fast - NO FALLBACK
                # Allow sk-test_ prefix for development
                if cartesia_key.startswith("fixed_") and not cartesia_key.startswith("sk-test_"):
                    raise ConfigurationError(
                        f"Test key 'fixed_cartesia_key' cannot be used with Cartesia API. "
                        f"Please update the client's Cartesia API key in the admin dashboard to a valid key. "
                        f"Current TTS provider is set to '{tts_provider}' but the API key is a test key."
                    )
                
                tts_plugin = cartesia.TTS(
                    voice=voice_settings.get("voice_id", "248be419-c632-4f23-adf1-5324ed7dbf1d"),
                    api_key=cartesia_key
                )
            
            # Validate TTS initialization
            ConfigValidator.validate_provider_initialization(f"{tts_provider} TTS", tts_plugin)
            
            # Configure VAD (Voice Activity Detection)
            # VAD is crucial for turn detection - it determines when user stops speaking
            try:
                vad = silero.VAD.load(
                    min_speech_duration=0.12,  # slightly quicker start
                    min_silence_duration=0.5,  # faster end detection
                )
                logger.info("✅ VAD loaded successfully with optimized parameters")
                logger.info(f"📊 DIAGNOSTIC: VAD type: {type(vad)}")
                logger.info("📊 DIAGNOSTIC: VAD params: min_speech=0.15s, min_silence=0.8s")
            except Exception as e:
                logger.error(f"❌ Failed to load VAD: {e}", exc_info=True)
                raise
            
            # Load multilingual turn detector (model-based) per docs
            try:
                turn_detect = MultilingualModel()
                logger.info("✅ Turn detector (MultilingualModel) loaded successfully")
            except Exception as e:
                logger.error(f"❌ Failed to load turn detector: {e}", exc_info=True)
                turn_detect = None
            
            # Initialize context manager if we have Supabase credentials
            context_manager = None
            start_context_manager = time.perf_counter()
            try:
                # Check if we can connect to client's Supabase
                logger.info("🔍 Checking for Supabase credentials in metadata...")
                client_supabase_url = metadata.get("supabase_url")
                client_supabase_key = metadata.get("supabase_service_key") or metadata.get("supabase_anon_key")
                logger.info(f"📌 Supabase URL found: {bool(client_supabase_url)}")
                logger.info(f"📌 Supabase key found: {bool(client_supabase_key)}")
                
                if client_supabase_url and client_supabase_key:
                    from supabase import create_client
                    client_supabase = create_client(client_supabase_url, client_supabase_key)
                    
                    # Create context manager
                    context_manager = AgentContextManager(
                        supabase_client=client_supabase,
                        agent_config=metadata,
                        user_id=metadata.get("user_id", "unknown"),
                        client_id=metadata.get("client_id", "unknown"),
                        api_keys=api_keys
                    )
                    logger.info("✅ Context manager initialized successfully")
                else:
                    logger.warning("No client Supabase credentials found - context features disabled")
            except Exception as e:
                logger.error(f"Failed to initialize context manager: {e}")
                logger.error(f"Context initialization error details: {type(e).__name__}: {str(e)}")
                context_manager = None
                # Continue without context - don't fail the entire agent
            perf_summary['context_manager_init'] = time.perf_counter() - start_context_manager

            # Connect to the room first
            start_connect = time.perf_counter()
            logger.info("Connecting to room...")
            await ctx.connect()
            logger.info("✅ Connected to room successfully")
            perf_summary['room_connection'] = time.perf_counter() - start_connect
            
            # First, try to build initial context if context manager is available
            enhanced_prompt = system_prompt
            
            if context_manager:
                try:
                    # Build initial context (only user profile, no RAG searches)
                    logger.info("Building initial context for agent...")
                    initial_context = await context_manager.build_initial_context(
                        user_id=ctx.user_id  # Pass user_id for initial context
                    )
                    
                    logger.info("✅ Initial context built successfully")
                    
                    enhanced_prompt = initial_context["enhanced_system_prompt"]
                    
                    # Log context metadata for debugging
                    logger.info(f"Initial context metadata: {initial_context['context_metadata']}")
                    
                    # If development mode, log the full enhanced prompt
                    if os.getenv("DEVELOPMENT_MODE", "false").lower() == "true":
                        logger.info(f"Enhanced System Prompt:\n{enhanced_prompt}")
                except Exception as e:
                    logger.error(f"Failed to build initial context: {e}")
                    logger.error(f"Context error type: {type(e).__name__}")
                    logger.error(f"Context error details: {str(e)}")
                    
                    # Initial context errors are not critical - we can continue with basic prompt
                    logger.warning("Initial context enhancement failed, continuing with basic prompt")
            
            # Keep the original enhanced prompt without hardcoded greeting
            # The proactive greeting will be handled via generate_reply() after session.start()
            greeting_enhanced_prompt = enhanced_prompt
            
            # Extract the user profile from initial context if available
            user_profile = None
            if 'initial_context' in locals() and initial_context:
                # Log what's in initial_context for debugging
                logger.info(f"📊 DIAGNOSTIC: initial_context keys: {list(initial_context.keys()) if isinstance(initial_context, dict) else 'not a dict'}")
                if "raw_context_data" in initial_context:
                    logger.info(f"📊 DIAGNOSTIC: raw_context_data keys: {list(initial_context['raw_context_data'].keys())}")
                    user_profile = initial_context.get("raw_context_data", {}).get("user_profile")
                    logger.info(f"📊 DIAGNOSTIC: Extracted user_profile: {user_profile}")
            
            # Extract names for later use in proactive greeting (prefer profile)
            user_name = (
                metadata.get("user_name")
                or metadata.get("display_name")
                or (user_profile.get("full_name") if isinstance(user_profile, dict) and user_profile.get("full_name") else None)
                or (user_profile.get("name") if isinstance(user_profile, dict) and user_profile.get("name") else None)
                or "there"
            )
            agent_name = metadata.get("name", metadata.get("agent_name", "your AI assistant"))
            
            logger.info(f"📢 Agent will greet user '{user_name}' as '{agent_name}' after session starts")
            
            # Use base LLM plugin directly (RAG/context injection handled in SidekickAgent hooks)
            logger.info("✅ Using base LLM plugin directly (no wrapper)")

            # Preflight: verify LLM invocation works (base and wrapper) before starting session
            async def _llm_preflight():
                try:
                    # Base LLM
                    base_msgs = [
                        llm.ChatMessage(role="system", content=[{"type": "text", "text": "You are a concise assistant."}]),
                        llm.ChatMessage(role="user", content=[{"type": "text", "text": "Reply with the single word: pong"}])
                    ]
                    logger.info("🔬 LLM preflight (base): starting")
                    base_stream = llm_plugin.chat(base_msgs)
                    first = None
                    async for chunk in base_stream:
                        first = chunk
                        break
                    if first is None:
                        raise RuntimeError("No tokens from base LLM preflight")
                    logger.info("✅ LLM preflight (base) produced tokens")

                    # No wrapper preflight (wrapper removed)
                except Exception as e:
                    logger.critical(f"🚨 LLM preflight failed: {type(e).__name__}: {e}")
                    raise

            # Optionally run LLM preflight if explicitly enabled via env
            if os.getenv("ENABLE_LLM_PREFLIGHT", "false").lower() == "true":
                # Run but do NOT abort session if it fails; log and continue
                try:
                    await asyncio.wait_for(_llm_preflight(), timeout=6.0)
                except Exception as e:
                    logger.warning(f"LLM preflight failed (non-blocking): {type(e).__name__}: {e}")
            else:
                logger.info("Skipping LLM preflight (ENABLE_LLM_PREFLIGHT=false)")
            
            # Create and configure the voice agent session with instructions
            # In LiveKit SDK v1.0+, AgentSession IS the agent - no separate Agent object is required
            logger.info("Creating voice agent session with system prompt...")
            # Create session without duplicating plugins; Agent will own stt/llm/tts/vad
            session = voice.AgentSession(
                turn_detection="stt",
                min_endpointing_delay=0.25,
                max_endpointing_delay=3.0,
            )
            logger.info("✅ Voice agent session created (turn_detection='stt', Deepgram endpointing_ms=500)")
            
            # Minimal diagnostics; rely on AgentSession automatic conversation flow
            logger.info(f"📊 DIAGNOSTIC: Session type: {type(session)}")
            logger.info(f"📊 DIAGNOSTIC: Session attributes (sample): {[attr for attr in dir(session) if not attr.startswith('_')][:20]}")
            
            # Store references for event handlers
            ctx.context_manager = context_manager
            ctx.original_system_prompt = system_prompt
            ctx.client_supabase = client_supabase if 'client_supabase' in locals() else None
            ctx.conversation_id = metadata.get("conversation_id") or f"voice_{ctx.room.name}_{ctx.user_id}"
            ctx.last_user_message = None  # Track last user message for turn storage
            
            # Conversation flow is automatic; avoid manual event-driven flow
            logger.info("Using AgentSession automatic flow (v1.0 pattern)")
            
            # Optional: minimal participant/track logging can be reintroduced for diagnostics if needed
            
            # Start the session - LiveKit Agents v1.2.2 requires an Agent instance
            logger.info("Starting agent session (v1.2.2 requires agent param)...")
            agent = SidekickAgent(
                instructions=enhanced_prompt,
                stt=stt_plugin,
                llm=llm_plugin,
                tts=tts_plugin,
                vad=vad,
                context_manager=context_manager,
                user_id=ctx.user_id,
            )
            await session.start(agent=agent, room=ctx.room)

            # Minimal diagnostic event logs for STT/LLM/TTS flow and reply triggering
            try:
                if hasattr(session, 'on'):
                    # Plugin introspection
                    try:
                        llm_obj = getattr(session, 'llm', None)
                        logger.info(f"🔎 session.llm type: {type(llm_obj)}")
                        current_agent = getattr(session, 'current_agent', None)
                        if current_agent is not None:
                            stt_t = type(getattr(current_agent, 'stt', None))
                            llm_t = type(getattr(current_agent, 'llm', None))
                            tts_t = type(getattr(current_agent, 'tts', None))
                            vad_t = type(getattr(current_agent, 'vad', None))
                            logger.info(f"🔎 current_agent plugin types: stt={stt_t}, llm={llm_t}, tts={tts_t}, vad={vad_t}")
                    except Exception as e:
                        logger.warning(f"Plugin introspection failed: {type(e).__name__}: {e}")

                    # Helper to register and log robustly
                    def _safe_register(event_name, callback):
                        try:
                            session.on(event_name)(callback)
                            logger.info(f"🔔 Registered session event handler for '{event_name}'")
                        except Exception as err:
                            logger.debug(f"Could not register handler for '{event_name}': {type(err).__name__}: {err}")

                    # Capture latest transcript (multiple event names across SDKs)
                    @session.on("transcription_final")
                    def _capture_transcription_final(evt):
                        try:
                            text = getattr(evt, "text", None)
                            if isinstance(text, str) and text.strip():
                                setattr(session, "latest_user_text", text.strip())
                                logger.info(f"🧾 final transcript captured: '{text[:120]}' (transcription_final)")
                        except Exception:
                            pass

                    @session.on("transcription_finalized")
                    def _capture_transcription_finalized(evt):
                        try:
                            text = getattr(evt, "text", None)
                            if isinstance(text, str) and text.strip():
                                setattr(session, "latest_user_text", text.strip())
                                logger.info(f"🧾 final transcript captured: '{text[:120]}' (transcription_finalized)")
                        except Exception:
                            pass

                    # Input-level STT finals for robustness
                    if hasattr(session, "input") and hasattr(session.input, "on"):
                        try:
                            @session.input.on("transcription_final")
                            def _input_transcription_final(evt):
                                try:
                                    text = getattr(evt, "text", None)
                                    if isinstance(text, str) and text.strip():
                                        setattr(session, "latest_user_text", text.strip())
                                        logger.info(f"🧾 input final transcript: '{text[:120]}'")
                                except Exception:
                                    pass
                        except Exception:
                            logger.debug("Could not register input transcription_final handler")

                    # Track and store complete turns
                    turn_info = {"user_message": None, "agent_response": None}

                    @session.on("user_speech_committed")
                    def _log_user_turn(committed_msg):
                        try:
                            content = getattr(committed_msg, "content", None)
                            preview = None
                            captured = None
                            if isinstance(content, str) and content.strip():
                                preview = content
                                captured = content.strip()
                            elif isinstance(content, list):
                                for part in content:
                                    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                                        preview = part.get("text")
                                        captured = preview.strip()
                                        break
                            logger.info(f"📝 user_speech_committed: {str(preview)[:160] if preview else '[no content]'}")
                            if captured:
                                setattr(session, "latest_user_text", captured)
                                turn_info["user_message"] = captured
                            else:
                                # As a fallback, if we captured a final transcript earlier, bind it now
                                try:
                                    if hasattr(session, "latest_user_text") and isinstance(session.latest_user_text, str) and session.latest_user_text.strip():
                                        turn_info["user_message"] = session.latest_user_text.strip()
                                        logger.info("🧾 user_speech_committed fallback used latest_user_text")
                                except Exception:
                                    pass
                        except Exception:
                            pass

                    @session.on("agent_speech_committed")
                    def _log_agent_turn(committed_msg):
                        try:
                            content = getattr(committed_msg, "content", None)
                            preview = content if isinstance(content, str) else str(content)
                            logger.info(f"🗣️ agent_speech_committed: {str(preview)[:160] if preview else '[no content]'}")
                        except Exception:
                            pass
                        # Also extract and persist turn if we have both sides
                        try:
                            content = getattr(committed_msg, "content", None)
                            captured = None
                            if isinstance(content, str) and content.strip():
                                captured = content.strip()
                            elif isinstance(content, list):
                                for part in content:
                                    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                                        captured = part.get("text").strip()
                                        break
                            if captured:
                                turn_info["agent_response"] = captured
                                if turn_info.get("user_message") and turn_info.get("agent_response") and getattr(ctx, 'client_supabase', None):
                                    logger.info("✅ Turn complete. Storing in database...")
                                    try:
                                        asyncio.create_task(
                                            _store_voice_turn(
                                                supabase_client=ctx.client_supabase,
                                                user_id=ctx.user_id,
                                                agent_id=metadata.get("agent_id", "unknown"),
                                                conversation_id=ctx.conversation_id,
                                                user_message=turn_info["user_message"],
                                                agent_response=turn_info["agent_response"],
                                            )
                                        )
                                    except Exception as e:
                                        logger.warning(f"Failed to schedule turn storage: {type(e).__name__}: {e}")
                                    finally:
                                        turn_info["user_message"] = None
                                        turn_info["agent_response"] = None
                        except Exception:
                            pass

                    # Simple logger for miscellaneous events
                    def _generic_logger(event_label):
                        def _inner(*args, **kwargs):
                            logger.info(f"🔎 event '{event_label}' fired with args={len(args)} kwargs={len(kwargs)}")
                        return _inner

                    for ev in [
                        "user_started_speaking",
                        "user_stopped_speaking",
                        "transcription_updated",
                        "vad_event",
                    ]:
                        _safe_register(ev, _generic_logger(ev))
            except Exception as e:
                logger.warning(f"Could not register diagnostic event logs: {type(e).__name__}: {e}")
            
            # Log session state after starting
            logger.info("📊 Post-start session inspection:")
            if hasattr(session, '_started'):
                logger.info(f"   Session started: {getattr(session, '_started', True)}")
            if hasattr(session, '_room'):
                logger.info(f"   Session has room: {hasattr(session, '_room')}")
            
            # Log successful start
            logger.info(f"✅ Agent session started successfully in room: {ctx.room.name}")
            logger.info(f"   - LLM: {llm_provider}")
            logger.info(f"   - STT: {stt_provider}")
            logger.info(f"   - TTS: {tts_provider}")
            
            # Store session reference for the job lifecycle
            ctx.session = session
            
            # Optional proactive greeting after successful start
            if os.getenv("DISABLE_PROACTIVE_GREETING", "false").lower() != "true":
                greeting_message = f"Hi {user_name}, how can I help you?"
                async def _try_greet():
                    try:
                        if hasattr(session, "say") and callable(getattr(session, "say")):
                            await asyncio.wait_for(session.say(greeting_message), timeout=5.0)
                            logger.info("✅ Proactive greeting delivered via session.say()")
                        elif hasattr(session, "generate_reply") and callable(getattr(session, "generate_reply")):
                            instr = f"Say exactly this greeting: '{greeting_message}'"
                            await asyncio.wait_for(session.generate_reply(instructions=instr), timeout=5.0)
                            logger.info("✅ Proactive greeting delivered via session.generate_reply()")
                        else:
                            logger.info("⚠️ No greeting method available on session; skipping proactive greeting")
                    except Exception as e:
                        logger.warning(f"Proactive greeting failed or timed out: {type(e).__name__}: {e}")
                asyncio.create_task(_try_greet())
            else:
                logger.info("Proactive greeting disabled via DISABLE_PROACTIVE_GREETING=true")

            # Conversation flow is automatic in AgentSession; no forced replies
            logger.info("✅ Agent ready - automatic STT→LLM→TTS flow engaged (v1.0 pattern)")
            
            # Remove deprecated commented greeting block (cleaned)
            
            logger.info("✅ Agent ready to converse")
            logger.info("📊 DIAGNOSTIC: Agent is now in automatic STT→LLM→TTS mode")
            
            logger.info("Agent ready to converse")
            
            # Check current participants in the room
            try:
                # Try to get participants - different SDK versions have different attributes
                participants = []
                if hasattr(ctx.room, 'remote_participants'):
                    participants = list(ctx.room.remote_participants.values())
                    logger.info(f"Current remote participants: {len(participants)}")
                    for p in participants:
                        is_publisher = p.is_publisher if hasattr(p, 'is_publisher') else 'N/A'
                        logger.info(f"  - {p.identity} (SID: {p.sid}, Publisher: {is_publisher})")
                elif hasattr(ctx.room, 'participants'):
                    participants = list(ctx.room.participants.values())
                    logger.info(f"Current participants: {len(participants)}")
                    for p in participants:
                        logger.info(f"  - {p.identity} (SID: {p.sid})")
                else:
                    logger.info("Room object doesn't have participants attribute")
                
                # Log room state
                logger.info(f"Room state - Connected: {ctx.room.isconnected() if hasattr(ctx.room, 'isconnected') else 'N/A'}")
                
                # If participants are already in the room, log their tracks
                for participant in participants:
                    if hasattr(participant, 'tracks'):
                        logger.info(f"Tracks for {participant.identity}: {len(participant.tracks)} tracks")
                        for track_id, track_pub in participant.tracks.items():
                            logger.info(f"  - Track {track_id}: {track_pub.kind if hasattr(track_pub, 'kind') else 'unknown'}")
                
            except Exception as e:
                logger.warning(f"Could not check participants: {e}")
                # Continue - the session will handle participant events
            
            # Keep the agent alive by waiting for the session to complete
            # The session will handle room events and manage its own lifecycle
            logger.info("Agent session is running. Waiting for completion...")
            
            # The session runs until the room closes or all participants disconnect
            # We need to keep this coroutine alive while the session is active
            try:
                # Wait for the room to disconnect or session to end
                while ctx.room.isconnected() if hasattr(ctx.room, 'isconnected') else True:
                    await asyncio.sleep(1)  # Check every second
                    
                    # Log periodic heartbeat to show agent is still active
                    if int(time.time()) % 30 == 0:  # Every 30 seconds
                        participants_count = len(ctx.room.remote_participants) if hasattr(ctx.room, 'remote_participants') else 0
                        logger.info(f"💓 Agent heartbeat - Room: {ctx.room.name}, Participants: {participants_count}")
                
                logger.info("Room disconnected or session ended. Agent shutting down.")
                
            except Exception as e:
                logger.error(f"Error in session wait loop: {e}")
                # Continue to cleanup
            
            # Clean shutdown
            if hasattr(ctx, 'session') and ctx.session:
                logger.info("Cleaning up agent session...")
                # Session cleanup happens automatically
            
        except ConfigurationError as e:
            # Configuration errors are fatal - don't try to recover
            logger.error(f"❌ Configuration error: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ Agent session failed: {e}", exc_info=True)
            # Log the type of error for better debugging
            logger.error(f"Error type: {type(e).__name__}")
            logger.error(f"Error details: {str(e)}")
            raise  # Re-raise to let LiveKit handle the error
        # --- End Migrated Logic ---

    except ConfigurationError as e:
        # Configuration errors should fail fast and clearly
        logger.critical(f"❌ FATAL: {e}")
        raise
    except Exception as e:
        logger.error(f"❌ Error in agent job: {e}", exc_info=True)
        raise  # Re-raise to let LiveKit handle the error
    finally:
        # Log summary for the entire job handler
        perf_summary['total_job_duration'] = time.perf_counter() - job_received_time
        log_perf("agent_job_handler_summary", ctx.room.name, perf_summary)


async def request_filter(job_request: JobRequest) -> None:
    """
    Filter function to accept/reject jobs
    
    EXPLICIT DISPATCH MODE: Only accept jobs that match our agent name.
    This ensures proper agent-to-room assignment in multi-tenant environments.
    """
    logger.info(f"Received job request: {job_request.job.id}")
    logger.info(f"Job agent name: {job_request.agent_name}")
    
    # Get our configured agent name from environment or use default
    our_agent_name = os.getenv("AGENT_NAME", "sidekick-agent")
    
    # EXPLICIT DISPATCH: Only accept jobs for our specific agent
    if job_request.agent_name == our_agent_name:
        logger.info(f"✅ Accepting job for our agent: {job_request.agent_name}")
        await job_request.accept()
    else:
        logger.info(f"❌ Rejecting job - agent mismatch. Expected: {our_agent_name}, Got: {job_request.agent_name}")
        await job_request.reject()


if __name__ == "__main__":
    try:
        # Import sys to pass command line args
        import sys
        
        # Validate credentials at startup
        if os.getenv("LIVEKIT_API_KEY") == "APIUtuiQ47BQBsk":
            logger.critical("🚨 INVALID LIVEKIT CREDENTIALS DETECTED 🚨")
            logger.critical("The LiveKit API key 'APIUtuiQ47BQBsk' is expired and no longer valid.")
            logger.critical("Please update the credentials using:")
            logger.critical("  python /root/sidekick-forge/scripts/update_livekit_credentials.py <url> <api_key> <api_secret>")
            sys.exit(1)
        
        # Default to 'start' command if none provided
        if len(sys.argv) == 1:
            sys.argv.append('start')
        
        # The LiveKit CLI manages its own event loop
        url = os.getenv("LIVEKIT_URL", "wss://litebridge-hw6srhvi.livekit.cloud")
        api_key = os.getenv("LIVEKIT_API_KEY")
        api_secret = os.getenv("LIVEKIT_API_SECRET")

        if not all([url, api_key, api_secret]):
            logger.critical("LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET must be set.")
            sys.exit(1)

        # Get agent name from environment or use default
        agent_name = os.getenv("AGENT_NAME", "sidekick-agent")
        
        logger.info(f"Starting agent worker...")
        logger.info(f"LiveKit URL: {url}")
        logger.info(f"Agent mode: EXPLICIT DISPATCH")
        logger.info(f"Agent name: {agent_name}")

        # Configure worker options with agent_name for EXPLICIT DISPATCH
        worker_options = WorkerOptions(
            entrypoint_fnc=agent_job_handler,
            request_fnc=request_filter,
            agent_name=agent_name,  # EXPLICIT: Only receive jobs for this agent name
        )

        # Let the CLI handle the event loop
        cli.run_app(worker_options)
    except KeyboardInterrupt:
        logger.info("Shutting down worker.")