-- Fix the valid_status check constraint on content_catalyst_runs
-- to include 'awaiting_review' which is used by the integrity phase.
-- The constraint was originally missing this status, causing the
-- INTEGRITY phase update to fail with a check constraint violation.

ALTER TABLE content_catalyst_runs
  DROP CONSTRAINT IF EXISTS valid_status;

ALTER TABLE content_catalyst_runs
  ADD CONSTRAINT valid_status CHECK (
    status IN ('pending', 'running', 'awaiting_review', 'completed', 'failed')
  );
