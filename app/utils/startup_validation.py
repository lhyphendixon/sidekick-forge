"""
Startup validation to ensure services don't start with invalid credentials
"""
import os
import sys
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

# Known expired/test credentials that should never be used
INVALID_CREDENTIALS = {
    "livekit_api_keys": ["APIUtuiQ47BQBsk", "test_key", "dummy_key"],
    "livekit_secrets": ["rVdSevKfORf5hNfvrsek4joeyrwjhdbkC1HIBsdfQcjM", "test_secret"],
}

class StartupValidator:
    """Validates configuration at startup to prevent common issues"""
    
    @staticmethod
    def validate_all() -> Tuple[bool, List[str]]:
        """Run all validations, returns (success, errors)"""
        errors = []
        
        # Check LiveKit credentials
        livekit_errors = StartupValidator.validate_livekit_credentials()
        errors.extend(livekit_errors)
        
        # Check required environment variables
        env_errors = StartupValidator.validate_required_env()
        errors.extend(env_errors)
        
        return len(errors) == 0, errors
    
    @staticmethod
    def validate_livekit_credentials() -> List[str]:
        """Validate LiveKit credentials are not expired/test values"""
        errors = []
        
        api_key = os.getenv("LIVEKIT_API_KEY", "")
        api_secret = os.getenv("LIVEKIT_API_SECRET", "")
        
        if api_key in INVALID_CREDENTIALS["livekit_api_keys"]:
            errors.append(f"Invalid LiveKit API key detected: {api_key}. Please update LIVEKIT_API_KEY with valid credentials.")
        
        if api_secret in INVALID_CREDENTIALS["livekit_secrets"]:
            errors.append(f"Invalid LiveKit API secret detected. Please update LIVEKIT_API_SECRET with valid credentials.")
        
        if not api_key:
            errors.append("LIVEKIT_API_KEY is not set")
        
        if not api_secret:
            errors.append("LIVEKIT_API_SECRET is not set")
        
        return errors
    
    @staticmethod
    def validate_required_env() -> List[str]:
        """Validate required environment variables"""
        errors = []
        required_vars = [
            "LIVEKIT_URL",
            # Add other critical env vars here
        ]
        
        for var in required_vars:
            if not os.getenv(var):
                errors.append(f"Required environment variable {var} is not set")
        
        return errors
    
    @staticmethod
    def exit_on_validation_failure():
        """Run validation and exit if failed"""
        success, errors = StartupValidator.validate_all()
        
        if not success:
            logger.critical("🚨 STARTUP VALIDATION FAILED 🚨")
            logger.critical("The following issues must be resolved:")
            for error in errors:
                logger.critical(f"  ❌ {error}")
            logger.critical("\nTo fix LiveKit credentials:")
            logger.critical("  python scripts/update_livekit_credentials.py <url> <api_key> <api_secret>")
            sys.exit(1)
        else:
            logger.info("✅ Startup validation passed")