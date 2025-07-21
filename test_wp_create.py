#!/usr/bin/env python3
"""
Test WordPress site creation with fixed structure
"""
import os
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.wordpress_site_service_supabase import WordPressSiteService
from app.models.wordpress_site import WordPressSiteCreate

def test_create():
    """Test creating a WordPress site with the fixed service"""
    # Get service
    supabase_url = os.getenv("SUPABASE_URL", "https://yuowazxcxwhczywurmmw.supabase.co")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY")
    
    wp_service = WordPressSiteService(supabase_url, supabase_key)
    
    # Create test site data
    site_data = WordPressSiteCreate(
        domain=f"test-site-{int(time.time())}.com",
        site_name="Test Site",
        admin_email="admin@test-site.com",
        client_id="df91fd06-816f-4273-a903-5a4861277040"  # Use the autonomite client ID
    )
    
    try:
        print("Creating WordPress site...")
        result = wp_service.create_site(site_data)
        print(f"✅ SUCCESS: Created site with ID {result.id}")
        print(f"API Key: {result.api_key}")
        print(f"API Secret: {result.api_secret}")
        
        # Clean up
        print("Cleaning up test site...")
        wp_service.delete_site(result.id)
        print("✅ Cleanup successful")
        
        return True
        
    except Exception as e:
        print(f"❌ FAILED: {e}")
        return False

if __name__ == "__main__":
    success = test_create()
    sys.exit(0 if success else 1)