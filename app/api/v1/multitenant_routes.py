"""
Multi-tenant routes to be included in the main app

This module exports all multi-tenant routers for easy integration.
"""
from . import trigger_multitenant
from . import agents_multitenant  
from . import clients_multitenant

# Export routers
trigger_router = trigger_multitenant.router
agents_router = agents_multitenant.router
clients_router = clients_multitenant.router

__all__ = ['trigger_router', 'agents_router', 'clients_router']