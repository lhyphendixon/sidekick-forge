# Tenant Provisioning Worker Plan

This document captures the first-pass design for the automatic client onboarding worker. The
implementation will live alongside the FastAPI application so we can reuse application settings
and service wiring without shipping an extra deployment artifact.

## Responsibilities

1. **Job Claiming**
   - Poll `client_provisioning_jobs` for unclaimed rows ordered by `created_at`.
   - Lock the job with a single `update ... where claimed_at is null returning *` to avoid races.
   - Mark the associated row in `clients` as `provisioning_status = 'creating_project'`.

2. **Supabase Project Provisioning**
   - Call the Supabase Management API (`POST /v1/projects`) using `SUPABASE_ORG_ID` and
     `SUPABASE_ACCESS_TOKEN` from the environment (optionally `SUPABASE_DEFAULT_REGION` and
     `SUPABASE_DEFAULT_PLAN`).
   - Persist the returned `project_ref`, anon key, and service key in the platform database.
   - Record cloud region / plan metadata inside `clients.additional_settings` for auditing.
   - Move the job to a follow-up stage (`job_type = 'schema_sync'`).

3. **Schema Synchronization**
   - Invoke the reusable helper (to be extracted from `scripts/sync_supabase_schema.py`) to apply the
     canonical migration set to the fresh project.
   - Track progress with `clients.schema_version` and set `provisioning_status` to
     `schema_syncing` while the call runs.
   - On success, update `provisioning_status` to `ready`, stamp
     `provisioning_completed_at`, and clear `provisioning_error`.

4. **Failure Handling**
   - Increment `attempts` and write the exception message into `client_provisioning_jobs.last_error`.
   - Reflect the error on the parent `clients` row (`provisioning_status = 'failed'`).
- Provide an admin endpoint to reset the job (`attempts = 0`, `claimed_at = NULL`) so operators can
  retry after fixing credentials.
  - `POST /clients/{client_id}/provisioning/retry` and `GET /clients/provisioning/jobs` expose retry and
    queue visibility for operational tooling. A companion CLI script `scripts/list_provisioning_jobs.py`
    is available for quick inspection.

5. **Observability**
   - Emit structured logs for each phase and a metric for provisioning latency.
   - Surface an `/admin/provisioning` diagnostic endpoint that lists queued/failed clients and the
     most recent error message.

## Integration Points

- The worker will spin up inside an `app.lifespan` task and reuse the existing Supabase client from
  `ClientConnectionManager` for database writes.
- `scripts/sync_supabase_schema.py` will be refactored into `app/services/schema_sync.py`, exposing a
  callable `apply_latest_schema(client_config)` that the worker can import.
- The public API will start returning the new provisioning fields (see
  `app/models/platform_client.py`) so the dashboard can surface status badges.

## Open Questions

- Decide whether we need to enqueue additional jobs (`schema_sync`, `smoke_checks`) or handle the
  entire workflow in a single job row with status fields.
- Determine a sane back-off policy for transient Supabase management API failures (initial version
  can retry with fixed delays and a max attempt count).
- Confirm if we need encryption-at-rest for tenant keys beyond storing them in Supabase; if yes,
  integrate with the existing secret management story before writing the keys to the `clients` table.
