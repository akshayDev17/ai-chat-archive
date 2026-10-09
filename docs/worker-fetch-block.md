# Why ingest returns 403, and what to do about it

**Root cause, proven:** Cloudflare's Workers runtime adds a `Cf-Worker` header to
every outbound `fetch()`. ChatGPT is itself behind Cloudflare, its edge sees that
header, and it refuses the request. The header cannot be overridden. A raw TCP
socket from the same Worker is not stamped, and gets `200 OK`.

## The evidence

All four from the same machine, at the same moment, against the same share page:

| how | result |
|---|---|
| `urllib` (Python) | **200**, 786,616 bytes |
| `curl` | **200** |
| `curl` with no User-Agent at all | **200** |
| Worker `fetch()` | **403** |
| Worker `fetch()` with no headers | **403** |
| Worker `fetch()` with `Cf-Worker: ""` | **403** |

So it was not the User-Agent, not the header set, and not the network. A probe
against `httpbin.org/headers` showed exactly what differs:

```
Worker fetch() sends:        curl sends:
  Accept                       Accept
  Cf-Worker: uaprobe…   ←      (nothing here)
  Host                         Host
  User-Agent                   User-Agent
```

`Cf-Worker` is added by the runtime. Setting it explicitly to an empty string
does not remove it — the runtime replaces it with its own value:

```
httpbin with Cf-Worker:''  →  "Cf-Worker": "uaprobe.example.com"
chatgpt with Cf-Worker:''  →  403
```

Cloudflare documents the receiving side of this: a zone can write a rule keyed on
[`cf.worker.upstream_zone`](https://developers.cloudflare.com/rules/transform/examples/add-request-header-subrequest-other-zone/),
which is "set to empty if the current request is not a Workers subrequest". Any
zone can therefore tell a Worker subrequest apart — and OpenAI's does.

**This is not our bug and not a misconfiguration.** It is reported on OpenAI's
own forum, unresolved, with the same signature:

> "Finding the same problem… using a local worker in Wrangler… Worker `fetch()`
> returns the OpenAI/Cloudflare challenge with `403`… A direct `curl` request
> from the same machine returns `200`"
> — <https://community.openai.com/t/cimd-metadata-fetch-from-cloudflare-workers-still-blocked-with-403-regression/1392271/2>

## The way through: `cloudflare:sockets`

Cloudflare's [TCP sockets](https://developers.cloudflare.com/workers/runtime-apis/tcp-sockets/)
API opens a raw connection, which no runtime headers are added to. Python Workers
can reach it — the `workers` SDK special-cases it by name:

```python
from workers.utils import import_from_javascript

sockets = import_from_javascript("cloudflare:sockets")
conn = sockets.connect(to_js({"hostname": "chatgpt.com", "port": 443}),
                       to_js({"secureTransport": "on"}))
```

`workers/utils.py` carries the message *"Only 'cloudflare:workers' and
'cloudflare:sockets' are available in the global scope"* — this module is
supported, not a hack.

Verified in `pywrangler dev`, from inside the Worker, same request:

```
RAW SOCKET status line: HTTP/1.1 200 OK
RAW SOCKET bytes     : 200,003
Content-Type: text/html; charset=utf-8
Server: cloudflare
```

That is the share page. It proves both the cause and the escape.

## What this means for the design

The `ShareFetcher` port was built for exactly this. `HttpShareFetcher` cannot
work from a Worker, so it becomes the local/off-Cloudflare implementation, and a
`SocketShareFetcher` implements the same port for the Worker. Nothing else
changes: `ShareService`, the decoder, the parser and the repositories are
untouched, and they are the parts with the tests.

The work is not trivial, because raw sockets mean speaking HTTP by hand:

- HTTP/1.1 request framing, and correctly handling the response's
  `Transfer-Encoding: chunked` — the share page is chunked, so a naive reader
  gets a truncated body.
- Redirects, timeouts, a response size ceiling.
- TLS is handled by `secureTransport: "on"`; TLS itself is not ours to write.

## The alternatives, and why they are worse

**Cloudflare Browser Rendering** — a real browser fetches the page, so the
request looks like a browser rather than a Worker. It would probably work, and it
is a paid feature with a heavier runtime per request. Worth revisiting if the
socket path proves brittle.

**Ingest somewhere other than the Worker** — a local script or a GitHub Action
posting to the API. It sidesteps the block entirely, but it means the desk's
"paste a link" UI cannot work, which is the product.

**Ask OpenAI to allowlist** — not a plan.

## Built, and verified against the working path

`chat_archive/chatgpt/socket_fetcher.py` implements the port. `entry.py` injects
it; `local_server.py` keeps `HttpShareFetcher`. Nothing else changed — the
service, decoder, parser and repositories do not know which they were handed.

The check that matters is not "does it return 200" but "is the transcript
complete", because the failure mode of a botched chunked read is a page that
parses perfectly and is short. Same session, both paths:

| | Worker, over a socket | local Python, urllib |
|---|---|---|
| messages | 24 | 24 |
| report | 13,636 chars | 13,636 chars |
| sources | 15 | 15 |
| transcript | 14,019 chars | 14,019 chars |

Identical. The parsing itself is pure functions over bytes with tests in
`test/test_socket_fetcher.py` — including the one that matters, a chunk
promising more bytes than arrived must raise rather than return what it got.

## Two things it got wrong first

Both found by running it, not by reading it.

**`build_request` returned `bytes` and was handed to `TextEncoder`, which takes
a string.** It does not raise on bytes — it coerces with `str()` and sends the
Python *repr*, so the server received `b'GET /share/… HTTP/1.1\r\n'` and
answered **400**. It returns `str` now, and a test says so.

**The request looked fine and still failed.** The first socket attempt returned
400 rather than 403, which was progress and also a trap: it would have been easy
to read that as "the socket approach does not work" and abandon it.

## Still open

The same block applies to anything else a Worker fetches on our behalf — if the
archive later pulls from Gemini, Claude or Elicit, each of those fetches needs
the socket path too. `FetchError` from a 403 should be the signal to reach for it.

No timeout on the socket read. A stalled connection would rely on the Worker's
own limits rather than failing cleanly, which is worth adding before this is
under load.
