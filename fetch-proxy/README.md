# fetch-proxy

A tiny, token-gated Deno Deploy function that fetches ChatGPT share pages for
the archive's Worker. It exists because **a Cloudflare Worker cannot reach
chatgpt.com at all** — `fetch()` is 403'd by the unstoppable `Cf-Worker` header,
and raw sockets are blocked because chatgpt.com sits on Cloudflare's own IP
ranges.

Deployed on [Deno Deploy](https://deno.com/deploy) (free, no card) so the request
egresses from a non-Cloudflare datacenter IP instead of from the Worker.

- `main.ts` — the function. `GET /?u=<encoded chatgpt share url>` returns the raw
  HTML; gated by an `x-fetch-token` header (fail-closed if unset), and only
  accepts `chatgpt.com/share/*` / `chat.openai.com/share/*`.

**Full explanation, the three walls (fetch 403 / socket block / CORS), the
reusable setup, and a "reuse it for another project" checklist →
[`../docs/fetch-proxy.md`](../docs/fetch-proxy.md).**
