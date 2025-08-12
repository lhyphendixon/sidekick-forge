#!/usr/bin/env python3
"""
Migration script to transition from single-tenant to multi-tenant architecture

This script helps with the migration process by:
1. Backing up the current main.py
2. Switching to the multi-tenant version
3. Verifying the application starts correctly
"""
import os
import shutil
import subprocess
import sys
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def backup_file(file_path):
    """Create a backup of a file with timestamp"""
    if os.path.exists(file_path):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{file_path}.backup_{timestamp}"
        shutil.copy2(file_path, backup_path)
        logger.info(f"Created backup: {backup_path}")
        return backup_path
    return None


def migrate_main_app():
    """Migrate the main application file"""
    app_dir = "/root/sidekick-forge/app"
    main_file = os.path.join(app_dir, "main.py")
    multitenant_file = os.path.join(app_dir, "main_multitenant.py")
    
    # Backup current main.py
    logger.info("Backing up current main.py...")
    backup_path = backup_file(main_file)
    
    # Copy multi-tenant version
    logger.info("Installing multi-tenant main.py...")
    shutil.copy2(multitenant_file, main_file)
    logger.info("✅ Multi-tenant main.py installed")
    
    return backup_path


def update_imports():
    """Update import statements in __init__.py files"""
    v1_init = "/root/sidekick-forge/app/api/v1/__init__.py"
    
    if os.path.exists(v1_init):
        logger.info("Updating API v1 imports...")
        
        # Read current content
        with open(v1_init, 'r') as f:
            content = f.read()
        
        # Add multi-tenant imports if not already present
        new_imports = """
# Multi-tenant endpoints
from . import trigger_multitenant
from . import agents_multitenant
from . import clients_multitenant
"""
        
        if "trigger_multitenant" not in content:
            # Backup first
            backup_file(v1_init)
            
            # Append new imports
            with open(v1_init, 'a') as f:
                f.write(new_imports)
            
            logger.info("✅ Updated API v1 imports")


def test_import():
    """Test if the application can be imported"""
    logger.info("Testing application import...")
    try:
        # Try to import the app
        result = subprocess.run(
            [sys.executable, "-c", "from app.main import app; print('Import successful')"],
            capture_output=True,
            text=True,
            cwd="/root/sidekick-forge"
        )
        
        if result.returncode == 0:
            logger.info("✅ Application imports successfully")
            return True
        else:
            logger.error(f"❌ Import failed: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"❌ Import test failed: {e}")
        return False


def rollback(backup_path):
    """Rollback to the backup version"""
    if backup_path and os.path.exists(backup_path):
        main_file = "/root/sidekick-forge/app/main.py"
        logger.info(f"Rolling back to {backup_path}...")
        shutil.copy2(backup_path, main_file)
        logger.info("✅ Rollback completed")


def main():
    """Main migration process"""
    logger.info("Starting multi-tenant migration...")
    
    # Step 1: Migrate main.py
    backup_path = migrate_main_app()
    
    # Step 2: Update imports
    update_imports()
    
    # Step 3: Test import
    if test_import():
        logger.info("✅ Migration completed successfully!")
        logger.info("\nNext steps:")
        logger.info("1. Restart the FastAPI application")
        logger.info("2. Test the /health endpoint")
        logger.info("3. Test multi-tenant endpoints:")
        logger.info("   - GET /api/v1/clients")
        logger.info("   - POST /api/v1/trigger-agent")
        logger.info("\nTo rollback if needed:")
        logger.info(f"   python3 {__file__} --rollback {backup_path}")
    else:
        logger.error("❌ Migration failed, rolling back...")
        rollback(backup_path)
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--rollback":
        if len(sys.argv) > 2:
            rollback(sys.argv[2])
        else:
            logger.error("Please provide backup path for rollback")
    else:
        main()