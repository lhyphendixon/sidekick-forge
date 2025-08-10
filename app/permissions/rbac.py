from typing import Optional, Set
from app.integrations.supabase_client import supabase_manager


async def get_platform_permissions(user_id: str) -> Set[str]:
    """Return permission keys granted via platform role memberships."""
    await supabase_manager.initialize()
    admin = supabase_manager.admin_client
    # Join: platform_role_memberships -> roles -> role_permissions -> permissions
    # Supabase Py doesn't support server-side joins; do it in two steps.
    role_rows = admin.table("platform_role_memberships").select("role_id").eq("user_id", user_id).execute().data
    if not role_rows:
        return set()
    role_ids = [r["role_id"] for r in role_rows]
    # role_permissions for these roles
    rp_rows = admin.table("role_permissions").select("permission_id, role_id").in_("role_id", role_ids).execute().data
    if not rp_rows:
        return set()
    perm_ids = list({r["permission_id"] for r in rp_rows})
    perm_rows = admin.table("permissions").select("id,key").in_("id", perm_ids).execute().data
    return {p["key"] for p in perm_rows}


async def get_tenant_permissions(user_id: str, client_id: str) -> Set[str]:
    """Return permission keys granted via tenant membership for a specific client."""
    await supabase_manager.initialize()
    admin = supabase_manager.admin_client
    # Get the role for this membership
    tm = (
        admin.table("tenant_memberships")
        .select("role_id,status")
        .eq("user_id", user_id)
        .eq("client_id", client_id)
        .single()
        .execute()
    )
    tm_data = tm.data if hasattr(tm, "data") else tm
    if not tm_data or tm_data.get("status") != "active":
        return set()
    role_id = tm_data.get("role_id")
    if not role_id:
        return set()
    # Map role to permissions
    rp_rows = admin.table("role_permissions").select("permission_id").eq("role_id", role_id).execute().data
    if not rp_rows:
        return set()
    perm_ids = [r["permission_id"] for r in rp_rows]
    perm_rows = admin.table("permissions").select("id,key").in_("id", perm_ids).execute().data
    return {p["key"] for p in perm_rows}


async def has_permission(user_id: str, permission_key: str, client_id: Optional[str] = None) -> bool:
    """Check whether a user has a permission, considering platform and optional tenant scope."""
    # Platform-level perms grant global access
    platform_perms = await get_platform_permissions(user_id)
    if permission_key in platform_perms:
        return True
    if client_id:
        tenant_perms = await get_tenant_permissions(user_id, client_id)
        if permission_key in tenant_perms:
            return True
    return False


