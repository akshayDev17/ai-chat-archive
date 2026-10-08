# Plan — Personal AI Chat Session Archive (akshayprabhakant.com)

> Status: Planning (no code yet)
> Last updated: 2026-10-08

## 1. Summary

- Display my AI assistant conversation history (ChatGPT, Gemini, Claude,
  Elicit) on akshayprabhakant.com in a ChatGPT-style layout; each session
  carries its transcript plus an AI-generated markdown summary that serves as
  its thumbnail.

## 2. Goal

- Archive AI assistant sessions — the transcript plus an AI-generated markdown
  summary — in a Cloudflare database.
- Serve them on the site in a ChatGPT-style list/detail view (summary = thumbnail).
- Own the data: keep a local copy on an external hard drive.
- v1 scope: **ChatGPT only** (schema is vendor-ready via a `source` column).

## 3. Final approach (decided)

### Storage (Cloudflare)
- **D1** (relational SQLite) is the system of record: `conversations`,
  `messages` (transcript), `reports` (summary markdown).
- Summary markdown stored **raw** in a D1 `TEXT` column; rendered to HTML only
  at read time.
- **R2** for binary only (images inside a summary), referenced by URL from D1.
- **Upsert** keyed on the share/conversation id — re-pasting refreshes, never
  duplicates.

### Capture (the "connector")
- Paste a **ChatGPT share link** into the site — no browser extension, no local
  script, no MCP/Actions, no server-side scraper.
- The Worker fetches the share and extracts the **full transcript** plus
  **per-turn citations/sources** (title + URL).
- Share the chat as **"anyone with the link"** (unguessable UUID); **revoke the
  link after ingest**.

### Site / UI
- `akshayprabhakant.com` — the **entire browse + upload** surface is behind login.
- Logged-in view has two tabs: **"My GPT sessions"** (reads from D1) and
  **"Upload a GPT session"** (paste a share link).

### Auth
- **Cloudflare Access** with **One-time PIN** (email OTP).
- An **Access policy allowlists only my email** — blocked emails never even
  receive a code.

### Abuse hardening (layered)
- Identity: Cloudflare Access (above).
- **SSRF guard**: the pasted string must match `https://chatgpt.com/share/<uuid>`
  exactly; nothing else is ever fetched.
- **Rate limiting + quotas** on pastes and total storage.
- **Idempotent upsert** so repeats are cheap, not storage-bloat.

### Fetch mechanics
- Try the JSON endpoint `backend-api/share/{id}` first; on a 403, fall back to
  parsing the page HTML's embedded data; escalate to Cloudflare Browser
  Rendering only if both fail.

### Backup / data ownership
- `wrangler d1 export` → `.sql` dump to the external drive.
- `rclone` to mirror R2 locally.
- D1 **Time Travel** as Cloudflare-side point-in-time recovery (not an offline
  backup).

### Deferred (not v1)
- Full-text search (FTS5) — conflicts with `d1 export` (virtual tables), so held
  back.
- Other vendors (Gemini, Claude, Elicit) — added as separate share-link parsers
  later.

## 4. Data model

```sql
-- One row per AI conversation
CREATE TABLE conversations (
  id          TEXT PRIMARY KEY,        -- our own UUID
  external_id TEXT UNIQUE NOT NULL,    -- source provider's conversation id (dedupe/upsert key)
  title       TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  source      TEXT DEFAULT 'chatgpt',  -- provider: chatgpt | gemini | claude | elicit
  report_id   TEXT                     -- FK -> reports.id (the "thumbnail")
);

-- The transcript itself (verbatim)
CREATE TABLE messages (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  seq             INTEGER NOT NULL,    -- order within the conversation
  role            TEXT NOT NULL,       -- user | assistant | tool | system
  content         TEXT,
  created_at      TEXT
);

-- The AI-generated markdown summary
CREATE TABLE reports (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  title           TEXT,
  markdown        TEXT NOT NULL,       -- raw markdown summary (render at read time)
  skill           TEXT,                -- which summary skill/version produced it
  generated_at    TEXT
);

CREATE INDEX idx_messages_conv ON messages(conversation_id, seq);
CREATE INDEX idx_reports_conv ON reports(conversation_id);
```

Design notes:
- `external_id UNIQUE` = idempotency key (upsert).
- Store raw markdown, not HTML.
- Images referenced in a summary live in R2; D1 stores text + URL.

## 5. Share-link facts (from research)

- **Transcript**: fully present in the share (JSON, or embedded in page HTML).
  Caveat: hidden chain-of-thought "reasoning" text is stripped from public
  shares; visible user/assistant messages survive.
- **Citations/sources**: present, with title + URL per turn.
- **Generated-file contents**: auth-gated — a share exposes only the file's
  name/size, not its bytes. This is the open question in §6.
- **Fetching**: the JSON endpoint `backend-api/share/{id}` is bot-protected
  (403 from a datacenter); the share *page* HTML returns 200 anonymously with
  the data embedded in a string table (parseable, more work).
- Reference tools: `pionxzh/chatgpt-exporter`, `AbdoKnbGit/shared_url_mcp`,
  `gin337/ChatGPTReversed`.

## 6. Verified — the share link carries everything (including the summary)

Empirically confirmed against a real share (`.../share/6ac7f3f2-...`):

- **Page is anonymously reachable** (HTTP 200); the JSON endpoint 403s (bot wall),
  so the Worker fetches the page and decodes the embedded data.
- **Transcript**: fully present (roles, code blocks, execution output).
- **Citations**: fully present — inline `[n]` markers plus a bibliography with
  titles and URLs.
- **Summary markdown**: **fully present and complete** — the `chat-to-markdown-report`
  skill emits it as a Python string in the assistant's code block
  (`report = r"""# ..."""`), which survives the share. The `sandbox:/mnt/data/*.md`
  "file" is just a code-interpreter artifact; the content lives in the code.

⇒ **No fallback field needed.** The parser extracts the triple-quoted `report`
string for `reports.markdown`, the messages for `messages`, and the bibliography
for citations.

Caveat: this holds because the skill writes the markdown as *visible code text*.
If a future skill emitted the summary as a `sediment://` file attachment only
(no inline text), the content would be name-only — keep the skill's
"emit as a Python string" shape.

## 7. Next steps

1. ✅ Q5b verified — the share carries transcript + citations + full summary.
2. Provision Cloudflare: D1 + R2 + Worker + Pages + Access (One-time PIN).
3. Build the Worker ingest (fetch page → decode → extract transcript/summary/citations → D1 upsert) and the two-tab UI.
4. Wire backups (`wrangler d1 export` + `rclone`) to the external drive.

## 8. References

- [Cloudflare D1 overview](https://developers.cloudflare.com/d1/)
- [Choose a data or storage product](https://developers.cloudflare.com/workers/platform/storage-options/)
- [D1 SQL API — JSON](https://developers.cloudflare.com/d1/sql-api/query-json/)
- [D1 Import/export](https://developers.cloudflare.com/d1/best-practices/import-export-data/)
- [D1 Time Travel](https://developers.cloudflare.com/d1/reference/time-travel/)
- [R2 Rclone](https://developers.cloudflare.com/r2/examples/rclone/)
- [Cloudflare Access — One-time PIN](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/one-time-pin/)
- [Cloudflare Browser Rendering](https://developers.cloudflare.com/browser-rendering/)
- [ChatGPT Markdown Exporter (share parsing reference)](https://github.com/devcxl/chatgpt-markdown-exporter)
- [shared_url_mcp (share endpoint reference)](https://github.com/AbdoKnbGit/shared_url_mcp)
