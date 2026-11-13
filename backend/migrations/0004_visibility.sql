-- 0004_visibility — each archive is public or private.
--
-- Until now every filed story was in the public edition. A filer can now keep
-- one to themselves: a `private` story is left out of the edition, its
-- permalink answers 404 to everyone but its filer, and its images are refused
-- on the public media route. The filer reads it through `/api/desk/*`.
--
-- Every story filed before this stays public — the column's default — so
-- nothing changes for today's readers.

ALTER TABLE conversations
  ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public'
  CHECK (visibility IN ('public', 'private'));

-- Serving an image now asks which stories show it: WHERE r2_key = ?.
CREATE INDEX IF NOT EXISTS idx_media_r2_key ON media(r2_key);
