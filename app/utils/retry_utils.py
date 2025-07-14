"""Utilities for retrying external API calls"""
import asyncio
import logging
from typing import TypeVar, Callable, Optional, Union, Any
from functools import wraps
import httpx
from httpx import HTTPError, TimeoutException, ConnectError

logger = logging.getLogger(__name__)

T = TypeVar('T')


class RetryConfig:
    """Configuration for retry behavior"""
    def __init__(
        self,
        max_attempts: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True
    ):
        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter


def calculate_delay(attempt: int, config: RetryConfig) -> float:
    """Calculate delay for next retry attempt"""
    delay = min(config.initial_delay * (config.exponential_base ** attempt), config.max_delay)
    
    if config.jitter:
        # Add jitter to prevent thundering herd
        import random
        delay = delay * (0.5 + random.random() * 0.5)
        
    return delay


async def retry_async(
    func: Callable[..., T],
    *args,
    config: Optional[RetryConfig] = None,
    retry_on: Optional[tuple] = None,
    **kwargs
) -> T:
    """
    Retry an async function with exponential backoff
    
    Args:
        func: Async function to retry
        config: Retry configuration
        retry_on: Tuple of exception types to retry on
        
    Returns:
        Result of the function call
        
    Raises:
        Last exception if all retries fail
    """
    if config is None:
        config = RetryConfig()
        
    if retry_on is None:
        retry_on = (HTTPError, TimeoutException, ConnectError, ConnectionError)
        
    last_exception = None
    
    for attempt in range(config.max_attempts):
        try:
            return await func(*args, **kwargs)
        except retry_on as e:
            last_exception = e
            
            if attempt < config.max_attempts - 1:
                delay = calculate_delay(attempt, config)
                logger.warning(
                    f"Attempt {attempt + 1}/{config.max_attempts} failed for {func.__name__}: {e}. "
                    f"Retrying in {delay:.2f}s..."
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    f"All {config.max_attempts} attempts failed for {func.__name__}: {e}"
                )
                
    raise last_exception


def with_retry(
    config: Optional[RetryConfig] = None,
    retry_on: Optional[tuple] = None
):
    """
    Decorator to add retry logic to async functions
    
    Usage:
        @with_retry(config=RetryConfig(max_attempts=5))
        async def call_external_api():
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await retry_async(func, *args, config=config, retry_on=retry_on, **kwargs)
        return wrapper
    return decorator


class RetryableHTTPClient:
    """HTTP client with built-in retry logic"""
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        retry_config: Optional[RetryConfig] = None,
        timeout: float = 30.0
    ):
        self.base_url = base_url
        self.retry_config = retry_config or RetryConfig()
        self.timeout = timeout
        self._client = None
        
    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout
        )
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()
            
    async def request(
        self,
        method: str,
        url: str,
        **kwargs
    ) -> httpx.Response:
        """Make HTTP request with retry logic"""
        if not self._client:
            raise RuntimeError("Client not initialized. Use async context manager.")
            
        @with_retry(config=self.retry_config)
        async def _make_request():
            response = await self._client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
            
        return await _make_request()
        
    async def get(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("GET", url, **kwargs)
        
    async def post(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("POST", url, **kwargs)
        
    async def put(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("PUT", url, **kwargs)
        
    async def delete(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("DELETE", url, **kwargs)
        
    async def patch(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("PATCH", url, **kwargs)


# Example usage for external API calls
async def call_livekit_api_with_retry(
    url: str,
    api_key: str,
    api_secret: str,
    method: str = "POST",
    json_data: Optional[dict] = None
) -> dict:
    """
    Call LiveKit API with automatic retry
    
    Args:
        url: LiveKit API endpoint
        api_key: LiveKit API key
        api_secret: LiveKit API secret
        method: HTTP method
        json_data: JSON payload
        
    Returns:
        API response as dict
    """
    retry_config = RetryConfig(
        max_attempts=3,
        initial_delay=1.0,
        max_delay=10.0
    )
    
    async with RetryableHTTPClient(retry_config=retry_config) as client:
        headers = {
            "Authorization": f"Bearer {api_key}:{api_secret}",
            "Content-Type": "application/json"
        }
        
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            json=json_data
        )
        
        return response.json()


async def call_llm_api_with_retry(
    provider: str,
    api_key: str,
    endpoint: str,
    payload: dict,
    timeout: float = 60.0
) -> dict:
    """
    Call LLM API with automatic retry
    
    Args:
        provider: LLM provider (openai, groq, etc.)
        api_key: API key
        endpoint: API endpoint
        payload: Request payload
        timeout: Request timeout
        
    Returns:
        API response as dict
    """
    retry_config = RetryConfig(
        max_attempts=3,
        initial_delay=2.0,
        max_delay=30.0,
        jitter=True
    )
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # Provider-specific headers
    if provider == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
    
    async with RetryableHTTPClient(retry_config=retry_config, timeout=timeout) as client:
        response = await client.post(
            endpoint,
            headers=headers,
            json=payload
        )
        
        return response.json()