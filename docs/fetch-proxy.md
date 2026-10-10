# Fetching a Cloudflare-fronted page from a Worker — the off-Cloudflare proxy

**The one-line lesson:** a Cloudflare Worker cannot fetch a page that is both
(a) served through Cloudflare and (b) behind bot protection. Two independent
walls close in, and neither is ours to remove. The fix is to **stop originating
the request from the Worker** — run a tiny token-gated proxy on non-Cloudflare
infrastructure and have the Worker call it.

This project hits the wall with ChatGPT share pages. The pattern generalises to
any target that is Cloudflare-fronted and bot-protected (many AI vendors are).

---

## The two walls (both Cloudflare/OpenAI policy, not our bug)

| transport | result | why |
|---|---|---|
| Worker `fetch()` | **403** | The runtime stamps every subrequest with a `Cf-Worker` header that cannot be overridden. The target's edge sees it and refuses. |
| raw socket (`cloudflare:sockets`) | **0 bytes / `Stream was cancelled`** | Cloudflare blocks outbound TCP sockets to its **own IP ranges** (loop prevention). If the target is on Cloudflare, its IP is in those ranges. |

Evidence:

- `fetch()` is 403'd even for `https://chatgpt.com/robots.txt` — blanket bot
  protection, not path-specific. Reported unresolved on OpenAI's own forum:
  <https://community.openai.com/t/cimd-metadata-fetch-from-cloudflare-workers-still-blocked-with-403-regression/1392271/>
- The socket block is documented: *"Outbound TCP sockets to Cloudflare IP ranges
  are blocked"* — <https://developers.cloudflare.com/workers/runtime-apis/tcp-sockets/>
  (which also says, for HTTP, use `fetch()` instead — but `fetch()` is the other wall).

## Why curl works and the Worker doesn't

`chatgpt.com` resolves to Cloudflare anycast IPs — `172.64.155.209`,
`104.18.32.47`, both inside Cloudflare's published ranges. Verify for yourself:

```bash
dig +short chatgpt.com A                 # Cloudflare IPs
curl -s https://www.cloudflare.com/ips-v4/ | grep -E '172\.64|104\.16'   # those ranges
```

| who | egress | result |
|---|---|---|
| curl / your laptop | your IP, no header, no loop-block | **200** |
| Worker in `wrangler dev` | **your laptop** (local workerd) | **200** — this is the trap |
| Worker deployed | Cloudflare's edge | **blocked** (both walls) |

**The trap:** `wrangler dev` runs the Worker on your machine, so its sockets
egress from *your* IP. Local success proves nothing about production.
`wrangler dev --remote` runs on the edge and reproduces the production block —
the way to catch this during development rather than after deploying.

## Why the browser can't just fetch it either (the CORS wall)

A browser *reaches* chatgpt.com fine (it's your IP, same as curl), but a JS
`fetch()` from another origin cannot *read* the response: chatgpt.com sends no
`Access-Control-Allow-Origin`, and CORS is a server-side opt-in we do not
control. So there are three walls in total:

1. `fetch()` 403 — Worker only (the `Cf-Worker` header).
2. socket block — Worker only (Cloudflare IP ranges).
3. CORS — browser only (missing `Access-Control-Allow-Origin`).

None of them are fixable from our side.

## The fix: a token-gated off-Cloudflare proxy

The request must originate from somewhere that is (1) not Cloudflare and (2) not
bot-flagged by the target. A ~60-line function on Deno Deploy (free, no card)
does it. It lives in `fetch-proxy/main.ts` in this repo.

```
browser ──/api/*──▶ Python Worker ──x-fetch-token──▶ Deno proxy ──▶ chatgpt.com
 (same-origin)      (parse + D1)                    (non-Cloudflare egress)
```

The Deno function only accepts `chatgpt.com/share/*` and
`chat.openai.com/share/*` (no open proxy / SSRF), requires a shared
`x-fetch-token` header (**fail-closed** if the token is unset), and returns the
raw HTML.

### Reusable setup (≈5 minutes)

1. Deploy `fetch-proxy/main.ts` to **Deno Deploy** (free, no card):
   [deno.com/deploy](https://deno.com/deploy) → **New App** → deploy from this
   repo, app directory `fetch-proxy`, entrypoint `main.ts`. **Framework preset:
   None, runtime Dynamic** (it is not the Next.js frontend).
2. Generate a token and set it as an env var, **Production** context, marked
   secret:

   ```bash
   openssl rand -hex 32        # the shared token
   ```

   key `FETCH_TOKEN`, value = that token.
3. Deploy, then test from any machine:

   ```bash
   curl -s -o /dev/null -w "%{http_code} %{size_download}\n" \
     "https://<app>.deno.dev/?u=https%3A%2F%2Fchatgpt.com%2Fshare%2F6ac877f9-c690-83ec-8394-61b0727ba5eb" \
     -H "x-fetch-token: <token>"
   ```

   `200` + a large byte count = the datacenter IP got through. `403` = this
   host's IPs are also bot-flagged; try Vercel/Netlify/Render (different egress,
   different reputation).

### Worker side

The Worker holds two things and calls the proxy instead of the target:

- `PROXY_BASE_URL` — a wrangler `vars` entry (not secret).
- `FETCH_TOKEN` — a wrangler secret: `npx wrangler secret put FETCH_TOKEN`.

`backend/chat_archive/chatgpt/proxy_fetcher.py` implements the `ShareFetcher`
port against the proxy (`?u=<encoded target>` + the `x-fetch-token` header), and
`entry.py` injects it. The rest of the backend — service, decoder, parser,
repositories — is unchanged, because it never knew which fetcher it was handed.

## Reuse checklist for another project

1. Confirm the diagnosis: `curl` from your machine → 200; Worker `fetch()` →
   403; `wrangler dev --remote` reproduces the 403.
2. Drop `fetch-proxy/main.ts` into a Deno Deploy app (or any non-Cloudflare
   host), widening `ALLOWED_ORIGINS` to your target if it is not ChatGPT.
3. Set the shared token as a secret on **both** the proxy and your Worker.
4. Point your fetcher at the proxy.
5. **Test one request before committing** — the proxy egresses from a datacenter
   IP, which *can* itself be bot-flagged even when the two Worker walls are gone.

## Alternatives considered, and rejected

- **`cloudflare:sockets`** — works in `wrangler dev` only; blocked in production
  by the Cloudflare-IP socket rule. See `docs/worker-fetch-block.md` for the
  full history.
- **Fetch from the browser** — CORS blocks reading the response.
- **ChatGPT "Export data"** — batch whole-account export, not per-share, and it
  does not necessarily preserve the per-message `content_references`.
- **Third-party scrape APIs** — they see your URLs, rate-limit, and are blocked
  by the same bot protection.
