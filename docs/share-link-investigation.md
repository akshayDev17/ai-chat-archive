# What a ChatGPT `chatgpt.com/share/{id}` link actually exposes

Investigation for evaluating a design where a Cloudflare Worker receives a pasted share
link and must extract the full conversation. Every claim is tagged **CONFIRMED**
(primary-source evidence: reverse-engineering source code, live HTTP observation) or
**UNCERTAIN** (inference, or not independently verified). No guessing.

## TL;DR for the design

- A public share's full transcript **is** machine-readable, anonymously, via the JSON
  endpoint `https://chatgpt.com/backend-api/share/{id}` **or** by parsing the share page
  HTML itself (which embeds the same data server-side).
- **Generated-file artifacts are the blocker.** The share exposes the file *list*
  (name, MIME type, size) but **not the file bytes**. Bytes sit behind `sediment://`
  pointers that resolve only to an **authenticated** session. An anonymous Worker cannot
  download a generated markdown/code artifact; it can only report that the file exists.
- The share page is a client-rendered React SPA with the conversation embedded server-side
  in an index-deduplicated string table (not readable prose), so a plain HTTP scraper must
  parse that table or call the JSON endpoint.
- Live test from this environment: the share **page** returned HTTP 200 anonymously
  (827 KB, full data embedded); the `backend-api/share` **JSON endpoint** returned HTTP 403
  with a Cloudflare "Enable JavaScript and cookies" challenge.

---

## 1. Data endpoint

**CONFIRMED** — the JSON endpoint exists and is
`GET https://chatgpt.com/backend-api/share/{share_id}`.

- `AbdoKnbGit/shared_url_mcp` `src/providers/chatgpt.ts` calls exactly this URL and reads it
  as JSON. Header comment: "The app's own backing endpoint returns the same conversation as
  ordinary JSON: `GET https://chatgpt.com/backend-api/share/<id>`".
  <https://github.com/AbdoKnbGit/shared_url_mcp/blob/main/src/providers/chatgpt.ts>
- `pionxzh/chatgpt-exporter` `src/api.ts` defines
  `shareConversationApi = urlcat(apiUrl, '/share/:id', { id })` where `apiUrl` is the
  `backend-api` base. <https://github.com/pionxzh/chatgpt-exporter/blob/master/src/api.ts>

**CONFIRMED** — no user authentication is required for the *transcript* of a public
("anyone with the link") share.

- `shared_url_mcp` fetches the endpoint with **no** auth headers; it only adds a
  browser-like `User-Agent`/`sec-ch-*`/`Referer` set to get past bot protection
  (`src/http.ts`). Auth (`Authorization: Bearer …` or a full `Cookie`) is used **only** when
  downloading attachment *bytes*, never for the conversation JSON.
- The payload itself carries an `is_public` flag (observed live, §5), consistent with
  "anyone with the link" sharing. (The OpenAI Help Center article "Sharing conversations and
  scheduled tasks in ChatGPT" documents public sharing but is Cloudflare-gated — see note
  at the end.)

**CONFIRMED (live observation) — bot protection matters.** The endpoint sits behind
Cloudflare bot management:

- `curl` with full browser-like headers to
  `https://chatgpt.com/backend-api/share/6a879b4b-b3d4-83ec-963c-dbb64702224e` →
  `HTTP 403`, `text/html`, a Cloudflare challenge page ("Enable JavaScript and cookies to
  continue").
- The share **page** (`chatgpt.com/share/{id}`) with the same headers →
  `HTTP 200`, `text/html; charset=utf-8`, 827,288 bytes.

**UNCERTAIN / risk for the Worker.** `shared_url_mcp` claims the JSON endpoint is reachable
with only browser-like headers. My datacenter egress was still challenged with those headers.
The pass/fail is likely IP-reputation and/or cookie dependent. A Cloudflare Worker's egress
should be assumed to hit the same challenge; the anonymous share **page** is the more
reliably-reachable target, at the cost of parsing a string table (§5).

## 2. Transcript

**CONFIRMED** — the payload contains the full ordered transcript, roles, code blocks, and
text.

- `shared_url_mcp` parses `linear_conversation` (authoritative array of nodes) with a
  fallback to walking the legacy `mapping` graph (`orderNodes`). Each node's `message` has
  `author.role` (`user` / `assistant` / `tool` / `system`) and `content.content_type`
  (`text`, `code` with `language`, `execution_output`, `thoughts`, `multimodal_text`, …).
- `pionxzh/chatgpt-exporter` `src/api.ts` defines the full `ConversationNodeMessage` union
  (text parts, `code` + `language`, `execution_output`, `tether_browsing_display`, etc.) and
  walks `mapping` via `current_node` → parent chain.
- Live share-page HTML contains a `linear_conversation` array (observed, §5).

**CONFIRMED caveat — reasoning text is stripped from shares.** `shared_url_mcp` reports:
"ChatGPT removes the reasoning text from public shares, so only the envelopes were present"
(`thoughts` nodes arrive with empty content). Chain-of-thought/reasoning is therefore
**not** recoverable from a share; it is present in the authenticated conversation only.

## 3. Citations / sources (web-search footnotes)

**CONFIRMED (schema)** — citations are part of the message metadata, with URL + title.

- `pionxzh/chatgpt-exporter` `src/api.ts` defines:
  - `metadata.content_references: ContentReference[]` with types `sources_footnote`,
    `grouped_webpages`, `webpage`, `file`, …; each carries `items` / `sources` /
    `supporting_websites` / `safe_urls`, where each source has `title`, `url`,
    `attribution`.
  - legacy `metadata.citations` (`start_ix`/`end_ix` + `metadata{url,title}`) and
    `_cite_metadata`.
- `src/utils/citations.ts` renders these into Markdown source lists (this is where
  `getSourcesFootnoteSources` pulls the end-of-answer "Sources" footnotes).

**CONFIRMED (share path)** — citations survive into shared conversations and are parsed
from shares. `chatgpt-exporter` treats share pages and the share API identically: a share is
loaded through the same `ApiConversation` type and the same `transformContentReferences`
export path as an authenticated conversation (`src/share.ts`, `src/api.ts` →
`fetchConversation` `__share__` branch). The `sources_footnote` / `file` citation handling
is exercised by `tests/citations.test.ts`.

**CONFIRMED (live)** — the `content_references` and `citations` fields are present in the
share page's embedded payload (observed verbatim `"citations",[],"content_references",[]`
inside the live HTML; that particular message simply had no web citations).

**UNCERTAIN (minor)** — I could not capture a live share that actually had *non-empty*
web-search citations, so I did not verify byte-for-byte that a populated
`content_references` array (with URLs) is retained verbatim in a share rather than
emptied. The schema field is present, and the exporter renders sources for shares, which
strongly implies non-empty citations survive. Note also that `shared_url_mcp` does **not**
currently extract citations — that is a tool gap, not evidence they are absent from the
payload.

## 4. Generated file artifacts ("View files in chat") — MOST IMPORTANT

**CONFIRMED** — generated/downloadable files are **not** inlined in the share. Only their
**metadata** is present.

- `shared_url_mcp` `src/providers/chatgpt.ts`:
  - The share JSON carries `metadata.attachments[]` (each with `name`, `mime_type`,
    `size`) and, for image/`file` content parts, an `asset_pointer`/`file_id` of the form
    `sediment://…`.
  - Header comment: "Attachment *bytes* live behind `sediment://` pointers that the page
    resolves client-side."
  - It builds `AttachmentRef`s with `status: "pointer-only"` and a note "Referenced by a
    `sediment://` pointer that ChatGPT resolves client-side."
- **CONFIRMED (live)** — observed in the share page HTML:
  `"asset_pointer","sediment://file_000000008cf881fa8e5ba030e64d25b4?shared_conversation_id=6a879b4b-b3d4-83ec-963c-dbb64702224e"`.

**CONFIRMED** — the file *bytes* are gated behind an authenticated session; anonymous
share viewers cannot download them.

- `shared_url_mcp` `src/providers/chatgpt.ts` (verbatim comments + code):
  - "Attachment bytes are gated behind an authenticated session: the public share page
    itself renders a login wall, so rendering it recovers nothing."
  - Download URL: `https://chatgpt.com/backend-api/files/{fileId}/download?shared_conversation_id={shareId}`,
    which "hands back a short-lived signed URL rather than bytes, so this is a two-step
    fetch" — and only works with `Authorization: Bearer <accessToken>` or a full `Cookie`
    header from an account that can open the conversation.

**Design conclusion.** A server holding only the share link can enumerate the generated
files (filename, MIME type, size) but **cannot obtain their contents** without the user's
ChatGPT session token/cookie. To deliver actual artifacts the Worker must either accept a
user-supplied access token/cookie, or degrade to showing the file list only.

(Related upstream reports about generated-file download fragility in general, for context:
<https://github.com/openai/codex/issues/48862>,
<https://github.com/openai/codex/issues/36933>.)

## 5. Rendering — server-rendered vs client-rendered

**CONFIRMED** — the share page is a **client-rendered React (React Router) SPA** with the
conversation **embedded server-side** in the HTML as hydration data.

- `pionxzh/chatgpt-exporter` `src/page.ts` reads it from
  `window.__reactRouterContext.state.loaderData['routes/share.$shareId.($action)'].serverResponse.data`
  and its `src/share.ts` notes: "Share pages usually embed the conversation in the React
  Router loader data."
- **CONFIRMED (live)** — the fetched share page HTML contains `__reactRouterContext`
  (×6), `serverResponse`, `linear_conversation`, `content_references`, `sediment`, and an
  `is_public` field.

**CONFIRMED** — the embedded data is an **index-deduplicated string table**, not readable
prose.

- `shared_url_mcp` README: a normal fetch of `chatgpt.com/share/...` gets "500 KB of HTML
  with the messages buried in an index-deduplicated string table" — which is why it uses
  the JSON endpoint instead.
- **CONFIRMED (live)** — the HTML shows Serde-style numeric back-references
  (e.g. `"_228":300`, `"_279":280`) and the raw strings (`"You're right. I can see the
  problem…"`) separated into a table. Parsing the conversation text therefore requires
  reconstructing the string table, not a simple HTML scrape.

**Design conclusion.** A plain HTTP server *can* retrieve the conversation anonymously from
the share page (HTTP 200), but it must either (a) parse the embedded string-table data, or
(b) call `backend-api/share/{id}` (clean JSON, but bot-protected). The visible DOM prose is
only produced after client-side hydration, so a non-JS scraper cannot read it directly.

## 6. Existing open-source tools

| Project | What it does | Link |
|---|---|---|
| `pionxzh/chatgpt-exporter` | Userscript (Tampermonkey) exporting to Text/HTML/Markdown/PNG/JSON. Handles **share pages** (reads embedded loader data, falls back to `backend-api/share`); renders citations and file names. | <https://github.com/pionxzh/chatgpt-exporter> |
| `AbdoKnbGit/shared_url_mcp` | MCP server that turns a share link into Markdown. ChatGPT path reads `backend-api/share/{id}` anonymously; documents the `sediment://` + auth-gated attachment behavior. | <https://github.com/AbdoKnbGit/shared_url_mcp> |
| `Dicklesworthstone/chat_shared_conversation_to_file` (`csctf`) | CLI (Bun) → Markdown + static HTML. For ChatGPT it scrapes the **rendered DOM** via headless Chromium (not the JSON endpoint). | <https://github.com/Dicklesworthstone/chat_shared_conversation_to_file> |
| `gin337/ChatGPTReversed` | Reverse-engineered reference for the (authenticated) `backend-api` — useful for the API shape, not share-specific. | <https://github.com/gin337/ChatGPTReversed> |
| `openai-markdown-share` (userscript) | Userscript for exporting shared conversations to Markdown. | <https://explore.market.dev/ecosystems/userscript/projects/openai-markdown-share> |
| `saboorcode/gpt-dialogues-extractor` | Extracts dialogues from ChatGPT/Bing/Poe share links to JSON. | <https://github.com/saboorcode/gpt-dialogues-extractor> |
| `svandragt/chatgpt-export` | Python CLI exporting a conversation to Markdown/JSON by conversation ID (authenticated `backend-api/conversation/{id}`, not share). | <https://github.com/svandragt/chatgpt-export> |

## Notes on sourcing limits

- OpenAI's own Help Center article was **Cloudflare-gated** (HTTP 403, JS challenge) from
  here, so it is cited by title/URL rather than quoted:
  <https://help.openai.com/en/articles/7925741-sharing-conversations-and-scheduled-tasks-in-chatgpt>.
- The `is_public` flag, `continue_conversation_url`, and the tooling behavior above are the
  evidence for "public / anyone-with-the-link" sharing semantics.
- The live observations used share id `6a879b4b-b3d4-83ec-963c-dbb64702224e` (a public
  share) fetched on the date of this investigation; the string-table serialization is
  explicitly noted by tool authors to "change shape between deploys," so treat the exact
  wire format as unstable while the semantic fields (transcript, citations, attachments,
  `sediment://` pointers) are stable.
