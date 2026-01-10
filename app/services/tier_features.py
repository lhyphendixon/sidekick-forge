"""
Tier-based feature management for Sidekick Forge.

Tiers:
- adventurer: Shared backend, 1 sidekick, learning phase
- champion: Dedicated infrastructure, full sidekick access
- paragon: White-glove bespoke, sovereign stack, maximum agency
"""
from typing import Any, Optional
from enum import Enum


class ClientTier(str, Enum):
    ADVENTURER = "adventurer"
    CHAMPION = "champion"
    PARAGON = "paragon"


class HostingType(str, Enum):
    SHARED = "shared"
    DEDICATED = "dedicated"


# Tier feature definitions
TIER_FEATURES = {
    ClientTier.ADVENTURER: {
        "display_name": "Adventurer",
        "display_emoji": "\U0001F7E2",  # Green circle
        "hosting_type": HostingType.SHARED,
        "max_sidekicks": 1,
        "max_documents": 50,
        "max_document_size_mb": 5,
        "max_conversations_stored": 100,
        # Feature flags
        "rag_enabled": True,
        "voice_chat_enabled": True,
        "text_chat_enabled": True,
        "video_chat_enabled": False,  # Excluded from Adventurer
        "custom_voice_enabled": False,
        "api_access_enabled": False,
        "webhooks_enabled": False,
        "custom_tools_enabled": False,
        "usersense_enabled": True,
        "content_catalyst_enabled": False,
        "priority_support": False,
        "white_label": False,
        "custom_domain": False,
    },
    ClientTier.CHAMPION: {
        "display_name": "Champion",
        "display_emoji": "\U0001F535",  # Blue circle
        "hosting_type": HostingType.DEDICATED,
        "max_sidekicks": None,  # Unlimited
        "max_documents": None,
        "max_document_size_mb": 50,
        "max_conversations_stored": None,
        # Feature flags
        "rag_enabled": True,
        "voice_chat_enabled": True,
        "text_chat_enabled": True,
        "video_chat_enabled": True,
        "custom_voice_enabled": True,
        "api_access_enabled": True,
        "webhooks_enabled": True,
        "custom_tools_enabled": True,
        "usersense_enabled": True,
        "content_catalyst_enabled": True,
        "priority_support": False,
        "white_label": False,
        "custom_domain": False,
    },
    ClientTier.PARAGON: {
        "display_name": "Paragon",
        "display_emoji": "\U0001F7E3",  # Purple circle
        "hosting_type": HostingType.DEDICATED,
        "max_sidekicks": None,
        "max_documents": None,
        "max_document_size_mb": None,
        "max_conversations_stored": None,
        # Feature flags - all enabled
        "rag_enabled": True,
        "voice_chat_enabled": True,
        "text_chat_enabled": True,
        "video_chat_enabled": True,
        "custom_voice_enabled": True,
        "api_access_enabled": True,
        "webhooks_enabled": True,
        "custom_tools_enabled": True,
        "usersense_enabled": True,
        "content_catalyst_enabled": True,
        "priority_support": True,
        "white_label": True,
        "custom_domain": True,
        # Paragon exclusives
        "dedicated_support_channel": True,
        "custom_integrations": True,
        "sovereign_deployment": True,
        "sla_guaranteed": True,
    },
}


def get_tier_features(tier: str | ClientTier) -> dict[str, Any]:
    """Get all features for a tier."""
    if isinstance(tier, str):
        try:
            tier = ClientTier(tier)
        except ValueError:
            tier = ClientTier.ADVENTURER
    return TIER_FEATURES.get(tier, TIER_FEATURES[ClientTier.ADVENTURER])


def get_feature(tier: str | ClientTier, feature: str, default: Any = None) -> Any:
    """Get a specific feature value for a tier."""
    features = get_tier_features(tier)
    return features.get(feature, default)


def check_feature_access(tier: str | ClientTier, feature: str) -> bool:
    """Check if a tier has access to a boolean feature."""
    value = get_feature(tier, feature, False)
    return bool(value)


def check_limit(tier: str | ClientTier, limit_name: str, current_value: int) -> tuple[bool, Optional[int]]:
    """
    Check if a limit would be exceeded.

    Returns:
        tuple: (is_within_limit, max_allowed)
        - is_within_limit: True if current_value < limit (or limit is None/unlimited)
        - max_allowed: The limit value, or None if unlimited
    """
    limit = get_feature(tier, limit_name)
    if limit is None:
        return True, None  # Unlimited
    return current_value < limit, limit


def get_hosting_type(tier: str | ClientTier) -> HostingType:
    """Get the hosting type for a tier."""
    features = get_tier_features(tier)
    return features.get("hosting_type", HostingType.DEDICATED)


def is_shared_hosting(tier: str | ClientTier) -> bool:
    """Check if a tier uses shared hosting."""
    return get_hosting_type(tier) == HostingType.SHARED


def get_upgrade_path(current_tier: str | ClientTier) -> Optional[ClientTier]:
    """Get the next tier in the upgrade path."""
    if isinstance(current_tier, str):
        try:
            current_tier = ClientTier(current_tier)
        except ValueError:
            return ClientTier.CHAMPION

    upgrade_map = {
        ClientTier.ADVENTURER: ClientTier.CHAMPION,
        ClientTier.CHAMPION: ClientTier.PARAGON,
        ClientTier.PARAGON: None,  # Already at top
    }
    return upgrade_map.get(current_tier)


def get_tier_comparison() -> list[dict[str, Any]]:
    """Get a comparison of all tiers for display purposes."""
    comparison = []
    for tier in ClientTier:
        features = get_tier_features(tier)
        comparison.append({
            "tier": tier.value,
            "display_name": features["display_name"],
            "emoji": features["display_emoji"],
            **features,
        })
    return comparison
