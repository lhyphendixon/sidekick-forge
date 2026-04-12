-- Add cerebras_api_key column to clients table
-- This column stores the Cerebras API key for LLM provider integration

ALTER TABLE clients 
ADD COLUMN IF NOT EXISTS cerebras_api_key TEXT;

-- Migrate any keys stored in additional_settings (from workaround)
UPDATE clients 
SET cerebras_api_key = additional_settings->>'cerebras_api_key'
WHERE additional_settings ? 'cerebras_api_key' 
  AND cerebras_api_key IS NULL;

-- Clean up the additional_settings after migration
UPDATE clients 
SET additional_settings = additional_settings - 'cerebras_api_key'
WHERE additional_settings ? 'cerebras_api_key';