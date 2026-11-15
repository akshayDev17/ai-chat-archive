/**
 * Serves the static export of the archive at /chat-archives/*.
 *
 * The Next.js app is exported to `out/` and uploaded as static assets; this
 * Worker routes the one dynamic path — a story permalink
 * `/chat-archives/<share-id>` — onto the single prerendered `[id]` page
 * (`chat-archives/_story.html`). The client reads the real id from the URL, so
 * one HTML file serves every story.
 *
 * Everything else falls through to the ASSETS binding, which applies Cloudflare's
 * clean-URL HTML handling (`/chat-archives` → `chat-archives.html`) and serves
 * the `_next/*` chunks and `vendors/*` marks.
 *
 * Every HTML page then passes through site-shell (the `SHELL` service binding,
 * akshayDev17/site-themes `shell/`), which adds the shared site theme to the
 * page's <head> as it streams out. `/chat-archives/_site/*` and `/_site/*`
 * forward to site-shell's own files (its picker script). In production the zone
 * routes `/_site/*` to site-shell directly, so this Worker sees it only under
 * `wrangler dev` (README: theme work), where it stands in for that route.
 *
 * The route is `chat-archives*` (so `/chat-archives?x=1` reaches the Worker;
 * see wrangler.jsonc), which also catches paths that merely start with the
 * prefix, such as `/chat-archivesfoo` or `/chat-archives.html`. Those are not
 * the archive's: they go to the origin exactly as they came. `/_next/*` and
 * `/vendors/*` are the archive's own routes and keep going to ASSETS.
 */
const PREFIX = '/chat-archives';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const site = url.pathname.startsWith(`${PREFIX}/_site/`) ? url.pathname.slice(PREFIX.length) : url.pathname;
    if (site.startsWith('/_site/') && env.SHELL) {
      return env.SHELL.fetch(new Request(new URL(site, url), request));
    }
    if (isPrefixLookalike(url.pathname)) return fetch(request);
    // Share ids are UUIDs; `login`, `desk`, `_story`, and the front page are not.
    const target = /^\/chat-archives\/[0-9a-fA-F-]{36}\/?$/.test(url.pathname)
      ? new URL(`${PREFIX}/_story`, url)
      : request;
    const page = () => env.ASSETS.fetch(target);
    return withShell(await page(), env, page, 'chat', `${PREFIX}/_site/`);
  },
};

/** `/chat-archivesfoo`, `/chat-archives.html`: starts with the prefix but is neither it nor under it. */
function isPrefixLookalike(path) {
  return path.startsWith(PREFIX) && path !== PREFIX && !path.startsWith(`${PREFIX}/`);
}

/**
 * Passes an HTML page through site-shell's compose(), failing open: with no
 * binding, or when site-shell is missing, unreachable or throws, the visitor
 * gets the page exactly as built. The page's body went to site-shell with the
 * call, so the fallback fetches the page again (a static asset, so free);
 * returning `res` there would answer 500 ("Body has already been used").
 */
async function withShell(res, env, refetch, app, assetBase) {
  const type = res.headers.get('content-type') || '';
  if (!env.SHELL || res.status !== 200 || !type.startsWith('text/html')) return res;
  try {
    return await env.SHELL.compose(res, { app, assetBase });
  } catch (err) {
    console.error('site-shell: compose failed, serving the page as built:', String(err));
    return refetch();
  }
}
