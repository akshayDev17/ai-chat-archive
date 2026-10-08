# ai-chat-archive

**The AI Digest** — archive your AI assistant chat sessions (transcript + markdown
report) into Cloudflare D1 by pasting a ChatGPT share link, and read them back as
a newspaper. See `plan.md` for the full design.

## Screenshots

### The front page

Sessions laid out as a newspaper front page: two leads above the fold, a third
heading the flow beneath, then three columns that each run to their own ragged
depth. The paste-a-link box sits in the masthead. Every story is a link through
to its report.

![The front page](docs/screenshots/01-front-page.png)

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

![Reading a session — chat view](docs/screenshots/03-reader-chat.png)

### Signing in

Email → one-time code → verifying → confirmed. In production this flow is
enforced by **Cloudflare Access** in front of the app (see `plan.md`).

| Email | One-time code |
| :---: | :---: |
| ![Email entry](docs/screenshots/04-login-email.png) | ![One-time code](docs/screenshots/05-login-code.png) |

| Verifying | Confirmed |
| :---: | :---: |
| ![Verifying](docs/screenshots/06-login-verifying.png) | ![Confirmed](docs/screenshots/07-login-confirmed.png) |

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
│   ├── ports.py            # interfaces (ABCs): ShareFetcher, PayloadDecoder,
│   │                       #   ConversationParser, ConversationRepository
│   ├── models.py           # domain dataclasses (Message, Citation, Conversation)
│   ├── service.py          # ShareService use case (SSRF guard + orchestration)
│   ├── serializers.py      # wire shape shared by the Worker and local server
│   ├── repository.py       # D1ConversationRepository
│   ├── sqlite_repository.py    # local development
│   ├── local_repository.py     # in-memory test double
│   └── chatgpt/            # vendor implementations of the ports
│       ├── fetcher.py      # HttpShareFetcher
│       ├── decoder.py      # ChatGptFlightDecoder (RSC flight unpacking)
│       └── parser.py       # ChatGptParser (transcript / report / citations)
├── local_server.py         # runs the same service off-Cloudflare (stdlib only)
├── test/test_decode.py     # local end-to-end test (stdlib only)
├── schema.sql              # D1 schema
├── wrangler.jsonc          # Worker + D1 binding config
└── pyproject.toml
```

`ports.py` is the extension seam: adding Gemini/Claude/Elicit later means adding a
new `chatgpt/`-style package implementing the same ports — no existing code
changes (Open/Closed + Dependency Inversion). The repository port already has
three implementations (D1, SQLite, in-memory) behind one interface.

### Frontend (TypeScript + Next.js)

```
frontend/
├── app/
│   ├── layout.tsx              # fonts + metadata
│   ├── page.tsx                # the front page
│   ├── globals.css             # the whole design system
│   ├── login/page.tsx          # sign-in flow
│   ├── sessions/[id]/page.tsx  # the reader
│   └── components/
│       ├── Masthead.tsx        # masthead + the paste-a-link box
│       ├── UploadInline.tsx    # paste-a-link form
│       ├── FrontPage.tsx       # leads + ragged columns
│       ├── StoryLink.tsx       # one story rendered as a link
│       ├── SessionReader.tsx   # reader with the Report/Chat toggle
│       └── AuthFlow.tsx        # email → code → verifying → confirmed
├── lib/
│   ├── api.ts                  # typed API client
│   ├── story.ts                # headline / standfirst / citation count
│   ├── citations.ts            # links [n] markers to the bibliography
│   └── chat.ts                 # strips source tokens, groups tool notes
├── types/index.ts
├── package.json
└── tsconfig.json
```

## Run locally

```bash
# backend (proves the decoder/parser against a real share link)
python3 backend/test/test_decode.py

# backend API server (SQLite, persists to backend/archive.db)
python3 backend/local_server.py            # → http://127.0.0.1:8787

# frontend (separate terminal)
cd frontend && npm install && npm run dev  # → http://127.0.0.1:3000
```

The frontend defaults to `http://127.0.0.1:8787` for the API; override with
`NEXT_PUBLIC_API_BASE`.

## Deploy

1. `cd backend && npx wrangler d1 create ai-chat-archive` → copy the id into
   `wrangler.jsonc`.
2. `npx wrangler d1 execute ai-chat-archive --remote --file=schema.sql`.
3. `npx wrangler deploy`.
4. Put the frontend (or the whole app) behind **Cloudflare Access** (One-time
   PIN + email allowlist) so only you can browse/upload. Also set
   `"workers_dev": false` so the Worker is reachable only through the
   Access-protected hostname.
