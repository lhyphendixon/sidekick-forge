-- Insert Autonomite as the first client in Sidekick Forge platform
-- This version doesn't use ON CONFLICT and will work without unique constraints

-- First check if Autonomite already exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM clients WHERE name = 'Autonomite') THEN
        INSERT INTO clients (
            name,
            supabase_url,
            supabase_service_role_key,
            livekit_url,
            livekit_api_key,
            livekit_api_secret,
            additional_settings
        ) VALUES (
            'Autonomite',
            'https://yuowazxcxwhczywurmmw.supabase.co',
            'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY',
            'wss://autonomite-m9fsc2wp.livekit.cloud',
            'APIrZaVVGtq5PCX',
            'mRj96UaZFIA8ECFqBK9kIZYFlfW0FHWYZz7Yi3loJ0V',
            jsonb_build_object(
                'is_first_client', true,
                'migration_date', now(),
                'notes', 'Migrated from original Autonomite setup'
            )
        );
        RAISE NOTICE 'Autonomite client inserted successfully';
    ELSE
        RAISE NOTICE 'Autonomite client already exists';
    END IF;
END $$;

-- Show the inserted client
SELECT id, name, created_at FROM clients WHERE name = 'Autonomite';