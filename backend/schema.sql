-- D1 schema. Apply with:
--   npx wrangler d1 execute ai-chat-archive --remote --file=schema.sql

CREATE TABLE IF NOT EXISTS conversations (
  id          TEXT PRIMARY KEY,          -- our own UUID
  external_id TEXT UNIQUE NOT NULL,      -- ChatGPT share id (dedupe/upsert key)
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

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, seq);
CREATE INDEX IF NOT EXISTS idx_reports_conv ON reports(conversation_id);
