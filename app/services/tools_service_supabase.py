from __future__ import annotations

from typing import List, Optional, Dict, Any, Tuple
from supabase import Client as SupabaseClient
from app.models.tools import ToolCreate, ToolUpdate, ToolOut
from app.services.client_service_supabase import ClientService
from app.services.perplexity_mcp_manager import get_perplexity_mcp_manager


class ToolsService:
    def __init__(self, client_service: ClientService) -> None:
        self.client_service = client_service

    def _ensure_default_global_tools(self) -> None:
        """Seed built-in global abilities (non-destructive)."""
        platform_sb = self.client_service.supabase
        if not self._table_exists(platform_sb, "tools"):
            return

        default_tools = [
            {
                "name": "Asana Task Manager",
                "slug": "asana_tasks",
                "description": "Interact with Asana projects to read and manage tasks using OAuth.",
                "type": "asana",
                "scope": "global",
                "client_id": None,
                "icon_url": "/static/images/ability-default.png",
                "config": {
                    "projects": [],
                    "workspace_gid": "",
                    "default_action": "list",
                    "list_include_completed": False,
                    "oauth_provider": "asana",
                },
                "enabled": False,
            },
            {
                "name": "HelpScout Connect",
                "slug": "helpscout_tickets",
                "description": "Query and manage HelpScout support conversations, reply to customers, add notes, and update ticket status.",
                "type": "helpscout",
                "scope": "global",
                "client_id": None,
                "icon_url": "/static/images/ability-default.png",
                "config": {
                    "mailboxes": [],
                    "default_mailbox_id": None,
                    "default_action": "list",
                    "max_results": 10,
                    "oauth_provider": "helpscout",
                },
                "enabled": False,
            },
        ]

        try:
            slugs = [entry["slug"] for entry in default_tools]
            existing = platform_sb.table("tools").select("slug").in_("slug", slugs).execute()
            existing_slugs = {row["slug"] for row in (existing.data or [])}
            for entry in default_tools:
                if entry["slug"] not in existing_slugs:
                    platform_sb.table("tools").insert(entry).execute()
        except Exception:
            # Seed failures should not block the admin UI; log and continue.
            return

    async def get_client_supabase(self, client_id: Optional[str]) -> SupabaseClient:
        if not client_id:
            return self.client_service.supabase
        return await self.client_service.get_client_supabase_client(client_id, auto_sync=False)

    # Global icon mapping for built-in abilities (by slug)
    BUILTIN_ICONS: Dict[str, str] = {
        "perplexity_ask": "/static/images/abilities/web-search.svg",
        "web_search": "/static/images/abilities/web-search.svg",
        "usersense": "/static/images/abilities/usersense.svg",
        "documentsense": "/static/images/abilities/documentsense.svg",
        "content_catalyst": "/static/images/abilities/content-catalyst.svg",
        "crypto_price_check": "/static/images/abilities/crypto-price.svg",
        "asana_tasks": "/static/images/abilities/asana.svg",
        "helpscout_tickets": "/static/images/abilities/helpscout.svg",
        "lingua": "/static/images/abilities/lingua.svg",
    }

    @staticmethod
    def _normalize_tool_row(row: Dict[str, Any], client_id: Optional[str] = None) -> Dict[str, Any]:
        data = dict(row)
        scope = data.get("scope") or ("client" if data.get("client_id") else "global")
        data["scope"] = scope
        if scope == "client" and not data.get("client_id"):
            data["client_id"] = client_id
        # Apply global icon for known built-in abilities
        slug = data.get("slug", "")
        if slug in ToolsService.BUILTIN_ICONS:
            data["icon_url"] = ToolsService.BUILTIN_ICONS[slug]
        return data

    @staticmethod
    def _table_exists(sb: SupabaseClient, table_name: str) -> bool:
        try:
            sb.table(table_name).select("id").limit(1).execute()
            return True
        except Exception:
            return False

    async def _find_tool_record(
        self,
        tool_id: str,
        client_id: Optional[str] = None,
    ) -> Tuple[Optional[SupabaseClient], Optional[Dict[str, Any]], Optional[str], Optional[str]]:
        platform_sb = self.client_service.supabase
        if self._table_exists(platform_sb, "tools"):
            pres = platform_sb.table("tools").select("*").eq("id", tool_id).limit(1).execute()
            if pres.data:
                row = self._normalize_tool_row(pres.data[0])
                row_client_id = row.get("client_id")
                if row["scope"] == "client":
                    if row_client_id and client_id and row_client_id != client_id:
                        raise ValueError("Tool does not belong to requested client")
                    if not client_id:
                        client_id = row_client_id
                return platform_sb, row, row["scope"], client_id

        if client_id:
            csb = await self.get_client_supabase(client_id)
            if self._table_exists(csb, "tools"):
                cres = csb.table("tools").select("*").eq("id", tool_id).limit(1).execute()
                if cres.data:
                    row = self._normalize_tool_row(cres.data[0], client_id)
                    return csb, row, row["scope"], client_id

        return None, None, None, client_id

    async def _platform_table_exists(self, table_name: str) -> bool:
        return self._table_exists(self.client_service.supabase, table_name)

    async def list_tools(
        self,
        client_id: Optional[str] = None,
        scope: Optional[str] = None,
        type: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[ToolOut]:
        platform_sb = self.client_service.supabase
        results: Dict[str, Dict[str, Any]] = {}

        def apply_filters(query):
            if type:
                query = query.eq("type", type)
            if search:
                query = query.ilike("name", f"%{search}%")
            return query

        platform_has_table = await self._platform_table_exists("tools")

        if scope in (None, "global") and platform_has_table:
            self._ensure_default_global_tools()
            q = platform_sb.table("tools").select("*").eq("scope", "global")
            q = apply_filters(q)
            res = q.execute()
            for row in res.data or []:
                normalized = self._normalize_tool_row(row)
                results[normalized["id"]] = normalized

        if scope in (None, "client") and client_id:
            # Include any platform-stored client tools for this tenant
            if platform_has_table:
                qp = platform_sb.table("tools").select("*").eq("scope", "client").eq("client_id", client_id)
                qp = apply_filters(qp)
                platform_client = qp.execute()
                for row in platform_client.data or []:
                    normalized = self._normalize_tool_row(row, client_id)
                    results[normalized["id"]] = normalized

            csb = await self.get_client_supabase(client_id)
            try:
                if self._table_exists(csb, "tools"):
                    qc = csb.table("tools").select("*")
                    qc = apply_filters(qc)
                    client_rows = qc.execute()
                    for row in client_rows.data or []:
                        normalized = self._normalize_tool_row(row, client_id)
                        results[normalized["id"]] = normalized
            except Exception:
                pass

        if scope == "client" and not client_id:
            return []

        return [ToolOut(**row) for row in results.values()]

    async def create_tool(self, payload: ToolCreate) -> ToolOut:
        if payload.scope == "client" and not payload.client_id:
            raise ValueError("client_id is required for client-scoped tools")
        target_client_id = payload.client_id if payload.scope == "client" else None
        sb = await self.get_client_supabase(target_client_id)
        if payload.scope == "client":
            if not self._table_exists(sb, "tools"):
                raise ValueError("Client tools table is not provisioned for this tenant.")
        else:
            if not await self._platform_table_exists("tools"):
                raise ValueError("Global tools are not available in this environment.")
        row = payload.dict()
        if payload.scope != "client":
            row["client_id"] = None
        insert_row = dict(row)
        if payload.scope == "client" and sb is not self.client_service.supabase:
            insert_row.pop("client_id", None)
            insert_row.pop("scope", None)
        res = sb.table("tools").insert(insert_row).execute()
        data = self._normalize_tool_row(res.data[0], target_client_id)
        return ToolOut(**data)

    async def update_tool(self, client_id: Optional[str], tool_id: str, payload: ToolUpdate) -> ToolOut:
        target_sb, existing, scope, resolved_client_id = await self._find_tool_record(tool_id, client_id)
        if not existing or not target_sb:
            raise ValueError("Tool not found")
        if not self._table_exists(target_sb, "tools"):
            raise ValueError("Tools table is not available for this operation.")
        if scope == "client" and not resolved_client_id:
            raise ValueError("client_id is required to update client-scoped tools")

        update_dict = {k: v for k, v in payload.dict().items() if v is not None}
        if scope != "client":
            update_dict.pop("scope", None)
            update_dict.pop("client_id", None)
        else:
            if target_sb is not self.client_service.supabase:
                update_dict.pop("scope", None)
                update_dict.pop("client_id", None)
        res = target_sb.table("tools").update(update_dict).eq("id", tool_id).execute()
        updated_rows = res.data or []
        row = updated_rows[0] if updated_rows else {**existing, **update_dict}
        normalized = self._normalize_tool_row(row, resolved_client_id)
        return ToolOut(**normalized)

    async def delete_tool(self, client_id: Optional[str], tool_id: str) -> None:
        target_sb, existing, scope, resolved_client_id = await self._find_tool_record(tool_id, client_id)
        if not existing or not target_sb:
            raise ValueError("Tool not found")
        if scope == "client" and not resolved_client_id:
            raise ValueError("client_id is required to delete client-scoped tools")
        if not self._table_exists(target_sb, "tools"):
            raise ValueError("Tools table is not available for this operation.")
        target_sb.table("tools").delete().eq("id", tool_id).execute()

    async def list_agent_tools(self, client_id: str, agent_id: str) -> List[ToolOut]:
        sb = self.client_service.supabase
        at = sb.table("agent_tools").select("tool_id").eq("agent_id", agent_id).execute()
        tool_ids = [r["tool_id"] for r in (at.data or [])]
        if not tool_ids:
            return []

        tools: Dict[str, Dict[str, Any]] = {}
        if await self._platform_table_exists("tools"):
            pr = sb.table("tools").select("*").in_("id", tool_ids).execute()
            for r in pr.data or []:
                normalized = self._normalize_tool_row(r)
                if normalized["scope"] == "client" and normalized.get("client_id") not in (None, client_id):
                    continue
                tools[normalized["id"]] = normalized

        csb = await self.get_client_supabase(client_id)
        try:
            if self._table_exists(csb, "tools"):
                cr = csb.table("tools").select("*").in_("id", tool_ids).execute()
                for r in cr.data or []:
                    normalized = self._normalize_tool_row(r, client_id)
                    tools[normalized["id"]] = normalized
        except Exception:
            pass

        tool_models: List[ToolOut] = []
        for row in tools.values():
            tool = ToolOut(**row)
            tool_models.append(await self._augment_tool_for_agent(tool, client_id))

        return tool_models

    async def set_agent_tools(self, client_id: str, agent_id: str, tool_ids: List[str]) -> None:
        platform_sb = self.client_service.supabase
        if tool_ids:
            allowed: Dict[str, Dict[str, Any]] = {}
            if await self._platform_table_exists("tools"):
                pr = platform_sb.table("tools").select("id", "scope", "client_id").in_("id", tool_ids).execute()
                allowed = {row["id"]: row for row in pr.data or []}
            missing = [tid for tid in tool_ids if tid not in allowed]
            if missing:
                csb = await self.get_client_supabase(client_id)
                if self._table_exists(csb, "tools"):
                    cr = csb.table("tools").select("id", "scope", "client_id").in_("id", missing).execute()
                    for row in cr.data or []:
                        row["scope"] = row.get("scope") or "client"
                        row["client_id"] = row.get("client_id") or client_id
                        allowed[row["id"]] = row
                else:
                    raise ValueError("Client tools table is not provisioned for this tenant.")
            for tid in tool_ids:
                row = allowed.get(tid)
                if not row:
                    raise ValueError(f"Tool {tid} not found for client")
                if (row.get("scope") or "client") == "client" and (row.get("client_id") or client_id) != client_id:
                    raise ValueError(f"Tool {tid} is not accessible for this client")

        platform_sb.table("agent_tools").delete().eq("agent_id", agent_id).execute()
        rows = [{"agent_id": agent_id, "tool_id": tid} for tid in tool_ids]
        if rows:
            platform_sb.table("agent_tools").insert(rows).execute()

    async def get_agents_for_tools(
        self, tool_ids: List[str], scoped_client_ids: Optional[List[str]] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get which agents have each tool enabled.
        Returns a dict mapping tool_id -> list of {id, name, agent_image, client_id}.
        """
        import asyncio
        from uuid import UUID

        if not tool_ids:
            return {}

        platform_sb = self.client_service.supabase

        # Get all agent_tools mappings for the given tool IDs
        at_result = platform_sb.table("agent_tools").select(
            "agent_id, tool_id"
        ).in_("tool_id", tool_ids).execute()

        if not at_result.data:
            return {tid: [] for tid in tool_ids}

        # Collect unique agent IDs
        agent_ids_needed = set(row["agent_id"] for row in at_result.data)

        # If no agents are assigned to any tools, return early
        if not agent_ids_needed:
            return {tid: [] for tid in tool_ids}

        # Fetch agent details from all clients using the multitenant service
        from app.services.client_service_multitenant import ClientService as PlatformClientService
        from app.services.agent_service_multitenant import AgentService as PlatformAgentService

        try:
            client_service = PlatformClientService()
            agent_service = PlatformAgentService()
            clients = await client_service.get_clients()

            # Filter clients if scoped
            if scoped_client_ids is not None:
                scoped_set = set(scoped_client_ids)
                clients = [c for c in clients if c.id in scoped_set]

            # Fetch agents from all clients in parallel
            async def fetch_client_agents(client):
                try:
                    client_uuid = UUID(client.id)
                    return await agent_service.get_agents(client_uuid)
                except Exception:
                    return []

            # Run all client fetches in parallel
            all_agent_lists = await asyncio.gather(
                *[fetch_client_agents(c) for c in clients],
                return_exceptions=True
            )

            # Collect matching agents
            agents_by_id: Dict[str, Dict[str, Any]] = {}
            for agent_list in all_agent_lists:
                if isinstance(agent_list, Exception) or not agent_list:
                    continue
                for agent in agent_list:
                    if agent.id in agent_ids_needed:
                        agents_by_id[agent.id] = {
                            "id": agent.id,
                            "name": getattr(agent, "name", None) or "Unnamed Sidekick",
                            "agent_image": getattr(agent, "agent_image", None),
                            "client_id": agent.client_id,
                        }
                        # Early exit if we found all needed agents
                        if len(agents_by_id) == len(agent_ids_needed):
                            break
        except Exception:
            # If multitenant services fail, return empty agent lists
            return {tid: [] for tid in tool_ids}

        # Build the mapping
        result: Dict[str, List[Dict[str, Any]]] = {tid: [] for tid in tool_ids}
        for row in at_result.data:
            agent = agents_by_id.get(row["agent_id"])
            if agent:
                result[row["tool_id"]].append(agent)

        return result

    async def _augment_tool_for_agent(self, tool: ToolOut, client_id: str) -> ToolOut:
        if (tool.slug or "") == "perplexity_ask" and tool.enabled:
            client = await self.client_service.get_client(client_id, auto_sync=False)
            if not client:
                raise ValueError("Client not found")

            api_key = None
            if client.settings and client.settings.api_keys:
                api_key = getattr(client.settings.api_keys, "perplexity_api_key", None)
            if not api_key:
                api_key = getattr(client, "perplexity_api_key", None)
            if not api_key:
                raise ValueError(
                    "Perplexity API key is not configured for this client. "
                    "Add it on the client record before assigning the Perplexity ability."
                )

            manager = get_perplexity_mcp_manager()
            server_url = await manager.ensure_running()

            config: Dict[str, Any] = dict(tool.config or {})
            config.setdefault("provider", "perplexity")
            config.setdefault("tool_name", tool.slug)
            config["server_url"] = server_url
            config.setdefault("transport", "sse")
            config.setdefault("api_key_name", "perplexity_api_key")
            config.setdefault("api_key_parameter", "api_key")
            config.setdefault("model", config.get("model") or "sonar-pro")
            config.setdefault("timeout", 60)
            config.setdefault("parameters", self._perplexity_parameters_schema())

            return tool.copy(update={"config": config})

        return tool

    @staticmethod
    def _perplexity_parameters_schema() -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "messages": {
                    "type": "array",
                    "description": "Array of conversation messages",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {
                                "type": "string",
                                "description": "Message role (system, user, assistant)",
                            },
                            "content": {
                                "type": "string",
                                "description": "Plain text content of the message",
                            },
                        },
                        "required": ["role", "content"],
                        "additionalProperties": False,
                    },
                },
                "model": {
                    "type": "string",
                    "description": "Override the default Perplexity model",
                },
                "api_key": {
                    "type": "string",
                    "description": "Optional API key override for multi-tenant usage",
                },
            },
            "required": ["messages"],
            "additionalProperties": False,
        }
