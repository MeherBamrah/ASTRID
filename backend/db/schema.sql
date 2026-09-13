-- Run this in Supabase SQL Editor (Project > SQL Editor > New Query)

-- 1. Enable pgvector extension (this is what makes Postgres a vector DB)
create extension if not exists vector;

-- 2. User profiles (auto-filled by Supabase Auth on Google login, we extend it)
create table if not exists profiles (
    id uuid references auth.users(id) primary key,
    email text,
    full_name text,
    avatar_url text,
    created_at timestamp with time zone default now()
);

-- Auto-create a profile row whenever someone signs up via Google OAuth
create or replace function public.handle_new_user()
returns trigger as $$
begin
  insert into public.profiles (id, email, full_name, avatar_url)
  values (
    new.id,
    new.email,
    new.raw_user_meta_data->>'full_name',
    new.raw_user_meta_data->>'avatar_url'
  );
  return new;
end;
$$ language plpgsql security definer;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

-- 3. Vector memory table (this replaces Chroma entirely)
-- Stores: past task transcripts, site quirks, learnings, user preferences
create table if not exists agent_memory (
    id bigserial primary key,
    user_id uuid references profiles(id),
    task_type text,              -- e.g. 'flight_search', 'site_navigation_note'
    content text,                 -- the raw text/note being embedded
    metadata jsonb,                -- extra info: site url, success/fail, timestamps
    embedding vector(384),         -- 384 = all-MiniLM-L6-v2 output size
    created_at timestamp with time zone default now()
);

-- 4. Vector similarity search function (this IS your RAG retrieval)
create or replace function match_memory (
    query_embedding vector(384),
    match_user_id uuid,
    match_count int default 5
)
returns table (
    id bigint,
    content text,
    metadata jsonb,
    similarity float
)
language sql stable
as $$
    select
        id,
        content,
        metadata,
        1 - (embedding <=> query_embedding) as similarity
    from agent_memory
    where (match_user_id is null or user_id = match_user_id)
    order by embedding <=> query_embedding
    limit match_count;
$$;

-- 5. Task run history (for showing user their past agent runs)
create table if not exists task_runs (
    id bigserial primary key,
    user_id uuid references profiles(id),
    goal text,
    status text default 'running',  -- running | done | failed
    result text,
    created_at timestamp with time zone default now()
);
