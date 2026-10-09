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
- One domain, four routes:
  - `https://akshayprabhakant.com/chat-archives` — the edition. Public.
  - `https://akshayprabhakant.com/chat-archives/<share-id>` — one story. Public.
  - `https://akshayprabhakant.com/chat-archives/login` — the sign-in screen.
  - `https://akshayprabhakant.com/chat-archives/desk` — the **copy desk**: file a
    share link. The only gated surface.
- The site root (`/`) is deliberately *not* the archive; it only points at
  `/chat-archives`.

### Who may see what (decided)

One rule: **public reads, private writes.**

| Action | Needs an identity? | Why |
|---|---|---|
| Read the edition (front page) | **No** — public | It is a newspaper. Everyone reads the same edition, from every filer. |
| Read one story by its `share-id` | **No** — public | The point of sending someone a story link is that it works. |
| File a share link | **Yes** | The only action that spends money and storage. |
| See your own filings | **Yes** | Provenance, not a boundary — it answers "what did I file?". |

Ownership is *provenance*, not an access boundary, and it **never leaves the
server on a read**: `conversations.owner_email` keys the upsert, scopes the
desk's "your recent filings" query, and is returned by exactly one endpoint
(`/api/whoami`, to its owner). No listing row carries an address — `/api/sessions`
is served to anonymous visitors, so a per-row address would publish every filer's
email in the JSON. It is retained in the upsert key — the pair
`(owner_email, external_id)` — so two people can file the same share link and both
appear in the edition.

The `source` column is what makes the other vendors cheap: `chatgpt | gemini |
claude | elicit` already exists, and each new one is another parser
implementation behind the existing ports, not a change to this model.

### Auth

- **Identity is a port**, not a vendor call: `IdentityProvider.identify(request)`
  returns an email or `None`. The router never learns how the answer was made.
- Implementations: `CloudflareAccessIdentity` (production), `DevIdentity` (local
  server), `AnonymousIdentity` (tests / safe default).
- **Access cannot do the whole job on its own** — it matches hostname and path,
  never the HTTP method or the query string, so it cannot express "public GET,
  private POST", and its one-time-PIN screen is served from a different domain
  (`<team>.cloudflareaccess.com`), so it cannot be restyled or replaced.
  See `docs/access-limits.md`.
- The route split is what makes Access *useful* though: `/chat-archives/desk` is
  a real path, so it — and only it — can be put behind Access.
- Access is optional as an *outer* lock; the authority for the table above is the
  Worker. A self-hosted OTP flow can be added later as one more
  `IdentityProvider` implementation.
- Rule regardless of mechanism: **fail closed.** No identity → 401. There is no
  default reader.

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
-- One row per archived conversation, ON ONE READER'S SHELF
CREATE TABLE conversations (
  id          TEXT PRIMARY KEY,        -- our own UUID
  owner_email TEXT NOT NULL,           -- who filed it (provenance, lowercased)
  external_id TEXT NOT NULL,           -- source provider's conversation id
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
  citations       TEXT,                -- JSON array of {n,title,url}
  skill           TEXT,                -- which summary skill/version produced it
  generated_at    TEXT
);

-- The upsert key is the PAIR, not external_id alone: the same share link may
-- legitimately sit on two readers' shelves at once.
CREATE UNIQUE INDEX idx_conversations_owner_external ON conversations(owner_email, external_id);

-- Every front-page query: WHERE owner_email = ? ORDER BY created_at DESC.
CREATE INDEX idx_conversations_owner_recent ON conversations(owner_email, created_at DESC);

-- Public permalink lookup, which must NOT filter by owner.
CREATE INDEX idx_conversations_external ON conversations(external_id);

CREATE INDEX idx_messages_conv ON messages(conversation_id, seq);
CREATE INDEX idx_reports_conv ON reports(conversation_id);
```

Design notes:
- `(owner_email, external_id) UNIQUE` = idempotency key, scoped per filer. A
  global `external_id UNIQUE` would be wrong: it would let the first person to
  file a link block everyone else from filing it.
- Ownership is a **column, not a URL prefix**. A story's permalink never
  contains an email, so the email can never leak into a shared link.
- Two listings, named so neither can be mistaken for the other: `list_all` is
  the public edition (unscoped, by design), `list_recent` is one filer's own.
  The scoped one refuses an empty owner (raises, rather than returning `[]`) —
  a lost identity and a genuinely empty list must not look the same.
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
