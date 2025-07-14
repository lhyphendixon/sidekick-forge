"""
Voice Assistant implementation for LiveKit Agent
"""

import logging
from typing import Optional
from livekit import agents, rtc
from livekit.agents import JobContext, AutoSubscribe
from livekit.plugins import openai, groq, elevenlabs, deepgram

from .config import AgentConfig

logger = logging.getLogger(__name__)

class VoiceAssistant:
    """Voice assistant that handles conversations in LiveKit rooms"""
    
    def __init__(self, config: AgentConfig, ctx: JobContext):
        self.config = config
        self.ctx = ctx
        self.assistant = None
        
    async def run(self):
        """Run the voice assistant"""
        try:
            # Validate configuration
            self.config.validate()
            
            # Set up STT provider
            stt = self._create_stt()
            
            # Set up TTS provider
            tts = self._create_tts()
            
            # Set up LLM provider
            llm = self._create_llm()
            
            # Create the assistant
            self.assistant = agents.VoiceAssistant(
                stt=stt,
                tts=tts,
                llm=llm,
                interrupt_speech_duration=0.5,
                interrupt_min_words=2,
            )
            
            # Start the assistant
            self.assistant.start(self.ctx.room)
            
            # Set up event handlers
            @self.ctx.room.on("track_subscribed")
            def on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
                if track.kind == rtc.TrackKind.KIND_AUDIO:
                    logger.info(f"Audio track subscribed from {participant.identity}")
            
            # Wait for room to close
            await self.ctx.wait_for_close()
            
        except Exception as e:
            logger.error(f"Voice assistant error: {e}", exc_info=True)
            raise
    
    def _create_stt(self):
        """Create STT provider based on configuration"""
        if self.config.stt_provider == "groq":
            return groq.STT(
                api_key=self.config.groq_api_key,
                model=self.config.stt_model,
                language="en"
            )
        elif self.config.stt_provider == "deepgram":
            return deepgram.STT(
                api_key=self.config.deepgram_api_key,
                model=self.config.stt_model or "nova-2",
                language="en"
            )
        else:  # Default to OpenAI
            return openai.STT(
                api_key=self.config.openai_api_key,
                model="whisper-1",
                language="en"
            )
    
    def _create_tts(self):
        """Create TTS provider based on configuration"""
        if self.config.tts_provider == "elevenlabs":
            return elevenlabs.TTS(
                api_key=self.config.elevenlabs_api_key,
                voice=self.config.voice_id
            )
        else:  # Default to OpenAI
            return openai.TTS(
                api_key=self.config.openai_api_key,
                model=self.config.tts_model or "tts-1",
                voice=self.config.voice_id
            )
    
    def _create_llm(self):
        """Create LLM provider based on configuration"""
        if self.config.model.startswith("claude"):
            # Would need anthropic plugin
            raise NotImplementedError("Anthropic support not yet implemented")
        else:  # Default to OpenAI
            return openai.LLM(
                api_key=self.config.openai_api_key,
                model=self.config.model,
                temperature=self.config.temperature,
                system_prompt=self.config.system_prompt
            )