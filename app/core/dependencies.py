"""
Core dependencies for the application
"""
import os
import redis
from typing import Generator


def get_redis_client() -> Generator[redis.Redis, None, None]:
    """Get Redis client"""
    client = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"), 
        port=int(os.getenv("REDIS_PORT", 6379)), 
        decode_responses=True
    )
    try:
        yield client
    finally:
        pass  # Redis client doesn't need explicit cleanup