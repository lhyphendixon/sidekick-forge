# v2.1.6 – 2024-10-05

## Highlights
- Fix LiveKit/Perplexity tool dispatch in text chat so user-triggered abilities run reliably again.
- Add client provisioning metadata and job coordination tables to support asynchronous onboarding.
- Harden deployment tooling so Nginx and SSL configs adopt the runtime domain instead of shipping staging URLs.

## Changes
- **Text chat tooling**
  - Patched the text chat proxy wiring to guarantee tool payloads route through the corrected ability registry.
  - Validated worker dispatch for abilities referenced from text chat conversations.
- **Client provisioning**
  - Added `public.client_provisioning_jobs` plus provisioning status columns on `public.clients`.
  - Seeded indexes and timestamp trigger for efficient job polling and updates.
- **Infrastructure**
  - Updated Nginx templates and generators to forward the request host dynamically and avoid hard-coded `server_name` overrides.
  - Required explicit domain configuration when generating Nginx or SSL assets, preventing misconfigured production deploys.

## Impact
- Database: apply `migrations/20241005_add_client_provisioning.sql`.
- Services: restart API workers after migrating so provisioning workers pick up the new job table.
- Deployment: rerun `scripts/deploy.sh <your-domain>` to regenerate domain-aware Nginx configs.

## Verification
1. Apply the migration and restart the API service.
2. Initiate a text chat session and trigger an ability (e.g., Perplexity search) to confirm tool execution is logged.
3. Provision a new client and verify a queued job appears in `public.client_provisioning_jobs` and transitions to `ready` on completion.
4. Access the platform through your configured domain and confirm redirects and admin flows retain the correct host.

## Links
- Applies to staging branch `staging/v2.1.6`
- GitHub compare: [v2.1.5…v2.1.6](https://github.com/lhyphendixon/sidekick-forge/compare/v2.1.5...v2.1.6)
