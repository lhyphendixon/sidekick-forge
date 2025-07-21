#!/usr/bin/env python3
"""
Create a test WordPress site and test the API key
"""
import os
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.wordpress_site_service_supabase import WordPressSiteService
from app.models.wordpress_site import WordPressSiteCreate

def create_and_test():
    """Create a test site and test the API key"""
    # Get service
    supabase_url = os.getenv("SUPABASE_URL", "https://yuowazxcxwhczywurmmw.supabase.co")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY")
    
    wp_service = WordPressSiteService(supabase_url, supabase_key)
    
    # Create test site data
    site_data = WordPressSiteCreate(
        domain=f"test-auth-{int(time.time())}.com",
        site_name="Test Auth Site",
        admin_email="admin@test-auth.com",
        client_id="df91fd06-816f-4273-a903-5a4861277040"  # Use the autonomite client ID
    )
    
    try:
        print("Creating WordPress site for testing...")
        result = wp_service.create_site(site_data)
        print(f"✅ Created site with ID: {result.id}")
        print(f"API Key: {result.api_key}")
        print(f"API Secret: {result.api_secret}")
        
        # Test the API key validation
        print("\nTesting API key validation...")
        validated_site = wp_service.validate_api_key(result.api_key)
        if validated_site:
            print(f"✅ API key validation successful!")
            print(f"Site ID: {validated_site.id}")
            print(f"Domain: {validated_site.domain}")
        else:
            print("❌ API key validation failed")
            
        return result.api_key
        
    except Exception as e:
        print(f"❌ FAILED: {e}")
        return None

if __name__ == "__main__":
    api_key = create_and_test()
    if api_key:
        print(f"\nTest this API key with:")
        print(f'curl -H "X-API-Key: {api_key}" http://localhost:8000/api/v1/wordpress-sites/auth/test')