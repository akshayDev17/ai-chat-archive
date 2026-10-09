# ai-chat-archive

**The AI Digest** — archive your AI assistant chat sessions (transcript + markdown
report) into Cloudflare D1 by pasting a ChatGPT share link, and read them back as
a newspaper. See `plan.md` for the full design.

## Screenshots

### The front page

Sessions laid out as a newspaper front page: two leads above the fold, a third
heading the flow beneath, then three columns that each run to their own ragged
depth. **Public, and the same edition for everyone.** The only control in the
masthead is Sign in — no import box, and no email address, on a page anyone can
read.

![The front page](docs/screenshots/01-front-page.png)

### The copy desk — `/chat-archives/desk`

Where a share link becomes a story. Reached by signing in, and gated: the
filing endpoint is the only write in the product. One job, one page — the form,
and nothing else. It is also the one place your address is shown back to you,
on a page that requires signing in.

![The copy desk](docs/screenshots/08-copy-desk.png)

### Reading a session — the report

The generated markdown report, rendered as an article. Inline `[n]` citation
markers are linked through to their entry in the bibliography at the foot of the
page.

![Reading a session — report view](docs/screenshots/02-reader-report.png)

### Reading a session — the chat

The same session as a verbatim transcript, rendered the way ChatGPT renders it:
user turns in a bubble, assistant turns as plain prose, redacted tool calls
collapsed into a single counted note. The Report/Chat toggle at the top swaps
between the two views.

Inline `[n]` markers are the conversation's own citations — ChatGPT's source
pills, restored rather than deleted (see `docs/sources.md`). Each assistant turn
carries the vendor's mark, and the `···` under it opens that reply's sources.

![Reading a session — chat view](docs/screenshots/03-reader-chat.png)

### Signing in

Email → one-time code → verifying → confirmed. The *screen* is ours; what
verifies the code depends on the identity provider (see `docs/access-limits.md`
for why Cloudflare Access cannot sit behind this page). Until the provider is
chosen, a local server stands in for it.

| Email | One-time code |
| :---: | :---: |
| ![Email entry](docs/screenshots/04-login-email.png) | ![One-time code](docs/screenshots/05-login-code.png) |

| Verifying | Confirmed |
| :---: | :---: |
| ![Verifying](docs/screenshots/06-login-verifying.png) | ![Confirmed](docs/screenshots/07-login-confirmed.png) |

## Who can see what

| Route | Anyone | Signed in |
|---|---|---|
| `/chat-archives` — the edition | **every filed story** | same |
| `/chat-archives/<share-id>` — one story | **readable** | readable |
| `/chat-archives/login` — sign in | screen | redirects to the desk |
| `/chat-archives/desk` — file copy | redirected to sign in | the filing form |

One rule, stated once: **public reads, private writes.** The only gated actions
are `POST /api/ingest` and `GET /api/filings`.

Ownership is still recorded (`conversations.owner_email`) but it is *provenance,
not an access boundary*, and it never leaves the server on a read: it keys the
upsert, scopes the desk's "your recent filings" query, and is returned by exactly
one endpoint — `/api/whoami`, to the person it belongs to. No listing carries an
address, because `/api/sessions` answers anonymous visitors and a per-row address
there would publish every filer's email in the JSON. The upsert key remains
`(owner_email, external_id)`, so two people can file the same share link and both
are in the edition.

Provenance is also what makes the other sources cheap to add: `conversations.source`
is already `chatgpt | gemini | claude | elicit`, and each becomes another
implementation of the parser ports rather than a change to this model.

### Why the copy desk and the sign-in screen have their own paths

Cloudflare Access scopes an application by **path**, and a query string is not
part of a path:

> "Query strings (such as `?foo=bar`) are not supported in Access application
> paths." — [Access application paths](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/app-paths/)

So `?desk` or `?login` can never be given their own policy. As real paths they
can, and Access's wildcard rules separate all four routes:

| Access application | Covers | Does not cover |
|---|---|---|
| `…/chat-archives` | the edition | login, desk, stories |
| `…/chat-archives/login` | sign in | everything else |
| `…/chat-archives/desk` | **filing — the one path to protect** | everything else |
| `…/chat-archives/*` | login, desk, stories | the edition |

Access has **no HTTP-method selector** at all (its documented selectors are
emails, IPs, countries, device posture, IdP groups, service tokens — no verbs),
so "public GET, private POST" is written in `entry.py`, not in a policy.
`docs/access-limits.md` has the citations.

## Structure

```
backend/     Python — Cloudflare Workers backend (ingest + list API, D1)
frontend/    TypeScript + Next.js — the newspaper front page and reader
design/      HTML explorations behind the front page and auth screens
docs/        research notes and screenshots
plan.md      the agreed design (storage, auth, hardening, verified facts)
```

### Backend (Python, SOLID/OOP)

```
backend/
├── chat_archive/
│   ├── entry.py            # Workers entrypoint + composition root + routing
│   ├── auth.py             # IdentityProvider port: who is asking?
│   ├── urls.py             # path/query helpers + the open-redirect guard
│   ├── ports.py            # interfaces (ABCs): ShareFetcher, PayloadDecoder,
│   │                       #   ConversationParser, ConversationRepository
│   ├── models.py           # domain dataclasses (Message, Citation, Conversation)
│   ├── service.py          # ShareService use case (SSRF guard + orchestration)
│   ├── serializers.py      # wire shape shared by the Worker and local server
│   ├── repository.py       # D1ConversationRepository
│   ├── sqlite_repository.py    # local development (+ in-place schema migration)
│   ├── local_repository.py     # in-memory test double
│   └── chatgpt/            # vendor implementations of the ports
│       ├── fetcher.py      # HttpShareFetcher
│       ├── decoder.py      # ChatGptFlightDecoder (RSC flight unpacking)
│       └── parser.py       # ChatGptParser (transcript / report / citations)
├── local_server.py         # runs the same service off-Cloudflare (stdlib only)
├── test/test_decode.py     # network test: decode + parse a real share link
├── test/test_ownership.py  # offline contract test: all 3 repositories agree
├── test/test_urls.py       # offline: the open-redirect guard
├── migrations/             # D1 migrations (0001_init.sql is the schema)
├── wrangler.jsonc          # Worker + D1 binding config
└── pyproject.toml
```

Two seams do the design work:

- **`ports.py`** — adding Gemini/Claude/Elicit later means adding a new
  `chatgpt/`-style package implementing the same ports; no existing code changes
  (Open/Closed + Dependency Inversion). The repository port already has three
  implementations (D1, SQLite, in-memory) behind one interface.
- **`auth.py`** — the router asks "who is this?" and never learns how the answer
  was produced. Swapping Cloudflare Access for a self-hosted OTP flow is one new
  class.

`test/test_ownership.py` is a *contract test*: the same assertions run against
all three repositories, so an implementation that leaks another reader's shelf
fails for that implementation specifically.

### Frontend (TypeScript + Next.js)

```
frontend/
├── app/
│   ├── layout.tsx              # fonts + metadata
│   ├── page.tsx                # NOT the archive — points at /chat-archives
│   ├── globals.css             # the whole design system
│   ├── chat-archives/page.tsx        # the edition (public)
│   ├── chat-archives/login/page.tsx  # sign in (own path)
│   ├── chat-archives/desk/page.tsx   # the copy desk — file a share link
│   ├── chat-archives/[id]/page.tsx   # the reader (?chat for the transcript)
│   └── components/
│       ├── Masthead.tsx        # the nameplate
│       ├── MastheadActions.tsx # top-right slot: Sign in, or Copy desk
│       ├── FrontPage.tsx       # leads + ragged columns
│       ├── CopyDesk.tsx        # the filing form + your recent filings
│       ├── StoryLink.tsx       # one story rendered as a link
│       ├── SessionReader.tsx   # reader with the Report/Chat toggle
│       ├── SourcesPanel.tsx    # a reply's sources, behind its ···
│       ├── SourceIcon.tsx      # vendor mark, else a domain monogram
│       └── AuthFlow.tsx        # email → code → verifying → confirmed
├── lib/
│   ├── api.ts                  # typed API client + ApiError/isSignInRequired
│   ├── story.ts                # headline / standfirst / citation count
│   ├── citations.ts            # links [n] markers to the bibliography
│   ├── chat.ts                 # resolves citation tokens, groups tool notes
│   ├── vendors.ts              # vendor lookup + the marks in public/vendors
│   └── nav.ts                  # AFTER_SIGN_IN + safeNext (redirect guard)
├── public/vendors/            # chatgpt, gemini, claude marks (+ reserved slot)
├── types/index.ts
├── package.json
└── tsconfig.json
```

## Run locally

```bash
# backend offline contract test (no network, no Cloudflare account)
python3 backend/test/test_ownership.py

# backend network test (needs a live ChatGPT share link)
python3 backend/test/test_decode.py

# backend API server (SQLite, persists to backend/archive.db)
python3 backend/local_server.py            # → http://127.0.0.1:8787

# frontend (separate terminal)
cd frontend && npm install && npm run dev  # → http://127.0.0.1:3000

# typecheck + production build — safe to run WHILE the dev server is up
cd frontend && npx tsc --noEmit && npm run build:check
```

Use `build:check`, not `build`, while developing. `next dev` and `next build`
both own `.next/` and both rewrite the webpack chunk graph inside it, so a plain
build against a live dev server corrupts it — the build replaces chunks the dev
server's `webpack-runtime.js` still points at, and every request then fails with
`Cannot find module './NN.js'`. `build:check` sets `NEXT_DIST_DIR`, so it writes
`.next-build/` and the two never touch. If it has already happened:

```bash
kill $(lsof -nP -iTCP:3000 -sTCP:LISTEN -t); rm -rf frontend/.next; cd frontend && npm run dev
```

`build:check` is what CI should run; `build` is for a clean checkout.

Open <http://127.0.0.1:3000/chat-archives>.

The frontend defaults to `http://127.0.0.1:8787` for the API; override with
`NEXT_PUBLIC_API_BASE`.

Copy `.env.local.example` to `.env.local` to enable local sign-in. The backend
is **anonymous by default**, so the real journey is walkable on your machine:

```
/chat-archives      Sign in →        (signed out)
/chat-archives/login                email → code → verified
/chat-archives/desk File a session  (now signed in)
/chat-archives      Copy desk →     (masthead flips)
```

"Send one-time code" calls `POST /api/dev/session`, which sets the
`archive_dev_email` cookie (with `NEXT_PUBLIC_DEV_AUTH=1`). No OTP is sent and
none is checked — it stands in for the identity provider, which is still
undecided. The desk has a **Sign out** so you can walk the flow again.

```bash
DEV_EMAIL=you@example.com python3 backend/local_server.py  # skip signing in
curl -H 'X-Archive-Email;' localhost:8787/api/whoami       # force anonymous
curl -H 'X-Archive-Email: guest@example.com' localhost:8787/api/filings
```

Identity precedence locally: `X-Archive-Email` header → `archive_dev_email`
cookie → `DEV_EMAIL`. All three exist **only** in `local_server.py`. The
deployed Worker derives identity from its `IdentityProvider` and never trusts a
client header or cookie, because a client can lie — and an endpoint that mints
identities is exactly what must never ship.

## Deploy

Nothing is deployed yet. **`docs/deploy.md` is the full guide** — it opens with
the one decision that shapes the rest (one Worker or two, one domain or two) and
carries the citations for every claim.

The short version:

1. `cd backend && npx wrangler d1 create ai-chat-archive --location apac` → the
   id lands in `wrangler.jsonc`. A location hint is a hint; a jurisdiction is
   permanent and set at creation only.
2. `npx wrangler d1 migrations apply ai-chat-archive --remote`.
3. `uv run pywrangler deploy`.
4. `curl https://akshayprabhakant.com/api/health` must report
   `"context_available": true`. `ctx.access` is documented for JavaScript only,
   so `/api/health` is how the deployed Python Worker proves or disproves it
   exists — instead of failing closed and 401ing everything with no explanation.
5. Keep `"workers_dev": false`, and reach the API through a **Route** rather than
   a Custom Domain: a Custom Domain "point[s] all paths of a domain or subdomain
   to your Worker", which would swallow the frontend.

