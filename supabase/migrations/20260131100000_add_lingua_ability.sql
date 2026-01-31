-- Migration: Add LINGUA ability for audio transcription and subtitle translation
-- This ability uses AssemblyAI for transcription and LLM for translation

-- ============================================================================
-- CREATE LINGUA_RUNS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.lingua_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL,
    agent_id UUID NOT NULL,
    user_id UUID,
    conversation_id UUID,
    session_id UUID,

    -- Source audio
    source_audio_url TEXT,
    source_filename TEXT,
    audio_duration_seconds INTEGER,

    -- Transcription settings
    source_language TEXT DEFAULT 'auto',
    detected_language TEXT,

    -- Target languages for translation
    target_languages TEXT[] DEFAULT ARRAY[]::TEXT[],

    -- Output format options
    output_formats TEXT[] DEFAULT ARRAY['srt', 'vtt', 'txt']::TEXT[],

    -- AssemblyAI tracking
    assemblyai_transcript_id TEXT,

    -- Status tracking
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'uploading', 'transcribing', 'translating', 'complete', 'failed')),
    current_phase TEXT DEFAULT 'input',
    error TEXT,

    -- Results
    original_transcript JSONB,  -- {segments: [{start, end, text}]}
    translations JSONB DEFAULT '{}'::JSONB,  -- {language_code: {segments: [{start, end, text}]}}

    -- Output file URLs (signed URLs to Supabase storage)
    output_files JSONB DEFAULT '{}'::JSONB,  -- {language_code: {srt: url, vtt: url, txt: url}}

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Indexes for lingua_runs
CREATE INDEX IF NOT EXISTS idx_lingua_runs_client ON public.lingua_runs(client_id);
CREATE INDEX IF NOT EXISTS idx_lingua_runs_user ON public.lingua_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_lingua_runs_status ON public.lingua_runs(status);
CREATE INDEX IF NOT EXISTS idx_lingua_runs_created ON public.lingua_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lingua_runs_session ON public.lingua_runs(session_id);

-- RLS policies for lingua_runs
ALTER TABLE public.lingua_runs ENABLE ROW LEVEL SECURITY;

-- Service role has full access
DROP POLICY IF EXISTS "Service role full access to lingua_runs" ON public.lingua_runs;
CREATE POLICY "Service role full access to lingua_runs"
    ON public.lingua_runs
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Authenticated users can view their own runs
DROP POLICY IF EXISTS "Users can view own lingua_runs" ON public.lingua_runs;
CREATE POLICY "Users can view own lingua_runs"
    ON public.lingua_runs
    FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

-- ============================================================================
-- INSERT LINGUA AS A GLOBAL TOOL
-- ============================================================================

INSERT INTO public.tools (
    id,
    name,
    slug,
    description,
    type,
    scope,
    client_id,
    icon_url,
    config,
    enabled,
    execution_phase,
    created_at,
    updated_at
) VALUES (
    gen_random_uuid(),
    'LINGUA',
    'lingua',
    'Transcribe audio files and translate subtitles to multiple languages. Upload MP3, WAV, M4A, or FLAC files to generate accurate transcripts with timestamps. Translate to Spanish, French, German, Japanese, and more. Export as SRT, VTT, or plain text files.',
    'lingua',
    'global',
    NULL,
    '/static/images/abilities/lingua.svg',
    '{
        "supported_audio_formats": ["mp3", "wav", "m4a", "flac", "ogg", "webm"],
        "max_file_size_mb": 100,
        "supported_languages": {
            "es": "Spanish",
            "fr": "French",
            "de": "German",
            "it": "Italian",
            "pt": "Portuguese",
            "nl": "Dutch",
            "ru": "Russian",
            "ja": "Japanese",
            "zh": "Chinese",
            "ko": "Korean",
            "ar": "Arabic",
            "hi": "Hindi"
        },
        "output_formats": ["srt", "vtt", "txt"],
        "transcription_provider": "assemblyai"
    }'::jsonb,
    true,
    'active',
    NOW(),
    NOW()
) ON CONFLICT (slug) WHERE scope = 'global' AND client_id IS NULL DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    config = EXCLUDED.config,
    icon_url = EXCLUDED.icon_url,
    execution_phase = EXCLUDED.execution_phase,
    updated_at = NOW();

-- ============================================================================
-- HELPER FUNCTIONS FOR LINGUA
-- ============================================================================

-- Create a new lingua run
CREATE OR REPLACE FUNCTION create_lingua_run(
    p_client_id UUID,
    p_agent_id UUID,
    p_user_id UUID DEFAULT NULL,
    p_conversation_id UUID DEFAULT NULL,
    p_session_id UUID DEFAULT NULL,
    p_source_language TEXT DEFAULT 'auto',
    p_target_languages TEXT[] DEFAULT ARRAY[]::TEXT[],
    p_output_formats TEXT[] DEFAULT ARRAY['srt', 'vtt', 'txt']::TEXT[]
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_run_id UUID;
BEGIN
    INSERT INTO public.lingua_runs (
        client_id,
        agent_id,
        user_id,
        conversation_id,
        session_id,
        source_language,
        target_languages,
        output_formats,
        status,
        current_phase
    ) VALUES (
        p_client_id,
        p_agent_id,
        p_user_id,
        p_conversation_id,
        p_session_id,
        p_source_language,
        p_target_languages,
        p_output_formats,
        'pending',
        'input'
    ) RETURNING id INTO v_run_id;

    RETURN v_run_id;
END;
$$;

-- Update lingua run status
CREATE OR REPLACE FUNCTION update_lingua_status(
    p_run_id UUID,
    p_status TEXT,
    p_current_phase TEXT DEFAULT NULL,
    p_error TEXT DEFAULT NULL
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    UPDATE public.lingua_runs
    SET
        status = p_status,
        current_phase = COALESCE(p_current_phase, current_phase),
        error = p_error,
        updated_at = NOW(),
        completed_at = CASE WHEN p_status IN ('complete', 'failed') THEN NOW() ELSE completed_at END
    WHERE id = p_run_id;

    RETURN FOUND;
END;
$$;

-- Save transcription results
CREATE OR REPLACE FUNCTION save_lingua_transcript(
    p_run_id UUID,
    p_assemblyai_transcript_id TEXT,
    p_original_transcript JSONB,
    p_detected_language TEXT DEFAULT NULL,
    p_audio_duration_seconds INTEGER DEFAULT NULL
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    UPDATE public.lingua_runs
    SET
        assemblyai_transcript_id = p_assemblyai_transcript_id,
        original_transcript = p_original_transcript,
        detected_language = COALESCE(p_detected_language, detected_language),
        audio_duration_seconds = COALESCE(p_audio_duration_seconds, audio_duration_seconds),
        updated_at = NOW()
    WHERE id = p_run_id;

    RETURN FOUND;
END;
$$;

-- Save translation for a specific language
CREATE OR REPLACE FUNCTION save_lingua_translation(
    p_run_id UUID,
    p_language_code TEXT,
    p_translated_segments JSONB
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    UPDATE public.lingua_runs
    SET
        translations = translations || jsonb_build_object(p_language_code, p_translated_segments),
        updated_at = NOW()
    WHERE id = p_run_id;

    RETURN FOUND;
END;
$$;

-- Save output file URLs
CREATE OR REPLACE FUNCTION save_lingua_output_files(
    p_run_id UUID,
    p_language_code TEXT,
    p_files JSONB  -- {srt: url, vtt: url, txt: url}
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    UPDATE public.lingua_runs
    SET
        output_files = output_files || jsonb_build_object(p_language_code, p_files),
        updated_at = NOW()
    WHERE id = p_run_id;

    RETURN FOUND;
END;
$$;

-- ============================================================================
-- COMMENTS
-- ============================================================================

COMMENT ON TABLE public.lingua_runs IS 'Tracks LINGUA audio transcription and translation runs';
COMMENT ON COLUMN public.lingua_runs.source_language IS 'Source language code or "auto" for auto-detection';
COMMENT ON COLUMN public.lingua_runs.target_languages IS 'Array of language codes to translate subtitles into';
COMMENT ON COLUMN public.lingua_runs.output_formats IS 'Array of output formats to generate: srt, vtt, txt';
COMMENT ON COLUMN public.lingua_runs.original_transcript IS 'Original transcription with segments containing start/end timestamps and text';
COMMENT ON COLUMN public.lingua_runs.translations IS 'Translated transcripts keyed by language code';
COMMENT ON COLUMN public.lingua_runs.output_files IS 'Generated file URLs keyed by language code and format';
