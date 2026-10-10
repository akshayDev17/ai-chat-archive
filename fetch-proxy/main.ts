// Fetch proxy for ChatGPT share pages, deployed on Deno Deploy.
//
// Why it exists: a Cloudflare Worker cannot reach chatgpt.com at all —
// fetch() is 403'd by the unstoppable Cf-Worker header, and raw sockets to
// Cloudflare's own IP ranges are blocked. This tiny function runs off
// Cloudflare, so its fetch carries no Cf-Worker header and egresses from a
// non-Cloudflare IP.
//
// Hardened against being an open proxy two ways:
//   1. it only accepts https://chatgpt.com/share/* and chat.openai.com/share/*
//   2. every request must carry the shared FETCH_TOKEN

const ALLOWED_ORIGINS = new Set([
  "https://chatgpt.com",
  "https://chat.openai.com",
]);

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

  if (!ALLOWED_ORIGINS.has(parsed.origin) || !parsed.pathname.startsWith("/share/")) {
    return new Response("not an allowed share url", { status: 400 });
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
