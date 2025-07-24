import asyncio
import logging
from datetime import datetime
from typing import Optional

from app.services.container_manager import container_manager

logger = logging.getLogger(__name__)

class BackgroundTaskManager:
    """Manages background tasks for the application"""
    
    def __init__(self):
        self.tasks = []
        self.running = False
        self.health_check_interval = 300  # 5 minutes
        self.scale_check_interval = 600  # 10 minutes
    
    async def start(self):
        """Start all background tasks"""
        if self.running:
            return
        
        self.running = True
        logger.info("🚀 Starting background tasks...")
        
        # Start container pool health checks
        self.tasks.append(
            asyncio.create_task(self._run_periodic_health_checks())
        )
        
        # Start scale-to-zero checks
        self.tasks.append(
            asyncio.create_task(self._run_periodic_scale_checks())
        )
        
        logger.info("✅ Background tasks started")
    
    async def stop(self):
        """Stop all background tasks"""
        self.running = False
        
        # Cancel all tasks
        for task in self.tasks:
            task.cancel()
        
        # Wait for tasks to complete
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        
        logger.info("🛑 Background tasks stopped")
    
    async def _run_periodic_health_checks(self):
        """Periodically check health of container pools"""
        while self.running:
            try:
                # Wait first, then check (allows clean startup)
                await asyncio.sleep(self.health_check_interval)
                
                if not self.running:
                    break
                
                logger.info("🏥 Running periodic container health check...")
                results = await container_manager.health_check_pool()
                
                if results.get("removed"):
                    logger.warning(f"⚠️ Removed unhealthy containers: {results['removed']}")
                    
            except Exception as e:
                logger.error(f"Error in periodic health check: {e}")
    
    async def _run_periodic_scale_checks(self):
        """Periodically check for idle containers to scale down"""
        while self.running:
            try:
                # Wait first, then check (allows clean startup)
                await asyncio.sleep(self.scale_check_interval)
                
                if not self.running:
                    break
                
                logger.info("📉 Running periodic scale-to-zero check...")
                results = await container_manager.scale_to_zero_check()
                
                if results.get("scaled_down", 0) > 0:
                    logger.info(f"♻️ Scaled down {results['scaled_down']} idle containers")
                    
            except Exception as e:
                logger.error(f"Error in periodic scale check: {e}")

# Create singleton instance
background_task_manager = BackgroundTaskManager()