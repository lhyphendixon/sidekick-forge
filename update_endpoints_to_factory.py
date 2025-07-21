#!/usr/bin/env python
"""
Script to show which files need updating to use the service factory
"""
import os
import re
from pathlib import Path


def find_service_usage():
    """Find all files that instantiate services directly"""
    app_dir = Path("app")
    patterns = [
        r'ClientService\s*\(',
        r'AgentService\s*\(',
        r'WordPressSiteService\s*\(',
        r'from\s+app\.services\.client_service_hybrid\s+import',
        r'from\s+app\.services\.agent_service\s+import',
        r'from\s+app\.services\.wordpress_site_service\s+import',
    ]
    
    results = {}
    
    for py_file in app_dir.rglob("*.py"):
        # Skip the service files themselves and the factory
        if any(skip in str(py_file) for skip in [
            "client_service_hybrid.py",
            "client_service_supabase.py", 
            "client_service.py",
            "agent_service.py",
            "agent_service_supabase.py",
            "wordpress_site_service.py",
            "wordpress_site_service_supabase.py",
            "service_factory.py"
        ]):
            continue
            
        with open(py_file, 'r') as f:
            content = f.read()
            
        matches = []
        for pattern in patterns:
            if re.search(pattern, content):
                matches.append(pattern)
                
        if matches:
            results[str(py_file)] = matches
            
    return results


def print_update_instructions(results):
    """Print instructions for updating each file"""
    print("Files that need updating to use service factory:\n")
    
    for file_path, patterns in results.items():
        print(f"📄 {file_path}")
        print("   Found patterns:")
        for pattern in patterns:
            print(f"   - {pattern}")
        print("\n   Update instructions:")
        print("   1. Replace imports with: from app.core.service_factory import get_client_service, get_agent_service")
        print("   2. Update service instantiation to use factory functions")
        print("   3. Remove direct Redis client dependencies where possible")
        print()


if __name__ == "__main__":
    print("Scanning for service usage...\n")
    results = find_service_usage()
    
    if results:
        print_update_instructions(results)
        print(f"\nTotal files to update: {len(results)}")
    else:
        print("No files found that need updating!")