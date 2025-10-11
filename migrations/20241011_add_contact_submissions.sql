-- Migration: Add contact form submissions tracking
-- Created: 2024-10-11
-- Purpose: Store contact form, demo requests, and early access signups

BEGIN;

-- Contact form submissions table
CREATE TABLE IF NOT EXISTS public.contact_submissions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    
    -- Contact info
    first_name text,
    last_name text,
    full_name text,
    email text NOT NULL,
    company text,
    phone_number text,
    country_code text DEFAULT 'US',
    
    -- Message/Details
    message text,
    business_name text,
    stage text, -- For early access: 'solo', 'small-business', 'growth'
    use_case text, -- For early access: what they plan to use it for
    
    -- Form type
    submission_type text NOT NULL DEFAULT 'contact', -- 'contact', 'demo', 'early_access'
    
    -- Lead management
    status text DEFAULT 'new', -- 'new', 'contacted', 'qualified', 'converted', 'spam', 'archived'
    assigned_to uuid,
    priority text DEFAULT 'normal', -- 'low', 'normal', 'high', 'urgent'
    notes text,
    
    -- Tracking metadata
    ip_address inet,
    user_agent text,
    referrer text,
    utm_source text,
    utm_medium text,
    utm_campaign text,
    utm_term text,
    utm_content text,
    
    -- Follow-up tracking
    first_contact_at timestamptz,
    last_contact_at timestamptz,
    contact_count integer DEFAULT 0,
    
    CONSTRAINT valid_submission_type CHECK (submission_type IN ('contact', 'demo', 'early_access')),
    CONSTRAINT valid_status CHECK (status IN ('new', 'contacted', 'qualified', 'converted', 'spam', 'archived')),
    CONSTRAINT valid_priority CHECK (priority IN ('low', 'normal', 'high', 'urgent'))
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_contact_submissions_created 
    ON public.contact_submissions(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_contact_submissions_status 
    ON public.contact_submissions(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_contact_submissions_type 
    ON public.contact_submissions(submission_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_contact_submissions_email 
    ON public.contact_submissions(email);

CREATE INDEX IF NOT EXISTS idx_contact_submissions_assigned 
    ON public.contact_submissions(assigned_to, status);

-- Updated timestamp trigger
CREATE OR REPLACE FUNCTION public.update_contact_submission_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_contact_submission_updated_at
    BEFORE UPDATE ON public.contact_submissions
    FOR EACH ROW
    EXECUTE FUNCTION public.update_contact_submission_timestamp();

-- RLS policies
ALTER TABLE public.contact_submissions ENABLE ROW LEVEL SECURITY;

-- Service role can insert (for form submissions from the website)
CREATE POLICY "Service role can insert submissions"
    ON public.contact_submissions FOR INSERT
    TO service_role
    WITH CHECK (true);

-- Service role can select (for API queries)
CREATE POLICY "Service role can select submissions"
    ON public.contact_submissions FOR SELECT
    TO service_role
    USING (true);

-- Admins can view all submissions (when we add admin user system)
-- This will need to be updated once we have proper admin authentication
CREATE POLICY "Authenticated users can view submissions"
    ON public.contact_submissions FOR SELECT
    TO authenticated
    USING (true);

-- Admins can update submissions
CREATE POLICY "Authenticated users can update submissions"
    ON public.contact_submissions FOR UPDATE
    TO authenticated
    USING (true);

COMMENT ON TABLE public.contact_submissions IS 'Stores all marketing form submissions including contact forms, demo requests, and early access signups';
COMMENT ON COLUMN public.contact_submissions.submission_type IS 'Type of form submission: contact, demo, or early_access';
COMMENT ON COLUMN public.contact_submissions.status IS 'Lead status for sales pipeline tracking';
COMMENT ON COLUMN public.contact_submissions.stage IS 'Business stage for early access signups';

COMMIT;

