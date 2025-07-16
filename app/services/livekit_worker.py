#!/usr/bin/env python3
"""
Backend LiveKit Worker for Thin Client Architecture
Spawns persistent workers that accept room jobs
"""

import asyncio
import os
import logging
import time
import json
import subprocess
import tempfile
from typing import Dict, Any, Optional
from pathlib import Path
import signal
import sys

logger = logging.getLogger(__name__)

class LiveKitWorkerManager:
    """Manages persistent LiveKit workers for the backend"""
    
    def __init__(self):
        self.workers = {}  # worker_id -> process info
        self.agent_path = "/root/wordpress-plugin/autonomite-agent/livekit-agents"
        self.agent_script = "autonomite_agent_v1_1_19_text_support.py"
        
    async def start_worker(self, 
                          worker_id: str,
                          livekit_url: str,
                          livekit_api_key: str, 
                          livekit_api_secret: str,
                          client_id: str = "backend",
                          agent_label: str = "clarence-coherence") -> Dict[str, Any]:
        """Start a persistent LiveKit worker"""
        
        if worker_id in self.workers:
            logger.warning(f"Worker {worker_id} already exists")
            return self.workers[worker_id]
            
        try:
            # Set up environment for the worker
            env = os.environ.copy()
            env.update({
                'LIVEKIT_URL': livekit_url,
                'LIVEKIT_API_KEY': livekit_api_key,
                'LIVEKIT_API_SECRET': livekit_api_secret,
                'AUTONOMITE_AGENT_LABEL': agent_label,
                'CLIENT_ID': client_id,
                # Backend workers use hardcoded API keys for now
                # TODO: Load from Supabase once authentication is fixed
                'GROQ_API_KEY': 'dummy',  # Will be loaded from metadata
                'DEEPGRAM_API_KEY': 'dummy',
                'ELEVENLABS_API_KEY': 'dummy', 
                'CARTESIA_API_KEY': 'dummy'
            })
            
            # Create worker script that accepts all jobs
            worker_script = self._create_backend_worker_script()
            
            # Start the worker process
            cmd = [
                f"{self.agent_path}/agent_env/bin/python",
                worker_script,
                "dev"
            ]
            
            logger.info(f"Starting worker {worker_id} with command: {' '.join(cmd)}")
            
            # Create log file for this worker
            log_file = f"/tmp/worker_{worker_id}.log"
            
            with open(log_file, 'w') as f:
                process = subprocess.Popen(
                    cmd,
                    env=env,
                    cwd=self.agent_path,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    preexec_fn=os.setsid  # Create new process group
                )
            
            # Store worker info
            worker_info = {
                'worker_id': worker_id,
                'process': process,
                'pid': process.pid,
                'log_file': log_file,
                'started_at': time.time(),
                'client_id': client_id,
                'agent_label': agent_label,
                'status': 'starting'
            }
            
            self.workers[worker_id] = worker_info
            
            # Wait a moment to see if it started successfully
            await asyncio.sleep(3)
            
            if process.poll() is None:
                worker_info['status'] = 'running'
                logger.info(f"✅ Worker {worker_id} started successfully (PID: {process.pid})")
            else:
                worker_info['status'] = 'failed'
                logger.error(f"❌ Worker {worker_id} failed to start")
                
            return worker_info
            
        except Exception as e:
            logger.error(f"Failed to start worker {worker_id}: {e}")
            return {
                'worker_id': worker_id,
                'status': 'error',
                'error': str(e)
            }
    
    def _create_backend_worker_script(self) -> str:
        """Create a backend worker script that accepts all jobs"""
        
        script_content = '''#!/usr/bin/env python3
"""
Backend LiveKit Worker - Accepts all room jobs for thin client architecture
"""

import asyncio
import os
import logging
import time
import json
from typing import Dict, Any

# LiveKit imports
from livekit.agents import JobContext, JobRequest, WorkerOptions, WorkerType, cli
from livekit import rtc

# Set up logging
logging.basicConfig(level=logging.INFO, force=True)
logger = logging.getLogger("backend-worker")

async def request_filter(req: JobRequest) -> None:
    """Accept all room jobs - this is a backend worker for thin client architecture"""
    try:
        room_name = req.room.name
        logger.info(f"🔍 Backend worker received job request for room: {room_name}")
        
        # Extract metadata if available
        metadata = {}
        if hasattr(req.room, 'metadata') and req.room.metadata:
            try:
                metadata = json.loads(req.room.metadata)
                logger.info(f"📋 Room metadata: {metadata}")
            except:
                pass
        
        # Backend workers accept ALL jobs - no filtering needed for thin client
        identity = f"backend-agent-{int(time.time())}"
        await req.accept(
            name="clarence-coherence", 
            identity=identity,
            attributes={
                "agent_type": "backend",
                "version": "thin-client",
                "worker_type": "persistent"
            }
        )
        
        logger.info(f"✅ Backend worker accepted job for room {room_name} with identity {identity}")
        
    except Exception as e:
        logger.error(f"❌ Error in request filter: {e}")
        try:
            await req.reject()
        except:
            pass

async def entrypoint(ctx: JobContext) -> None:
    """Main entry point for accepted jobs"""
    try:
        room_name = ctx.room.name
        logger.info(f"🚀 Backend worker starting job for room: {room_name}")
        
        # For now, import and run the full agent
        # TODO: Create lightweight backend version
        from autonomite_agent_v1_1_19_text_support import entrypoint as agent_entrypoint
        await agent_entrypoint(ctx)
        
    except Exception as e:
        logger.error(f"❌ Error in entrypoint: {e}")

if __name__ == "__main__":
    logger.info("🚀 Starting Backend LiveKit Worker...")
    logger.info(f"LiveKit URL: {os.getenv('LIVEKIT_URL')}")
    logger.info(f"Agent Label: {os.getenv('AUTONOMITE_AGENT_LABEL', 'clarence-coherence')}")
    
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            request_fnc=request_filter,
            agent_name="backend-clarence",
            worker_type=WorkerType.ROOM,
        )
    )
'''
        
        # Write to temporary file
        script_path = "/tmp/backend_worker.py"
        with open(script_path, 'w') as f:
            f.write(script_content)
        
        # Make executable
        os.chmod(script_path, 0o755)
        
        return script_path
    
    async def stop_worker(self, worker_id: str) -> bool:
        """Stop a specific worker"""
        if worker_id not in self.workers:
            logger.warning(f"Worker {worker_id} not found")
            return False
            
        try:
            worker = self.workers[worker_id]
            process = worker['process']
            
            # Try graceful shutdown first
            process.terminate()
            
            # Wait up to 5 seconds for graceful shutdown
            try:
                process.wait(timeout=5)
                logger.info(f"Worker {worker_id} terminated gracefully")
            except subprocess.TimeoutExpired:
                # Force kill if still running
                process.kill()
                logger.warning(f"Worker {worker_id} force killed")
            
            # Clean up
            del self.workers[worker_id]
            return True
            
        except Exception as e:
            logger.error(f"Error stopping worker {worker_id}: {e}")
            return False
    
    async def stop_all_workers(self):
        """Stop all workers"""
        worker_ids = list(self.workers.keys())
        for worker_id in worker_ids:
            await self.stop_worker(worker_id)
    
    def get_worker_status(self, worker_id: str) -> Optional[Dict[str, Any]]:
        """Get status of a specific worker"""
        if worker_id not in self.workers:
            return None
            
        worker = self.workers[worker_id]
        process = worker['process']
        
        status = {
            'worker_id': worker_id,
            'pid': worker['pid'],
            'started_at': worker['started_at'],
            'uptime': time.time() - worker['started_at'],
            'client_id': worker['client_id'],
            'agent_label': worker['agent_label'],
            'log_file': worker['log_file'],
            'status': 'running' if process.poll() is None else 'stopped'
        }
        
        return status
    
    def list_workers(self) -> Dict[str, Any]:
        """List all workers with their status"""
        workers = {}
        for worker_id in self.workers:
            workers[worker_id] = self.get_worker_status(worker_id)
        return workers

# Global worker manager instance
worker_manager = LiveKitWorkerManager()