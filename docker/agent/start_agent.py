#!/usr/bin/env python3
"""
Simple wrapper to start the production agent
"""
import subprocess
import sys

# Start the agent in production mode
subprocess.run([sys.executable, "-m", "agent.main", "start"])