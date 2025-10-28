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
import types
from typing import Optional, Dict, Any, List
from datetime import datetime

from livekit import agents, rtc
from livekit.agents import JobContext, JobRequest, WorkerOptions, cli, llm, voice
from livekit.plugins import deepgram, elevenlabs, openai, groq, silero, cartesia
from api_key_loader import APIKeyLoader
from config_validator import ConfigValidator, ConfigurationError
from context import AgentContextManager
from sidekick_agent import SidekickAgent
from tool_registry import ToolRegistry

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
            # "source": "voice",  # Column doesn't exist yet
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
            # "source": "voice",  # Column doesn't exist yet
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

    # Attach detailed LiveKit room diagnostics for track lifecycle events
    try:
        def _on_local_track_published(publication, track):
            try:
                logger.info(
                    "📡 local_track_published kind=%s track_sid=%s muted=%s",
                    getattr(publication, "kind", None),
                    getattr(publication, "track_sid", None) or getattr(publication, "sid", None),
                    getattr(publication, "muted", None),
                )
            except Exception as log_err:
                logger.debug(f"Failed to log local_track_published: {log_err}")

        def _on_track_published(publication, participant):
            try:
                logger.info(
                    "📡 track_published kind=%s track_sid=%s participant=%s muted=%s",
                    getattr(publication, "kind", None),
                    getattr(publication, "track_sid", None) or getattr(publication, "sid", None),
                    getattr(participant, "identity", None),
                    getattr(publication, "muted", None),
                )
            except Exception as log_err:
                logger.debug(f"Failed to log track_published: {log_err}")

        def _on_track_subscribed(track, publication, participant):
            try:
                logger.info(
                    "📡 track_subscribed kind=%s track_sid=%s participant=%s muted=%s",
                    getattr(track, "kind", None),
                    getattr(publication, "track_sid", None) or getattr(publication, "sid", None),
                    getattr(participant, "identity", None),
                    getattr(publication, "muted", None),
                )
            except Exception as log_err:
                logger.debug(f"Failed to log track_subscribed: {log_err}")

        def _on_track_unsubscribed(track, publication, participant):
            try:
                logger.info(
                    "📡 track_unsubscribed kind=%s track_sid=%s participant=%s",
                    getattr(track, "kind", None),
                    getattr(publication, "track_sid", None) or getattr(publication, "sid", None),
                    getattr(participant, "identity", None),
                )
            except Exception as log_err:
                logger.debug(f"Failed to log track_unsubscribed: {log_err}")

        def _on_track_muted(participant, publication):
            try:
                logger.info(
                    "📡 track_muted participant=%s track_sid=%s",
                    getattr(participant, "identity", None),
                    getattr(publication, "track_sid", None) or getattr(publication, "sid", None),
                )
            except Exception as log_err:
                logger.debug(f"Failed to log track_muted: {log_err}")

        def _on_track_unmuted(participant, publication):
            try:
                logger.info(
                    "📡 track_unmuted participant=%s track_sid=%s",
                    getattr(participant, "identity", None),
                    getattr(publication, "track_sid", None) or getattr(publication, "sid", None),
                )
            except Exception as log_err:
                logger.debug(f"Failed to log track_unmuted: {log_err}")

        def _on_track_subscription_failed(participant, track_sid, error):
            try:
                logger.warning(
                    "⚠️ track_subscription_failed participant=%s track_sid=%s error=%s",
                    getattr(participant, "identity", None),
                    track_sid,
                    error,
                )
            except Exception as log_err:
                logger.debug(f"Failed to log track_subscription_failed: {log_err}")

        room.on("local_track_published", _on_local_track_published)
        room.on("track_published", _on_track_published)
        room.on("track_subscribed", _on_track_subscribed)
        room.on("track_unsubscribed", _on_track_unsubscribed)
        room.on("track_muted", _on_track_muted)
        room.on("track_unmuted", _on_track_unmuted)
        room.on("track_subscription_failed", _on_track_subscription_failed)
        logger.info("🔍 LiveKit room diagnostics handlers attached")
    except Exception as attach_err:
        logger.warning(f"Unable to attach room diagnostics handlers: {attach_err}")

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
                livekit_api = None
                try:
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
                finally:
                    if livekit_api:
                        try:
                            await livekit_api.aclose()
                        except Exception as close_error:
                            logger.warning(f"Failed to close LiveKitAPI session: {close_error}")
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
        
        # Merge job metadata (explicit dispatch) with room metadata, preferring job keys
        if hasattr(ctx, 'job') and ctx.job and hasattr(ctx.job, 'metadata'):
            logger.info(f"Checking job metadata: {ctx.job.metadata}")
            try:
                job_metadata = None
                if isinstance(ctx.job.metadata, str) and ctx.job.metadata:
                    job_metadata = json.loads(ctx.job.metadata)
                    logger.info(f"Successfully parsed job metadata with {len(job_metadata)} keys")
                elif isinstance(ctx.job.metadata, dict):
                    job_metadata = ctx.job.metadata
                
                if isinstance(job_metadata, dict):
                    base = metadata if isinstance(metadata, dict) else {}
                    # Merge: room/previous metadata first, then override with job metadata
                    merged = {**base, **job_metadata}
                    metadata = merged
                    logger.info("Merged room metadata with job metadata (job has priority)")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse job metadata as JSON: {e}")
                logger.error(f"Raw job metadata (first 500 chars): {ctx.job.metadata[:500]}")
                logger.error(f"Job metadata type: {type(ctx.job.metadata)}, length: {len(ctx.job.metadata)}")
                logger.warning("Continuing with room metadata if available")
        
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
            elif llm_provider == "deepinfra":
                deepinfra_key = api_keys.get("deepinfra_api_key")
                if not deepinfra_key:
                    raise ConfigurationError("DeepInfra API key required but not found")
                model = voice_settings.get("llm_model", metadata.get("model", "meta-llama/Llama-3.1-8B-Instruct"))
                # Use OpenAI-compatible base_url for DeepInfra
                llm_plugin = openai.LLM(
                    model=model,
                    api_key=deepinfra_key,
                    base_url="https://api.deepinfra.com/v1/openai"
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
                    endpointing_ms=1000
                )
                logger.info("📊 DIAGNOSTIC: Deepgram configured with model=nova-3, language=en-US, endpointing_ms=1000")
            
            # Validate STT initialization
            ConfigValidator.validate_provider_initialization(f"{stt_provider} STT", stt_plugin)
            
            # Configure TTS - NO FALLBACK to environment variables
            tts_provider = voice_settings.get("tts_provider") or voice_settings.get("provider")
            if not tts_provider:
                raise ConfigurationError("TTS provider required but not found (tts_provider or provider)")
            provider_config = voice_settings.get("provider_config") or {}
            if not isinstance(provider_config, dict):
                provider_config = {}
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
                
                # Enforce no-fallback: require explicit TTS model for Cartesia
                # Accept both voice_settings.model and voice_settings.tts_model, and metadata.tts_model for compatibility
                tts_model = (
                    voice_settings.get("model")
                    or voice_settings.get("tts_model")
                    or metadata.get("tts_model")
                )
                if not tts_model:
                    raise ConfigurationError(
                        "Cartesia TTS requires an explicit 'model'. Set voice_settings.model or metadata.tts_model."
                    )
                cartesia_voice_id = (
                    voice_settings.get("voice_id")
                    or voice_settings.get("cartesia_voice_id")
                    or provider_config.get("cartesia_voice_id")
                )
                if not cartesia_voice_id:
                    raise ConfigurationError(
                        "Cartesia TTS requires voice_settings.voice_id (or cartesia_voice_id) to be set"
                    )
                cartesia_voice_id = str(cartesia_voice_id).strip()
                if len(cartesia_voice_id) < 8:
                    raise ConfigurationError(
                        "Cartesia voice_id appears invalid (too short); provide the full Cartesia UUID"
                    )
                # Normalize voice_id back into settings so downstream logs show the actual value
                voice_settings["voice_id"] = cartesia_voice_id
                tts_plugin = cartesia.TTS(
                    voice=cartesia_voice_id,
                    model=tts_model,
                    api_key=cartesia_key
                )
                logger.info(
                    f"✅ Cartesia TTS configured with voice_id={cartesia_voice_id} model={tts_model}"
                )

            # Validate TTS initialization
            ConfigValidator.validate_provider_initialization(f"{tts_provider} TTS", tts_plugin)
            
            # Configure VAD (Voice Activity Detection)
            # VAD is crucial for turn detection - it determines when user stops speaking
            try:
                vad = silero.VAD.load(
                    min_speech_duration=0.15,
                    min_silence_duration=0.5,
                )
                logger.info("✅ VAD loaded successfully with optimized parameters")
                logger.info(f"📊 DIAGNOSTIC: VAD type: {type(vad)}")
                logger.info("📊 DIAGNOSTIC: VAD params: min_speech=0.15s, min_silence=0.5s")
            except Exception as e:
                logger.error(f"❌ Failed to load VAD: {e}", exc_info=True)
                raise
            
            # Remove multilingual turn detector to avoid heavy HF model load/crash
            turn_detect = None
            
            # Initialize context manager if we have Supabase credentials
            context_manager = None
            client_id = metadata.get("client_id")  # Define at outer scope for use throughout
            start_context_manager = time.perf_counter()
            try:
                # Check if we can connect to client's Supabase
                logger.info("🔍 Checking for Supabase credentials in metadata...")
                client_supabase_url = metadata.get("supabase_url")
                # Get service role key (required - no fallback)
                client_supabase_key = metadata.get("supabase_service_key") or metadata.get("supabase_service_role_key")
                
                # If no service key, try to load from environment
                if not client_supabase_key:
                    if client_id:
                        env_key = f"CLIENT_{client_id.replace('-', '_').upper()}_SUPABASE_SERVICE_KEY"
                        client_supabase_key = os.getenv(env_key)
                        if client_supabase_key:
                            logger.info(f"📌 Using service key from environment: {env_key}")
                
                # NO FALLBACK POLICY: Fail fast if no service role key
                if not client_supabase_key:
                    logger.error("❌ No service role key found for client Supabase - cannot proceed")
                    raise ValueError("Client Supabase service role key is required - no fallback to anon key allowed")
                
                logger.info(f"📌 Supabase URL found: {bool(client_supabase_url)}")
                logger.info(f"📌 Supabase key found: {bool(client_supabase_key)}")
                
                if client_supabase_url and client_supabase_key:
                    from supabase import create_client
                    try:
                        client_supabase = create_client(client_supabase_url, client_supabase_key)
                        logger.info("✅ Client Supabase connection created")
                    except Exception as e:
                        logger.error(f"Failed to create Supabase client: {e}")
                        client_supabase = None
                    
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
            logger.info("Connecting to room with close_on_disconnect=False for stability...")
            # Connect; per SDK version, input options are applied on session.start below
            await ctx.connect(auto_subscribe="audio_only")  # Only subscribe to audio tracks for voice agents
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
            # Create SidekickAgent directly - eliminate nested AgentSession + voice.Agent architecture
            # This fixes duplicate event handling by using single layer architecture
            
            # Get agent_id and config from metadata (prefer UUID)
            agent_id = metadata.get("agent_id") or metadata.get("agent_slug") or "default"
            agent_slug = metadata.get("agent_slug") or metadata.get("agent_id") or "default"
            show_citations = metadata.get("show_citations", True)
            dataset_ids = metadata.get("dataset_ids", [])
            
            agent = SidekickAgent(
                instructions=enhanced_prompt,
                stt=stt_plugin,
                llm=llm_plugin,
                tts=tts_plugin,
                vad=vad,
                context_manager=context_manager,
                user_id=ctx.user_id,
                client_id=client_id,
                agent_config={'id': agent_id, 'agent_slug': agent_slug, 'show_citations': show_citations, 'dataset_ids': dataset_ids},
            )
            logger.info("✅ Voice agent created with single-layer architecture")
            
            # Store references for event handlers in agent
            agent._room = ctx.room  # Store room reference for agent use
            # Enforce conversation_id from metadata (no-fallback policy)
            conv_id = metadata.get("conversation_id")
            if not conv_id:
                logger.critical("❌ Missing conversation_id in metadata - cannot proceed (no-fallback policy)")
                raise ConfigurationError("conversation_id is required in room/job metadata")
            agent._conversation_id = conv_id
            logger.info(f"📌 Using conversation_id: {agent._conversation_id}")
            # Pass the Supabase client that was created earlier
            agent._supabase_client = client_supabase if 'client_supabase' in locals() else None
            # Ensure transcript storage uses UUID when available
            agent._agent_id = metadata.get("agent_id") or metadata.get("agent_slug")
            agent._user_id = metadata.get("user_id") or ctx.user_id

            base_tool_context: Dict[str, Any] = {
                "conversation_id": agent._conversation_id,
                "user_id": agent._user_id,
                "agent_slug": metadata.get("agent_slug") or metadata.get("agent_id"),
                "client_id": metadata.get("client_id") or client_id,
                "session_id": metadata.get("session_id")
                or metadata.get("voice_session_id")
                or metadata.get("room_session_id"),
            }

            registry: Optional[ToolRegistry] = None
            tracked_tool_slugs: List[str] = []

            def push_runtime_context(updates: Dict[str, Any]) -> None:
                if not registry or not tracked_tool_slugs or not isinstance(updates, dict):
                    return
                sanitized = {k: v for k, v in updates.items() if v is not None}
                if not sanitized:
                    return
                for slug in tracked_tool_slugs:
                    registry.update_runtime_context(slug, sanitized)
                try:
                    logger.info(
                        "🧰 Runtime context updated for %s with keys=%s",
                        tracked_tool_slugs,
                        list(sanitized.keys()),
                    )
                except Exception:
                    pass
            
            # Log what's available for transcript storage
            logger.info(f"📝 Transcript storage setup:")
            logger.info(f"   - Has Supabase: {agent._supabase_client is not None}")
            logger.info(f"   - Conversation ID: {agent._conversation_id}")
            logger.info(f"   - Agent ID: {agent._agent_id}")
            logger.info(f"   - User ID: {agent._user_id}")
            
            logger.info(f"📊 DIAGNOSTIC: Agent type: {type(agent)}")
            logger.info(f"📊 DIAGNOSTIC: Agent inherits from: {type(agent).__bases__}")
            
            # Set up transcript storage with room reference
            agent.setup_transcript_storage(ctx.room)
            
            # Create AgentSession with the plugins - proper LiveKit v1.x pattern
            logger.info("Creating AgentSession with plugins...")
            session = voice.AgentSession(
                vad=vad,
                stt=stt_plugin,
                llm=llm_plugin,
                tts=tts_plugin,
                turn_detection="stt"
            )

            # Register tools (Abilities) if provided in metadata
            try:
                tool_defs = metadata.get("tools") or []
                if tool_defs:
                    logger.info(f"🧰 Preparing to register tools: count={len(tool_defs)}")
                    try:
                        slugs = [t.get("slug") or t.get("name") or t.get("id") for t in tool_defs]
                        logger.info(f"🧰 Tool defs slugs: {slugs}")
                    except Exception:
                        pass
                    registry = ToolRegistry(
                        tools_config=metadata.get("tools_config") or {},
                        api_keys=metadata.get("api_keys") or {},
                    )
                    tools = registry.build(tool_defs)
                    if tools:
                        tracked_tool_slugs = []
                        for tool_def in tool_defs:
                            if tool_def.get("type") != "n8n":
                                continue
                            slug_candidate = (
                                tool_def.get("slug")
                                or tool_def.get("name")
                                or tool_def.get("id")
                            )
                            if slug_candidate:
                                tracked_tool_slugs.append(slug_candidate)
                        if tracked_tool_slugs:
                            logger.info(
                                "🧰 Tracking runtime context for n8n tools: %s",
                                tracked_tool_slugs,
                            )
                            push_runtime_context(base_tool_context)
                        combined_tools = list(agent.tools) + list(tools)
                        update_fn = getattr(agent, "update_tools", None)
                        if callable(update_fn):
                            result = update_fn(combined_tools)
                            if inspect.isawaitable(result):
                                await result
                            logger.info(
                                "🧰 Registered %s tools via agent.update_tools",
                                len(tools),
                            )
                        else:
                            fallback = list(getattr(agent, "_injected_tools", []) or [])
                            fallback.extend(tools)
                            setattr(agent, "_injected_tools", fallback)
                            logger.info(
                                "🧰 Agent update_tools missing; stored %s tools on agent fallback",
                                len(tools)
                            )
                    else:
                        logger.warning("🧰 No tools were built from provided definitions")
            except Exception as e:
                logger.warning(f"Tool registration failed: {type(e).__name__}: {e}")
            
            # Log and capture STT transcripts; commit turn on finals
            commit_delay = float(os.getenv("VOICE_TURN_COMMIT_DELAY", "0.8"))
            commit_timeout = float(os.getenv("VOICE_TRANSCRIPT_TIMEOUT", "0.5"))

            if not hasattr(session, "_pending_commit_task"):
                session._pending_commit_task = None
            if not hasattr(session, "_current_turn_text"):
                session._current_turn_text = ""
            if not hasattr(agent, "_current_turn_text"):
                agent._current_turn_text = ""
            if not hasattr(session, "_last_committed_text"):
                session._last_committed_text = ""
            if not hasattr(agent, "_last_committed_text"):
                agent._last_committed_text = ""

            def _merge_transcript_text(existing: str, incoming: str) -> str:
                """
                Combine partial ASR chunks into a single utterance while avoiding obvious duplication.

                The Deepgram stream we receive sometimes emits disjoint final chunks (e.g., "Hey" then
                "is coming"), so we append when the new chunk does not already appear in the aggregate.
                If the recognizer sends the entire sentence, we replace the aggregate to keep spacing right.
                """

                if not incoming:
                    return existing

                incoming = incoming.strip()
                if not incoming:
                    return existing

                if not existing:
                    return incoming

                # If the incoming chunk already contains the existing text, prefer the richer version
                if incoming.startswith(existing) or existing in incoming:
                    return incoming

                # If the existing text already includes this chunk (or a trimmed variant), keep existing
                if incoming in existing:
                    return existing

                # Otherwise append with a space separator
                return f"{existing.rstrip()} {incoming}".strip()

            @session.on("user_input_transcribed")
            def on_user_input_transcribed(ev):
                try:
                    txt = getattr(ev, 'transcript', '') or ''
                    is_final = bool(getattr(ev, 'is_final', False))
                    logger.info(f"📝 STT transcript: '{txt[:200]}' final={is_final}")
                    if txt:
                        merged = _merge_transcript_text(
                            getattr(session, "_current_turn_text", ""),
                            txt,
                        )
                        session._current_turn_text = merged
                        agent._current_turn_text = merged
                        session.latest_user_text = merged
                        agent.latest_user_text = merged
                        if is_final:
                            try:
                                fut = session.interrupt(force=True)
                                if fut:
                                    if asyncio.iscoroutine(fut) or isinstance(fut, asyncio.Future):
                                        async def _await_interrupt(task):
                                            try:
                                                await task
                                                logger.info("⛔ Assistant speech interrupted due to user transcript final chunk")
                                            except Exception as interrupt_err:
                                                logger.debug("Interrupt future raised %s: %s", type(interrupt_err).__name__, interrupt_err)

                                        asyncio.create_task(_await_interrupt(fut))
                            except Exception as interrupt_exc:
                                logger.debug("Interrupt call failed: %s: %s", type(interrupt_exc).__name__, interrupt_exc)
                        if is_final:
                            push_runtime_context({"latest_user_text": merged})
                except Exception as e:
                    logger.error(f"user_input_transcribed handler failed: {e}")

            # Minimal capture of latest user text for RAG, without watchdog or transcript writes
            @session.on("user_speech_committed")
            def on_user_speech(msg: llm.ChatMessage):
                try:
                    user_text = None
                    if hasattr(msg, 'content'):
                        if isinstance(msg.content, str):
                            user_text = msg.content
                        elif isinstance(msg.content, list):
                            for part in msg.content:
                                if isinstance(part, dict) and part.get("type") == "text":
                                    user_text = part.get("text")
                                    break
                    if user_text:
                        session.latest_user_text = user_text
                        agent.latest_user_text = user_text
                        logger.info(f"💬 Captured user speech: {user_text[:100]}...")
                        push_runtime_context({"latest_user_text": user_text})
                except Exception as e:
                    logger.error(f"Failed to capture user speech: {e}")

            # Deterministic finalize: commit assistant transcript on agent_speech_committed
            @session.on("agent_speech_committed")
            def on_agent_speech(msg: llm.ChatMessage):
                try:
                    agent_text = None
                    if hasattr(msg, 'content'):
                        if isinstance(msg.content, str):
                            agent_text = msg.content
                        elif isinstance(msg.content, list):
                            for part in msg.content:
                                if isinstance(part, dict) and part.get("type") == "text":
                                    agent_text = part.get("text")
                                    break
                    if not agent_text:
                        return
                    # Deduplicate by last commit
                    if getattr(agent, "_last_assistant_commit", "") == agent_text:
                        return
                    try:
                        agent._last_assistant_commit = agent_text
                    except Exception:
                        pass
                    if hasattr(agent, 'store_transcript'):
                        logger.info("📝 Committing assistant transcript on agent_speech_committed")
                        asyncio.create_task(agent.store_transcript('assistant', agent_text))
                except Exception as e:
                    logger.error(f"Failed to commit assistant transcript: {e}")

            # Store session reference on agent for access in on_user_turn_completed
            agent._agent_session = session
            
            # Start the session with the agent and room
            logger.info("Starting AgentSession with agent and room...")
            # Import room_io for input options
            from livekit.agents.voice import room_io
            
            # Configure input options to prevent early disconnect
            input_options = room_io.RoomInputOptions(
                close_on_disconnect=False  # Keep agent running even if user disconnects briefly
            )

            output_options = room_io.RoomOutputOptions(
                audio_enabled=True,
                transcription_enabled=True,
                audio_track_name="agent_audio",
            )

            logger.info("Priming RoomIO audio output before starting AgentSession...")
            try:
                session_room_io = room_io.RoomIO(
                    agent_session=session,
                    room=ctx.room,
                    input_options=input_options,
                    output_options=output_options,
                )
                await session_room_io.start()
                logger.info(
                    "✅ RoomIO primed | audio_attached=%s transcription_attached=%s",
                    bool(session.output.audio),
                    bool(session.output.transcription),
                )
            except Exception as room_io_err:
                logger.error(f"❌ Failed to initialize RoomIO before session.start: {room_io_err}")
                raise

            await session.start(
                room=ctx.room,
                agent=agent,
            )

            try:
                audio_output = session.output.audio
                if audio_output is not None:
                    try:
                        await asyncio.wait_for(audio_output.subscribed, timeout=5.0)
                        logger.info("🔊 audio output subscription confirmed")
                    except Exception as sub_err:
                        logger.warning(f"⚠️ audio output subscription check failed: {type(sub_err).__name__}: {sub_err}")
                else:
                    logger.warning("⚠️ session.output.audio is None after start")
            except Exception as sub_check_err:
                logger.warning(f"⚠️ audio output subscription instrumentation failed: {type(sub_check_err).__name__}: {sub_check_err}")

            try:
                # Log local and remote participants right after start
                def describe_pub(pub):
                    return {"track_sid": getattr(pub, 'track_sid', None) or getattr(pub, 'sid', None),
                            "kind": getattr(pub, 'kind', None),
                            "source": getattr(pub, 'source', None),
                            "name": getattr(pub, 'track_name', None),
                            "muted": getattr(pub, 'muted', None)}

                local_pubs = []
                try:
                    tracks = getattr(ctx.room.local_participant, 'tracks', None)
                    if tracks:
                        for pub in tracks.values():
                            local_pubs.append(describe_pub(pub))
                except Exception:
                    pass
                remote_summary = []
                try:
                    participants = getattr(ctx.room, 'remote_participants', {})
                    for identity, participant in participants.items():
                        pubs = []
                        try:
                            track_map = getattr(participant, 'tracks', None)
                            if track_map:
                                for pub in track_map.values():
                                    pubs.append(describe_pub(pub))
                        except Exception:
                            pass
                        remote_summary.append({
                            'identity': identity,
                            'kind': getattr(participant, 'kind', None),
                            'pubs': pubs
                        })
                except Exception:
                    pass
                logger.info(f"🔎 Local participant publications after start: {local_pubs}")
                logger.info(f"🔎 Remote participants after start: {remote_summary}")
            except Exception as diag_err:
                logger.warning(f"Participant publication diagnostics failed: {type(diag_err).__name__}: {diag_err}")

            # Instrument audio sink to confirm frames are forwarded to LiveKit
            try:
                audio_output = session.output.audio
                if not audio_output and hasattr(session, "_room_io"):
                    audio_output = getattr(session._room_io, "audio_output", None)
                    if audio_output:
                        session.output.audio = audio_output
                        logger.info("🔧 Attached RoomIO audio output onto session.output.audio")

                room_io_audio = getattr(session._room_io, "audio_output", None) if hasattr(session, "_room_io") else None
                logger.info(
                    "🔍 RoomIO diagnostics post-start | has_output=%s has_room_io=%s room_io_audio=%s",
                    bool(session.output.audio),
                    hasattr(session, "_room_io"),
                    bool(room_io_audio),
                )

                if audio_output:
                    chain_labels = []
                    link = audio_output
                    while link is not None and link not in chain_labels:
                        chain_labels.append(type(link).__name__)
                        link = getattr(link, "next_in_chain", None)
                    logger.info("🔍 RoomIO audio chain: %s", " -> ".join(chain_labels) or "(empty)")

                    current = audio_output
                    visited = set()
                    while current and current not in visited:
                        visited.add(current)
                        try:
                            original_capture = current.capture_frame
                        except AttributeError:
                            original_capture = None

                        if original_capture and not getattr(current, "_diag_capture_wrapped", False):
                            async def capture_with_log(self, frame, *args, **kwargs):
                                try:
                                    import audioop

                                    rms = audioop.rms(frame.data, 2) if hasattr(frame, "data") else None
                                    logger.info(
                                        "🎧 capture_frame label=%s sr=%s samples=%s duration_ms=%.2f rms=%s",
                                        getattr(self, "label", None),
                                        getattr(frame, "sample_rate", None),
                                        getattr(frame, "samples_per_channel", None),
                                        (getattr(frame, "duration", None) or 0) * 1000.0,
                                        rms,
                                    )
                                except Exception:
                                    logger.info(
                                        "🎧 capture_frame label=%s (frame stats unavailable)",
                                        getattr(self, "label", None),
                                    )
                                return await original_capture(frame, *args, **kwargs)

                            current.capture_frame = types.MethodType(capture_with_log, current)
                            current._diag_capture_wrapped = True

                        try:
                            original_flush = current.flush
                        except AttributeError:
                            original_flush = None

                        if original_flush and not getattr(current, "_diag_flush_wrapped", False):
                            def flush_with_log(self, *args, **kwargs):
                                logger.info(
                                    "🎧 audio_output.flush label=%s", getattr(self, "label", None)
                                )
                                return original_flush(*args, **kwargs)

                            current.flush = types.MethodType(flush_with_log, current)
                            current._diag_flush_wrapped = True

                        current = getattr(current, "next_in_chain", None)
            except Exception as audio_patch_err:
                logger.warning(f"Audio output diagnostics attachment failed: {audio_patch_err}")

            # Additional diagnostics: speaking and error events
            try:
                # Keep minimal speaking diagnostics; no transcript writes here
                @session.on("agent_started_speaking")
                def _on_agent_started():
                    logger.info("🔈 agent_started_speaking")

                @session.on("agent_stopped_speaking")
                def _on_agent_stopped():
                    logger.info("🔇 agent_stopped_speaking")

                @session.on("error")
                def _on_session_error(err: Exception):
                    logger.error(f"🛑 session error: {type(err).__name__}: {err}")

                # Mirror events for SDKs that emit 'assistant_*'
                @session.on("assistant_started_speaking")
                def _on_assistant_started():
                    logger.info("🔈 assistant_started_speaking")

                @session.on("assistant_stopped_speaking")
                def _on_assistant_stopped():
                    logger.info("🔇 assistant_stopped_speaking")

                @session.on("metrics_collected")
                def _on_metrics(metrics):
                    try:
                        logger.info(f"📈 metrics_collected: {metrics}")
                    except Exception:
                        logger.info("📈 metrics_collected (unserializable)")

                @session.on("function_tools_executed")
                def _on_tools_executed(ev):
                    try:
                        raw_calls = list(getattr(ev, "function_calls", []) or [])
                        calls_summary = []
                        tool_results: List[Dict[str, Any]] = []
                        for call in raw_calls:
                            try:
                                logger.debug("🛠️ function_call payload: %s", getattr(call, "__dict__", {}))
                            except Exception:
                                pass
                            name = getattr(call, "name", None)
                            calls_summary.append(name)
                            entry: Dict[str, Any] = {
                                "slug": name,
                                "type": getattr(call, "tool", None) or getattr(call, "type", None),
                            }
                            success = getattr(call, "success", None)
                            if isinstance(success, bool):
                                entry["success"] = success
                            else:
                                status = str(getattr(call, "status", "")).lower()
                                entry["success"] = status not in {"error", "failed"}

                            output = getattr(call, "output", None)
                            if output is None:
                                # LiveKit may expose the tool return value under `response` or `result`
                                output = getattr(call, "response", None)
                            if output is None:
                                output = getattr(call, "result", None)
                            if output is None:
                                payload = getattr(call, "tool_output", None)
                                if payload is not None:
                                    output = payload
                            if output is not None and not isinstance(output, (str, int, float, bool)):
                                try:
                                    entry["output"] = json.dumps(output, ensure_ascii=False)
                                except Exception:
                                    entry["output"] = str(output)
                            else:
                                entry["output"] = output

                            error_msg = getattr(call, "error", None)
                            if error_msg:
                                entry["error"] = error_msg

                            tool_results.append(entry)

                        logger.info("🛠️ function_tools_executed: %s", calls_summary)

                        if tool_results and hasattr(agent, "_latest_tool_results"):
                            try:
                                agent._latest_tool_results = tool_results  # type: ignore[attr-defined]
                            except Exception as assign_err:
                                logger.debug("Failed to attach tool results to agent: %s", assign_err)
                    except Exception:
                        logger.info("🛠️ function_tools_executed (unable to serialize event)")

                @session.on("speech_created")
                def _on_speech_created(ev):
                    try:
                        sh = getattr(ev, "speech_handle", None)
                        handle_id = getattr(sh, "id", None)
                        logger.info(
                            "🔊 speech_created source=%s user_initiated=%s handle=%s",
                            getattr(ev, "source", None),
                            getattr(ev, "user_initiated", None),
                            handle_id,
                        )
                    except Exception:
                        logger.info("🔊 speech_created (unable to serialize event)")

                @session.on("user_started_speaking")
                def _on_user_started():
                    logger.info("🎤 user_started_speaking")
                    pending = getattr(session, "_pending_commit_task", None)
                    if pending and not pending.done():
                        pending.cancel()

                    # Attempt to barge-in by interrupting any active assistant speech
                    try:
                        interrupt_future = session.interrupt(force=True)

                        if interrupt_future:
                            async def _log_interrupt_result(fut: asyncio.Future):
                                try:
                                    await fut
                                    logger.info("⛔ Assistant speech interrupted due to user start")
                                except Exception as interrupt_err:
                                    logger.debug(
                                        "Interrupt future raised %s: %s",
                                        type(interrupt_err).__name__,
                                        interrupt_err,
                                    )

                            asyncio.create_task(_log_interrupt_result(interrupt_future))
                    except RuntimeError:
                        logger.debug("Interrupt called while session inactive")
                    except Exception as interrupt_call_err:
                        logger.warning(
                            "Failed to interrupt assistant speech: %s: %s",
                            type(interrupt_call_err).__name__,
                            interrupt_call_err,
                        )

                @session.on("user_stopped_speaking")
                def _on_user_stopped():
                    logger.info("🛑 user_stopped_speaking")
                    pending = getattr(session, "_pending_commit_task", None)
                    if pending and not pending.done():
                        pending.cancel()

                    final_text = getattr(session, "_current_turn_text", "").strip()
                    if not final_text:
                        logger.info("🛑 user_stopped_speaking but no buffered transcript; skipping commit schedule")
                        return

                    async def _delayed_commit():
                        try:
                            await asyncio.sleep(commit_delay)
                            buffered = getattr(session, "_current_turn_text", "").strip()
                            if not buffered:
                                logger.debug("Delayed commit skipped (buffer cleared before execution)")
                                return
                            session.latest_user_text = buffered
                            agent.latest_user_text = buffered
                            session._last_committed_text = buffered
                            agent._last_committed_text = buffered
                            push_runtime_context({"latest_user_text": buffered})
                            session.commit_user_turn(transcript_timeout=commit_timeout)
                            session._current_turn_text = ""
                            agent._current_turn_text = ""
                        except asyncio.CancelledError:
                            logger.debug("Delayed commit cancelled before execution")
                        except Exception as ce:
                            logger.warning(f"commit_user_turn failed: {type(ce).__name__}: {ce}")
                        finally:
                            session._pending_commit_task = None

                    session._pending_commit_task = asyncio.create_task(_delayed_commit())

                # Primary commit: persist assistant transcript when conversation history updates
                @session.on("conversation_item_added")
                def _on_conversation_item_added(item):
                    try:
                        # Unwrap event container if needed
                        raw = item.item if hasattr(item, "item") and item.item is not None else item
                        role = getattr(raw, "role", None)
                        # Observability
                        try:
                            tc = getattr(raw, 'text_content', None)
                            has_tc = isinstance(tc, str) or (tc is not None)
                            logger.info(f"🧩 conversation_item_added: role={role} has_text_content={has_tc} has_text={hasattr(raw, 'text')} content_type={type(getattr(raw, 'content', None))}")
                        except Exception:
                            pass
                        # Only handle assistant items
                        if role != "assistant":
                            return
                        # Extract text robustly (text_content is a property, not callable)
                        text_value = getattr(raw, "text_content", None)
                        if callable(text_value):
                            try:
                                text_value = text_value()
                            except Exception as call_exc:
                                logger.debug(f"text_content callable failed: {call_exc}")
                                text_value = None
                        if not text_value and hasattr(raw, "text") and isinstance(getattr(raw, "text"), str) and raw.text:
                            text_value = raw.text
                        if not text_value and hasattr(raw, "content"):
                            content = raw.content
                            if isinstance(content, str) and content:
                                text_value = content
                            elif isinstance(content, list):
                                collected: list[str] = []
                                for part in content:
                                    if isinstance(part, str):
                                        collected.append(part)
                                    elif isinstance(part, dict):
                                        ptype = part.get("type")
                                        if ptype in ("text", "output_text", "response_text", "output_text_delta") and part.get("text"):
                                            collected.append(part.get("text"))
                                if collected:
                                    text_value = "".join(collected)
                        if not text_value:
                            logger.info("ℹ️ conversation_item_added: assistant item had no extractable text")
                            return
                        try:
                            logger.info(f"🧪 Extracted assistant text (len={len(text_value)}): {text_value[:120]}...")
                        except Exception:
                            pass
                        # Dedup by last commit content
                        if getattr(agent, "_last_assistant_commit", "") == text_value:
                            return
                        try:
                            agent._last_assistant_commit = text_value
                        except Exception:
                            pass
                        if hasattr(agent, "store_transcript"):
                            logger.info("📝 Committing assistant transcript via conversation_item_added")
                            asyncio.create_task(agent.store_transcript("assistant", text_value))
                    except Exception as e:
                        logger.error(f"conversation_item_added handler failed: {e}")
            except Exception as e:
                logger.warning(f"Could not add diagnostic handlers: {e}")

            # Minimal diagnostic event logs for agent plugin configuration
            try:
                # Plugin introspection for the agent
                try:
                    stt_t = type(getattr(agent, 'stt', None))
                    llm_t = type(getattr(agent, 'llm', None))
                    tts_t = type(getattr(agent, 'tts', None))
                    vad_t = type(getattr(agent, 'vad', None))
                    logger.info(f"🔎 Agent plugin types: stt={stt_t}, llm={llm_t}, tts={tts_t}, vad={vad_t}")
                except Exception as e:
                    logger.warning(f"Plugin introspection failed: {type(e).__name__}: {e}")

                # Event handling is now fully delegated to the SidekickAgent class
                # This eliminates duplicate event processing that was causing double responses
                logger.info("🔔 Event handling delegated to SidekickAgent - no duplicate session handlers")
            except Exception as e:
                logger.warning(f"Could not complete diagnostic inspection: {type(e).__name__}: {e}")
            
            # Log agent state after starting
            logger.info("📊 Post-start agent inspection:")
            if hasattr(agent, '_started'):
                logger.info(f"   Agent started: {getattr(agent, '_started', True)}")
            if hasattr(agent, '_room'):
                logger.info(f"   Agent has room: {hasattr(agent, '_room')}")
            
            # Log successful start
            logger.info(f"✅ Agent session started successfully in room: {ctx.room.name}")
            logger.info(f"   - LLM: {llm_provider}")
            logger.info(f"   - STT: {stt_provider}")
            logger.info(f"   - TTS: {tts_provider}")
            
            # Note: ctx.agent is read-only property - agent is already managed by the framework
            
            # Proactive greeting: trigger only when a user is present per LiveKit specs
            if os.getenv("ENABLE_PROACTIVE_GREETING", "false").lower() == "true":
                greeting_message = f"Hi {user_name}, how can I help you?"
                greeted_flag = {"done": False}
                async def greet_now():
                    if greeted_flag["done"]:
                        return
                    greeted_flag["done"] = True
                    try:
                        if 'session' in locals() and hasattr(session, "say") and callable(getattr(session, "say")):
                            await asyncio.wait_for(session.say(greeting_message), timeout=6.0)
                            logger.info("✅ Proactive greeting delivered via session.say()")
                            # Conversation events will capture the greeting transcript; no manual store needed
                        else:
                            logger.info("⚠️ No greeting method available on session; skipping proactive greeting")
                    except Exception as e:
                        logger.warning(f"Proactive greeting failed or timed out: {type(e).__name__}: {e}")

                # If a participant is already in the room, greet immediately
                try:
                    participants = []
                    if hasattr(ctx.room, 'remote_participants'):
                        participants = list(ctx.room.remote_participants.values())
                    elif hasattr(ctx.room, 'participants'):
                        participants = [p for p in ctx.room.participants.values() if getattr(p, 'is_local', False) is False]
                    if participants:
                        asyncio.create_task(greet_now())
                    else:
                        # Otherwise, greet on first participant_connected
                        @ctx.room.on("participant_connected")
                        def _on_participant_connected(_participant):
                            try:
                                asyncio.create_task(greet_now())
                            except Exception:
                                pass
                except Exception as e:
                    logger.warning(f"Could not wire participant_connected for greeting: {type(e).__name__}: {e}")
            else:
                logger.info("Proactive greeting disabled (ENABLE_PROACTIVE_GREETING=false)")

            # Conversation flow is automatic in SidekickAgent; no forced replies
            logger.info("✅ Agent ready - automatic STT→LLM→TTS flow engaged (single-layer pattern)")
            
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
            
            # Keep the agent alive by waiting for the room to close
            # The agent will handle room events and manage its own lifecycle
            logger.info("Agent is running. Waiting for completion...")
            
            # The agent runs until the room closes or all participants disconnect
            # We need to keep this coroutine alive while the agent is active
            try:
                # Wait for the room to disconnect or session to end
                while ctx.room.isconnected() if hasattr(ctx.room, 'isconnected') else True:
                    await asyncio.sleep(1)  # Check every second
                    
                    # Log periodic heartbeat to show agent is still active
                    if int(time.time()) % 30 == 0:  # Every 30 seconds
                        participants_count = len(ctx.room.remote_participants) if hasattr(ctx.room, 'remote_participants') else 0
                        logger.info(f"💓 Agent heartbeat - Room: {ctx.room.name}, Participants: {participants_count}")
                
                logger.info("Room disconnected or agent ended. Agent shutting down.")
                
            except Exception as e:
                logger.error(f"Error in session wait loop: {e}")
                # Continue to cleanup
            
            # Clean shutdown
            if hasattr(ctx, 'agent') and ctx.agent:
                logger.info("Cleaning up agent...")
                # Agent cleanup happens automatically
            
        except ConfigurationError as e:
            # Configuration errors are fatal - don't try to recover
            logger.error(f"❌ Configuration error: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ Agent failed: {e}", exc_info=True)
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
        # Use THREAD executor to avoid process forking issues
        from livekit.agents import JobExecutorType
        worker_options = WorkerOptions(
            entrypoint_fnc=agent_job_handler,
            request_fnc=request_filter,
            agent_name=agent_name,  # EXPLICIT: Only receive jobs for this agent name
            job_executor_type=JobExecutorType.THREAD,  # Use threads instead of processes
            num_idle_processes=0,  # Disable pre-warming of processes
        )

        # Let the CLI handle the event loop
        cli.run_app(worker_options)
    except KeyboardInterrupt:
        logger.info("Shutting down worker.")
