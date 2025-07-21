# Redis to Supabase Migration Guide

This guide explains how to migrate from the Redis-hybrid storage to Supabase-only storage.

## Overview

The system currently uses a hybrid approach with Redis for caching and Supabase for persistence. This migration removes Redis dependency and uses Supabase exclusively.

## Migration Steps

### 1. Run Database Migrations

First, ensure all required tables exist in Supabase:

```sql
-- Run these migrations in your Supabase SQL editor:
-- 001_create_clients_table.sql
-- 002_create_wordpress_sites_table.sql
```

### 2. Enable Supabase-Only Mode

Set the environment variable to enable Supabase-only mode:

```bash
export USE_SUPABASE_ONLY=true
```

Or add to your `.env` file:
```
USE_SUPABASE_ONLY=true
```

### 3. Update Service Imports

The system uses a service factory (`app.core.service_factory`) that automatically switches between modes based on the `USE_SUPABASE_ONLY` environment variable.

Current endpoints that need to be updated to use the service factory:
- `/admin/routes.py` - Admin dashboard
- `/api/v1/clients.py` - Client API
- `/api/v1/agents.py` - Agent API
- `/api/v1/trigger.py` - Trigger endpoint
- All proxy endpoints

### 4. Remove Redis Dependencies

Once confirmed working in Supabase-only mode:

1. Remove Redis from Docker Compose:
```yaml
# Remove the redis service from docker-compose.yml
```

2. Remove Redis configuration from `app/config.py`:
- `redis_host`
- `redis_port`
- `redis_db`
- `redis_url` property

3. Remove Redis from requirements:
```bash
# Remove from requirements.txt:
# redis==6.2.0
# hiredis==3.2.1
```

### 5. Test Migration

Use the provided test scripts:

```bash
# Test both modes
python test_service_modes.py

# Test Supabase-only mode
USE_SUPABASE_ONLY=true python test_supabase_migration.py
```

## Service Comparison

### Redis-Hybrid Mode (Current)
- **Pros:**
  - Fast cached responses (10-minute TTL)
  - Fallback to Redis when Supabase is unavailable
  - Reduced Supabase API calls
- **Cons:**
  - Complex cache invalidation
  - Data consistency issues
  - Additional infrastructure (Redis)
  - More complex debugging

### Supabase-Only Mode (New)
- **Pros:**
  - Simplified architecture
  - Always consistent data
  - No cache invalidation needed
  - Easier debugging
  - Lower infrastructure cost
- **Cons:**
  - Potentially slower responses
  - More Supabase API calls
  - No fallback if Supabase is down

## Performance Optimization

To maintain performance in Supabase-only mode:

1. **Connection Pooling:** Reuse Supabase client instances
2. **Proper Indexes:** Ensure all frequently queried fields are indexed
3. **Batch Operations:** Use bulk queries where possible
4. **Edge Functions:** Consider Supabase Edge Functions for complex queries

## Rollback Plan

If issues arise, rollback is simple:

1. Set `USE_SUPABASE_ONLY=false`
2. Restart the application
3. Redis-hybrid mode will be re-enabled automatically

## Monitoring

Monitor these metrics after migration:

1. **Response Times:** Track API response times
2. **Supabase Usage:** Monitor API calls and bandwidth
3. **Error Rates:** Watch for increased errors
4. **Database Performance:** Check query execution times

## Future Improvements

1. **Request-Level Caching:** Implement in-memory caching per request
2. **Supabase Realtime:** Use Supabase realtime subscriptions for live updates
3. **Edge Caching:** Utilize Supabase's CDN for static data
4. **Query Optimization:** Continuously optimize database queries