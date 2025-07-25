import logging
import traceback

import aiohttp
import os

logger = logging.getLogger(__name__)

# Example handler with logging
@agent.on("user_speech_committed")
async def on_user_speech_committed(event):
    logger.info("User speech committed event", extra={'event_data': event, 'room': event.room_name})
    try:
        # Existing logic
        logger.debug("Processing speech", extra={'transcript': event.transcript})
        # ...
    except Exception as e:
        logger.error(f"Error in speech handler: {str(e)}", exc_info=True, extra={'traceback': traceback.format_exc()})
        raise
    finally:
        logger.info("Speech handler completed")

# Add similar for other handlers: on_track_subscribed, on_participant_joined, etc.

@agent.on("track_subscribed")
async def on_track_subscribed(track, publication, participant):
    logger.debug("Track subscribed", extra={'participant_id': participant.identity, 'track_kind': track.kind})

# Greeting example
async def send_greeting(room):
    logger.info("Sending greeting", extra={'room': room.name})
    # ... 

async def custom_cartesia_tts(text: str, voice_id: str) -> bytes:
    api_key = os.getenv('CARTESIA_API_KEY')
    if not api_key:
        logger.error("Missing Cartesia API key")
        raise ValueError("No API key")
    url = 'wss://api.cartesia.ai/tts/websocket'  # Clean URL, no key in params
    headers = {'Authorization': f'Bearer {api_key}'}
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url, headers=headers) as ws:
            logger.info("Connected to Cartesia WS with headers")
            await ws.send_json({'text': text, 'voice_id': voice_id})
            response = await ws.receive_bytes()
            logger.debug("TTS response received", extra={'length': len(response)})
            return response
    logger.error("WS connection failed")
    raise Exception("TTS failed")

# In pipeline/handlers, replace plugin calls with:
# audio = await custom_cartesia_tts(transcript, voice_id) 