#!/usr/bin/env python3
"""
Autonomite SaaS Connectivity Test Script
Tests various endpoints to ensure everything is working
"""

import requests
import json
from datetime import datetime

def test_endpoint(url, description):
    """Test a single endpoint"""
    try:
        response = requests.get(url, timeout=10)
        status = "✅ PASS" if response.status_code == 200 else f"❌ FAIL ({response.status_code})"
        print(f"{status} | {description}")
        print(f"      URL: {url}")
        if response.status_code == 200:
            try:
                if response.headers.get('content-type', '').startswith('application/json'):
                    data = response.json()
                    if 'status' in data:
                        print(f"      Status: {data['status']}")
                else:
                    content_length = len(response.text)
                    print(f"      Content: HTML ({content_length} chars)")
            except:
                pass
        print()
        return response.status_code == 200
    except Exception as e:
        print(f"❌ ERROR | {description}")
        print(f"      URL: {url}")
        print(f"      Error: {str(e)}")
        print()
        return False

def main():
    print("🔍 Autonomite SaaS Connectivity Test")
    print("=" * 50)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print()

    # Test endpoints
    endpoints = [
        ("https://agents.autonomite.net/", "HTTPS Root"),
        ("https://agents.autonomite.net/health", "Health Check"),
        ("https://agents.autonomite.net/admin/test", "Admin Test Page"),
        ("https://agents.autonomite.net/docs", "API Documentation"),
        ("http://agents.autonomite.net/health", "HTTP Redirect Test"),
    ]

    passed = 0
    total = len(endpoints)

    for url, description in endpoints:
        if test_endpoint(url, description):
            passed += 1

    print("=" * 50)
    print(f"📊 Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! Your Autonomite SaaS backend is fully operational.")
        print("✅ SSL certificates are working")
        print("✅ Admin dashboard is accessible")
        print("✅ API endpoints are responding")
        print()
        print("🚀 Ready for WordPress plugin integration!")
        print("   Backend URL: https://agents.autonomite.net")
    else:
        print("⚠️  Some tests failed. Check the error messages above.")

if __name__ == "__main__":
    main()