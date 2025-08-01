"""
Default IDs utility module
Provides centralized management of default client and user IDs
"""
import os
from typing import Optional

# Default client ID - Autonomite
# This should be loaded from environment or database in production
DEFAULT_CLIENT_ID = os.getenv("DEFAULT_CLIENT_ID", "11389177-e4d8-49a9-9a00-f77bb4de6592")

# Default admin user ID for testing/preview
# This should be the actual admin user's ID from authentication
DEFAULT_ADMIN_USER_ID = os.getenv("DEFAULT_ADMIN_USER_ID", "351bb07b-03fc-4fb4-b09b-748ef8a72084")


def get_default_client_id() -> str:
    """Get the default client ID (Autonomite)"""
    return DEFAULT_CLIENT_ID


def get_default_admin_user_id() -> str:
    """Get the default admin user ID for testing/preview"""
    return DEFAULT_ADMIN_USER_ID


def validate_uuid(uuid_string: str) -> bool:
    """Validate if a string is a valid UUID"""
    import re
    uuid_pattern = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        re.IGNORECASE
    )
    return bool(uuid_pattern.match(uuid_string))


def get_client_id_from_request(request_client_id: Optional[str]) -> str:
    """
    Get client ID from request or use default
    
    Args:
        request_client_id: Client ID from request
        
    Returns:
        Valid client ID (from request or default)
    """
    if request_client_id and validate_uuid(request_client_id):
        return request_client_id
    return get_default_client_id()


def get_user_id_from_request(request_user_id: Optional[str], admin_user: Optional[dict] = None) -> str:
    """
    Get user ID from request, admin session, or use default
    
    Args:
        request_user_id: User ID from request
        admin_user: Admin user from session
        
    Returns:
        Valid user ID (from request, session, or default)
    """
    # First try request
    if request_user_id and validate_uuid(request_user_id):
        return request_user_id
    
    # Then try admin session
    if admin_user and admin_user.get('id') and validate_uuid(admin_user['id']):
        return admin_user['id']
    
    # Finally use default
    return get_default_admin_user_id()