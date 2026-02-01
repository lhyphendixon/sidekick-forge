# Sidekick Forge Production Validation Plan

**Version:** 1.0
**Date:** January 2026
**Purpose:** Comprehensive testing checklist for production readiness

---

## Table of Contents

1. [Claude-Testable Items](#1-claude-testable-items)
2. [Human Testing Required](#2-human-testing-required)
3. [Test Execution Order](#3-test-execution-order)
4. [Sign-Off Checklist](#4-sign-off-checklist)

---

## 1. Claude-Testable Items

These items can be validated through code analysis, API testing, and automated checks.

### 1.1 API Contract Validation

| Test | Endpoint | Expected | Status |
|------|----------|----------|--------|
| Auth signup validation | `POST /api/v1/signup` | Returns 400 for invalid email/weak password | ⬜ |
| Auth login success | `POST /api/v1/login` | Returns JWT tokens for valid credentials | ⬜ |
| Auth login failure | `POST /api/v1/login` | Returns 401 for invalid credentials | ⬜ |
| Token refresh | `POST /api/v1/refresh` | Issues new access token with valid refresh | ⬜ |
| Agent CRUD - Create | `POST /api/v1/agents/client/{id}` | Creates agent, returns 201 | ⬜ |
| Agent CRUD - Read | `GET /api/v1/agents/client/{id}/{slug}` | Returns agent data | ⬜ |
| Agent CRUD - Update | `PUT /api/v1/agents/client/{id}/{slug}` | Updates fields, returns 200 | ⬜ |
| Agent CRUD - Delete | `DELETE /api/v1/agents/client/{id}/{slug}` | Soft deletes, returns 200 | ⬜ |
| Agent slug uniqueness | `POST /api/v1/agents/client/{id}` | Returns 409 for duplicate slug | ⬜ |
| Client CRUD - Create | `POST /api/v1/clients/` | Creates client with tenant DB | ⬜ |
| Client CRUD - Read | `GET /api/v1/clients/{id}` | Returns client config | ⬜ |
| Client by domain | `GET /api/v1/clients/by-domain/{domain}` | Returns correct client | ⬜ |
| Document upload request | `POST /api/v1/documents/upload/request` | Returns pre-signed URL | ⬜ |
| Document file validation | `POST /api/v1/knowledge-base/upload` | Rejects invalid types/sizes | ⬜ |
| Document search | `POST /api/v1/documents/search` | Returns ranked results | ⬜ |
| LiveKit room create | `POST /api/v1/livekit/rooms/create` | Creates room with metadata | ⬜ |
| LiveKit token gen | `POST /api/v1/livekit/rooms/{name}/token` | Returns valid JWT | ⬜ |
| WordPress session exchange | `POST /api/v1/wordpress/session` | Validates HMAC, returns tokens | ⬜ |
| WordPress exchange alias | `POST /api/v1/wordpress/session/exchange` | Same as above (alias) | ⬜ |
| Wizard session create | `POST /api/v1/wizard/session` | Creates session, returns ID | ⬜ |
| Wizard step update | `PUT /api/v1/wizard/session/{id}/step` | Updates step data | ⬜ |
| Wizard completion | `POST /api/v1/wizard/sidekick/create` | Creates agent from wizard | ⬜ |
| Health check | `GET /api/v1/diagnostics/health` | Returns component status | ⬜ |
| Tools list | `GET /api/v1/tools` | Returns platform tools | ⬜ |
| Agent tools | `GET /api/v1/agents/{id}/tools` | Returns assigned tools | ⬜ |

### 1.2 Authentication & Authorization

| Test | Description | Status |
|------|-------------|--------|
| Protected endpoint rejection | Endpoints return 401 without auth token | ⬜ |
| Expired token handling | Returns 401 for expired JWT | ⬜ |
| Invalid token handling | Returns 401 for malformed JWT | ⬜ |
| Admin auth required | Admin endpoints reject non-admin users | ⬜ |
| WordPress HMAC validation | Rejects invalid signatures | ⬜ |
| WordPress clock skew | Accepts within 300s, rejects beyond | ⬜ |
| Multi-tenant isolation | Users can't access other clients' data | ⬜ |
| Service role bypass | Backend can access all data | ⬜ |
| API key validation | Invalid keys return 401 | ⬜ |

### 1.3 Database & Data Integrity

| Test | Description | Status |
|------|-------------|--------|
| RLS policy enforcement | Client isolation via current_client_id | ⬜ |
| Agent-document relationship | `agent_documents` join table integrity | ⬜ |
| Conversation cascade | Deleting conversation removes messages | ⬜ |
| Document cascade | Deleting document removes chunks | ⬜ |
| Index presence | Vector index on embeddings exists | ⬜ |
| Unique constraints | Slug uniqueness per client enforced | ⬜ |
| JSONB field parsing | voice_settings, webhooks properly parsed | ⬜ |
| Timestamp handling | created_at/updated_at properly set | ⬜ |

### 1.4 Configuration Validation

| Test | Description | Status |
|------|-------------|--------|
| Required env vars | Startup fails without SUPABASE_URL | ⬜ |
| Required env vars | Startup fails without ADMIN_AUTH_TOKEN | ⬜ |
| No hardcoded secrets | Code scan for embedded credentials | ⬜ |
| API key obfuscation | Client API keys not logged | ⬜ |
| CORS configuration | Only configured domains allowed | ⬜ |
| Security headers | CSP, HSTS, X-Frame-Options present | ⬜ |
| Cookie settings | httponly, secure, samesite flags | ⬜ |

### 1.5 Error Handling

| Test | Description | Status |
|------|-------------|--------|
| NO FALLBACK policy | Explicit errors when RAG fails | ⬜ |
| Validation errors | Clear messages for bad input | ⬜ |
| Provider timeout | Graceful handling of slow APIs | ⬜ |
| Missing configuration | Clear error for missing API keys | ⬜ |
| Rate limit response | 429 with Retry-After header | ⬜ |
| Database connection | Handles connection pool exhaustion | ⬜ |

### 1.6 Business Logic

| Test | Description | Status |
|------|-------------|--------|
| Tier quota enforcement | Adventurer: 120 voice min, 5000 text | ⬜ |
| Tier quota enforcement | Champion: 500 voice min, 25000 text | ⬜ |
| Usage tracking | Voice minutes properly recorded | ⬜ |
| Usage tracking | Text messages properly counted | ⬜ |
| Monthly reset | Quotas reset at billing period | ⬜ |
| Agent tools assignment | Tools linked via agent_tools table | ⬜ |
| Document chunking | Proper size and overlap | ⬜ |
| Embedding dimensions | 1024 for Qwen, matches pgvector | ⬜ |

### 1.7 Integration Points (Contract Testing)

| Integration | Test | Status |
|-------------|------|--------|
| OpenAI | API key validation, model availability | ⬜ |
| Groq | API key validation, model availability | ⬜ |
| Cerebras | API key validation, model availability | ⬜ |
| Deepgram | STT endpoint reachable | ⬜ |
| Cartesia | TTS endpoint reachable | ⬜ |
| ElevenLabs | TTS endpoint reachable | ⬜ |
| LiveKit | Server connectivity | ⬜ |
| Stripe | API key validation | ⬜ |
| Perplexity | Search endpoint reachable | ⬜ |
| Supabase | Connection to platform DB | ⬜ |

### 1.8 Code Quality Checks

| Check | Description | Status |
|-------|-------------|--------|
| Type hints | Critical functions have type annotations | ⬜ |
| Error logging | All exceptions logged with context | ⬜ |
| SQL injection | Parameterized queries used | ⬜ |
| XSS prevention | Template escaping in place | ⬜ |
| Secret handling | No secrets in logs or responses | ⬜ |
| Async safety | Proper await on all async calls | ⬜ |
| Resource cleanup | Connections properly closed | ⬜ |

---

## 2. Human Testing Required

These items require manual verification with real user interaction.

### 2.1 User Interface & Experience

| Test | Steps | Expected Result | Status |
|------|-------|-----------------|--------|
| **Marketing Site** |
| Homepage load | Visit homepage | Loads < 3s, all images render | ⬜ |
| Pricing page | Visit /pricing | All tiers display correctly | ⬜ |
| Contact form | Submit contact form | Email received, confirmation shown | ⬜ |
| Signup flow | Complete signup | Account created, email sent | ⬜ |
| **Admin Dashboard** |
| Dashboard load | Login as admin | Dashboard renders with data | ⬜ |
| Client list | View clients page | All clients display correctly | ⬜ |
| Agent creation | Create new agent | Agent created with all fields | ⬜ |
| Agent editing | Modify agent settings | Changes persist correctly | ⬜ |
| Document upload | Upload PDF via UI | Document processed, chunks created | ⬜ |
| Knowledge base view | View documents list | Documents display with status | ⬜ |
| Usage monitoring | View usage page | Accurate quota display | ⬜ |
| HTMX updates | Interact with partials | Real-time updates work | ⬜ |
| **Wizard Flow** |
| Start wizard | Click "Create Sidekick" | Wizard opens at step 1 | ⬜ |
| Name step | Enter name, proceed | Name saved, step 2 loads | ⬜ |
| Personality step | Enter description + traits | Data saved correctly | ⬜ |
| Voice selection | Browse and select voice | Preview plays, selection saves | ⬜ |
| Avatar generation | Generate avatar | Image generates, can retry | ⬜ |
| Abilities step | Toggle abilities | Selections persist | ⬜ |
| Knowledge step | Upload document | Background processing starts | ⬜ |
| Config step | Select default/advanced | Settings applied correctly | ⬜ |
| API keys step | Enter keys (if BYOK) | Keys saved securely | ⬜ |
| Launch step | Review and create | Agent created successfully | ⬜ |
| **Embedded Widget** |
| Widget load | Load sidekick.html | Widget initializes | ⬜ |
| WordPress bridge | Load on WordPress site | Auto-login works | ⬜ |
| Text chat | Send text message | Response received with citations | ⬜ |

### 2.2 Voice Chat Testing

| Test | Steps | Expected Result | Status |
|------|-------|-----------------|--------|
| Microphone permission | Start voice chat | Browser prompts for mic | ⬜ |
| Voice connection | Click connect | LiveKit room joined | ⬜ |
| Speech recognition | Speak clearly | Transcript appears in < 2s | ⬜ |
| Agent response | Wait for response | TTS plays back naturally | ⬜ |
| Interruption | Speak while agent talks | Agent stops, processes new input | ⬜ |
| Thinking sound | Wait during processing | Keyboard sound plays (if enabled) | ⬜ |
| Ambient sound | Enable ambient sound | Background audio plays | ⬜ |
| Multiple turns | Have 5+ turn conversation | Context maintained | ⬜ |
| RAG retrieval | Ask about uploaded docs | Accurate answer with citations | ⬜ |
| Voice quality | Listen to TTS | Natural, clear audio | ⬜ |
| Latency | Measure response time | < 2s from end of speech | ⬜ |
| Session timeout | Leave idle 5+ minutes | Graceful disconnect message | ⬜ |
| Reconnection | Refresh page mid-call | Can reconnect to same room | ⬜ |

### 2.3 Real-Time Features

| Test | Steps | Expected Result | Status |
|------|-------|-----------------|--------|
| Transcript streaming | Have voice conversation | Transcripts appear in real-time | ⬜ |
| Citation display | Ask RAG question | Citations show with source | ⬜ |
| Multiple devices | Open same conversation on 2 devices | Both see updates | ⬜ |
| Conversation history | Close and reopen conversation | History loads correctly | ⬜ |

### 2.4 Payment & Billing (Use Stripe Test Mode)

| Test | Steps | Expected Result | Status |
|------|-------|-----------------|--------|
| Checkout initiation | Click upgrade to Champion | Stripe checkout opens | ⬜ |
| Successful payment | Use test card 4242... | Subscription created | ⬜ |
| Failed payment | Use decline card 4000... | Error message displayed | ⬜ |
| Webhook processing | Complete payment | Webhook updates client tier | ⬜ |
| Quota update | After upgrade | New tier limits applied | ⬜ |
| Subscription cancel | Cancel subscription | Cancellation scheduled | ⬜ |
| Period end | Wait for period end | Access reverts to free tier | ⬜ |
| Invoice access | View billing page | Invoices available | ⬜ |

### 2.5 WordPress Plugin Testing

| Test | Steps | Expected Result | Status |
|------|-------|-----------------|--------|
| Plugin install | Upload ZIP to WordPress | Plugin activates | ⬜ |
| Settings page | Configure API endpoint | Settings save correctly | ⬜ |
| Shortcode embed | Add [sidekick_forge_embed] | Widget loads on page | ⬜ |
| Auto-login | Visit page as logged-in WP user | No login prompt in widget | ⬜ |
| Auto-login (guest) | Visit page as guest | Login prompt appears | ⬜ |
| Cache compatibility | Enable WP cache plugin | Widget still works after purge | ⬜ |
| Knowledge sync | Enable KB sync | Posts sync to knowledge base | ⬜ |

### 2.6 External Integration Testing

| Integration | Test | Expected Result | Status |
|-------------|------|-----------------|--------|
| **Telegram** |
| Bot connection | Send /start to bot | Welcome message received | ⬜ |
| Text message | Send text to bot | Agent responds | ⬜ |
| Voice note | Send voice message | Transcribed and responded | ⬜ |
| **Asana** |
| OAuth connect | Authorize Asana | Connection saved | ⬜ |
| Task creation | Ask agent to create task | Task appears in Asana | ⬜ |
| Task retrieval | Ask about tasks | Correct info returned | ⬜ |
| **HelpScout** |
| OAuth connect | Authorize HelpScout | Connection saved | ⬜ |
| Conversation lookup | Ask about customer | HelpScout data retrieved | ⬜ |

### 2.7 Avatar/Video Chat Testing

| Test | Steps | Expected Result | Status |
|------|-------|-----------------|--------|
| Bithuman avatar | Configure Bithuman agent | Video avatar loads | ⬜ |
| Avatar lip sync | Speak to avatar agent | Lips sync with speech | ⬜ |
| Avatar expressions | Ask emotional question | Expressions change | ⬜ |
| Video quality | Observe video stream | Clear, smooth video | ⬜ |
| Video latency | Measure audio-video sync | < 200ms offset | ⬜ |

### 2.8 Mobile Responsiveness

| Test | Device | Expected Result | Status |
|------|--------|-----------------|--------|
| Admin dashboard | iPhone 14 | Usable, no horizontal scroll | ⬜ |
| Admin dashboard | iPad | Full functionality | ⬜ |
| Embedded widget | iPhone 14 | Chat works, voice may vary | ⬜ |
| Embedded widget | Android phone | Chat works, voice may vary | ⬜ |
| Wizard flow | Tablet | All steps accessible | ⬜ |

### 2.9 Browser Compatibility

| Browser | Version | Admin Dashboard | Embedded Widget | Status |
|---------|---------|-----------------|-----------------|--------|
| Chrome | Latest | ⬜ | ⬜ | ⬜ |
| Firefox | Latest | ⬜ | ⬜ | ⬜ |
| Safari | Latest | ⬜ | ⬜ | ⬜ |
| Edge | Latest | ⬜ | ⬜ | ⬜ |

### 2.10 Error States & Edge Cases

| Test | Steps | Expected Result | Status |
|------|-------|-----------------|--------|
| Network disconnect | Disable network mid-chat | Graceful error, reconnect option | ⬜ |
| Large file upload | Upload 100MB PDF | Processes or clear size error | ⬜ |
| Rapid clicking | Click buttons rapidly | No duplicate submissions | ⬜ |
| Empty agent | Query agent with no docs | Graceful "I don't know" response | ⬜ |
| Quota exceeded | Exceed voice minutes | Clear quota exceeded message | ⬜ |
| Invalid API key | Configure bad OpenAI key | Clear configuration error | ⬜ |
| Concurrent sessions | Open 3 voice chats | All function independently | ⬜ |

### 2.11 Accessibility

| Test | Description | Status |
|------|-------------|--------|
| Keyboard navigation | Tab through all controls | All interactive elements reachable | ⬜ |
| Screen reader | Test with VoiceOver/NVDA | Content announced correctly | ⬜ |
| Color contrast | Check WCAG AA compliance | Sufficient contrast ratios | ⬜ |
| Focus indicators | Visible focus on all elements | Clear focus ring | ⬜ |

### 2.12 Performance Under Load

| Test | Method | Target | Status |
|------|--------|--------|--------|
| Concurrent users | Load test with 50 users | < 500ms response time | ⬜ |
| Concurrent voice | 10 simultaneous voice chats | All functional | ⬜ |
| Large knowledge base | Agent with 100+ documents | RAG still fast (< 2s) | ⬜ |
| Long conversation | 50+ turn conversation | No degradation | ⬜ |

---

## 3. Test Execution Order

### Phase 1: Infrastructure (Claude-testable)
1. Environment configuration validation
2. Database connectivity and schema verification
3. API endpoint contract testing
4. Authentication flow verification
5. Integration endpoint reachability

### Phase 2: Core Features (Hybrid)
1. Client/Agent CRUD operations (Claude tests API, human verifies UI)
2. Document processing pipeline (Claude tests API, human uploads files)
3. Wizard flow (Claude tests API, human walks through UI)

### Phase 3: Real-Time Features (Human required)
1. Voice chat end-to-end
2. Transcript streaming
3. Avatar/video functionality

### Phase 4: Integrations (Human required)
1. WordPress plugin on real site
2. Telegram bot interaction
3. Stripe payment flow (test mode)
4. OAuth integrations (Asana, HelpScout)

### Phase 5: Edge Cases & Polish (Human required)
1. Error state handling
2. Mobile/browser compatibility
3. Performance under load
4. Accessibility audit

---

## 4. Sign-Off Checklist

### Pre-Launch Requirements

| Category | Owner | Sign-Off | Date |
|----------|-------|----------|------|
| **Infrastructure** |
| All environment variables configured | DevOps | ⬜ | |
| Database backups configured | DevOps | ⬜ | |
| SSL certificates valid | DevOps | ⬜ | |
| Monitoring/alerting in place | DevOps | ⬜ | |
| **Security** |
| No hardcoded credentials | Claude | ⬜ | |
| CORS properly configured | Claude | ⬜ | |
| Rate limiting enabled | Claude | ⬜ | |
| Security headers present | Claude | ⬜ | |
| **Core Features** |
| User signup/login works | Human | ⬜ | |
| Agent CRUD verified | Claude + Human | ⬜ | |
| Document upload/processing works | Human | ⬜ | |
| Voice chat functional | Human | ⬜ | |
| Text chat functional | Human | ⬜ | |
| **Integrations** |
| WordPress plugin tested | Human | ⬜ | |
| Stripe payments work (test mode) | Human | ⬜ | |
| LiveKit rooms functional | Human | ⬜ | |
| **Documentation** |
| API documentation current | Claude | ⬜ | |
| User guide available | Human | ⬜ | |
| WordPress plugin docs | Human | ⬜ | |

### Go/No-Go Criteria

**Must have (Blockers):**
- [ ] All authentication flows work
- [ ] Voice chat functional with < 3s latency
- [ ] Payment processing verified in test mode
- [ ] WordPress auto-login works
- [ ] No critical security vulnerabilities
- [ ] Error messages are clear (NO FALLBACK)

**Should have:**
- [ ] All tier quotas enforced correctly
- [ ] Telegram integration functional
- [ ] Avatar video chat working
- [ ] Mobile responsive

**Nice to have:**
- [ ] All OAuth integrations tested
- [ ] Accessibility audit complete
- [ ] Load testing passed

---

## Appendix: Test Data

### Test Accounts
```
Admin: admin@sidekickforge.com / [secure password]
Test Client ID: [to be filled]
Test Agent Slug: test-agent
```

### Test Cards (Stripe)
```
Success: 4242 4242 4242 4242
Decline: 4000 0000 0000 0002
Requires Auth: 4000 0025 0000 3155
```

### Test Documents
```
- sample.pdf (10 pages, 500KB)
- large.pdf (100 pages, 50MB)
- sample.docx (5 pages)
- sample.txt (plain text)
```

---

*Document maintained by: Engineering Team*
*Last updated: January 2026*
