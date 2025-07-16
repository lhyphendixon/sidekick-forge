"""
LiveKit Worker Management API
"""
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workers"])


class StartWorkerRequest(BaseModel):
    """Request model for starting a worker"""
    worker_id: Optional[str] = Field(None, description="Custom worker ID (auto-generated if not provided)")
    client_id: str = Field("backend", description="Client ID for this worker")
    agent_label: str = Field("clarence-coherence", description="Agent label/name")


class WorkerResponse(BaseModel):
    """Response model for worker operations"""
    success: bool
    worker_id: str
    status: str
    message: str
    data: Optional[Dict[str, Any]] = None


@router.post("/start", response_model=WorkerResponse)
async def start_worker(request: StartWorkerRequest) -> WorkerResponse:
    """Start a new LiveKit worker"""
    try:
        from app.services.livekit_worker import worker_manager
        from app.integrations.livekit_client import livekit_manager
        import time
        
        # Generate worker ID if not provided
        worker_id = request.worker_id or f"worker-{int(time.time())}"
        
        logger.info(f"Starting worker {worker_id} for client {request.client_id}")
        
        result = await worker_manager.start_worker(
            worker_id=worker_id,
            livekit_url=livekit_manager.url,
            livekit_api_key=livekit_manager.api_key,
            livekit_api_secret=livekit_manager.api_secret,
            client_id=request.client_id,
            agent_label=request.agent_label
        )
        
        return WorkerResponse(
            success=result['status'] in ['running', 'starting'],
            worker_id=worker_id,
            status=result['status'],
            message=f"Worker {worker_id} {result['status']}",
            data=result
        )
        
    except Exception as e:
        logger.error(f"Error starting worker: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{worker_id}", response_model=WorkerResponse)
async def stop_worker(worker_id: str) -> WorkerResponse:
    """Stop a specific worker"""
    try:
        from app.services.livekit_worker import worker_manager
        
        logger.info(f"Stopping worker {worker_id}")
        
        success = await worker_manager.stop_worker(worker_id)
        
        return WorkerResponse(
            success=success,
            worker_id=worker_id,
            status="stopped" if success else "error",
            message=f"Worker {worker_id} {'stopped' if success else 'failed to stop'}"
        )
        
    except Exception as e:
        logger.error(f"Error stopping worker {worker_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/", response_model=Dict[str, Any])
async def list_workers():
    """List all workers with their status"""
    try:
        from app.services.livekit_worker import worker_manager
        
        workers = worker_manager.list_workers()
        
        return {
            "success": True,
            "workers": workers,
            "count": len(workers),
            "active_count": len([w for w in workers.values() if w and w['status'] == 'running'])
        }
        
    except Exception as e:
        logger.error(f"Error listing workers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{worker_id}", response_model=Dict[str, Any])
async def get_worker_status(worker_id: str):
    """Get detailed status of a specific worker"""
    try:
        from app.services.livekit_worker import worker_manager
        
        status = worker_manager.get_worker_status(worker_id)
        
        if not status:
            raise HTTPException(status_code=404, detail=f"Worker {worker_id} not found")
        
        # Read recent log entries
        log_lines = []
        try:
            with open(status['log_file'], 'r') as f:
                log_lines = f.readlines()[-20:]  # Last 20 lines
        except:
            log_lines = ["Log file not available"]
        
        return {
            "success": True,
            "worker": status,
            "recent_logs": log_lines
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting worker status {worker_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/", response_model=Dict[str, Any])
async def stop_all_workers():
    """Stop all workers"""
    try:
        from app.services.livekit_worker import worker_manager
        
        logger.info("Stopping all workers")
        
        await worker_manager.stop_all_workers()
        
        return {
            "success": True,
            "message": "All workers stopped"
        }
        
    except Exception as e:
        logger.error(f"Error stopping all workers: {e}")
        raise HTTPException(status_code=500, detail=str(e))