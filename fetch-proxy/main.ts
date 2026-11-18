// Fetch proxy for ChatGPT share pages, deployed on Deno Deploy.
//
// Why it exists: a Cloudflare Worker cannot reach chatgpt.com at all —
// fetch() is 403'd by the unstoppable Cf-Worker header, and raw sockets to
// Cloudflare's own IP ranges are blocked. This tiny function runs off
// Cloudflare, so its fetch carries no Cf-Worker header and egresses from a
// non-Cloudflare IP.
//
// It also fetches the images ChatGPT's GenUI replies show
// (images.openai.com/static-rsc-*), which the Worker copies into R2: that host is
// on Cloudflare too, so the Worker is refused there for the same reason. And it
// fetches a share's linked report file, in the two signed-out hops the share
// page's own "Download" button makes: the resolver on chatgpt.com, then the
// short-lived signed blob it answers on sdmntpr*.oaiusercontent.com.
//
// Hardened against being an open proxy two ways:
//   1. it only accepts the strict URL shapes in allowlist.ts
//   2. every request must carry the shared FETCH_TOKEN

import { allowedKind } from "./allowlist.ts";

// The share page caps the resolver's answer at 64 KB too.
const REPORT_RESOLVER_MAX_BYTES = 64 * 1024;
const REPORT_FILE_MAX_BYTES = 1024 * 1024;

const USER_AGENT =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36";

Deno.serve(async (req) => {
  if (req.method !== "GET") {
    return new Response("method not allowed", { status: 405 });
  }

  // Fail closed: refuse everything if the secret is not configured.
  const expected = Deno.env.get("FETCH_TOKEN");
  if (!expected || req.headers.get("x-fetch-token") !== expected) {
    return new Response("forbidden", { status: 403 });
  }

  const target = req.url.includes("?") ? new URL(req.url).searchParams.get("u") : null;
  if (!target) {
    return new Response("missing ?u=<share url>", { status: 400 });
  }

  let parsed: URL;
  try {
    parsed = new URL(target);
  } catch {
    return new Response("bad url", { status: 400 });
  }

  const kind = allowedKind(parsed);
  if (kind === null) {
    return new Response("not an allowed url", { status: 400 });
  }

  if (kind === "reportResolver") {
    return await fetchCapped(parsed, "application/json", REPORT_RESOLVER_MAX_BYTES);
  }

  if (kind === "reportFile") {
    // The signed query is part of `parsed`, passed through untouched.
    return await fetchCapped(parsed, "text/markdown,text/plain", REPORT_FILE_MAX_BYTES);
  }

  if (kind === "image") {
    // Bounded, so a hung upstream fails this one image instead of outliving
    // the Worker's cron run (which would leave the whole batch stuck copying).
    let image: Response;
    try {
      image = await fetch(parsed, {
        headers: { "User-Agent": USER_AGENT, "Accept": "image/avif,image/webp,image/*" },
        redirect: "follow",
        signal: AbortSignal.timeout(20_000),
      });
    } catch {
      return new Response("upstream timed out", { status: 504 });
    }
    if (image.status !== 200) {
      return new Response(`upstream ${image.status}`, { status: 502 });
    }
    // Bytes, untouched, with the upstream type: the Worker keys and serves by it.
    return new Response(image.body, {
      status: 200,
      headers: { "content-type": image.headers.get("content-type") ?? "application/octet-stream" },
    });
  }

  const upstream = await fetch(parsed, {
    headers: {
      "User-Agent": USER_AGENT,
      "Accept": "text/html,application/xhtml+xml",
      "Accept-Language": "en-US,en;q=0.9",
    },
    redirect: "follow",
  });

  if (upstream.status !== 200) {
    return new Response(`upstream ${upstream.status}`, { status: 502 });
  }

  const html = await upstream.text();
  return new Response(html, {
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
});

// One report hop: no redirects, bounded in time and in size, and the upstream
// content type passed through so the Worker can tell a report from an error page.
async function fetchCapped(url: URL, accept: string, maxBytes: number): Promise<Response> {
  let upstream: Response;
  let body: Uint8Array | null;
  try {
    upstream = await fetch(url, {
      headers: { "User-Agent": USER_AGENT, "Accept": accept },
      redirect: "error",
      signal: AbortSignal.timeout(20_000),
    });
    if (upstream.status !== 200) {
      await upstream.body?.cancel();
      return new Response(`upstream ${upstream.status}`, { status: 502 });
    }
    body = await readCapped(upstream, maxBytes);
  } catch {
    return new Response("upstream failed", { status: 504 });
  }
  if (body === null) {
    return new Response("upstream too large", { status: 502 });
  }
  return new Response(body, {
    status: 200,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/octet-stream" },
  });
}

// The body, or null once it grows past maxBytes.
async function readCapped(response: Response, maxBytes: number): Promise<Uint8Array | null> {
  const chunks: Uint8Array[] = [];
  let size = 0;
  for await (const chunk of response.body ?? []) {
    size += chunk.byteLength;
    if (size > maxBytes) return null;
    chunks.push(chunk);
  }
  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}
