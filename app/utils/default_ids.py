"""
Default IDs utility module
Provides centralized management of default client and user IDs
"""
import os
from typing import Optional

# No default client ID - must be explicitly provided
# Load from environment if needed, but NO hardcoded fallbacks
DEFAULT_CLIENT_ID = os.getenv("DEFAULT_CLIENT_ID", None)

# No default admin user ID - must come from authentication
# Load from environment if needed, but NO hardcoded fallbacks
DEFAULT_ADMIN_USER_ID = os.getenv("DEFAULT_ADMIN_USER_ID", None)


def get_default_client_id() -> Optional[str]:
    """Get the default client ID if configured (should be None in production)"""
    return DEFAULT_CLIENT_ID


def get_default_admin_user_id() -> Optional[str]:
    """Get the default admin user ID if configured (should be None in production)"""
    return DEFAULT_ADMIN_USER_ID


def validate_uuid(uuid_string: str) -> bool:
    """Validate if a string is a valid UUID"""
    import re
    uuid_pattern = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        re.IGNORECASE
    )
    return bool(uuid_pattern.match(uuid_string))


def get_client_id_from_request(request_client_id: Optional[str]) -> Optional[str]:
    """
    Get client ID from request - should be explicitly provided
    
    Args:
        request_client_id: Client ID from request
        
    Returns:
        Valid client ID from request or None if not provided
    """
    if request_client_id and validate_uuid(request_client_id):
        return request_client_id
    # Return None instead of default - let caller handle missing client ID
    return None


def get_user_id_from_request(request_user_id: Optional[str], admin_user: Optional[dict] = None) -> Optional[str]:
    """
    Get user ID from request or admin session - no defaults
    
    Args:
        request_user_id: User ID from request
        admin_user: Admin user from session
        
    Returns:
        Valid user ID from request/session or None
    """
    # First try request
    if request_user_id and validate_uuid(request_user_id):
        return request_user_id
    
    # Then try admin session - check both 'id' and 'user_id' fields
    if admin_user:
        user_id = admin_user.get('user_id') or admin_user.get('id')
        if user_id and validate_uuid(user_id):
            return user_id
    
    # Return None - no defaults
    return None