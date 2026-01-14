## Sidekick Forge v2.6.0 (staging)

Key updates (Jan 14, 2026):
- DocumentSense: preserves document identity in RAG chunks, adds document title/source URL to match_documents output, enriches citations with document context, and introduces opt-in DocumentSense intelligence (new platform/tenant tables, executor + batch worker, API endpoints, and tool wiring) so agents can answer document-specific queries like "What are the best quotes from Recording 239?".
- Scrape URL default tool: scrape_url now ships as a built-in tool for every agent; tries Firecrawl first and automatically falls back to the bundled BeautifulSoup/html2text scraper (with new agent runtime deps) to return cleaned markdown, titles, and metadata without DB configuration.
- Content Catalyst: new 'document' source type (migration), agent-level tool enablement checks, document-fetching endpoint for article generation, and widget updates with a document picker default tab, reordered tabs (Document → URL → Audio), always-visible instructions field, loading/search states, and mobile polish.
- Citations UI: DocumentSense citations are prioritized and visually distinct; when present, the UI now shows two sources (DocumentSense + top RAG result) and groups citations by both doc_id and document_id fields.
