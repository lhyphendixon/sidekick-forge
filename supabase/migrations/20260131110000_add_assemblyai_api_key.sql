-- Add AssemblyAI API key column for LINGUA transcription ability
ALTER TABLE clients ADD COLUMN IF NOT EXISTS assemblyai_api_key TEXT;

COMMENT ON COLUMN clients.assemblyai_api_key IS 'AssemblyAI API key for LINGUA audio transcription';
