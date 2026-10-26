-- 0003_media_paths — store each image under its story and message in R2.
--
-- 0002 keyed copied images by a hash of their URL, all in one flat `img/`
-- folder, deduplicated across stories. That made the bucket unreadable, tied
-- stories together through shared objects, and left an object with no trace of
-- its story once the story was retracted.
--
-- Now each image's path is fixed when the story is filed:
--
--   chat-archives/<share_id>/<chatgpt_message_id>/<NN>     (+ .<ext> once copied)
--
-- `share_id` is the share link's id (the story's URL), the message id is
-- ChatGPT's own (stable across re-filings), and NN is the image's order within
-- the message. Two filers of the same link share the folder; different stories
-- never share an object. Retracting the last copy of a story deletes its folder.
--
-- Rows filed before this have no path — their message ids were never stored —
-- so they cannot be copied into the new layout. Already-copied ones keep
-- serving from `img/`; anything not yet copied is marked failed with the fix:
-- file the story again, which re-creates its rows with paths.

ALTER TABLE media ADD COLUMN object_path TEXT;

UPDATE media
   SET status = 'failed',
       last_error = 'filed before images were stored per story — file this story again to copy them'
 WHERE object_path IS NULL AND status != 'copied';

-- Re-filing and a second filer of the same link look up copied images by path.
CREATE INDEX IF NOT EXISTS idx_media_object_path ON media(object_path);
