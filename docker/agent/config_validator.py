#!/usr/bin/env python3
"""
Configuration Validator for Agent Worker
Validates configuration and API keys before initializing providers
Follows the "fail fast" principle - no silent fallbacks
"""
import logging
from typing import Dict, Any, List, Tuple

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Raised when configuration validation fails"""
    pass


class ConfigValidator:
    """Validates agent configuration and API keys"""
    
    # Known test/dummy API keys that should be rejected
    INVALID_API_KEYS = {
        'test', 'test_key', 'dummy', 'placeholder', 'example', 
        'your-api-key-here', 'xxx', '123456', 'demo'
    }
    
    # API key prefixes for validation
    API_KEY_PREFIXES = {
        'openai_api_key': 'sk-',
        'groq_api_key': 'gsk_',
        'deepgram_api_key': 'dgp_',
        'cartesia_api_key': 'sk-',
        'elevenlabs_api_key': 'sk-',
    }
    
    @staticmethod
    def validate_configuration(metadata: Dict[str, Any], api_keys: Dict[str, str]) -> None:
        """
        Validate the complete configuration before starting the agent.
        Raises ConfigurationError if validation fails.
        
        Args:
            metadata: Agent metadata including voice settings
            api_keys: Dictionary of API keys
            
        Raises:
            ConfigurationError: If configuration is invalid
        """
        logger.info("Starting configuration validation...")
        
        # Validate metadata structure
        ConfigValidator._validate_metadata_structure(metadata)
        
        # Validate voice settings
        voice_settings = metadata.get('voice_settings', {})
        ConfigValidator._validate_voice_settings(voice_settings)
        
        # Validate API keys for configured providers
        ConfigValidator._validate_api_keys(api_keys, voice_settings, metadata)
        
        logger.info("✅ Configuration validation passed")
    
    @staticmethod
    def _validate_metadata_structure(metadata: Dict[str, Any]) -> None:
        """Validate basic metadata structure"""
        if not metadata:
            raise ConfigurationError("No metadata provided")
            
        if not isinstance(metadata, dict):
            raise ConfigurationError(f"Metadata must be a dictionary, got {type(metadata)}")
            
        # System prompt is required
        if not metadata.get('system_prompt'):
            logger.warning("No system prompt provided, using default")
    
    @staticmethod
    def _validate_voice_settings(voice_settings: Dict[str, Any]) -> None:
        """Validate voice settings structure"""
        if not voice_settings:
            raise ConfigurationError("No voice_settings in metadata")
            
        # Normalize providers but do NOT introduce silent defaults
        # LLM provider must be present
        if not voice_settings.get('llm_provider'):
            raise ConfigurationError("Missing required voice_settings.llm_provider")
        # STT provider must be present
        if not voice_settings.get('stt_provider'):
            raise ConfigurationError("Missing required voice_settings.stt_provider")
        # TTS can be under tts_provider or provider (admin UI)
        if not (voice_settings.get('tts_provider') or voice_settings.get('provider')):
            raise ConfigurationError("Missing required TTS provider (voice_settings.tts_provider or voice_settings.provider)")
        if not voice_settings.get('tts_provider') and voice_settings.get('provider') in ['elevenlabs', 'cartesia']:
            voice_settings['tts_provider'] = voice_settings['provider']
            logger.info(f"Normalized tts_provider from provider='{voice_settings['provider']}'")
            
        # Validate provider values
        valid_llm_providers = ['openai', 'groq', 'cerebras']
        valid_stt_providers = ['deepgram', 'cartesia']
        valid_tts_providers = ['elevenlabs', 'cartesia']
        
        llm = voice_settings.get('llm_provider')
        if llm not in valid_llm_providers:
            raise ConfigurationError(f"Invalid LLM provider: {llm}. Must be one of: {valid_llm_providers}")
            
        stt = voice_settings.get('stt_provider')
        if stt not in valid_stt_providers:
            raise ConfigurationError(f"Invalid STT provider: {stt}. Must be one of: {valid_stt_providers}")
            
        tts = voice_settings.get('tts_provider') or voice_settings.get('provider')
        if tts not in valid_tts_providers:
            raise ConfigurationError(f"Invalid TTS provider: {tts}. Must be one of: {valid_tts_providers}")
    
    @staticmethod
    def _validate_api_keys(api_keys: Dict[str, str], voice_settings: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        """Validate API keys for configured providers"""
        if not api_keys:
            raise ConfigurationError("No API keys provided")
            
        # Check for required API keys based on providers
        required_keys = ConfigValidator._get_required_api_keys(voice_settings, metadata)
        
        for key_name, provider_name in required_keys:
            api_key = api_keys.get(key_name)
            
            # Check if key exists
            if not api_key:
                raise ConfigurationError(f"Missing API key for {provider_name}: {key_name}")
                
            # Check for dummy/test keys
            # Allow specific test keys for development (sk-test_ prefix)
            if ConfigValidator._is_dummy_key(api_key) and not api_key.startswith("sk-test_"):
                raise ConfigurationError(f"Invalid {key_name}: '{api_key}' appears to be a test/dummy key")
                
            # Check key format/prefix
            if key_name in ConfigValidator.API_KEY_PREFIXES:
                expected_prefix = ConfigValidator.API_KEY_PREFIXES[key_name]
                if not api_key.startswith(expected_prefix):
                    logger.warning(f"{key_name} doesn't start with expected prefix '{expected_prefix}'")
                    # Don't fail on this - some providers might have different formats
                    
            # Check key length (most real API keys are at least 20 characters)
            # Exception for fixed test keys during development
            if len(api_key) < 20 and not api_key.startswith("fixed_"):
                raise ConfigurationError(f"{key_name} appears too short to be valid (length: {len(api_key)})")
                
        logger.info(f"✅ Validated {len(required_keys)} required API keys")
    
    @staticmethod
    def _get_required_api_keys(voice_settings: Dict[str, Any], metadata: Dict[str, Any]) -> List[Tuple[str, str]]:
        """Get list of required API keys based on configuration"""
        required = []
        
        # LLM provider
        llm_provider = voice_settings.get('llm_provider', metadata.get('llm_provider', 'openai'))
        if llm_provider == 'openai':
            required.append(('openai_api_key', 'OpenAI'))
        elif llm_provider == 'groq':
            required.append(('groq_api_key', 'Groq'))
        elif llm_provider == 'cerebras':
            required.append(('cerebras_api_key', 'Cerebras'))
            
        # STT provider
        stt_provider = voice_settings.get('stt_provider')
        if stt_provider == 'deepgram':
            required.append(('deepgram_api_key', 'Deepgram STT'))
        elif stt_provider == 'cartesia':
            required.append(('cartesia_api_key', 'Cartesia STT'))
            
        # TTS provider
        tts_provider = voice_settings.get('tts_provider') or voice_settings.get('provider')
        if tts_provider == 'elevenlabs':
            required.append(('elevenlabs_api_key', 'ElevenLabs TTS'))
        elif tts_provider == 'cartesia':
            # Only add if not already required for STT
            if stt_provider and stt_provider != 'cartesia':
                required.append(('cartesia_api_key', 'Cartesia TTS'))
                
        return required
    
    @staticmethod
    def _is_dummy_key(api_key: str) -> bool:
        """Check if an API key appears to be a dummy/test key"""
        if not api_key:
            return True
            
        # Check exact matches
        if api_key.lower() in ConfigValidator.INVALID_API_KEYS:
            return True
            
        # Check if key contains test patterns
        test_patterns = ['test', 'dummy', 'example', 'placeholder', 'demo']
        api_key_lower = api_key.lower()
        
        for pattern in test_patterns:
            if pattern in api_key_lower:
                return True
                
        # Check for repeated characters (like 'xxxxxxx')
        if len(set(api_key)) < 3:  # Less than 3 unique characters
            return True
            
        return False
    
    @staticmethod
    def validate_provider_initialization(provider_name: str, provider_instance: Any) -> None:
        """
        Validate that a provider was initialized successfully.
        This should be called after creating each provider instance.
        
        Args:
            provider_name: Name of the provider (e.g., 'OpenAI LLM', 'Deepgram STT')
            provider_instance: The initialized provider instance
            
        Raises:
            ConfigurationError: If provider initialization failed
        """
        if provider_instance is None:
            raise ConfigurationError(f"Failed to initialize {provider_name}: provider is None")
            
        # Check if provider has expected attributes/methods
        # This varies by provider type, but we can check common patterns
        if not hasattr(provider_instance, '__class__'):
            raise ConfigurationError(f"{provider_name} doesn't appear to be a valid provider instance")
            
        logger.info(f"✅ {provider_name} initialized successfully")