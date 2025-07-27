#!/usr/bin/env python3
"""
LiveKit Agent Worker Entrypoint
Implements proper worker registration and job handling for the Autonomite agent
"""

import asyncio
import os
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from livekit import agents, rtc
from livekit.agents import JobContext, JobRequest, WorkerOptions, cli, llm, voice
from livekit.plugins import deepgram, elevenlabs, openai, groq, silero, cartesia
from api_key_loader import APIKeyLoader
from config_validator import ConfigValidator, ConfigurationError

# Configure logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Agent class removed - using VoiceAssistant directly


async def agent_job_handler(ctx: JobContext):
    """
    This function is called for each new agent job.
    It creates an agent, connects to the room, and handles the session.
    """
    logger.info(f"Received job for room: {ctx.room.name}")
    
    # The context provides a connected room object
    room = ctx.room

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
        try:
            ConfigValidator.validate_configuration(metadata, api_keys)
        except ConfigurationError as e:
            logger.error(f"❌ Configuration validation failed: {e}")
            # Fail fast - don't try to start with invalid configuration
            raise

        # --- Migrated Agent Logic ---
        try:
            # Extract configuration from metadata
            system_prompt = metadata.get("system_prompt", "You are a helpful AI assistant.")
            voice_settings = metadata.get("voice_settings", {})
            llm_provider = voice_settings.get("llm_provider", metadata.get("llm_provider", "openai"))
            
            # Configure LLM based on provider - NO FALLBACK to environment variables
            if llm_provider == "groq":
                groq_key = api_keys.get("groq_api_key")
                if not groq_key:
                    raise ConfigurationError("Groq API key required but not found")
                llm_plugin = groq.LLM(
                    model=metadata.get("model", "llama-3.1-70b-versatile"),
                    api_key=groq_key
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
            stt_provider = voice_settings.get("stt_provider", "deepgram")
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
                    api_key=deepgram_key
                )
            
            # Validate STT initialization
            ConfigValidator.validate_provider_initialization(f"{stt_provider} STT", stt_plugin)
            
            # Configure TTS - NO FALLBACK to environment variables
            tts_provider = voice_settings.get("tts_provider", "cartesia")
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
                if cartesia_key.startswith("fixed_"):
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
            vad = silero.VAD.load()
            
            # Connect to the room first
            logger.info("Connecting to room...")
            await ctx.connect()
            logger.info("✅ Connected to room successfully")
            
            # Create and configure the voice agent session
            logger.info("Creating voice agent session...")
            session = voice.AgentSession(
                vad=vad,
                stt=stt_plugin,
                llm=llm_plugin,
                tts=tts_plugin
            )
            logger.info("✅ Voice agent session created")
            
            # Add event handlers for logging and monitoring
            @session.on("user_speech_committed")
            def on_user_speech(msg: llm.ChatMessage):
                logger.info(f"💬 User said: {msg.content}")
            
            @session.on("agent_speech_committed")
            def on_agent_speech(msg: llm.ChatMessage):
                logger.info(f"🤖 Agent responded: {msg.content}")
            
            @session.on("agent_thinking")
            def on_thinking_started():
                logger.info("🤔 Agent is thinking...")
            
            # Create the agent with instructions
            agent = voice.Agent(instructions=system_prompt)
            
            # Start the session - this connects it to the room
            # According to LiveKit docs, session.start() must be awaited
            logger.info("Starting agent session...")
            await session.start(room=ctx.room, agent=agent)
            
            # Log successful start
            logger.info(f"✅ Agent session started successfully in room: {ctx.room.name}")
            logger.info(f"   - LLM: {llm_provider}")
            logger.info(f"   - STT: {stt_provider}")
            logger.info(f"   - TTS: {tts_provider}")
            
            # Store session reference for the job lifecycle
            ctx.session = session
            
            # Wait a moment for the audio pipeline to be ready
            logger.info("Waiting for audio pipeline to be ready...")
            await asyncio.sleep(0.5)
            
            # Check if there are participants in the room before greeting
            # Note: ctx.room might not have participants attribute immediately
            try:
                # Try to get participants - different SDK versions have different attributes
                participants = []
                if hasattr(ctx.room, 'remote_participants'):
                    participants = list(ctx.room.remote_participants.values())
                    logger.info(f"Found {len(participants)} remote participant(s) in room")
                elif hasattr(ctx.room, 'participants'):
                    participants = list(ctx.room.participants.values())
                    logger.info(f"Found {len(participants)} participant(s) in room")
                else:
                    logger.info("Room object doesn't have participants attribute yet")
                
                # Always send greeting - agent should be ready when user joins
                logger.info("🎤 Preparing agent to greet user...")
                # The session will handle greeting when participant joins
                
            except Exception as e:
                logger.warning(f"Could not check participants: {e}")
                # Continue - the session will handle participant events
            
            # The session manages the lifecycle - we don't need explicit wait
            # The job will stay alive until the room closes or agent disconnects
            
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


async def request_filter(job_request: JobRequest) -> None:
    """
    Filter function to accept/reject jobs
    
    We accept all jobs and use metadata to determine agent configuration.
    This allows one worker to handle multiple agent types.
    """
    logger.info(f"Received job request: {job_request.job.id}")
    logger.info(f"Job agent name: {job_request.agent_name}")
    
    # Accept all jobs - we'll handle different agent configurations via metadata
    logger.info(f"Accepting job for agent: {job_request.agent_name}")
    await job_request.accept()


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

        logger.info(f"Starting agent worker...")
        logger.info(f"LiveKit URL: {url}")
        logger.info(f"Agent name: sidekick-agent")

        # Configure worker options with agent_name for explicit dispatch
        worker_options = WorkerOptions(
            entrypoint_fnc=agent_job_handler,
            request_fnc=request_filter,
            agent_name="sidekick-agent",  # Enable explicit dispatch
        )

        # Let the CLI handle the event loop
        cli.run_app(worker_options)
    except KeyboardInterrupt:
        logger.info("Shutting down worker.")