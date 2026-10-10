/**
 * Serves the static export of the archive at /chat-archives/*.
 *
 * The Next.js app is exported to `out/` and uploaded as static assets; this
 * Worker's only job is to route the one dynamic path — a story permalink
 * `/chat-archives/<share-id>` — onto the single prerendered `[id]` page
 * (`chat-archives/_story.html`). The client reads the real id from the URL, so
 * one HTML file serves every story.
 *
 * Everything else falls through to the ASSETS binding, which applies Cloudflare's
 * clean-URL HTML handling (`/chat-archives` → `chat-archives.html`) and serves
 * the `_next/*` chunks and `vendors/*` marks.
 */
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    // Share ids are UUIDs; `login`, `desk`, `_story`, and the front page are not.
    if (/^\/chat-archives\/[0-9a-fA-F-]{36}\/?$/.test(url.pathname)) {
      return env.ASSETS.fetch(new URL('/chat-archives/_story', url));
    }
    return env.ASSETS.fetch(request);
  },
};
