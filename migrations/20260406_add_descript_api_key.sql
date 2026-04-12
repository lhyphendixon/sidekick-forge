-- Add descript_api_key column to clients table (platform database)
-- Required by v2.10.0 which added Descript as a new ability
ALTER TABLE clients ADD COLUMN IF NOT EXISTS descript_api_key TEXT;
