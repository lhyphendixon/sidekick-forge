#!/usr/bin/env python3
"""Debug voice chat connection issue using Playwright"""
import asyncio
import time
from playwright.async_api import async_playwright
import sys

async def debug_voice_chat():
    async with async_playwright() as p:
        # Launch browser with debugging enabled
        browser = await p.chromium.launch(
            headless=True,  # Run in headless mode (no display)
            args=['--disable-blink-features=AutomationControlled']
        )
        
        # Create context with permissions
        context = await browser.new_context(
            permissions=['microphone'],
            ignore_https_errors=True  # Allow self-signed certs
        )
        
        # Enable console logging
        page = await context.new_page()
        
        # Capture console messages
        console_logs = []
        page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))
        
        # Capture network failures
        page.on("requestfailed", lambda request: print(f"Request failed: {request.url} - {request.failure}"))
        
        try:
            print("1. Navigating to admin dashboard...")
            await page.goto("https://sidekickforge.com/admin", wait_until="networkidle")
            
            # Take screenshot of login page
            await page.screenshot(path="/tmp/01_login_page.png")
            print("   Screenshot saved: /tmp/01_login_page.png")
            
            # Check if we need to login
            if await page.locator("input[name='email']").count() > 0:
                print("2. Logging in...")
                # Use development admin credentials
                await page.fill("input[name='email']", "admin@autonomite.ai")
                await page.fill("input[name='password']", "dev")
                await page.click("button[type='submit']")
                await page.wait_for_timeout(2000)  # Wait for login to process
            
            print("3. Navigating to agents page...")
            await page.goto("https://sidekickforge.com/admin/agents")
            await page.wait_for_load_state("networkidle")
            await page.screenshot(path="/tmp/02_agents_page.png")
            print("   Screenshot saved: /tmp/02_agents_page.png")
            
            # Find and click on Clarence Coherence agent
            print("4. Looking for Clarence Coherence agent...")
            
            # Scroll down to make sure all agents are visible
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)
            
            # Take screenshot after scrolling
            await page.screenshot(path="/tmp/02b_agents_scrolled.png")
            print("   Screenshot after scrolling saved: /tmp/02b_agents_scrolled.png")
            
            # Debug: List all text to find Clarence
            print("   Looking for Clarence Coherence...")
            
            # Try to find all elements containing "System Prompt Preview" which appears in each card
            all_cards = await page.locator("text=System Prompt Preview").all()
            print(f"   Found {len(all_cards)} agent cards")
            
            # Look for text containing "Clarence Coherence" 
            clarence_element = page.locator("text=You are Clarence Coherence").first
            if await clarence_element.count() > 0:
                print("   Found Clarence Coherence by system prompt")
                # Click the Edit button in the same card
                parent_card = clarence_element.locator("xpath=ancestor::div[contains(@class, 'rounded-lg')]").first
                edit_button = parent_card.locator("text=Edit").first
                if await edit_button.count() > 0:
                    print("   Clicking Edit link to go to agent detail page")
                    await edit_button.click()
                    await page.wait_for_load_state("networkidle")
                
                # Take screenshot
                await page.screenshot(path="/tmp/03_agent_detail.png")
                print("   Screenshot saved: /tmp/03_agent_detail.png")
                
                # Look for voice preview button/link
                print("5. Looking for voice preview option...")
                
                # Scroll down to find the test/preview section
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1000)
                
                # Take screenshot after scrolling
                await page.screenshot(path="/tmp/03b_agent_detail_scrolled.png")
                print("   Screenshot after scrolling saved: /tmp/03b_agent_detail_scrolled.png")
                
                # Try different selectors for voice preview - look for mode selector
                voice_selectors = [
                    "button[data-mode='voice']",
                    "[data-mode='voice']",
                    "button:has-text('Test Voice')",
                    "button:has-text('Preview Voice')",
                    "text=Test Agent >> xpath=../..//button[contains(text(), 'Voice')]",
                    "[hx-get*='preview'][hx-get*='voice']"
                ]
                
                voice_button = None
                for selector in voice_selectors:
                    try:
                        if await page.locator(selector).count() > 0:
                            voice_button = page.locator(selector).first
                            print(f"   Found voice button with selector: {selector}")
                            break
                    except:
                        pass
                
                if voice_button:
                    # Start capturing network activity
                    network_logs = []
                    page.on("request", lambda request: network_logs.append(f"Request: {request.method} {request.url}"))
                    page.on("response", lambda response: network_logs.append(f"Response: {response.status} {response.url}"))
                    
                    print("6. Clicking voice preview...")
                    await voice_button.click()
                    
                    # Wait for modal to appear
                    await page.wait_for_timeout(2000)
                    
                    # Take screenshot of preview modal
                    await page.screenshot(path="/tmp/04_preview_modal.png")
                    print("   Screenshot saved: /tmp/04_preview_modal.png")
                    
                    # Click on Voice Chat tab
                    print("7. Clicking Voice Chat tab...")
                    voice_tab = page.locator("button:has-text('Voice Chat')").first
                    if await voice_tab.count() > 0:
                        await voice_tab.click()
                        await page.wait_for_timeout(2000)
                        
                        # Take screenshot after clicking Voice Chat
                        await page.screenshot(path="/tmp/05_voice_interface.png")
                        print("   Screenshot saved: /tmp/05_voice_interface.png")
                        
                        # Look for the connection status
                        print("8. Checking connection status...")
                        try:
                            status_text = await page.locator("#statusText").text_content(timeout=5000)
                            print(f"   Status: {status_text}")
                        except:
                            print("   Could not find #statusText element")
                            # Try alternative selectors
                            status_el = page.locator(".text-sm.text-dark-text-secondary").first
                            if await status_el.count() > 0:
                                status_text = await status_el.text_content()
                                print(f"   Alternative status: {status_text}")
                    else:
                        print("   Could not find Voice Chat tab!")
                    
                    try:
                        room_info = await page.locator("p:has-text('Room:')").text_content()
                        print(f"   {room_info}")
                    except:
                        print("   Could not find room info")
                    
                    # Wait a bit more to see if connection succeeds
                    await page.wait_for_timeout(5000)
                    
                    # Check status again
                    try:
                        status_text_after = await page.locator("#statusText").text_content()
                        print(f"   Status after 5s: {status_text_after}")
                    except:
                        print("   Still no #statusText element after 5s")
                    
                    # Get any JavaScript errors
                    print("\n9. Console logs:")
                    for log in console_logs[-20:]:  # Last 20 logs
                        print(f"   {log}")
                    
                    print("\n10. Network activity:")
                    for log in network_logs[-10:]:  # Last 10 network logs
                        print(f"   {log}")
                    
                    # Try to get LiveKit connection details from console
                    print("\n11. Checking LiveKit connection parameters...")
                    livekit_info = await page.evaluate(r"""() => {
                        return {
                            serverUrl: document.querySelector('#statusText')?.parentElement?.parentElement?.querySelector('script')?.textContent?.match(/server_url['"]\s*:\s*['"]([^'"]+)/)?.[1] || 'not found',
                            hasToken: document.querySelector('#statusText')?.parentElement?.parentElement?.querySelector('script')?.textContent?.includes('user_token'),
                            livekitLoaded: typeof window.LiveKitSDK !== 'undefined' || typeof window.LivekitClient !== 'undefined'
                        }
                    }""")
                    print(f"   LiveKit info: {livekit_info}")
                    
                    # Final screenshot
                    await page.screenshot(path="/tmp/05_final_state.png")
                    print("   Screenshot saved: /tmp/05_final_state.png")
                    
                else:
                    print("   ERROR: Could not find voice preview button!")
                    # Take screenshot of what we see
                    await page.screenshot(path="/tmp/error_no_voice_button.png")
                    print("   Screenshot saved: /tmp/error_no_voice_button.png")
            else:
                print("   ERROR: Could not find Clarence Coherence agent!")
                await page.screenshot(path="/tmp/error_no_agent.png")
                print("   Screenshot saved: /tmp/error_no_agent.png")
                
        except Exception as e:
            print(f"\nERROR: {e}")
            await page.screenshot(path="/tmp/error_exception.png")
            print("   Screenshot saved: /tmp/error_exception.png")
            
        finally:
            # Close browser
            print("\nClosing browser...")
            await browser.close()

if __name__ == "__main__":
    asyncio.run(debug_voice_chat())