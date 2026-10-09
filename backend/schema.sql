-- D1 schema. Apply with:
--   npx wrangler d1 execute ai-chat-archive --remote --file=schema.sql
--
-- Provenance model
-- ----------------
-- Every conversation records who filed it (conversations.owner_email). That is
-- PROVENANCE, not an access boundary: the edition is public, so list_all()
-- deliberately does not filter on it. It survives because it answers "what did
-- I file?" for the copy desk.
--
-- It is also NEVER published on a listing. list_all() and list_recent() both
-- omit owner_email from their SELECT, because /api/sessions answers anonymous
-- visitors and a per-row address there would publish every filer's email in the
-- JSON. The column is read for its WHERE clause and its uniqueness, not to be
-- handed to a client. The one endpoint that returns an address is /api/whoami,
-- to the person it belongs to.
--
-- The dedupe/upsert key is the PAIR (owner_email, external_id), not
-- external_id alone: two people may legitimately file the same ChatGPT share
-- link and both copies belong in the edition. A global UNIQUE(external_id)
-- would let whoever filed first block everyone else. An unauthenticated ingest
-- has no filer and is rejected by the Worker before it reaches this table.

CREATE TABLE IF NOT EXISTS conversations (
  id          TEXT PRIMARY KEY,          -- our own UUID
  owner_email TEXT NOT NULL,             -- who filed it (normalized, lowercase)
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

-- One copy of a share link per filer. Replaces the old UNIQUE(external_id):
-- uniqueness is scoped to whoever filed it.
CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_owner_external
  ON conversations(owner_email, external_id);

-- The public edition: ORDER BY created_at DESC (no owner filter).
-- The scoped read (your own filings): WHERE owner_email = ? ORDER BY created_at DESC.
CREATE INDEX IF NOT EXISTS idx_conversations_owner_recent
  ON conversations(owner_email, created_at DESC);

-- Public permalink lookup: WHERE external_id = ?  (no owner filter).
CREATE INDEX IF NOT EXISTS idx_conversations_external ON conversations(external_id);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, seq);
CREATE INDEX IF NOT EXISTS idx_reports_conv ON reports(conversation_id);

