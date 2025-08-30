#!/usr/bin/env python3
"""
Test main to verify container execution
"""

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    """Simple test to verify execution"""
    logger.info("🔥 TEST MAIN: Container is running test_main.py successfully!")
    logger.info("🔥 TEST MAIN: If you see this, the container is executing our code")
    logger.info("🔥 TEST MAIN: The validation error must be coming from somewhere else")
    
    import os
    logger.info(f"🔥 TEST MAIN: GROQ_API_KEY from env: {bool(os.getenv('GROQ_API_KEY'))}")
    logger.info(f"🔥 TEST MAIN: STT_PROVIDER from env: {os.getenv('STT_PROVIDER', 'not set')}")
    
    # Keep running so we can see logs
    import time
    logger.info("🔥 TEST MAIN: Staying alive for 60 seconds...")
    time.sleep(60)

if __name__ == "__main__":
    main()