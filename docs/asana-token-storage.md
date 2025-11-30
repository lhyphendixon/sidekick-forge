# Asana Token Storage Configuration

The agent runtime can now read Asana OAuth credentials from either a client-specific
Supabase project or the Sidekick Forge platform project.  Two new environment
variables control which store is preferred and whether tokens are mirrored:

- `ASANA_TOKEN_PREFERRED_STORE` (default: `platform`)<br>
  - `platform`: store new tokens in the platform Supabase project, but fall back to
    a client Supabase if the platform record is missing.
  - `primary`: prefer the client Supabase project and fall back to the platform
    project when a client record is unavailable.

- `ASANA_TOKEN_MIRROR_STORES` (default: `false`)<br>
  When enabled, tokens are written to every available store (client and platform)
  so both environments stay in sync.

- `ASANA_TOKEN_REFRESH_MARGIN_SECONDS` (default: `300` seconds)<br>
  Controls how soon before expiry the platform proactively refreshes the stored
  Asana access token. Increase this value if you observe tokens expiring between
  worker restarts; decrease it for slower-moving, self-hosted deployments.

### SaaS deployments
Keep the default configuration (`platform`, `false`).  Tokens are written to the
platform database and the agent automatically falls back to it even when a tenant
Supabase client is provided at runtime.

### Self-hosted deployments
Set `ASANA_TOKEN_PREFERRED_STORE=primary` if you are running a single Supabase
instance for the entire deployment.  No additional mirroring is required unless
you intentionally maintain multiple Supabase projects.
