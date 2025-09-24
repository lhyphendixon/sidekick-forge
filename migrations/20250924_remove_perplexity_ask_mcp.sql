-- Remove legacy Perplexity Ask MCP ability now superseded by the n8n integration
-- First clear any agent assignments referencing the legacy tool
DELETE FROM public.agent_tools
WHERE tool_id = 'b5091c46-f44a-4c07-93d2-63cf343577b6';

-- Then drop the global tool definition itself
DELETE FROM public.tools
WHERE id = 'b5091c46-f44a-4c07-93d2-63cf343577b6'
  AND slug = 'perplexity_ask';
