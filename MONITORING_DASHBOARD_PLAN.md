# Sidekick Forge Monitoring Dashboard Plan

## Overview

This plan outlines the implementation of a comprehensive monitoring dashboard for Sidekick Forge. The dashboard will provide real-time usage statistics, system health, and activity insights for both admin users (who can see all clients) and regular users (who see their assigned clients).

---

## 1. Dashboard Sections

### 1.1 Usage Overview (Top Section)
**Purpose:** High-level view of resource consumption with visual quota indicators

**Components:**
- **Voice Usage Card**
  - Circular progress indicator showing minutes used vs limit
  - Color coding: Green (<60%), Yellow (60-80%), Red (>80%)
  - Display: "42/100 minutes used"
  - Trend arrow (up/down vs last period)

- **Text Messages Card**
  - Same circular progress style
  - Display: "723/1000 messages"
  - Trend comparison

- **Embedding Chunks Card**
  - Same style
  - Display: "4,521/10,000 chunks"
  - Trend comparison

- **Period Selector** (top right)
  - Current billing period (default)
  - Last 7 days
  - Last 30 days
  - Custom date range

### 1.2 Sidekick Status Grid
**Purpose:** Quick view of all sidekicks with their status and usage

**Layout:** Grid of cards (3-4 per row on desktop)

**Per-Sidekick Card Contains:**
- Sidekick avatar/icon
- Sidekick name
- Status indicator (Online/Offline/Idle)
- Mini usage bars (voice, text, embeddings)
- Active conversations count
- Last activity timestamp
- Quick action: "View Details" link

**Admin-Only Enhancement:**
- Group sidekicks by Client
- Collapsible client sections
- Client-level aggregated stats header

### 1.3 Activity Timeline
**Purpose:** Real-time feed of system activity

**Events Displayed:**
- New conversation started (with user identifier)
- Conversation ended (with duration)
- Usage threshold warnings (80%, 90%, 100%)
- Agent configuration changes
- Knowledge base updates
- System errors/issues

**Features:**
- Auto-refresh every 30 seconds
- Filter by event type
- Filter by sidekick
- Expandable event details

### 1.4 Conversation Analytics
**Purpose:** Insights into conversation patterns and quality

**Metrics:**
- **Total Conversations** (with period comparison)
- **Average Duration** (voice sessions)
- **Average Messages per Conversation**
- **Channel Distribution** (pie chart: Voice vs Text vs Hybrid)
- **Conversations by Hour** (bar chart showing peak times)
- **Active vs Archived vs Deleted** (status breakdown)

### 1.5 System Health Panel
**Purpose:** Monitor infrastructure and connectivity

**Status Indicators:**
- LiveKit Server Status (green/yellow/red)
- Database Connectivity
- API Response Time (avg ms)
- Active Voice Sessions Count

**Admin-Only:**
- Per-client health status
- Container resource usage (if applicable)
- Error rate trends

### 1.6 Usage Trends Chart
**Purpose:** Historical view of resource consumption

**Chart Type:** Multi-line chart with toggleable series

**Series:**
- Voice minutes (line 1)
- Text messages (line 2)
- Embedding chunks (line 3)

**Time Range Options:**
- Last 7 days (daily)
- Last 30 days (daily)
- Last 6 months (weekly)
- Last 12 months (monthly)

---

## 2. Technical Implementation

### 2.1 Backend Routes to Create/Update

**New Route:** `/admin/monitoring/data` (JSON endpoint)
```python
@router.get("/monitoring/data")
async def get_monitoring_data(
    client_id: Optional[str] = None,  # None = all accessible clients
    period: str = "current",           # current, 7d, 30d
    admin_user: Dict = Depends(get_admin_user)
) -> MonitoringData:
    """Returns aggregated monitoring data for dashboard"""
```

**New Route:** `/admin/monitoring/activity` (JSON endpoint)
```python
@router.get("/monitoring/activity")
async def get_activity_feed(
    limit: int = 50,
    offset: int = 0,
    event_type: Optional[str] = None,
    client_id: Optional[str] = None,
    admin_user: Dict = Depends(get_admin_user)
) -> List[ActivityEvent]:
    """Returns recent activity events"""
```

**Update Existing:** `/admin/monitoring` (HTML)
- Render the new monitoring.html template
- Pass initial data for server-side rendering

### 2.2 Data Models to Create

```python
class SidekickStatus(BaseModel):
    agent_id: str
    agent_name: str
    agent_slug: str
    client_id: str
    client_name: str  # For admin grouping
    status: str  # "online", "offline", "idle"
    active_conversations: int
    last_activity: Optional[datetime]
    voice_usage: QuotaStatus
    text_usage: QuotaStatus
    embedding_usage: QuotaStatus

class MonitoringData(BaseModel):
    period_start: date
    period_end: date

    # Aggregated quotas
    voice_quota: VoiceQuotaStatus
    text_quota: QuotaStatus
    embedding_quota: QuotaStatus

    # Sidekick statuses
    sidekicks: List[SidekickStatus]

    # Conversation stats
    total_conversations: int
    avg_conversation_duration_minutes: float
    avg_messages_per_conversation: float
    conversations_by_channel: Dict[str, int]
    conversations_by_status: Dict[str, int]

    # System health
    livekit_healthy: bool
    database_healthy: bool
    active_voice_sessions: int

    # Trends (for charts)
    daily_usage: List[DailyUsage]

class DailyUsage(BaseModel):
    date: date
    voice_seconds: int
    text_messages: int
    embedding_chunks: int
    conversations_started: int

class ActivityEvent(BaseModel):
    id: str
    event_type: str  # "conversation_start", "conversation_end", "quota_warning", etc.
    timestamp: datetime
    client_id: str
    client_name: str
    agent_id: Optional[str]
    agent_name: Optional[str]
    description: str
    metadata: Dict[str, Any]
```

### 2.3 Frontend Template Structure

```
admin/monitoring.html
├── Header Section
│   ├── Page Title: "Monitoring Dashboard"
│   ├── Period Selector (dropdown)
│   ├── Client Filter (admin only)
│   └── Refresh Button + Auto-refresh indicator
│
├── Usage Overview Row (3 cards)
│   ├── Voice Usage Card
│   ├── Text Messages Card
│   └── Embedding Chunks Card
│
├── Main Grid (2 columns on desktop)
│   ├── Left Column (wider)
│   │   ├── Sidekick Status Grid
│   │   └── Usage Trends Chart
│   │
│   └── Right Column (narrower)
│       ├── System Health Panel
│       └── Activity Timeline
│
└── Bottom Section
    └── Conversation Analytics (full width)
```

### 2.4 HTMX Integration

**Auto-refresh partials:**
- `/admin/partials/monitoring/usage-cards` - Every 60s
- `/admin/partials/monitoring/sidekick-grid` - Every 30s
- `/admin/partials/monitoring/activity-feed` - Every 15s
- `/admin/partials/monitoring/health-panel` - Every 30s

**Interactive elements:**
- Period selector triggers full data reload
- Client filter (admin) triggers scoped reload
- Sidekick card click opens detail modal

---

## 3. UI/UX Design

### 3.1 Visual Style (Matching Admin Theme)
- Dark background with glassmorphic cards
- Brand teal (#01a4a6) for positive indicators
- Brand orange (#fc7244) for warnings
- Brand salmon (#f56453) for errors/exceeded
- Futura PT font throughout
- Smooth transitions on data updates

### 3.2 Responsive Design
- **Desktop (≥1024px):** Full 2-column layout
- **Tablet (768-1023px):** Single column, cards stack
- **Mobile (<768px):** Simplified view, key metrics only

### 3.3 Loading States
- Skeleton loaders for initial load
- Subtle pulse animation on auto-refresh
- Non-blocking updates (data loads in background)

### 3.4 Empty States
- "No conversations yet" with helpful guidance
- "No activity in selected period" with suggestion to expand range

---

## 4. Implementation Phases

### Phase 1: Core Dashboard (MVP)
1. Create `monitoring.html` template with base layout
2. Implement usage overview cards with quota data
3. Add sidekick status grid with basic info
4. Wire up existing usage API endpoints

### Phase 2: Real-Time Features
1. Add activity timeline with event feed
2. Implement HTMX auto-refresh for all sections
3. Add system health panel
4. Create activity event tracking

### Phase 3: Analytics & Charts
1. Add usage trends chart (Chart.js or similar)
2. Implement conversation analytics section
3. Add channel distribution visualization
4. Create hourly activity heatmap

### Phase 4: Admin Enhancements
1. Add client grouping for admin view
2. Implement client-level aggregation
3. Add cross-client comparison features
4. Create exportable reports

---

## 5. Files to Create/Modify

### New Files:
- `/app/templates/admin/monitoring.html` - Main dashboard template
- `/app/templates/admin/partials/monitoring/usage_cards.html`
- `/app/templates/admin/partials/monitoring/sidekick_grid.html`
- `/app/templates/admin/partials/monitoring/activity_feed.html`
- `/app/templates/admin/partials/monitoring/health_panel.html`
- `/app/templates/admin/partials/monitoring/conversation_analytics.html`
- `/app/models/monitoring.py` - Monitoring data models

### Modify:
- `/app/admin/routes.py` - Add new monitoring endpoints
- `/app/services/usage_tracking.py` - Add trend data methods

---

## 6. Data Queries Required

### Usage Aggregation (existing)
```python
usage_service.get_client_aggregated_usage(client_id)
usage_service.get_all_agents_usage(client_id, client_supabase)
```

### Conversation Stats (new)
```sql
-- Total conversations by status
SELECT status, COUNT(*) as count
FROM conversations
WHERE created_at >= :period_start
GROUP BY status;

-- Average messages per conversation
SELECT AVG(message_count) as avg_messages
FROM (
  SELECT conversation_id, COUNT(*) as message_count
  FROM conversation_transcripts
  WHERE created_at >= :period_start
  GROUP BY conversation_id
) subq;

-- Channel distribution
SELECT channel, COUNT(*) as count
FROM conversations
WHERE created_at >= :period_start
GROUP BY channel;

-- Hourly activity (for heatmap)
SELECT EXTRACT(HOUR FROM created_at) as hour, COUNT(*) as count
FROM conversations
WHERE created_at >= :period_start
GROUP BY hour
ORDER BY hour;
```

### Daily Usage Trends (new)
```sql
-- Daily aggregation for trends chart
SELECT
  DATE(updated_at) as date,
  SUM(voice_seconds_used) as voice_seconds,
  SUM(text_messages_used) as text_messages,
  SUM(embedding_chunks_used) as embedding_chunks
FROM agent_usage
WHERE client_id = :client_id
  AND updated_at >= :period_start
GROUP BY DATE(updated_at)
ORDER BY date;
```

---

## 7. Additional Recommendations

### 7.1 Alert System
- Email notifications when quotas reach 80% and 100%
- In-app notification badge for quota warnings
- Webhook support for external monitoring integration

### 7.2 Export Features
- Download usage report as CSV
- Download conversation history as JSON
- Generate PDF summary report

### 7.3 Comparison Tools
- Compare usage across time periods
- Compare sidekick performance
- Benchmark against tier averages

### 7.4 Quick Actions from Dashboard
- Pause/resume sidekick
- Upgrade tier prompt when exceeded
- Clear archived conversations
- Refresh knowledge base

---

## 8. Security Considerations

- All endpoints require authentication via `get_admin_user`
- Users only see data for their assigned clients
- Admin-only features gated by role check
- Rate limiting on data endpoints
- Sanitize all user-facing data

---

## Summary

This monitoring dashboard will transform the current error page into a comprehensive, real-time monitoring hub that provides:

1. **At-a-glance usage status** with visual quota indicators
2. **Per-sidekick monitoring** with status and activity tracking
3. **Historical trends** for capacity planning
4. **Conversation analytics** for quality insights
5. **System health** monitoring for proactive maintenance
6. **Activity timeline** for audit and debugging

The implementation follows existing Sidekick Forge patterns (HTMX, Tailwind, Jinja2 templates) and leverages existing backend services while adding minimal new infrastructure.
