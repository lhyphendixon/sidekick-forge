-- Add an LLM-generated narrative summary of the birth chart to the
-- lore_astrology row (alongside the existing human_design_analysis).
--
-- Idempotent — safe to re-run. Must be applied to every client Supabase that
-- has the lore_astrology table.

ALTER TABLE lore_astrology
    ADD COLUMN IF NOT EXISTS birth_chart_analysis TEXT;
