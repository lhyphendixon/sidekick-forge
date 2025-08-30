from typing import Optional, Dict
from livekit.plugins import openai as lk_openai, groq as lk_groq


def get_llm(provider: str, model: Optional[str], api_keys: Dict[str, Optional[str]]):
    """
    Return an initialized LLM plugin instance for the given provider/model.
    Supports: openai, groq, cerebras (via openai shim: with_cerebras).
    Raises ValueError on missing keys or unsupported provider.
    """
    provider = (provider or '').lower()
    model = model or ''

    if provider == 'groq':
        key = api_keys.get('groq_api_key')
        if not key:
            raise ValueError('Missing API key for Groq')
            
        
        # Map legacy Groq model names to current
        if model in ('llama3-70b-8192', 'llama-3.1-70b-versatile'):
            model = 'llama-3.3-70b-versatile'
        return lk_groq.LLM(model=model or 'llama-3.3-70b-versatile', api_key=key)

    if provider == 'cerebras':
        key = api_keys.get('cerebras_api_key')
        if not key:
            raise ValueError('Missing API key for Cerebras')
        # Use LiveKit OpenAI shim for Cerebras
        # Default to a valid documented model
        return lk_openai.LLM.with_cerebras(model=model or 'llama3.1-8b', api_key=key)

    if provider == 'deepinfra':
        key = api_keys.get('deepinfra_api_key')
        if not key:
            raise ValueError('Missing API key for DeepInfra')
        # DeepInfra exposes an OpenAI-compatible API at this base URL
        # Reference: https://deepinfra.com/docs/openai
        return lk_openai.LLM(model=model or 'meta-llama/Llama-3.1-8B-Instruct', api_key=key, base_url='https://api.deepinfra.com/v1/openai')

    if provider == 'openai' or not provider:
        key = api_keys.get('openai_api_key')
        if not key:
            raise ValueError('Missing API key for OpenAI')
        return lk_openai.LLM(model=model or 'gpt-4', api_key=key)

    raise ValueError(f'Unsupported LLM provider: {provider}')


