-- Fix ability icons migration
-- Updates staging URLs to relative paths and adds missing icons for built-in abilities

-- Fix staging URLs -> relative paths
UPDATE public.tools
SET icon_url = REPLACE(icon_url, 'https://staging.sidekickforge.com', '')
WHERE icon_url LIKE 'https://staging.sidekickforge.com%';

-- Update specific abilities with proper SVG icons
UPDATE public.tools SET icon_url = '/static/images/abilities/content-catalyst.svg'
WHERE slug = 'content_catalyst' AND (icon_url IS NULL OR icon_url = '' OR icon_url LIKE '%ability-content-catalyst.png%');

UPDATE public.tools SET icon_url = '/static/images/abilities/image-catalyst.svg'
WHERE (slug = 'image_catalyst' OR slug = 'image-catalyst' OR LOWER(name) LIKE '%image catalyst%');

UPDATE public.tools SET icon_url = '/static/images/abilities/lingua.svg'
WHERE (slug = 'lingua' OR LOWER(name) LIKE '%lingua%');

UPDATE public.tools SET icon_url = '/static/images/abilities/documentsense.svg'
WHERE (slug = 'documentsense' OR type = 'documentsense');

UPDATE public.tools SET icon_url = '/static/images/abilities/usersense.svg'
WHERE (slug = 'usersense' OR LOWER(name) LIKE '%usersense%');

UPDATE public.tools SET icon_url = '/static/images/abilities/helpscout.svg'
WHERE (slug LIKE '%helpscout%' OR type = 'helpscout' OR LOWER(name) LIKE '%helpscout%');

UPDATE public.tools SET icon_url = '/static/images/abilities/asana.svg'
WHERE (slug LIKE '%asana%' OR type = 'asana' OR LOWER(name) LIKE '%asana%');

UPDATE public.tools SET icon_url = '/static/images/abilities/web-search.svg'
WHERE (slug LIKE '%perplexity%' OR LOWER(name) LIKE '%perplexity%' OR LOWER(name) LIKE '%web search%');

UPDATE public.tools SET icon_url = '/static/images/abilities/crypto-price.svg'
WHERE (slug LIKE '%crypto%' OR LOWER(name) LIKE '%crypto%price%');

UPDATE public.tools SET icon_url = '/static/images/abilities/prediction-market.svg'
WHERE (slug LIKE '%prediction%' OR LOWER(name) LIKE '%prediction%market%');

UPDATE public.tools SET icon_url = '/static/images/abilities/print-ready.svg'
WHERE (slug LIKE '%print%ready%' OR LOWER(name) LIKE '%printready%' OR LOWER(name) LIKE '%print ready%');

-- Set default icon for any remaining tools without icons
UPDATE public.tools SET icon_url = '/static/images/ability-default.png'
WHERE icon_url IS NULL OR icon_url = '';
