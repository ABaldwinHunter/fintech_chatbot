-- Postgres + pgvector. One table for doc chunks, one for conversations,
-- one for messages, one for the per-request LLM log.

CREATE EXTENSION IF NOT EXISTS vector;

-- Allowlist: only docs approved here ever get indexed.
CREATE TABLE approved_sources (
    id          SERIAL PRIMARY KEY,
    source_type TEXT NOT NULL,          -- 'confluence_space', 'confluence_page', ...
    source_id   TEXT NOT NULL,          -- e.g. Confluence space key or page id
    owner_email TEXT NOT NULL,          -- who to ping when a doc looks stale/wrong
    UNIQUE (source_type, source_id)
);

CREATE TABLE doc_chunks (
    id            BIGSERIAL PRIMARY KEY,
    page_id       TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    chunk_index   INT  NOT NULL,
    content       TEXT NOT NULL,
    embedding     vector(1024) NOT NULL,  -- dimension depends on embedding model
    last_updated  TIMESTAMPTZ NOT NULL,   -- from Confluence; used to flag staleness
    owner_email   TEXT NOT NULL,
    UNIQUE (page_id, chunk_index)
);
CREATE INDEX ON doc_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE conversations (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',   -- open | closed | expired | escalated
    resolved     BOOLEAN,                         -- from end-of-chat thumbs up/down
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_message_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    seq             INT  NOT NULL,                -- ordering for reload on any device
    role            TEXT NOT NULL,                -- user | assistant
    content_json    JSONB NOT NULL,               -- redacted before insert
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, seq)
);

-- Our own usage log: ties tokens/cost to conversations and outcomes,
-- which the provider dashboard can't do.
CREATE TABLE llm_requests (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id UUID REFERENCES conversations(id),
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    input_tokens    INT  NOT NULL,
    output_tokens   INT  NOT NULL,
    latency_ms      INT  NOT NULL,
    stop_reason     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
