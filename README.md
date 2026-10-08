# ai-chat-archive

Archive AI assistant chat sessions (transcript + markdown summary) into
Cloudflare D1 by pasting a ChatGPT share link. See `plan.md` for the full design.

## Structure

```
backend/     Python — Cloudflare Workers backend (ingest + list API, D1)
frontend/    TypeScript + Next.js — the two-tab UI
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
│   ├── repository.py       # D1ConversationRepository
│   └── chatgpt/            # vendor implementations of the ports
│       ├── fetcher.py      # HttpShareFetcher
│       ├── decoder.py      # ChatGptFlightDecoder (RSC flight unpacking)
│       └── parser.py       # ChatGptParser (transcript / report / citations)
├── test/test_decode.py     # local end-to-end test (stdlib only)
├── schema.sql              # D1 schema
├── wrangler.jsonc          # Worker + D1 binding config
└── pyproject.toml
```

The `ports.py` interfaces are the extension seam: adding Gemini/Claude/Elicit
later means adding a new `chatgpt/`-style package that implements the same
three ports — no existing code changes (Open/Closed + Dependency Inversion).

### Frontend (TypeScript + Next.js)

```
frontend/
├── app/
│   ├── layout.tsx
│   ├── page.tsx
│   ├── globals.css
│   └── components/          # ArchiveView (tabs), SessionList, UploadForm
├── lib/api.ts               # typed API client
├── types/index.ts           # Session / IngestResult types
├── package.json
└── tsconfig.json
```

## Run locally

```bash
# backend (proves the decoder/parser against a real share link)
python3 backend/test/test_decode.py

# frontend (dev server)
cd frontend && npm install && NEXT_PUBLIC_API_BASE=http://127.0.0.1:8787 npm run dev
```

## Deploy

1. `cd backend && npx wrangler d1 create ai-chat-archive` → copy the id into
   `wrangler.jsonc`.
2. `npx wrangler d1 execute ai-chat-archive --remote --file=schema.sql`.
3. `npx wrangler deploy`.
4. Put the frontend (or the whole app) behind **Cloudflare Access** (One-time
   PIN + email allowlist) so only you can browse/upload.
