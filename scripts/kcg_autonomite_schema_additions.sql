-- Supplemental schema: RAG RPCs and indexes (1024-dim)
-- Run this after kcg_full_schema.sql in the KCG project's SQL editor

-- Ensure pgvector
create extension if not exists vector;

-- Fast vector indexes (adjust lists as needed)
create index if not exists idx_transcripts_embeddings
  on public.conversation_transcripts using ivfflat (embeddings vector_cosine_ops) with (lists = 100);

create index if not exists idx_docs_embeddings
  on public.documents using ivfflat (embeddings vector_cosine_ops) with (lists = 100);

create index if not exists idx_doc_chunks_embeddings
  on public.document_chunks using ivfflat (embeddings vector_cosine_ops) with (lists = 100);

-- RPC: match_documents (cosine similarity)
create or replace function public.match_documents(
  query_embedding vector(1024),
  match_threshold double precision default 0.2,
  match_count int default 10
)
returns table(
  id bigint,
  content text,
  similarity double precision,
  metadata jsonb,
  title text
)
language sql stable
as $$
  select d.id,
         d.content,
         1 - (d.embeddings <=> query_embedding) as similarity,
         d.metadata,
         d.title
  from public.documents d
  where d.embeddings is not null
    and (1 - (d.embeddings <=> query_embedding)) >= match_threshold
  order by d.embeddings <=> query_embedding asc
  limit match_count;
$$;

-- grant execute on function public.match_documents(vector(1024), double precision, int) to anon, authenticated;

-- RPC: match_conversation_transcripts_secure (cosine similarity)
create or replace function public.match_conversation_transcripts_secure(
  query_embedding vector(1024),
  match_count int default 10,
  conversation uuid default null
)
returns table(
  id uuid,
  conversation_id uuid,
  content text,
  similarity double precision
)
language sql stable
as $$
  select t.id,
         t.conversation_id,
         coalesce(t.content, t.message) as content,
         1 - (t.embeddings <=> query_embedding) as similarity
  from public.conversation_transcripts t
  where t.embeddings is not null
    and (conversation is null or t.conversation_id = conversation)
  order by t.embeddings <=> query_embedding asc
  limit match_count;
$$;

-- grant execute on function public.match_conversation_transcripts_secure(vector(1024), int, uuid) to anon, authenticated;


