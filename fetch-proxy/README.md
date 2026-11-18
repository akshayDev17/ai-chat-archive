# fetch-proxy

A tiny, token-gated Deno Deploy function that fetches ChatGPT share pages for
the archive's Worker. It exists because **a Cloudflare Worker cannot reach
chatgpt.com at all** — `fetch()` is 403'd by the unstoppable `Cf-Worker` header,
and raw sockets are blocked because chatgpt.com sits on Cloudflare's own IP
ranges.

Deployed on [Deno Deploy](https://deno.com/deploy) (free, no card) so the request
egresses from a non-Cloudflare datacenter IP instead of from the Worker.

- `main.ts` — the function. `GET /?u=<encoded url>` returns the target's body;
  gated by an `x-fetch-token` header (fail-closed if unset). For a share page
  that is the raw HTML; it also fetches GenUI images and the two hops of a
  share's linked report file (resolver, then signed blob).
- `allowlist.ts` — the only URL shapes it accepts: `chatgpt.com/share/*` /
  `chat.openai.com/share/*`, `images.openai.com/static-rsc-*`,
  `chatgpt.com/backend-anon/share/<uuid>/file_from_message/<uuid>?file_path=/mnt/data/…`
  and `sdmntpr*.oaiusercontent.com/files/<id>/raw?<signature>`.
  `allowlist_test.ts` pins them: `deno test` here.

**Full explanation, the three walls (fetch 403 / socket block / CORS), the
reusable setup, and a "reuse it for another project" checklist →
[`../docs/fetch-proxy.md`](../docs/fetch-proxy.md).**
