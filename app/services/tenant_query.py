"""
Tenant-aware query utilities for shared/dedicated infrastructure.

This module provides query wrappers that automatically handle client_id
filtering for shared pool databases (Adventurer tier) while being
transparent for dedicated databases (Champion/Paragon tier).
"""
from typing import Any, Optional, List, Dict
from uuid import UUID
from supabase import Client
import logging

logger = logging.getLogger(__name__)


class TenantQuery:
    """
    Wrapper that automatically handles client_id filtering based on hosting type.

    For shared hosting (Adventurer tier):
    - All SELECT queries are filtered by client_id
    - All INSERT operations include client_id
    - All UPDATE/DELETE operations are scoped to client_id

    For dedicated hosting (Champion/Paragon):
    - Queries pass through unchanged (the entire DB belongs to the client)

    Usage:
        tq = TenantQuery(db_client, client_id, hosting_type='shared')
        result = tq.table('agents').select('*').execute()  # Auto-filters by client_id
    """

    def __init__(self, client: Client, client_id: UUID, hosting_type: str = 'dedicated'):
        """
        Initialize a tenant-aware query wrapper.

        Args:
            client: Supabase client connection
            client_id: The client's UUID
            hosting_type: 'shared' or 'dedicated'
        """
        self.client = client
        self.client_id = client_id
        self.hosting_type = hosting_type
        self.is_shared = hosting_type == 'shared'

    def table(self, table_name: str) -> 'TenantTableQuery':
        """
        Get a tenant-aware table query builder.

        For shared hosting, all operations will be scoped to the client_id.
        """
        return TenantTableQuery(
            self.client.table(table_name),
            self.client_id,
            self.is_shared,
            table_name
        )

    def rpc(self, function_name: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """
        Call an RPC function with automatic client_id injection for shared hosting.

        For shared hosting, injects p_client_id as the first parameter.
        """
        params = params or {}

        if self.is_shared:
            # Inject client_id for shared pool RPCs
            # Convention: shared pool functions expect p_client_id as first param
            params = {'p_client_id': str(self.client_id), **params}
            logger.debug(f"RPC {function_name}: Injected client_id for shared hosting")

        return self.client.rpc(function_name, params)


class TenantTableQuery:
    """
    A query builder that enforces client_id isolation for shared hosting.

    Wraps the Supabase query builder to automatically add client_id filters
    and ensure proper isolation in shared databases.
    """

    def __init__(self, query, client_id: UUID, is_shared: bool, table_name: str):
        self._query = query
        self._client_id = client_id
        self._is_shared = is_shared
        self._table_name = table_name
        self._client_id_applied = False
        self._is_write_op = False  # Track if this is an insert/upsert (no WHERE needed)

    def _ensure_client_filter(self):
        """Add client_id filter if in shared mode and not yet applied."""
        # Skip for insert/upsert operations (client_id is in the data, not WHERE clause)
        if self._is_write_op:
            return self._query
        if self._is_shared and not self._client_id_applied:
            self._query = self._query.eq('client_id', str(self._client_id))
            self._client_id_applied = True
            logger.debug(f"Table {self._table_name}: Added client_id filter for shared hosting")
        return self._query

    def select(self, columns: str = '*', **kwargs):
        """Select with automatic client_id filtering."""
        # Select must be called first, then we can chain filters
        self._query = self._query.select(columns, **kwargs)
        self._ensure_client_filter()
        return self

    def insert(self, data: Dict[str, Any] | List[Dict[str, Any]], **kwargs):
        """Insert with automatic client_id injection."""
        self._is_write_op = True  # Mark as write operation (no WHERE clause)
        if self._is_shared:
            if isinstance(data, dict):
                data = {**data, 'client_id': str(self._client_id)}
            elif isinstance(data, list):
                data = [{**item, 'client_id': str(self._client_id)} for item in data]
            logger.debug(f"Table {self._table_name}: Added client_id to insert data")

        self._query = self._query.insert(data, **kwargs)
        return self

    def upsert(self, data: Dict[str, Any] | List[Dict[str, Any]], **kwargs):
        """Upsert with automatic client_id injection."""
        self._is_write_op = True  # Mark as write operation (no WHERE clause)
        if self._is_shared:
            if isinstance(data, dict):
                data = {**data, 'client_id': str(self._client_id)}
            elif isinstance(data, list):
                data = [{**item, 'client_id': str(self._client_id)} for item in data]

        self._query = self._query.upsert(data, **kwargs)
        return self

    def update(self, data: Dict[str, Any], **kwargs):
        """Update with automatic client_id scoping."""
        self._ensure_client_filter()
        self._query = self._query.update(data, **kwargs)
        return self

    def delete(self, **kwargs):
        """Delete with automatic client_id scoping."""
        self._ensure_client_filter()
        self._query = self._query.delete(**kwargs)
        return self

    # Pass-through query methods
    def eq(self, column: str, value: Any):
        """Add equality filter."""
        self._ensure_client_filter()
        self._query = self._query.eq(column, value)
        return self

    def neq(self, column: str, value: Any):
        """Add not-equal filter."""
        self._ensure_client_filter()
        self._query = self._query.neq(column, value)
        return self

    def gt(self, column: str, value: Any):
        """Add greater-than filter."""
        self._ensure_client_filter()
        self._query = self._query.gt(column, value)
        return self

    def gte(self, column: str, value: Any):
        """Add greater-than-or-equal filter."""
        self._ensure_client_filter()
        self._query = self._query.gte(column, value)
        return self

    def lt(self, column: str, value: Any):
        """Add less-than filter."""
        self._ensure_client_filter()
        self._query = self._query.lt(column, value)
        return self

    def lte(self, column: str, value: Any):
        """Add less-than-or-equal filter."""
        self._ensure_client_filter()
        self._query = self._query.lte(column, value)
        return self

    def like(self, column: str, pattern: str):
        """Add LIKE filter."""
        self._ensure_client_filter()
        self._query = self._query.like(column, pattern)
        return self

    def ilike(self, column: str, pattern: str):
        """Add case-insensitive LIKE filter."""
        self._ensure_client_filter()
        self._query = self._query.ilike(column, pattern)
        return self

    def is_(self, column: str, value: Any):
        """Add IS filter (for NULL checks)."""
        self._ensure_client_filter()
        self._query = self._query.is_(column, value)
        return self

    def in_(self, column: str, values: List[Any]):
        """Add IN filter."""
        self._ensure_client_filter()
        self._query = self._query.in_(column, values)
        return self

    def contains(self, column: str, value: Any):
        """Add JSONB contains filter."""
        self._ensure_client_filter()
        self._query = self._query.contains(column, value)
        return self

    def order(self, column: str, **kwargs):
        """Add ORDER BY clause."""
        self._ensure_client_filter()
        self._query = self._query.order(column, **kwargs)
        return self

    def limit(self, count: int):
        """Add LIMIT clause."""
        self._ensure_client_filter()
        self._query = self._query.limit(count)
        return self

    def offset(self, count: int):
        """Add OFFSET clause."""
        self._ensure_client_filter()
        self._query = self._query.offset(count)
        return self

    def single(self):
        """Expect single result."""
        self._ensure_client_filter()
        self._query = self._query.single()
        return self

    def maybe_single(self):
        """Expect zero or one result."""
        self._ensure_client_filter()
        self._query = self._query.maybe_single()
        return self

    def execute(self):
        """Execute the query."""
        self._ensure_client_filter()
        return self._query.execute()


def get_tenant_query(
    db_client: Client,
    client_id: UUID,
    hosting_type: str = 'dedicated'
) -> TenantQuery:
    """
    Factory function to create a TenantQuery.

    Args:
        db_client: Supabase client connection
        client_id: The client's UUID
        hosting_type: 'shared' or 'dedicated'

    Returns:
        A TenantQuery instance configured for the hosting type
    """
    return TenantQuery(db_client, client_id, hosting_type)
