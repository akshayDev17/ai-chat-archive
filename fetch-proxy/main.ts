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
// on Cloudflare too, so the Worker is refused there for the same reason.
//
// Hardened against being an open proxy two ways:
//   1. it only accepts chatgpt.com/share/*, chat.openai.com/share/* and
//      images.openai.com/static-rsc-*
//   2. every request must carry the shared FETCH_TOKEN

const SHARE_ORIGINS = new Set([
  "https://chatgpt.com",
  "https://chat.openai.com",
]);

const IMAGE_ORIGIN = "https://images.openai.com";
const IMAGE_PATH_PREFIX = "/static-rsc-";

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

  const isImage = parsed.origin === IMAGE_ORIGIN && parsed.pathname.startsWith(IMAGE_PATH_PREFIX);
  const isShare = SHARE_ORIGINS.has(parsed.origin) && parsed.pathname.startsWith("/share/");
  if (!isImage && !isShare) {
    return new Response("not an allowed url", { status: 400 });
  }

  if (isImage) {
    const image = await fetch(parsed, {
      headers: { "User-Agent": USER_AGENT, "Accept": "image/avif,image/webp,image/*" },
      redirect: "follow",
    });
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
