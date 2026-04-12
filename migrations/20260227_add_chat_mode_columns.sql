-- Add chat mode columns to agents table
-- These columns control whether voice, text, and video chat are enabled for an agent
-- Run this in Supabase SQL Editor for client projects (e.g., Autonomite: yuowazxcxwhczywurmmw)

-- Add voice_chat_enabled column (defaults to true for backward compatibility)
ALTER TABLE agents ADD COLUMN IF NOT EXISTS voice_chat_enabled boolean DEFAULT true;

-- Add text_chat_enabled column (defaults to true for backward compatibility)
ALTER TABLE agents ADD COLUMN IF NOT EXISTS text_chat_enabled boolean DEFAULT true;

-- Add video_chat_enabled column (defaults to false - video is opt-in)
ALTER TABLE agents ADD COLUMN IF NOT EXISTS video_chat_enabled boolean DEFAULT false;

-- Verify the columns were added
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'agents'
  AND column_name IN ('voice_chat_enabled', 'text_chat_enabled', 'video_chat_enabled')
ORDER BY column_name;
