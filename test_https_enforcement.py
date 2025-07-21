#!/usr/bin/env python3
"""
Test HTTPS enforcement for Autonomite SaaS
"""

import requests
from urllib.parse import urlparse

def test_https_enforcement():
    print("🔒 Testing HTTPS Enforcement for agents.autonomite.net")
    print("=" * 60)
    
    test_urls = [
        "http://agents.autonomite.net/",
        "http://agents.autonomite.net/admin/",
        "http://agents.autonomite.net/health",
        "http://agents.autonomite.net/api/v1/test",
        "http://agents.autonomite.net/docs"
    ]
    
    all_passed = True
    
    for url in test_urls:
        try:
            # Make request with redirect disabled
            response = requests.get(url, allow_redirects=False, timeout=5)
            
            if response.status_code == 301 or response.status_code == 302:
                redirect_url = response.headers.get('Location', '')
                parsed = urlparse(redirect_url)
                
                if parsed.scheme == 'https':
                    print(f"✅ PASS: {url}")
                    print(f"   → Redirects to: {redirect_url}")
                else:
                    print(f"❌ FAIL: {url}")
                    print(f"   → Redirects to non-HTTPS: {redirect_url}")
                    all_passed = False
            else:
                print(f"❌ FAIL: {url}")
                print(f"   → No redirect (Status: {response.status_code})")
                all_passed = False
                
        except Exception as e:
            print(f"❌ ERROR: {url}")
            print(f"   → {str(e)}")
            all_passed = False
    
    print("\n" + "=" * 60)
    
    # Test HTTPS security headers
    print("\n🔒 Testing HTTPS Security Headers")
    print("=" * 60)
    
    try:
        response = requests.get("https://agents.autonomite.net/", timeout=5)
        headers_to_check = [
            ('Strict-Transport-Security', 'HSTS'),
            ('X-Frame-Options', 'Clickjacking Protection'),
            ('X-Content-Type-Options', 'MIME Type Sniffing Protection'),
            ('X-XSS-Protection', 'XSS Protection'),
            ('Referrer-Policy', 'Referrer Policy')
        ]
        
        for header, description in headers_to_check:
            value = response.headers.get(header)
            if value:
                print(f"✅ {description}: {value}")
            else:
                print(f"❌ {description}: Missing")
                all_passed = False
                
    except Exception as e:
        print(f"❌ ERROR checking security headers: {str(e)}")
        all_passed = False
    
    print("\n" + "=" * 60)
    
    if all_passed:
        print("🎉 All HTTPS enforcement tests passed!")
        print("✅ HTTP redirects to HTTPS")
        print("✅ Security headers are present")
        print("✅ Your site enforces HTTPS properly")
    else:
        print("⚠️  Some tests failed. Check the results above.")
    
    return all_passed

if __name__ == "__main__":
    test_https_enforcement()