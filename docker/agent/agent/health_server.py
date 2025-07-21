import asyncio
import json
from aiohttp import web
from datetime import datetime
import logging
from prometheus_client import Counter, Histogram, Gauge, generate_latest

logger = logging.getLogger(__name__)

# Prometheus metrics
health_checks = Counter('agent_health_checks_total', 'Total health check requests')
active_sessions = Gauge('agent_active_sessions', 'Number of active sessions')
session_duration = Histogram('agent_session_duration_seconds', 'Session duration in seconds')

class HealthServer:
    """HTTP server for health checks and metrics"""
    
    def __init__(self, config):
        self.config = config
        self.app = web.Application()
        self.runner = None
        self.site = None
        self.start_time = datetime.utcnow()
        
        # Set up routes
        self.app.router.add_get('/health', self.health_check)
        self.app.router.add_get('/metrics', self.metrics)
        self.app.router.add_get('/info', self.info)
    
    async def start(self):
        """Start the health check server"""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, '0.0.0.0', 8080)
        await self.site.start()
        logger.info("Health check server started on port 8080")
    
    def stop(self):
        """Stop the health check server"""
        if self.runner:
            asyncio.create_task(self.runner.cleanup())
    
    async def health_check(self, request):
        """Basic health check endpoint"""
        health_checks.inc()
        
        return web.json_response({
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "uptime_seconds": (datetime.utcnow() - self.start_time).total_seconds()
        })
    
    async def metrics(self, request):
        """Prometheus metrics endpoint"""
        if not self.config.enable_metrics:
            return web.Response(status=404)
        
        metrics_data = generate_latest()
        return web.Response(
            body=metrics_data,
            content_type='text/plain; version=0.0.4'
        )
    
    async def info(self, request):
        """Agent information endpoint"""
        return web.json_response({
            "container_name": self.config.container_name,
            "site_id": self.config.site_id,
            "site_domain": self.config.site_domain,
            "agent_slug": self.config.agent_slug,
            "agent_name": self.config.agent_name,
            "model": self.config.model,
            "voice_id": self.config.voice_id,
            "stt_provider": self.config.stt_provider,
            "tts_provider": self.config.tts_provider,
            "uptime_seconds": (datetime.utcnow() - self.start_time).total_seconds(),
            "start_time": self.start_time.isoformat()
        })