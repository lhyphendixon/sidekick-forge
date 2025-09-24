from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional

from app.core.dependencies import get_client_service
from app.models.tools import ToolAssignmentRequest, ToolCreate, ToolOut, ToolUpdate
from app.services.client_service_supabase import ClientService
from app.services.tools_service_supabase import ToolsService

router = APIRouter(tags=["tools"])


def get_tools_service(client_service: ClientService = Depends(get_client_service)) -> ToolsService:
    return ToolsService(client_service)


@router.get("/tools", response_model=List[ToolOut])
async def list_tools(
    client_id: Optional[str] = Query(None),
    scope: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    svc: ToolsService = Depends(get_tools_service),
) -> List[ToolOut]:
    if scope == "client" and not client_id:
        raise HTTPException(status_code=400, detail="client_id is required when scope=client")
    try:
        return await svc.list_tools(client_id=client_id, scope=scope, type=type, search=search)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tools", response_model=ToolOut)
async def create_tool(payload: ToolCreate, svc: ToolsService = Depends(get_tools_service)) -> ToolOut:
    if payload.scope == "client" and not payload.client_id:
        raise HTTPException(status_code=400, detail="client_id is required for client-scoped tools")
    try:
        return await svc.create_tool(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/tools/{tool_id}", response_model=ToolOut)
async def update_tool(
    tool_id: str,
    payload: ToolUpdate,
    client_id: Optional[str] = Query(None),
    svc: ToolsService = Depends(get_tools_service),
) -> ToolOut:
    try:
        return await svc.update_tool(client_id, tool_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/tools/{tool_id}")
async def delete_tool(
    tool_id: str,
    client_id: Optional[str] = Query(None),
    svc: ToolsService = Depends(get_tools_service),
) -> dict:
    try:
        await svc.delete_tool(client_id, tool_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True}


@router.get("/agents/{agent_id}/tools", response_model=List[ToolOut])
async def list_agent_tools(
    agent_id: str,
    client_id: str = Query(...),
    svc: ToolsService = Depends(get_tools_service),
) -> List[ToolOut]:
    try:
        return await svc.list_agent_tools(client_id, agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/agents/{agent_id}/tools")
async def set_agent_tools(
    agent_id: str,
    payload: ToolAssignmentRequest,
    client_id: str = Query(...),
    svc: ToolsService = Depends(get_tools_service),
) -> dict:
    try:
        await svc.set_agent_tools(client_id, agent_id, payload.tool_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True}
