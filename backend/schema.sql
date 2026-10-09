-- D1 schema. Apply with:
--   npx wrangler d1 execute ai-chat-archive --remote --file=schema.sql
--
-- Ownership model
-- ---------------
-- The archive is multi-tenant by email address: every conversation belongs to
-- exactly one reader's shelf (conversations.owner_email). The dedupe/upsert key
-- is therefore the PAIR (owner_email, external_id), not external_id alone --
-- two readers may legitimately archive the same ChatGPT share link, and each
-- must get their own copy. An unauthenticated ingest has no owner and is
-- rejected by the Worker before it reaches this table.
--
-- Reading is separate from listing: a story is public by its share id, but
-- enumerating stories always filters on owner_email (idx_conversations_owner).

CREATE TABLE IF NOT EXISTS conversations (
  id          TEXT PRIMARY KEY,          -- our own UUID
  owner_email TEXT NOT NULL,             -- whose archive holds this (normalized, lowercase)
  external_id TEXT NOT NULL,             -- ChatGPT share id (upsert key, with owner_email)
  title       TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  source      TEXT DEFAULT 'chatgpt',    -- chatgpt | gemini | claude | elicit
  report_id   TEXT                       -- FK -> reports.id (the "thumbnail")
);

CREATE TABLE IF NOT EXISTS messages (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  seq             INTEGER NOT NULL,      -- order within the conversation
  role            TEXT NOT NULL,         -- user | assistant | tool | system
  content         TEXT,
  created_at      TEXT
);

CREATE TABLE IF NOT EXISTS reports (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  title           TEXT,
  markdown        TEXT NOT NULL,         -- raw markdown summary (render at read time)
  citations       TEXT,                  -- JSON array of {n,title,url}
  skill           TEXT,                  -- which summary skill produced it
  generated_at    TEXT
);

-- One copy of a share link per shelf. This replaces the old UNIQUE(external_id):
-- uniqueness is now scoped to the owner.
CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_owner_external
  ON conversations(owner_email, external_id);

-- The front page query: WHERE owner_email = ? ORDER BY created_at DESC.
CREATE INDEX IF NOT EXISTS idx_conversations_owner_recent
  ON conversations(owner_email, created_at DESC);

-- Public permalink lookup: WHERE external_id = ?  (no owner filter).
CREATE INDEX IF NOT EXISTS idx_conversations_external ON conversations(external_id);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, seq);
CREATE INDEX IF NOT EXISTS idx_reports_conv ON reports(conversation_id);

