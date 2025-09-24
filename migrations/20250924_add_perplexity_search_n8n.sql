-- Seed global Perplexity Search (n8n) ability
WITH existing AS (
  SELECT 1 FROM public.tools WHERE scope = 'global' AND slug = 'perplexity_search_n8n'
)
INSERT INTO public.tools (id, name, slug, description, type, scope, icon_url, config, enabled)
SELECT
  'd58f0c6f-7d2a-4f4a-9bc0-3c7d14f7a1a0'::uuid,
  'Perplexity Search (n8n)',
  'perplexity_search_n8n',
  'Trigger an n8n workflow that queries Perplexity for the latest information.',
  'n8n',
  'global',
  'https://staging.sidekickforge.com/static/images/perplexity-icon.png',
  jsonb_build_object(
    'webhook_url', 'https://action.autonomite.net/webhook/7d35b36a-4906-4da5-b475-5864d8143910',
    'method', 'POST',
    'timeout', 20,
    'user_inquiry_field', 'userInquiry',
    'include_context', true,
    'strip_nulls', true,
    'default_payload', jsonb_build_object(
      'executionMode', 'production'
    ),
    'system_prompt_instructions', $$Only call `perplexity_search_n8n` when the user explicitly asks for live or current information, or when the needed facts are absent from provided context and your training data. Provide a concise (<=120 chars) `user_inquiry`. Do not use the tool for chit-chat or when you already have a reliable answer. Summarize the results in your own words and credit Perplexity as the source.$$ 
  ),
  TRUE
WHERE NOT EXISTS (SELECT 1 FROM existing);
