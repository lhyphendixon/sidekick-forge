-- Add missing supabase_anon_key column to clients table
ALTER TABLE clients 
ADD COLUMN IF NOT EXISTS supabase_anon_key TEXT;

-- Update existing Autonomite clients with the known anon key
UPDATE clients 
SET supabase_anon_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzU3ODQ1NzMsImV4cCI6MjA1MTM2MDU3M30.SmqTIWrScKQWkJ2_PICWVJYpRSKfvqkRcjMMt0ApH1U'
WHERE id IN ('df91fd06-816f-4273-a903-5a4861277040', '11389177-e4d8-49a9-9a00-f77bb4de6592')
AND supabase_url = 'https://yuowazxcxwhczywurmmw.supabase.co';