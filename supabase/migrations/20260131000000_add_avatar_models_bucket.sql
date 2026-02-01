-- Migration: Add avatar-models storage bucket for IMX model files
-- This bucket stores Bithuman .imx avatar models uploaded by clients

-- Create the avatar-models bucket if it doesn't exist
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'avatar-models',
    'avatar-models',
    false,  -- Private bucket
    524288000,  -- 500MB limit
    ARRAY['application/octet-stream']::text[]
)
ON CONFLICT (id) DO NOTHING;

-- RLS policies for avatar-models bucket
-- Allow service role full access (for API uploads)
CREATE POLICY "Service role can manage avatar models"
ON storage.objects
FOR ALL
TO service_role
USING (bucket_id = 'avatar-models')
WITH CHECK (bucket_id = 'avatar-models');

-- Allow authenticated users to read their client's models
-- Path format: avatar-models/{client_id}/{agent_id}/model.imx
CREATE POLICY "Users can read their client avatar models"
ON storage.objects
FOR SELECT
TO authenticated
USING (
    bucket_id = 'avatar-models'
    AND (storage.foldername(name))[1] IN (
        SELECT id::text FROM clients WHERE id IN (
            SELECT client_id FROM user_clients WHERE user_id = auth.uid()
        )
    )
);
