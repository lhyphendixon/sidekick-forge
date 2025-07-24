"""
Circuit Breaker implementation for fault tolerance

Implements the circuit breaker pattern to prevent cascading failures
and provide graceful degradation when services are unavailable.
"""
import asyncio
import logging
from typing import Callable, Optional, Any, Dict
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass, field
import functools

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """Circuit breaker states"""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failures exceeded threshold, blocking calls
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitStats:
    """Circuit breaker statistics"""
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    total_calls: int = 0
    circuit_opened_count: int = 0


class CircuitBreaker:
    """
    Circuit breaker for handling failures gracefully
    
    Features:
    - Automatic circuit opening on repeated failures
    - Half-open state for testing recovery
    - Configurable thresholds and timeouts
    - Fallback function support
    - Detailed statistics
    """
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout: timedelta = timedelta(seconds=60),
        half_open_max_calls: int = 3,
        fallback_function: Optional[Callable] = None
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout = timeout
        self.half_open_max_calls = half_open_max_calls
        self.fallback_function = fallback_function
        
        self.state = CircuitState.CLOSED
        self.stats = CircuitStats()
        self._half_open_calls = 0
        self._state_change_time = datetime.now()
        self._lock = asyncio.Lock()
        
        logger.info(f"🔌 Circuit breaker '{name}' initialized with threshold={failure_threshold}, timeout={timeout}")
    
    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)"""
        return self.state == CircuitState.CLOSED
    
    @property
    def is_open(self) -> bool:
        """Check if circuit is open (blocking calls)"""
        if self.state != CircuitState.OPEN:
            return False
        
        # Check if timeout has passed
        if (datetime.now() - self._state_change_time) >= self.timeout:
            self._transition_to_half_open()
            return False
        
        return True
    
    @property
    def is_half_open(self) -> bool:
        """Check if circuit is half-open (testing recovery)"""
        return self.state == CircuitState.HALF_OPEN
    
    def _transition_to_open(self):
        """Transition to open state"""
        self.state = CircuitState.OPEN
        self._state_change_time = datetime.now()
        self.stats.circuit_opened_count += 1
        logger.warning(f"⚡ Circuit breaker '{self.name}' OPENED after {self.stats.consecutive_failures} failures")
    
    def _transition_to_closed(self):
        """Transition to closed state"""
        self.state = CircuitState.CLOSED
        self._state_change_time = datetime.now()
        self.stats.consecutive_failures = 0
        self._half_open_calls = 0
        logger.info(f"✅ Circuit breaker '{self.name}' CLOSED after recovery")
    
    def _transition_to_half_open(self):
        """Transition to half-open state"""
        self.state = CircuitState.HALF_OPEN
        self._state_change_time = datetime.now()
        self._half_open_calls = 0
        logger.info(f"🔄 Circuit breaker '{self.name}' HALF-OPEN, testing recovery")
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function through circuit breaker
        
        Args:
            func: Async function to execute
            *args: Function arguments
            **kwargs: Function keyword arguments
        
        Returns:
            Function result or fallback result
        
        Raises:
            Exception if circuit is open and no fallback provided
        """
        async with self._lock:
            self.stats.total_calls += 1
            
            # Check if circuit is open
            if self.is_open:
                logger.warning(f"🚫 Circuit breaker '{self.name}' is OPEN, rejecting call")
                if self.fallback_function:
                    return await self._execute_fallback(*args, **kwargs)
                raise Exception(f"Circuit breaker '{self.name}' is OPEN")
            
            # Check half-open call limit
            if self.is_half_open and self._half_open_calls >= self.half_open_max_calls:
                logger.warning(f"🚫 Circuit breaker '{self.name}' half-open limit reached")
                if self.fallback_function:
                    return await self._execute_fallback(*args, **kwargs)
                raise Exception(f"Circuit breaker '{self.name}' half-open limit reached")
        
        # Execute the function
        try:
            result = await func(*args, **kwargs)
            await self._record_success()
            return result
        except Exception as e:
            await self._record_failure(e)
            
            # Use fallback if available
            if self.fallback_function:
                logger.info(f"🔄 Using fallback for circuit breaker '{self.name}'")
                return await self._execute_fallback(*args, **kwargs)
            
            raise
    
    async def _execute_fallback(self, *args, **kwargs) -> Any:
        """Execute fallback function"""
        try:
            if asyncio.iscoroutinefunction(self.fallback_function):
                return await self.fallback_function(*args, **kwargs)
            else:
                return self.fallback_function(*args, **kwargs)
        except Exception as e:
            logger.error(f"Fallback function failed for '{self.name}': {e}")
            raise
    
    async def _record_success(self):
        """Record successful call"""
        async with self._lock:
            self.stats.success_count += 1
            self.stats.last_success_time = datetime.now()
            self.stats.consecutive_successes += 1
            self.stats.consecutive_failures = 0
            
            if self.is_half_open:
                self._half_open_calls += 1
                
                # Check if we can close the circuit
                if self.stats.consecutive_successes >= self.success_threshold:
                    self._transition_to_closed()
    
    async def _record_failure(self, error: Exception):
        """Record failed call"""
        async with self._lock:
            self.stats.failure_count += 1
            self.stats.last_failure_time = datetime.now()
            self.stats.consecutive_failures += 1
            self.stats.consecutive_successes = 0
            
            logger.error(f"Circuit breaker '{self.name}' recorded failure: {error}")
            
            if self.is_half_open:
                # Failure in half-open state reopens circuit
                self._transition_to_open()
            elif self.is_closed:
                # Check if we should open the circuit
                if self.stats.consecutive_failures >= self.failure_threshold:
                    self._transition_to_open()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get circuit breaker statistics"""
        return {
            "name": self.name,
            "state": self.state,
            "stats": {
                "failure_count": self.stats.failure_count,
                "success_count": self.stats.success_count,
                "consecutive_failures": self.stats.consecutive_failures,
                "consecutive_successes": self.stats.consecutive_successes,
                "total_calls": self.stats.total_calls,
                "circuit_opened_count": self.stats.circuit_opened_count,
                "last_failure_time": self.stats.last_failure_time.isoformat() if self.stats.last_failure_time else None,
                "last_success_time": self.stats.last_success_time.isoformat() if self.stats.last_success_time else None
            },
            "config": {
                "failure_threshold": self.failure_threshold,
                "success_threshold": self.success_threshold,
                "timeout_seconds": self.timeout.total_seconds(),
                "half_open_max_calls": self.half_open_max_calls
            }
        }
    
    def reset(self):
        """Reset circuit breaker to initial state"""
        self.state = CircuitState.CLOSED
        self.stats = CircuitStats()
        self._half_open_calls = 0
        self._state_change_time = datetime.now()
        logger.info(f"🔄 Circuit breaker '{self.name}' reset")


def circuit_breaker(
    name: Optional[str] = None,
    failure_threshold: int = 5,
    success_threshold: int = 2,
    timeout: timedelta = timedelta(seconds=60),
    fallback_function: Optional[Callable] = None
):
    """
    Decorator to apply circuit breaker to async functions
    
    Usage:
        @circuit_breaker(name="external_api", failure_threshold=3)
        async def call_external_api():
            ...
    """
    def decorator(func):
        breaker_name = name or f"{func.__module__}.{func.__name__}"
        breaker = CircuitBreaker(
            name=breaker_name,
            failure_threshold=failure_threshold,
            success_threshold=success_threshold,
            timeout=timeout,
            fallback_function=fallback_function
        )
        
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await breaker.call(func, *args, **kwargs)
        
        # Attach breaker instance for access
        wrapper.circuit_breaker = breaker
        
        return wrapper
    
    return decorator


# Global circuit breaker registry
_circuit_breakers: Dict[str, CircuitBreaker] = {}


def get_circuit_breaker(name: str) -> Optional[CircuitBreaker]:
    """Get circuit breaker by name from registry"""
    return _circuit_breakers.get(name)


def register_circuit_breaker(breaker: CircuitBreaker):
    """Register circuit breaker in global registry"""
    _circuit_breakers[breaker.name] = breaker
    logger.info(f"Registered circuit breaker: {breaker.name}")


def get_all_circuit_breakers() -> Dict[str, CircuitBreaker]:
    """Get all registered circuit breakers"""
    return _circuit_breakers.copy()


def reset_all_circuit_breakers():
    """Reset all circuit breakers"""
    for breaker in _circuit_breakers.values():
        breaker.reset()
    logger.info(f"Reset {len(_circuit_breakers)} circuit breakers")