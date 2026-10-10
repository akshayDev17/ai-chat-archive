# Deploying

Everything in this file's scope is now live: the Python API Worker on `/api/*`,
Cloudflare Access + OTP on `/api/desk/*`, D1, and the frontend as a static export
served by a second Worker on `/chat-archives/*`. See §1 for the settled topology
and §4a/`docs/fetch-proxy.md` for how ingest reaches ChatGPT.

Read §1 first: it records the topology decision, now settled (static export).

---

## 0. What is already live

Verified against the real domain, not inferred:

| | |
|---|---|
| `akshayprabhakant.com/api/health` | **200** — `{"runtime": "python-workers", "context_available": true, "access_available": false}` |
| `akshayprabhakant.com/api/sessions` | 200, `{"sessions": []}` — D1 is reachable and empty |
| `akshayprabhakant.com/api/whoami` | `{"email": null}` — no Access configured, so everyone is anonymous |
| `akshayprabhakant.com/chat-archives` | **404, served by GitHub Pages** |
| `akshayprabhakant.com/` | 200 — **an existing portfolio site, also on GitHub Pages** |

So the API Worker is deployed and the route works. Two things follow.

**`context_available: true` settles §8 of `docs/access-limits.md`.** `ctx.access`
is documented for JavaScript only; this is production evidence that Python
Workers expose it. Access-as-identity is viable.

**The apex is occupied.** `akshayprabhakant.com/*` is a live portfolio on GitHub
Pages, and `/chat-archives` 404s there because GitHub Pages has never heard of
it. So the frontend Worker must be routed to a **path**, not to `/*`:

```
akshayprabhakant.com/api/*             → the Python Worker      (live)
akshayprabhakant.com/chat-archives/*   → the frontend Worker    (to do)
everything else                        → GitHub Pages           (untouched)
```

Routes are matched by pattern and "the most specific route pattern wins", and
requests matching no route fall through to the zone's origin. Attaching the
frontend to `/*` would replace the portfolio.

## 1. How many Workers, and on how many domains

**A Worker runs one runtime.** The API is Python (`compatibility_flags:
["python_workers"]`). Next.js, whenever it does any server rendering, produces a
*JavaScript* Worker. Those cannot be the same Worker, so the question is really
"does the frontend need a server at all?".

| | one domain, two Workers | one domain, one Worker | two domains |
|---|---|---|---|
| frontend | Next.js via OpenNext on `/*` | static export served by the Python Worker | either |
| API | Python Worker on `/api/*` | the same Worker | `api.` subdomain |
| CORS | none (same origin) | none | **required** |
| sign-in | one cookie | one cookie | **a second login** |
| deploys | two | one | two |

**One domain.** `CF_Authorization` is set per hostname, and the docs are explicit:
"Users who log in to `example.com` will be issued a cookie for `example.com`. When
the user's browser requests `api.mysite.com`, Cloudflare Access looks for a cookie
specific to `api.mysite.com`."
— <https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/>

Splitting the API onto a subdomain would mean a second sign-in for every reader,
and CORS on every request, in exchange for tidiness nobody can see.

Routing both from one hostname is ordinary: routes are matched by pattern and
"the most specific route pattern wins", so `/api/*` beats `/*`.
— <https://developers.cloudflare.com/workers/configuration/routing/routes/>

> **Use Routes, not a Custom Domain, for this.** "Unlike Routes, Custom Domains
> point all paths of a domain or subdomain to your Worker." A Custom Domain on the
> API Worker would swallow the frontend.

### Which frontend option

**Two Workers** (OpenNext) keeps `/chat-archives/<share-id>` working exactly as it
does today, because a server can render any path. It costs a second deploy and a
JavaScript runtime sitting in front of every asset.

**One Worker** requires the frontend to be a *static export* — no server
rendering at all — and then Workers Static Assets serves the files and only hands
`/api/*` to the Python script:

```jsonc
"assets": {
  "directory": "../frontend/out",
  "not_found_handling": "single-page-application",
  "run_worker_first": ["/api/*"]
}
```

"In this configuration, requests to `/api/*` routes will invoke the Worker script
first."
— <https://developers.cloudflare.com/workers/static-assets/binding/>

That is genuinely attractive here, because **this frontend fetches everything
from the API in the browser** — no SSR data fetching, no server actions, no
middleware. But it is blocked by one thing:

> `output: 'export'` cannot emit `/chat-archives/<share-id>`, because the set of
> share ids is unbounded. Next needs `generateStaticParams`, and we cannot
> enumerate every id that will ever exist.

So the single-Worker option needs the reader's URL to change shape — e.g.
`/chat-archives/story?id=<share-id>`, which is statically emittable. That is a
product decision, not a technical one, and it is the whole of the tradeoff.

**Decision (settled): static export, served by a second Worker — not OpenNext.**
The frontend is `output: 'export'` plus a tiny static-assets Worker
(`frontend/worker.js` + `frontend/wrangler.jsonc`) on `/chat-archives/*`,
`/_next/*` and `/vendors/*`, with a UUID rewrite onto the one prerendered
`_story.html`. The "unbounded share ids" blocker above was solved with a
placeholder `generateStaticParams` plus that rewrite, not by changing the URL.

> Cloudflare recommends **vinext** over OpenNext for Next.js on Workers, but
> vinext "Targets Next.js 16.x" and this app is on **15.5.27**. The docs route
> existing apps to OpenNext: "Use this guide to maintain an existing OpenNext
> application. Migrate to vinext when compatibility allows."
> — <https://developers.cloudflare.com/workers/framework-guides/web-apps/nextjs/>

---

## 2. Data: D1

### Create it once

```bash
cd backend
npx wrangler d1 create ai-chat-archive --location apac
```

Say **Yes** to the prompt that offers to write the binding into
`wrangler.jsonc`, or copy the `database_id` in by hand.

`--location` is a performance hint, not a promise: "Providing a location hint
does not guarantee that D1 runs in your preferred location." A **jurisdiction**
(`--jurisdiction eu|fedramp|us`) is a data-residency commitment, and unlike the
hint it is permanent: "Jurisdictions can only be set on database creation and
cannot be added or updated after the database exists."
— <https://developers.cloudflare.com/d1/configuration/data-location/>

### Apply the schema

```bash
npx wrangler d1 migrations apply ai-chat-archive --remote
npx wrangler d1 migrations apply ai-chat-archive --local   # for `wrangler dev`
```

`0001_init.sql` is the whole schema. Every later change is a new numbered file —
**never edit an applied one**, or a database that ran it and a fresh one will
disagree with nothing to show for it.

Why migrations rather than `d1 execute --file=schema.sql`: the runner records
what it applied in a `d1_migrations` table, takes a backup first, and "If applying
a migration results in an error, this migration will be rolled back, and the
previous successful migration will remain applied." In CI it also "skips the
confirmation step".
— <https://developers.cloudflare.com/d1/reference/migrations/>

### What the free plan gives

| | free |
|---|---|
| databases | 10 |
| max size per database | **500 MB** |
| rows read | **5,000,000 / day** |
| rows written | **100,000 / day** |
| Time Travel (restore window) | 7 days |
| queries per Worker invocation | 50 |

— <https://developers.cloudflare.com/d1/platform/limits/> and
<https://developers.cloudflare.com/d1/platform/pricing/>

Sizing, roughly: a 24-message session with 15 sources writes on the order of 40
rows. So the write ceiling is about **2,400 ingestions a day**, and a front-page
load reads one row per conversation plus one per report. Neither is close.

Two limits worth knowing before they bite:
- **Rows written per day.** `upsert` deletes and re-inserts a conversation's
  messages and sources on every re-file, so re-filing the same link costs the
  same as filing it fresh.
- **50 queries per invocation.** `list_all` is one query; `get` is four
  (conversation, messages, report, sources). Fine, but a "load everything"
  endpoint would not be.

### Backup

```bash
npx wrangler d1 export ai-chat-archive --remote --output=./backup-$(date +%F).sql
```

Caveats from the docs: "A running export will block other database requests",
and "Export is not supported for virtual tables" — which is why FTS5 is deferred
in `plan.md`. Free-plan Time Travel keeps only 7 days, so this is the durable
copy, and it belongs on the external drive.
— <https://developers.cloudflare.com/d1/best-practices/import-export-data/>

---

## 3. Data: R2 — not yet

**Nothing in the codebase reads or writes R2.** It is in `plan.md` and unwired,
and creating it now would be a bucket nothing touches and — on the free plan — a
checkout flow to complete.

Its first real use is already identified: **favicons, fetched once at ingest and
stored**, instead of asking a third party for them on every page load (see
`docs/sources.md`). Provision it when that work starts.

When it does:

```bash
npx wrangler r2 bucket create ai-chat-archive-media
```

```jsonc
"r2_buckets": [{ "binding": "MEDIA", "bucket_name": "ai-chat-archive-media" }]
```

Free tier: 10 GB-month storage, 1 million Class A operations, 10 million Class B,
and **egress is free** — "Egressing directly from R2, including via the Workers
API, S3 API, and r2.dev domains does not incur data transfer (egress) charges."
Bucket names must be lowercase, 3–63 characters, and "buckets are not public by
default".
— <https://developers.cloudflare.com/r2/pricing/>

---

## 4. Deploying the API Worker

```bash
cd backend
uv run pywrangler deploy
```

Python Workers deploy through `pywrangler`, which wraps Wrangler; `uv` is already
on this machine.
— <https://developers.cloudflare.com/workers/languages/python/>

Then confirm the identity wiring actually arrived:

```bash
curl https://akshayprabhakant.com/api/health
```

It should report `"context_available": true`. `ctx.access` is documented for
JavaScript only, so this endpoint exists to let the deployed Worker answer the
question rather than have us assume it — without it, a missing `self.ctx` fails
closed and every protected route 401s with no explanation.

---

## 4a. Ingest cannot fetch ChatGPT — read this first

**The Worker cannot reach chatgpt.com at all.** `fetch()` is 403'd (the
unstoppable `Cf-Worker` header), and raw sockets are blocked (chatgpt.com is on
Cloudflare's own IP ranges). The fix is a token-gated off-Cloudflare proxy on
Deno Deploy — see **`docs/fetch-proxy.md`** for the full story and the reusable
setup.

This does not block reading, the API, D1 or Access. It blocks *ingest* — the one
route that fetches an external page — and the proxy is how ingest reaches it.

## 4b. Running the real Worker locally — the sandbox

**`pywrangler dev` boots `entry.py` in the real workerd runtime against a local
D1, with no Cloudflare account and no credentials.** This is the thing to reach
for before deploying, and it is the only test that exercises the Worker as
deployed:

```bash
cd backend
uv sync                                              # installs pywrangler
npx wrangler d1 migrations apply ai-chat-archive --local
uv run pywrangler dev --port 8788
curl http://127.0.0.1:8788/api/health
```

`backend/local_server.py` is **not** this. It is a parallel implementation that
shares the service, parser and repositories but has its own HTTP handler and a
urllib fetcher. Anything specific to the Worker — the FFI, `js.fetch`, `ctx`,
D1's real API, whether the entrypoint even imports — is invisible to it.

That gap is not theoretical. The first time this Worker was ever booted it
failed three times in a row, on three things `local_server.py` cannot see:

1. **`ModuleNotFoundError: No module named 'workers'`** — plain `wrangler dev`
   does not wire the Python SDK in; `pywrangler` does.
2. **`ImportError: attempted relative import with no known parent package`** —
   `main` pointed at `chat_archive/entry.py`, so wrangler treated
   `chat_archive/` as the module root and flattened it, leaving `entry.py` with
   no parent package. Hence `backend/entry.py`, a two-line shim, so the root is
   `backend/` and `chat_archive/` is attached as a package.
3. **`TypeError: Incorrect type: the provided value is not of type 'Sequence'`
   and a dead isolate** — `HttpShareFetcher` called `js.fetch` with Python
   keyword arguments and a Python dict. JS has no keyword arguments, and its
   `init` must be a real JS object; passing one kills the isolate rather than
   raising. `worker_fetch` in `entry.py` now does the `to_js` conversion at the
   one place that knows it is talking to JavaScript.

Two of those would have failed identically in production. The third crashed the
runtime.

## 5. CI/CD

**GitHub Actions, not Workers Builds.** Workers Builds expects one Worker per
connected repository and root directory, and this repo has two deployables.
More importantly, neither Workers Builds nor `cloudflare/wrangler-action`
documents Python Worker support, and `uv` — which `pywrangler` needs — does not
appear in the Workers Builds build-image tooling table. A plain GitHub Actions
step runs the command we know works.

Two secrets, from **Account API tokens → Create Token → Edit Cloudflare Workers**:

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`

> **Add `D1 Edit` to the token by hand.** The Edit Cloudflare Workers template
> grants Workers Routes, Workers Scripts, KV, R2 and account settings — and **no
> D1 permission at all**. The token Workers Builds generates for you is missing
> D1 too. Miscoping means a deploy that succeeds and a migration that quietly
> cannot run.
> — <https://developers.cloudflare.com/fundamentals/api/reference/template/>

The docs on storing it: "Don't store the value of `CLOUDFLARE_API_TOKEN` in your
repository, as it gives access to deploy Workers on your account."
— <https://developers.cloudflare.com/workers/ci-cd/external-cicd/github-actions/>

**This is built: `.github/workflows/ci.yml`.**

- **`test`** — runs on every push and pull request. Backend tests twice, once as
  scripts and once through pytest, then `npm ci`, `npm run test:lib`,
  `tsc --noEmit` and `npm run build:check`.
- **`deploy-api`** — `main` only, after `test` passes: a secrets check, a D1
  permission check, `d1 migrations apply --remote`, `pywrangler deploy`, then a
  health check against the deployed Worker.

`test_decode.py` is the one backend test that touches the network, and the
workflow sets `ARCHIVE_LIVE_TESTS=0` so it skips itself. A build that goes red
because ChatGPT rate-limited us, or decided runner traffic looked like a bot,
says nothing about the change under test.

A **`deploy-frontend`** job is built: `npm ci` → static-export build
(`NEXT_PUBLIC_API_BASE='' npm run build`) → `npx wrangler deploy` (the frontend
Worker) → verify `/chat-archives` and a story URL both answer 200.

### The D1 check, and why it is a step of its own

The deploy job calls

```
GET /accounts/$CLOUDFLARE_ACCOUNT_ID/d1/database
```

before doing anything, and fails with the fix in the message if the token cannot
reach D1. Without it, a token missing `D1 Edit` produces the worst outcome
available: the migration step fails or does nothing, the deploy step succeeds,
and the pipeline is green on a database that never got its tables.

`scripts/setup-cloudflare.sh` performs the same check while you are still in the
browser, which is when it is cheap to fix.

---

## 6. Secrets and local development

Worker secrets: `npx wrangler secret put <KEY>`. Note that it "creates a new
version of the Worker and deploys it immediately".

Local values go in `.dev.vars` or `.env` next to the Wrangler config — "Choose to
use either `.dev.vars` or `.env` but not both" — and **neither is committed**:
"The `.dev.vars` and `.env` files should not be committed to git." Both patterns
are already in `.gitignore`.
— <https://developers.cloudflare.com/workers/configuration/secrets/>

---

## 7. Order of operations

1. Zone: `akshayprabhakant.com` on Cloudflare, with a proxied DNS record.
2. `wrangler d1 create --location apac` → id into `wrangler.jsonc`.
3. `wrangler d1 migrations apply ai-chat-archive --remote`.
4. `uv run pywrangler deploy` → `curl /api/health` and check
   `context_available: true`.
5. File one session through `/chat-archives/desk` and read it back.
6. Frontend topology decision (§1), then deploy it.
7. `wrangler d1 export` to the external drive, and put it on a schedule.
8. Only then, CI/CD.

Do 4 and 5 by hand before wiring a pipeline. A pipeline that deploys something
nobody has run once is a faster way to be confused.
