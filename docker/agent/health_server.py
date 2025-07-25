#!/usr/bin/env python3
"""
Simple health check server for the agent container
"""

import asyncio
import logging
from aiohttp import web
from datetime import datetime

logger = logging.getLogger(__name__)


class HealthServer:
    def __init__(self, port: int = 8080):
        self.port = port
        self.app = web.Application()
        self.runner = None
        self.start_time = datetime.now()
        self.is_healthy = True
        self.worker_registered = False
        
        # Set up routes
        self.app.router.add_get('/health', self.health_check)
        self.app.router.add_get('/ready', self.ready_check)
        
    async def health_check(self, request):
        """Basic health check endpoint"""
        return web.json_response({
            'status': 'healthy' if self.is_healthy else 'unhealthy',
            'timestamp': datetime.now().isoformat(),
            'uptime_seconds': (datetime.now() - self.start_time).total_seconds()
        })
    
    async def ready_check(self, request):
        """Readiness check - returns 200 only if worker is registered"""
        if self.worker_registered:
            return web.json_response({
                'status': 'ready',
                'worker_registered': True
            })
        else:
            return web.json_response({
                'status': 'not_ready',
                'worker_registered': False
            }, status=503)
    
    def set_worker_registered(self, registered: bool):
        """Update worker registration status"""
        self.worker_registered = registered
        logger.info(f"Worker registration status updated: {registered}")
    
    async def start(self):
        """Start the health check server"""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '0.0.0.0', self.port)
        await site.start()
        logger.info(f"Health server started on port {self.port}")
        
        # Keep the server running
        while True:
            await asyncio.sleep(3600)  # Sleep for an hour
    
    async def stop(self):
        """Stop the health check server"""
        if self.runner:
            await self.runner.cleanup()
            logger.info("Health server stopped")