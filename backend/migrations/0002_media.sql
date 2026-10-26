-- 0002_media — images shown by ChatGPT's GenUI components, and the job of
-- copying them into R2.
--
-- `<AsyncImage/>` and `<AsyncImageGroup/>` resolve to images.openai.com URLs the
-- archive does not control. Filing records one row per image as `pending`; a
-- once-a-minute Cron Trigger copies the bytes into the R2 bucket
-- `ai-chat-archive-media` and marks the row `copied`. Copying cannot happen
-- inside the filing request: the Workers Free plan allows 50 subrequests per
-- invocation, and one story alone has shown 28 images.
--
-- These rows ARE the job — there is no queue. Cloudflare Queues on Free keeps a
-- message for 24 hours, so a copy that died overnight would vanish; a row stays
-- until it is done.
--
--   pending  → copying  → copied
--      ↑          │
--      └─ retry ──┴→ failed   (after 3 attempts; the filer can retry)
--
-- A row left `copying` longer than five minutes belonged to a run that was
-- killed, and the next run reclaims it (`claimed_at`).
--
-- Keyed by (conversation_id, message_seq) like `sources`, and placed within the
-- message by `tag_start`: the code-point offset of the component's tag in the
-- stored text. `ordinal` orders the images of one group.

CREATE TABLE IF NOT EXISTS media (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  message_seq     INTEGER NOT NULL,
  tag_start       INTEGER NOT NULL,
  ordinal         INTEGER NOT NULL,
  content_url     TEXT NOT NULL,      -- images.openai.com source of the bytes
  origin_url      TEXT,               -- the page the image came from
  status          TEXT NOT NULL DEFAULT 'pending',
  attempts        INTEGER NOT NULL DEFAULT 0,
  last_error      TEXT,
  claimed_at      TEXT,
  r2_key          TEXT,               -- img/<sha256(content_url)>.<ext>, once copied
  content_type    TEXT
);

CREATE INDEX IF NOT EXISTS idx_media_message ON media(conversation_id, message_seq, tag_start, ordinal);

-- The copier's two questions: "what is pending?" and "what has gone stale?"
CREATE INDEX IF NOT EXISTS idx_media_status ON media(status, claimed_at);

-- Re-filing and cross-story reuse look up already-copied images by URL.
CREATE INDEX IF NOT EXISTS idx_media_content_url ON media(content_url);
