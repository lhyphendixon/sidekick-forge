"""
Tier upgrade service for Sidekick Forge.

Handles upgrading clients from Adventurer (shared) to Champion (dedicated),
including data migration from shared pool to dedicated Supabase project.
"""
import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from supabase import Client

from app.services.client_connection_manager import get_connection_manager
from app.services.tier_features import ClientTier, HostingType, get_tier_features
from app.services.schema_sync import apply_schema, project_ref_from_url
from app.services.onboarding.supabase_management import create_project, SupabaseManagementError

logger = logging.getLogger(__name__)


class TierUpgradeError(Exception):
    """Raised when tier upgrade fails."""
    pass


# Tables to migrate from shared pool to dedicated project
TABLES_TO_MIGRATE = [
    "agents",
    "documents",
    "document_chunks",
    "agent_documents",
    "conversations",
    "conversation_transcripts",
    "user_overviews",
]


async def upgrade_to_champion(
    client_id: UUID,
    management_token: str,
    org_id: str,
    region: str = "us-east-1",
) -> Dict[str, Any]:
    """
    Upgrade an Adventurer client to Champion tier.

    This process:
    1. Creates a new dedicated Supabase project
    2. Applies the standard schema
    3. Migrates all data from shared pool to dedicated project
    4. Updates client record with new credentials
    5. Cleans up data from shared pool

    Args:
        client_id: The client UUID to upgrade
        management_token: Supabase management API token
        org_id: Supabase organization ID
        region: Region for new project

    Returns:
        Dict with upgrade status and details
    """
    connection_manager = get_connection_manager()
    platform_db = connection_manager.platform_client

    # 1. Verify client is currently Adventurer tier
    client = await _fetch_client(platform_db, client_id)
    if not client:
        raise TierUpgradeError(f"Client {client_id} not found")

    current_tier = client.get("tier", "champion")
    current_hosting = client.get("hosting_type", "dedicated")

    if current_hosting != "shared":
        raise TierUpgradeError(
            f"Client {client_id} is not on shared hosting. "
            f"Current tier: {current_tier}, hosting: {current_hosting}"
        )

    logger.info(f"Starting upgrade for client {client_id} from Adventurer to Champion")

    # 2. Update status to upgrading
    await _update_client(platform_db, client_id, {
        "provisioning_status": "upgrading",
        "provisioning_error": None,
    })

    try:
        # 3. Create dedicated Supabase project
        logger.info(f"Creating dedicated Supabase project for client {client_id}")
        client_name = client.get("name", str(client_id))

        project_info = await asyncio.to_thread(
            create_project,
            token=management_token,
            org_id=org_id,
            name=client_name,
            client_id=str(client_id),
            region=region,
            plan="free",
        )

        if not project_info:
            raise TierUpgradeError("Failed to create Supabase project")

        project_ref = project_info.get("project_ref")
        supabase_url = project_info.get("supabase_url")
        service_key = project_info.get("service_role_key")
        anon_key = project_info.get("anon_key")

        logger.info(f"Created project {project_ref} for client {client_id}")

        # 4. Apply schema to new project
        logger.info(f"Applying schema to new project {project_ref}")
        results = await asyncio.to_thread(apply_schema, project_ref, management_token, True)
        failures = [detail for _, ok, detail in results if not ok]
        if failures:
            raise TierUpgradeError(f"Schema sync failed: {'; '.join(failures)}")

        # 5. Migrate data from shared pool
        shared_pool = connection_manager._get_shared_pool_client()
        dedicated = await asyncio.to_thread(
            lambda: __import__('supabase').create_client(supabase_url, service_key)
        )

        migration_stats = await _migrate_data(client_id, shared_pool, dedicated)
        logger.info(f"Migrated data for client {client_id}: {migration_stats}")

        # 6. Update client record with new credentials and tier
        await _update_client(platform_db, client_id, {
            "tier": "champion",
            "hosting_type": "dedicated",
            "max_sidekicks": None,  # Unlimited
            "supabase_project_ref": project_ref,
            "supabase_url": supabase_url,
            "supabase_service_role_key": service_key,
            "supabase_anon_key": anon_key,
            "provisioning_status": "ready",
            "provisioning_completed_at": datetime.utcnow().isoformat(),
            "provisioning_error": None,
        })

        # 7. Clean up shared pool data
        await _cleanup_shared_pool(client_id, shared_pool)
        logger.info(f"Cleaned up shared pool data for client {client_id}")

        # Clear cache
        connection_manager.clear_cache(client_id)

        return {
            "status": "upgraded",
            "from_tier": "adventurer",
            "to_tier": "champion",
            "project_ref": project_ref,
            "migration_stats": migration_stats,
        }

    except Exception as e:
        logger.exception(f"Upgrade failed for client {client_id}: {e}")
        await _update_client(platform_db, client_id, {
            "provisioning_status": "upgrade_failed",
            "provisioning_error": str(e),
        })
        raise TierUpgradeError(f"Upgrade failed: {e}")


async def _fetch_client(platform_db: Client, client_id: UUID) -> Optional[Dict[str, Any]]:
    """Fetch client record from platform database."""
    def _fetch():
        result = platform_db.table("clients").select("*").eq("id", str(client_id)).single().execute()
        return result.data
    return await asyncio.to_thread(_fetch)


async def _update_client(platform_db: Client, client_id: UUID, data: Dict[str, Any]) -> None:
    """Update client record in platform database."""
    def _update():
        platform_db.table("clients").update(data).eq("id", str(client_id)).execute()
    await asyncio.to_thread(_update)


async def _migrate_data(
    client_id: UUID,
    source: Client,
    dest: Client,
) -> Dict[str, int]:
    """
    Migrate all data for a client from shared pool to dedicated project.

    Args:
        client_id: The client UUID
        source: Shared pool Supabase client
        dest: Dedicated Supabase client

    Returns:
        Dict with row counts per table
    """
    stats = {}

    for table_name in TABLES_TO_MIGRATE:
        try:
            count = await _migrate_table(table_name, client_id, source, dest)
            stats[table_name] = count
            logger.info(f"Migrated {count} rows from {table_name} for client {client_id}")
        except Exception as e:
            logger.error(f"Failed to migrate {table_name} for client {client_id}: {e}")
            stats[table_name] = -1  # Indicate failure

    return stats


async def _migrate_table(
    table_name: str,
    client_id: UUID,
    source: Client,
    dest: Client,
    batch_size: int = 100,
) -> int:
    """
    Migrate a single table's data from shared to dedicated.

    Args:
        table_name: Name of the table to migrate
        client_id: The client UUID
        source: Shared pool client
        dest: Dedicated client
        batch_size: Number of rows per batch

    Returns:
        Number of rows migrated
    """
    def _migrate():
        # Fetch all rows for this client from shared pool
        result = source.table(table_name).select("*").eq("client_id", str(client_id)).execute()

        if not result.data:
            return 0

        rows = result.data
        total = len(rows)

        # Remove client_id column (not needed in dedicated DB)
        for row in rows:
            row.pop("client_id", None)

        # Insert in batches
        for i in range(0, total, batch_size):
            batch = rows[i:i + batch_size]
            dest.table(table_name).insert(batch).execute()

        return total

    return await asyncio.to_thread(_migrate)


async def _cleanup_shared_pool(client_id: UUID, shared_pool: Client) -> None:
    """
    Remove all data for a client from the shared pool.

    Called after successful migration to dedicated project.
    """
    def _cleanup():
        for table_name in reversed(TABLES_TO_MIGRATE):  # Reverse order for FK constraints
            try:
                shared_pool.table(table_name).delete().eq("client_id", str(client_id)).execute()
                logger.debug(f"Deleted {table_name} rows for client {client_id} from shared pool")
            except Exception as e:
                logger.warning(f"Failed to delete {table_name} for client {client_id}: {e}")

    await asyncio.to_thread(_cleanup)


async def check_upgrade_eligibility(client_id: UUID) -> Dict[str, Any]:
    """
    Check if a client is eligible for tier upgrade.

    Returns:
        Dict with eligibility status and details
    """
    connection_manager = get_connection_manager()
    client_info = connection_manager.get_client_info(client_id)

    current_tier = client_info.get("tier", "champion")
    hosting_type = client_info.get("hosting_type", "dedicated")

    if current_tier == "paragon":
        return {
            "eligible": False,
            "reason": "Already at highest tier (Paragon)",
            "current_tier": current_tier,
            "next_tier": None,
        }

    if current_tier == "champion":
        return {
            "eligible": True,
            "reason": "Eligible for upgrade to Paragon",
            "current_tier": current_tier,
            "next_tier": "paragon",
            "requires_migration": False,
        }

    if current_tier == "adventurer":
        return {
            "eligible": True,
            "reason": "Eligible for upgrade to Champion",
            "current_tier": current_tier,
            "next_tier": "champion",
            "requires_migration": True,
            "migration_info": "Data will be migrated from shared pool to dedicated project",
        }

    return {
        "eligible": False,
        "reason": f"Unknown tier: {current_tier}",
        "current_tier": current_tier,
        "next_tier": None,
    }


__all__ = [
    "upgrade_to_champion",
    "check_upgrade_eligibility",
    "TierUpgradeError",
]
