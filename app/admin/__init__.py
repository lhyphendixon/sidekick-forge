"""
Admin dashboard module for Autonomite SaaS

This module provides the admin interface for managing clients,
containers, and system monitoring.
"""

from .routes import router

__all__ = ["router"]