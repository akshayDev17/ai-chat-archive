-- 0001_init — the whole schema, as it stood when the project was first deployed.
--
-- Apply with:
--   npx wrangler d1 migrations apply ai-chat-archive --remote
--
-- This replaced a bare `schema.sql` run through `d1 execute`. That worked once
-- and gave nothing afterwards: no record of what had been applied, no rollback,
-- and no way to tell a database that had the schema from one that did not.
-- `d1 migrations` keeps what it applied in a `d1_migrations` table, takes a
-- backup before applying, rolls the failed migration back while leaving earlier
-- ones in place, and skips the confirmation prompt in CI.
-- https://developers.cloudflare.com/d1/reference/migrations/
--
-- Every later change is a new numbered file here. Never edit this one: a
-- database somewhere has already run it, and editing it would make that database
-- and a fresh one disagree with nothing to show for it.
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
-- handed to a client. The one endpoint that returns an address is /api/desk/whoami,
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

-- Web sources cited inside a message, from the payload's
-- `message.metadata.content_references`.
--
-- These are NOT the same thing as `reports.citations`. The report's citations
-- are whatever the generated markdown happened to write into its own
-- bibliography; these are the conversation's own sources — the pills ChatGPT
-- renders inline and the "Sources" panel at the foot of a reply. They were
-- being discarded, which is why a reply citing eighteen URLs could show none.
--
-- Keyed by (conversation_id, message_seq) rather than by a message id because
-- the sequence is what the reader has: the transcript is delivered as an
-- ordered list, and a message has no stable id of its own on the wire.
--
-- `spans` is a JSON array of [start, end] offsets into that message's text —
-- plural, because one source is often cited several times in one reply, and
-- collapsing them would leave later inline markers with nothing to point at.
CREATE TABLE IF NOT EXISTS sources (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  message_seq     INTEGER NOT NULL,      -- order of the message in the conversation
  seq             INTEGER NOT NULL,      -- order within the message (= source index)
  kind            TEXT NOT NULL,         -- cite | link
  title           TEXT,
  url             TEXT NOT NULL,         -- canonical, tracking params removed
  attribution     TEXT,
  pub_date        INTEGER,
  spans           TEXT                   -- JSON [[start,end], ...]
);

CREATE INDEX IF NOT EXISTS idx_sources_message ON sources(conversation_id, message_seq, seq);

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

