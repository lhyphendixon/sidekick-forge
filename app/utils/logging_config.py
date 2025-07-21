"""
Structured logging configuration for Autonomite SaaS platform
"""
import logging
import json
import sys
from datetime import datetime
from typing import Any, Dict

class JSONFormatter(logging.Formatter):
    """Custom JSON formatter for structured logging"""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON"""
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add any extra fields from the record
        if hasattr(record, "client_id"):
            log_data["client_id"] = record.client_id
        if hasattr(record, "agent_slug"):
            log_data["agent_slug"] = record.agent_slug
        if hasattr(record, "room_name"):
            log_data["room_name"] = record.room_name
        if hasattr(record, "user_id"):
            log_data["user_id"] = record.user_id
        if hasattr(record, "container_id"):
            log_data["container_id"] = record.container_id
        if hasattr(record, "request_id"):
            log_data["request_id"] = record.request_id
            
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
            
        return json.dumps(log_data)


class ContextLogger:
    """Logger wrapper that includes context in all log messages"""
    
    def __init__(self, logger: logging.Logger, context: Dict[str, Any]):
        self.logger = logger
        self.context = context
        
    def _log(self, level: int, msg: str, *args, **kwargs):
        """Internal log method that adds context"""
        extra = kwargs.get("extra", {})
        extra.update(self.context)
        kwargs["extra"] = extra
        self.logger.log(level, msg, *args, **kwargs)
        
    def debug(self, msg: str, *args, **kwargs):
        self._log(logging.DEBUG, msg, *args, **kwargs)
        
    def info(self, msg: str, *args, **kwargs):
        self._log(logging.INFO, msg, *args, **kwargs)
        
    def warning(self, msg: str, *args, **kwargs):
        self._log(logging.WARNING, msg, *args, **kwargs)
        
    def error(self, msg: str, *args, **kwargs):
        self._log(logging.ERROR, msg, *args, **kwargs)
        
    def critical(self, msg: str, *args, **kwargs):
        self._log(logging.CRITICAL, msg, *args, **kwargs)


def setup_logging(log_level: str = "INFO", json_logs: bool = True):
    """
    Configure structured logging for the application
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_logs: Whether to use JSON formatting
    """
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    
    if json_logs:
        # Use JSON formatter
        formatter = JSONFormatter()
    else:
        # Use standard formatter for development
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
    
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # Set levels for specific loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    
    
def get_context_logger(name: str, **context) -> ContextLogger:
    """
    Get a logger with automatic context injection
    
    Args:
        name: Logger name
        **context: Context fields to include in all logs
        
    Returns:
        ContextLogger instance
    """
    logger = logging.getLogger(name)
    return ContextLogger(logger, context)