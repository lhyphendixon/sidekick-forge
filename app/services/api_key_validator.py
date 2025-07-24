"""
API Key validation service
"""
import httpx
import asyncio
import logging
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


class APIKeyValidator:
    """Service to validate API keys for various providers"""
    
    @staticmethod
    async def validate_siliconflow_key(api_key: str) -> Tuple[bool, str]:
        """Validate SiliconFlow API key by making a test request"""
        if not api_key:
            return False, "No API key provided"
        
        try:
            async with httpx.AsyncClient() as client:
                # Test with a simple embedding request
                response = await client.post(
                    "https://api.siliconflow.cn/v1/embeddings",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "BAAI/bge-large-en-v1.5",
                        "input": "test",
                        "encoding_format": "float"
                    },
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    return True, "Valid API key"
                elif response.status_code == 401:
                    return False, "Invalid API key"
                elif response.status_code == 429:
                    return True, "Valid API key (rate limited)"
                else:
                    return False, f"API error: {response.status_code} - {response.text}"
                    
        except httpx.TimeoutException:
            return False, "API request timed out"
        except Exception as e:
            return False, f"Validation error: {str(e)}"
    
    @staticmethod
    async def validate_openai_key(api_key: str) -> Tuple[bool, str]:
        """Validate OpenAI API key"""
        if not api_key:
            return False, "No API key provided"
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={
                        "Authorization": f"Bearer {api_key}"
                    },
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    return True, "Valid API key"
                elif response.status_code == 401:
                    return False, "Invalid API key"
                else:
                    return False, f"API error: {response.status_code}"
                    
        except Exception as e:
            return False, f"Validation error: {str(e)}"
    
    @staticmethod
    async def validate_groq_key(api_key: str) -> Tuple[bool, str]:
        """Validate Groq API key"""
        if not api_key:
            return False, "No API key provided"
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={
                        "Authorization": f"Bearer {api_key}"
                    },
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    return True, "Valid API key"
                elif response.status_code == 401:
                    return False, "Invalid API key"
                else:
                    return False, f"API error: {response.status_code}"
                    
        except Exception as e:
            return False, f"Validation error: {str(e)}"
    
    @staticmethod
    async def validate_cartesia_key(api_key: str) -> Tuple[bool, str]:
        """Validate Cartesia API key"""
        if not api_key:
            return False, "No API key provided"
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.cartesia.ai/v1/voices",
                    headers={
                        "X-API-Key": api_key
                    },
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    return True, "Valid API key"
                elif response.status_code == 401:
                    return False, "Invalid API key"
                else:
                    return False, f"API error: {response.status_code}"
                    
        except Exception as e:
            return False, f"Validation error: {str(e)}"
    
    @staticmethod
    async def validate_deepgram_key(api_key: str) -> Tuple[bool, str]:
        """Validate Deepgram API key"""
        if not api_key:
            return False, "No API key provided"
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.deepgram.com/v1/projects",
                    headers={
                        "Authorization": f"Token {api_key}"
                    },
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    return True, "Valid API key"
                elif response.status_code == 401:
                    return False, "Invalid API key"
                else:
                    return False, f"API error: {response.status_code}"
                    
        except Exception as e:
            return False, f"Validation error: {str(e)}"
    
    @staticmethod
    async def validate_elevenlabs_key(api_key: str) -> Tuple[bool, str]:
        """Validate ElevenLabs API key"""
        if not api_key:
            return False, "No API key provided"
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.elevenlabs.io/v1/user",
                    headers={
                        "xi-api-key": api_key
                    },
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    return True, "Valid API key"
                elif response.status_code == 401:
                    return False, "Invalid API key"
                else:
                    return False, f"API error: {response.status_code}"
                    
        except Exception as e:
            return False, f"Validation error: {str(e)}"
    
    @staticmethod
    async def validate_novita_key(api_key: str) -> Tuple[bool, str]:
        """Validate Novita API key"""
        if not api_key:
            return False, "No API key provided"
        
        try:
            async with httpx.AsyncClient() as client:
                # Test with a simple model list request
                response = await client.get(
                    "https://api.novita.ai/v3/openai/models",
                    headers={
                        "Authorization": f"Bearer {api_key}"
                    },
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    return True, "Valid API key"
                elif response.status_code == 401:
                    return False, "Invalid API key"
                else:
                    return False, f"API error: {response.status_code}"
                    
        except Exception as e:
            return False, f"Validation error: {str(e)}"
    
    @staticmethod
    async def validate_jina_key(api_key: str) -> Tuple[bool, str]:
        """Validate Jina API key"""
        if not api_key:
            return False, "No API key provided"
        
        try:
            async with httpx.AsyncClient() as client:
                # Test with a simple reranker request
                response = await client.post(
                    "https://api.jina.ai/v1/rerank",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "jina-reranker-v2-base-multilingual",
                        "query": "test",
                        "documents": ["test document"]
                    },
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    return True, "Valid API key"
                elif response.status_code == 401:
                    return False, "Invalid API key"
                else:
                    return False, f"API error: {response.status_code}"
                    
        except Exception as e:
            return False, f"Validation error: {str(e)}"
    
    @staticmethod
    async def validate_api_keys(api_keys: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
        """
        Validate multiple API keys concurrently
        
        Returns:
            Dict with validation results for each key
        """
        results = {}
        
        # Map of provider to validation function
        validators = {
            'siliconflow_api_key': APIKeyValidator.validate_siliconflow_key,
            'openai_api_key': APIKeyValidator.validate_openai_key,
            'groq_api_key': APIKeyValidator.validate_groq_key,
            'cartesia_api_key': APIKeyValidator.validate_cartesia_key,
            'deepgram_api_key': APIKeyValidator.validate_deepgram_key,
            'elevenlabs_api_key': APIKeyValidator.validate_elevenlabs_key,
            'novita_api_key': APIKeyValidator.validate_novita_key,
            'jina_api_key': APIKeyValidator.validate_jina_key
        }
        
        # Run validations concurrently
        tasks = []
        for key_name, api_key in api_keys.items():
            if key_name in validators and api_key:
                validator = validators[key_name]
                tasks.append((key_name, validator(api_key)))
        
        if tasks:
            # Execute all validations concurrently
            results_list = await asyncio.gather(*[task[1] for task in tasks])
            
            # Map results back to key names
            for i, (key_name, _) in enumerate(tasks):
                is_valid, message = results_list[i]
                results[key_name] = {
                    'valid': is_valid,
                    'message': message
                }
                
                if not is_valid:
                    logger.warning(f"Invalid {key_name}: {message}")
        
        return results


# Create singleton instance
api_key_validator = APIKeyValidator()