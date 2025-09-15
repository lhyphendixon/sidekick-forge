#!/usr/bin/env python3
"""
AI Processor Service for embedding generation
Integrates with the existing LiveKit agents AI processing bridge
"""

import asyncio
import logging
import os
import json
import httpx
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)


class AIProcessor:
    """Handles AI processing tasks including embedding generation"""
    
    def __init__(self):
        self.default_embedding_models = {
            'document': 'text-embedding-3-small',  # OpenAI model for documents
            'conversation': 'text-embedding-3-small',  # OpenAI model for conversations
        }
        self.http_client = httpx.AsyncClient(timeout=30.0)
    
    async def generate_embeddings(
        self, 
        text: str, 
        context: str = 'document',
        client_settings: Optional[Dict] = None
    ) -> Optional[List[float]]:
        """Generate embeddings for text using the appropriate model and provider"""
        try:
            if not text or not text.strip():
                return None
            
            # Get embedding configuration from client settings
            if client_settings:
                embedding_config = client_settings.get('embedding', {})
                provider = embedding_config.get('provider', 'openai')
                if context == 'document':
                    model = embedding_config.get('document_model', 'text-embedding-3-small')
                else:
                    model = embedding_config.get('conversation_model', 'text-embedding-3-small')
                dimension = embedding_config.get('dimension', None)
                api_keys = client_settings.get('api_keys', {})
            else:
                provider = 'openai'
                model = self.default_embedding_models.get(context, 'text-embedding-3-small')
                dimension = None
                api_keys = {}
            
            logger.info(f"Generating embeddings with provider={provider}, model={model}, context={context}")
            
            # Route to appropriate provider
            if provider == 'openai':
                return await self._generate_openai_embeddings(text, model, api_keys)
            elif provider == 'deepinfra':
                return await self._generate_deepinfra_embeddings(text, model, api_keys, dimension)
            elif provider == 'novita':
                return await self._generate_novita_embeddings(text, model, api_keys)
            elif provider == 'siliconflow':
                return await self._generate_siliconflow_embeddings(text, model, api_keys)
            else:
                logger.error(f"Unsupported embedding provider: {provider}")
                return None
            
        except Exception as e:
            logger.error(f"Error generating embeddings: {e}")
            return None
    
    async def _get_api_key_from_settings(self, key_name: str, client_settings: Optional[Dict] = None) -> Optional[str]:
        """Get API key from client settings or Supabase"""
        try:
            # First check if key is in client_settings
            if client_settings and 'api_keys' in client_settings:
                api_keys = client_settings.get('api_keys', {})
                if key_name in api_keys and api_keys[key_name]:
                    return api_keys[key_name]
            
            # If not in client settings, try to fetch from global_settings table
            # This is for backward compatibility
            from app.integrations.supabase_client import supabase_manager
            
            # Use the admin client from the manager
            if not supabase_manager.admin_client:
                await supabase_manager.initialize()
            
            result = supabase_manager.admin_client.table('global_settings')\
                .select('setting_value')\
                .eq('setting_key', key_name)\
                .single()\
                .execute()
            
            if result.data:
                return result.data['setting_value']
            
            return None
            
        except Exception as e:
            logger.error(f"Error fetching API key from settings: {e}")
            return None
    
    async def generate_conversation_summary(self, messages: List[Dict]) -> Optional[str]:
        """Generate a summary of conversation messages"""
        try:
            if not messages:
                return None
            
            # Format messages for summarization
            conversation_text = ""
            for msg in messages:
                role = msg.get('role', 'unknown')
                content = msg.get('content', '')
                speaker = 'User' if role == 'user' else 'Assistant'
                conversation_text += f"{speaker}: {content}\n"
            
            # For now, return a simple summary
            # Later, integrate with LLM for better summarization
            word_count = len(conversation_text.split())
            return f"Conversation with {len(messages)} messages ({word_count} words)"
            
        except Exception as e:
            logger.error(f"Error generating conversation summary: {e}")
            return None
    
    async def rerank_results(
        self, 
        query: str, 
        documents: List[Dict], 
        top_k: int = 5
    ) -> List[Dict]:
        """Rerank search results for better relevance"""
        try:
            # For now, return documents as-is
            # Later, integrate with reranking model
            return documents[:top_k]
            
        except Exception as e:
            logger.error(f"Error reranking results: {e}")
            return documents[:top_k]


    async def _generate_openai_embeddings(self, text: str, model: str, api_keys: Dict) -> Optional[List[float]]:
        """Generate embeddings using OpenAI"""
        try:
            import openai
            
            api_key = api_keys.get('openai_api_key') or os.getenv('OPENAI_API_KEY')
            if not api_key:
                # Pass the entire client settings for better key resolution
                client_settings = {'api_keys': api_keys} if api_keys else None
                api_key = await self._get_api_key_from_settings('openai_api_key', client_settings)
            
            if not api_key:
                logger.error("No OpenAI API key available")
                return None
            
            client = openai.OpenAI(api_key=api_key)
            response = client.embeddings.create(input=text, model=model)
            
            if response.data:
                return response.data[0].embedding
            return None
            
        except Exception as e:
            logger.error(f"OpenAI embedding error: {e}")
            return None
    
    async def _generate_deepinfra_embeddings(self, text: str, model: str, api_keys: Dict, dimension: Optional[int] = None) -> Optional[List[float]]:
        """Generate embeddings using DeepInfra"""
        try:
            api_key = api_keys.get('deepinfra_api_key') or os.getenv('DEEPINFRA_API_KEY')
            if not api_key:
                # Try to get from settings
                client_settings = {'api_keys': api_keys} if api_keys else None
                api_key = await self._get_api_key_from_settings('deepinfra_api_key', client_settings)
            
            if not api_key:
                logger.error("No DeepInfra API key available")
                return None
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            # DeepInfra uses model-specific endpoints for some models like Qwen
            # Check if this is a Qwen model
            if "qwen" in model.lower():
                # Use the model-specific inference endpoint
                url = f"https://api.deepinfra.com/v1/inference/{model}"
                data = {
                    "inputs": [text]  # Qwen expects array format
                }
                # Add dimension parameter if specified for Qwen embedding models
                if dimension and "embedding" in model.lower():
                    data["dimension"] = dimension
                    logger.info(f"Using dimension {dimension} for Qwen embedding model {model}")
            else:
                # Use standard embeddings endpoint for other models
                url = "https://api.deepinfra.com/v1/embeddings"
                data = {
                    "input": text,
                    "model": model
                }
            
            response = await self.http_client.post(
                url,
                headers=headers,
                json=data
            )
            
            if response.status_code == 200:
                result = response.json()
                # Handle different response formats
                if 'embeddings' in result:
                    # Qwen format: {"embeddings": [[...]], ...}
                    if isinstance(result['embeddings'], list) and len(result['embeddings']) > 0:
                        return result['embeddings'][0]
                elif 'data' in result and len(result['data']) > 0:
                    # Standard format: {"data": [{"embedding": [...]}]}
                    return result['data'][0]['embedding']
            else:
                logger.error(f"DeepInfra API error: {response.status_code} - {response.text}")
            
            return None
            
        except Exception as e:
            logger.error(f"DeepInfra embedding error: {e}")
            return None
    
    async def _generate_novita_embeddings(self, text: str, model: str, api_keys: Dict) -> Optional[List[float]]:
        """Generate embeddings using Novita AI"""
        try:
            api_key = api_keys.get('novita_api_key') or os.getenv('NOVITA_API_KEY')
            if not api_key:
                # Try to get from settings
                client_settings = {'api_keys': api_keys} if api_keys else None
                api_key = await self._get_api_key_from_settings('novita_api_key', client_settings)
            
            if not api_key:
                logger.error(f"No Novita API key available. API keys provided: {list(api_keys.keys())}")
                # Try to fall back to OpenAI if available
                if api_keys.get('openai_api_key'):
                    logger.warning("Falling back to OpenAI embeddings due to missing Novita key")
                    return await self._generate_openai_embeddings(text, 'text-embedding-3-small', api_keys)
                return None
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            # Novita uses a different endpoint structure
            data = {
                "model": model,
                "input": text,
                "encoding_format": "float"
            }
            
            response = await self.http_client.post(
                "https://api.novita.ai/v3/embeddings",
                headers=headers,
                json=data
            )
            
            if response.status_code == 200:
                result = response.json()
                if 'data' in result and len(result['data']) > 0:
                    return result['data'][0]['embedding']
            else:
                logger.error(f"Novita API error: {response.status_code} - {response.text}")
            
            return None
            
        except Exception as e:
            logger.error(f"Novita embedding error: {e}")
            return None
    
    async def _generate_siliconflow_embeddings(self, text: str, model: str, api_keys: Dict) -> Optional[List[float]]:
        """Generate embeddings using SiliconFlow"""
        # Create a fresh HTTP client with longer timeout for SiliconFlow which can be slow
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            try:
                logger.info(f"[DEBUG] _generate_siliconflow_embeddings called with model={model}")
                logger.info(f"[DEBUG] API keys passed: {list(api_keys.keys())}")
                logger.info(f"[DEBUG] siliconflow_api_key in api_keys: {'siliconflow_api_key' in api_keys}")
                
                api_key = api_keys.get('siliconflow_api_key') or os.getenv('SILICONFLOW_API_KEY')
                logger.info(f"[DEBUG] Initial api_key from dict/env: {api_key[:10] if api_key else 'None'}...")
                
                if not api_key:
                    # Try to get from settings
                    client_settings = {'api_keys': api_keys} if api_keys else None
                    api_key = await self._get_api_key_from_settings('siliconflow_api_key', client_settings)
                    logger.info(f"[DEBUG] api_key after _get_api_key_from_settings: {api_key[:10] if api_key else 'None'}...")
                
                if not api_key:
                    logger.error(f"No SiliconFlow API key available. API keys provided: {list(api_keys.keys())}")
                    # Try to fall back to OpenAI if available
                    if api_keys.get('openai_api_key'):
                        logger.warning("Falling back to OpenAI embeddings due to missing SiliconFlow key")
                        return await self._generate_openai_embeddings(text, 'text-embedding-3-small', api_keys)
                    return None
                
                # Log the API key (masked for security)
                logger.info(f"Using SiliconFlow API key: {api_key[:10]}...{api_key[-10:] if len(api_key) > 20 else '***'}")
                
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
                
                # SiliconFlow uses OpenAI-compatible API
                data = {
                    "model": model,
                    "input": text,
                    "encoding_format": "float",
                    "dimensions": 1024  # Explicitly request 1024 dimensions for pgvector compatibility
                }
                
                response = await client.post(
                    "https://api.siliconflow.com/v1/embeddings",
                    headers=headers,
                    json=data
                )
                
                logger.info(f"[DEBUG] SiliconFlow response status: {response.status_code}")
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"[DEBUG] SiliconFlow response keys: {list(result.keys()) if result else 'None'}")
                    if 'data' in result and len(result['data']) > 0:
                        embedding = result['data'][0].get('embedding')
                        if embedding:
                            logger.info(f"[DEBUG] SiliconFlow embedding length: {len(embedding)}")
                            return embedding
                        else:
                            logger.error(f"SiliconFlow response missing embedding field: {result}")
                    else:
                        logger.error(f"SiliconFlow response missing data: {result}")
                else:
                    logger.error(f"SiliconFlow API error: {response.status_code} - {response.text}")
                
                return None
                
            except Exception as e:
                import traceback
                logger.error(f"SiliconFlow embedding error: {str(e)}")
                logger.error(f"SiliconFlow traceback: {traceback.format_exc()}")
                return None
    
    async def close(self):
        """Close HTTP client"""
        await self.http_client.aclose()


# Create singleton instance
ai_processor = AIProcessor()