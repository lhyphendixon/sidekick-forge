from __future__ import annotations

import time
from typing import List, Optional, Dict, Any
from supabase import create_client, Client as SupabaseClient
from app.models.tools import ToolCreate, ToolUpdate, ToolOut
from app.services.client_service_supabase import ClientService


class ToolsService:
    def __init__(self, client_service: ClientService) -> None:
        self.client_service = client_service

    async def get_client_supabase(self, client_id: Optional[str]) -> SupabaseClient:
        # Global tools live in the platform DB
        if not client_id:
            return self.client_service.supabase
        # Client-scoped
        sb = await self.client_service.get_client_supabase_client(client_id, auto_sync=False)
        return sb

    async def list_tools(
        self,
        client_id: Optional[str] = None,
        scope: Optional[str] = None,
        type: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[ToolOut]:
        # Global + client-scoped union from appropriate DB
        platform_sb = self.client_service.supabase
        parts: List[Dict[str, Any]] = []
        # Global
        q = platform_sb.table("tools").select("*")
        if scope:
            q = q.eq("scope", scope)
        else:
            q = q.in_("scope", ["global", "client"])  # allow both for platform view
        if type:
            q = q.eq("type", type)
        if search:
            q = q.ilike("name", f"%{search}%")
        res = q.execute()
        parts.extend(res.data or [])
        # Client tools (if client_id provided)
        if client_id:
            csb = await self.get_client_supabase(client_id)
            cq = csb.table("tools").select("*")
            cq = cq.eq("scope", "client")
            if type:
                cq = cq.eq("type", type)
            if search:
                cq = cq.ilike("name", f"%{search}%")
            cres = cq.execute()
            parts.extend(cres.data or [])
        return [ToolOut(**p) for p in parts]

    async def create_tool(self, payload: ToolCreate) -> ToolOut:
        sb = await self.get_client_supabase(payload.client_id if payload.scope == "client" else None)
        row = payload.dict()
        res = sb.table("tools").insert(row).execute()
        return ToolOut(**res.data[0])

    async def update_tool(self, client_id: Optional[str], tool_id: str, payload: ToolUpdate) -> ToolOut:
        # Determine DB based on row location: if client_id provided, prefer client DB else platform
        sb = await self.get_client_supabase(client_id)
        update_dict = {k: v for k, v in payload.dict().items() if v is not None}
        res = sb.table("tools").update(update_dict).eq("id", tool_id).execute()
        return ToolOut(**res.data[0])

    async def delete_tool(self, client_id: Optional[str], tool_id: str) -> None:
        sb = await self.get_client_supabase(client_id)
        sb.table("tools").delete().eq("id", tool_id).execute()

    async def list_agent_tools(self, client_id: str, agent_id: str) -> List[ToolOut]:
        # agent_tools is stored in platform DB referencing tool ids across scopes
        sb = self.client_service.supabase
        at = sb.table("agent_tools").select("tool_id").eq("agent_id", agent_id).execute()
        tool_ids = [r["tool_id"] for r in (at.data or [])]
        if not tool_ids:
            return []
        # Fetch from both platform and client DBs
        tools: Dict[str, Dict[str, Any]] = {}
        pr = sb.table("tools").select("*").in_("id", tool_ids).execute()
        for r in pr.data or []:
            tools[r["id"]] = r
        csb = await self.get_client_supabase(client_id)
        cr = csb.table("tools").select("*").in_("id", tool_ids).execute()
        for r in cr.data or []:
            tools[r["id"]] = r
        return [ToolOut(**r) for r in tools.values()]

    async def set_agent_tools(self, client_id: str, agent_id: str, tool_ids: List[str]) -> None:
        sb = self.client_service.supabase
        # Replace assignments atomically-ish
        sb.table("agent_tools").delete().eq("agent_id", agent_id).execute()
        rows = [{"agent_id": agent_id, "tool_id": tid} for tid in tool_ids]
        if rows:
            sb.table("agent_tools").insert(rows).execute()


