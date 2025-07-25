# End-to-End Browser Testing for Voice Preview

This directory contains browser-based end-to-end tests that verify the complete user flow from UI interaction to agent response.

## Purpose

These E2E tests catch issues that backend API tests miss:
- HTMX errors and UI update failures
- JavaScript errors in the browser
- WebSocket connection issues
- Audio playback problems
- UI state management bugs
- The complete user experience flow

## Requirements

1. **Install Playwright**:
   ```bash
   pip install --break-system-packages playwright pytest-playwright
   playwright install chromium
   playwright install-deps
   ```

2. **Configure Environment** (optional):
   ```bash
   export BASE_URL=http://localhost:8000
   export ADMIN_EMAIL=admin@example.com
   export ADMIN_PASSWORD=your-password
   export HEADLESS=true  # For CI/CD
   ```

## Test Coverage

### `test_preview_e2e.py`

Tests the complete voice preview flow:

1. **Admin Login** - Verifies authentication works
2. **Navigate to Agent** - Tests navigation to agent page
3. **Voice Preview UI** - Checks UI elements are present
4. **Start Voice Chat** - Tests clicking start and connection
5. **Agent Greeting** - Verifies agent responds with greeting
6. **UI Responsiveness** - Tests HTMX updates and stop functionality

## Running Tests

### Standalone E2E Test:
```bash
python tests/test_preview_e2e.py
```

### With Mission Critical Tests:
```bash
python scripts/test_mission_critical_with_e2e.py
```

### Headless Mode (for CI):
```bash
HEADLESS=true python tests/test_preview_e2e.py
```

## Understanding Results

The E2E test provides:
- **Screenshots** - Saved to `/tmp/voice_preview_e2e_*.png`
- **JSON Reports** - Detailed results in `/tmp/e2e_test_report_*.json`
- **Console Output** - Real-time test progress
- **Evidence Collection** - DOM state, console logs, network activity

## What It Catches

This E2E test has caught real issues like:

1. **HTMX Not Loading**:
   ```
   ❌ FAILED UI Responsiveness
      Details: htmx is not defined
   ```

2. **Connection Status Not Updating**:
   ```
   ❌ FAILED Start Voice Chat
      Details: Connection timeout - #connectionStatus never showed 'Connected'
   ```

3. **Agent Not Sending Greeting**:
   ```
   ❌ FAILED Agent Greeting
      Evidence: {
        "greeting_found": false,
        "audio_playing": false
      }
   ```

4. **UI Elements Missing**:
   ```
   ❌ FAILED Voice Preview UI
      Details: Start button not found
   ```

## Integration with CI/CD

Add to your deployment pipeline:

```yaml
# .github/workflows/test.yml
- name: Install Playwright
  run: |
    pip install playwright pytest-playwright
    playwright install chromium
    playwright install-deps

- name: Run E2E Tests
  run: |
    HEADLESS=true python scripts/test_mission_critical_with_e2e.py
```

## Debugging Failed Tests

1. **Check Screenshots**:
   ```bash
   ls -la /tmp/voice_preview_e2e_*.png
   # View with: xdg-open /tmp/voice_preview_e2e_*.png
   ```

2. **Review JSON Report**:
   ```bash
   cat /tmp/e2e_test_report_*.json | jq .
   ```

3. **Run in Non-Headless Mode**:
   ```bash
   HEADLESS=false python tests/test_preview_e2e.py
   ```

4. **Enable Playwright Debug Mode**:
   ```bash
   DEBUG=pw:api python tests/test_preview_e2e.py
   ```

## Expected Flow

When everything works correctly:

```
✅ PASSED Admin Login
   Redirected to: http://localhost:8000/admin/clients

✅ PASSED Navigate to Agent
   On agent page: http://localhost:8000/admin/agents/df91fd06/clarence-coherence

✅ PASSED Voice Preview UI
   Start button found

✅ PASSED Start Voice Chat
   Voice chat started
   Evidence: {
     "connection_status": "Connected",
     "audio_elements": 1
   }

✅ PASSED Agent Greeting
   Greeting detected: Hello! I'm Clarence Coherence. How can I help you today?

✅ PASSED UI Responsiveness
   UI elements responding to interactions
```

## Extending Tests

To add more test cases:

1. Add new test functions in `test_preview_e2e.py`
2. Update the `run_e2e_tests()` function to call them
3. Add to critical tests list if needed

Example:
```python
def test_voice_interaction(page):
    """Test speaking to the agent"""
    # Simulate microphone input or play audio file
    # Check for agent response
    pass
```

## Troubleshooting

### "Playwright not installed"
```bash
playwright install chromium
playwright install-deps
```

### "No module named playwright"
```bash
pip install --break-system-packages playwright
```

### "Browser launch failed"
Add `--no-sandbox` flag in test or install missing dependencies:
```bash
playwright install-deps
```

### Tests timeout
Increase timeout in test:
```python
page.wait_for_selector("#connectionStatus", timeout=30000)  # 30 seconds
```