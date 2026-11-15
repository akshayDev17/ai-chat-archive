// worker.js with stand-in bindings: the story rewrite, the site-shell hook and
// its fail-open paths. The real compose() runs only in workerd; that pairing is
// covered by shell-contract/contract.test.mjs.
//
//   node --test worker.test.mjs
import assert from 'node:assert/strict';
import { test } from 'node:test';
import worker from './worker.js';

const HTML = '<!DOCTYPE html><html><head><meta charSet="utf-8"/></head><body>page</body></html>';
const STORY = '6ac877f9-c690-83ec-8394-61b0727ba5eb';

/** ASSETS: answers every path with `respond(path)` and records what was asked. */
function assets(respond = () => new Response(HTML, { headers: { 'content-type': 'text/html' } })) {
  const calls = [];
  return {
    calls,
    fetch: async (input) => {
      const path = new URL(input instanceof Request ? input.url : input).pathname;
      calls.push(path);
      return respond(path);
    },
  };
}

/** SHELL: compose() marks the page (reading its body, as the RPC transfer does); fetch() echoes the path. */
function shell({ fail } = {}) {
  const calls = { compose: [], fetch: [] };
  return {
    calls,
    compose: async (res, opts) => {
      calls.compose.push(opts);
      const body = await res.text();
      if (fail) throw new Error(fail);
      return new Response(body.replace('<meta charSet="utf-8"/>', '$&<script data-site-shell></script>'), {
        headers: { 'content-type': 'text/html', 'x-site-shell': `test; app=${opts.app}` },
      });
    },
    fetch: async (req) => {
      calls.fetch.push(new URL(req.url).pathname);
      return new Response('site.js', { headers: { 'content-type': 'text/javascript' } });
    },
  };
}

const get = (path, env) => worker.fetch(new Request(`https://akshayprabhakant.com${path}`), env);

test('a story permalink is served from the one prerendered story page', async () => {
  const env = { ASSETS: assets() };
  for (const path of [`/chat-archives/${STORY}`, `/chat-archives/${STORY}/`]) {
    const res = await get(path, env);
    assert.equal(res.status, 200);
  }
  assert.deepEqual(env.ASSETS.calls, ['/chat-archives/_story', '/chat-archives/_story']);
});

test('other paths go to ASSETS unchanged', async () => {
  const env = { ASSETS: assets() };
  for (const path of ['/chat-archives', '/chat-archives/login', '/chat-archives/desk', '/_next/static/x.js', '/vendors/claude.svg']) {
    await get(path, env);
  }
  assert.deepEqual(env.ASSETS.calls, ['/chat-archives', '/chat-archives/login', '/chat-archives/desk', '/_next/static/x.js', '/vendors/claude.svg']);
});

test('with SHELL bound, HTML pages are composed with the app name and the forwarded asset base', async () => {
  const env = { ASSETS: assets(), SHELL: shell() };
  for (const path of ['/chat-archives', `/chat-archives/${STORY}`]) {
    const res = await get(path, env);
    assert.equal(res.status, 200);
    assert.equal(res.headers.get('x-site-shell'), 'test; app=chat');
    assert.match(await res.text(), /<meta charSet="utf-8"\/><script data-site-shell><\/script>/);
  }
  assert.deepEqual(env.SHELL.calls.compose, [
    { app: 'chat', assetBase: '/chat-archives/_site/' },
    { app: 'chat', assetBase: '/chat-archives/_site/' },
  ]);
  assert.deepEqual(env.ASSETS.calls, ['/chat-archives', '/chat-archives/_story'], 'one ASSETS fetch per page when compose succeeds');
});

test('unbound: no SHELL binding serves the page exactly as built', async () => {
  const env = { ASSETS: assets() };
  const res = await get('/chat-archives', env);
  assert.equal(res.status, 200);
  assert.equal(res.headers.get('x-site-shell'), null);
  assert.equal(await res.text(), HTML);
});

test('fail open: compose() rejecting (site-shell missing, unreachable or throwing) re-fetches the page as built', async (t) => {
  const errors = t.mock.method(console, 'error', () => {});
  for (const fail of ['Worker "site-shell" not found.', 'fault: compose threw']) {
    const env = { ASSETS: assets(), SHELL: shell({ fail }) };
    const res = await get(`/chat-archives/${STORY}`, env);
    assert.equal(res.status, 200);
    assert.equal(res.headers.get('x-site-shell'), null);
    assert.equal(await res.text(), HTML, 'the page as built, not a 500 from the spent body');
    assert.deepEqual(env.ASSETS.calls, ['/chat-archives/_story', '/chat-archives/_story'], 'the same page fetched again');
  }
  assert.equal(errors.mock.callCount(), 2);
  assert.match(String(errors.mock.calls[0].arguments[0]), /site-shell: compose failed/);
});

test('non-HTML and non-200 responses never reach compose()', async () => {
  const responses = {
    '/_next/static/css/app.css': () => new Response('a{}', { headers: { 'content-type': 'text/css', etag: 'W/"1"' } }),
    '/vendors/claude.svg': () => new Response('<svg/>', { headers: { 'content-type': 'image/svg+xml' } }),
    '/chat-archives/missing': () => new Response('nope', { status: 404, headers: { 'content-type': 'text/html' } }),
    '/chat-archives/': () => new Response(null, { status: 307, headers: { location: '/chat-archives' } }),
    '/chat-archives/cached': () => new Response(null, { status: 304, headers: { 'content-type': 'text/html' } }),
  };
  const env = { ASSETS: assets((path) => responses[path]()), SHELL: shell() };
  for (const path of Object.keys(responses)) {
    const res = await get(path, env);
    assert.equal(res.headers.get('x-site-shell'), null, path);
  }
  assert.equal(env.SHELL.calls.compose.length, 0);
  assert.equal((await get('/_next/static/css/app.css', env)).headers.get('etag'), 'W/"1"', 'validators kept');
});

test('/chat-archives/_site/* forwards to site-shell with the prefix stripped', async () => {
  const env = { ASSETS: assets(), SHELL: shell() };
  const res = await get('/chat-archives/_site/1132669f0ee1/site.js', env);
  assert.equal(await res.text(), 'site.js');
  assert.deepEqual(env.SHELL.calls.fetch, ['/_site/1132669f0ee1/site.js']);
  assert.deepEqual(env.ASSETS.calls, []);
});

test('/_site/* forwards to site-shell as is (only under wrangler dev; the zone routes it to site-shell)', async () => {
  const env = { ASSETS: assets(), SHELL: shell() };
  const res = await get('/_site/1132669f0ee1/site.js', env);
  assert.equal(await res.text(), 'site.js');
  assert.deepEqual(env.SHELL.calls.fetch, ['/_site/1132669f0ee1/site.js']);
  assert.deepEqual(env.ASSETS.calls, []);
});

test('/chat-archives/_site/* without SHELL falls through to ASSETS as before', async () => {
  const env = { ASSETS: assets(() => new Response('Not Found', { status: 404 })) };
  const res = await get('/chat-archives/_site/1132669f0ee1/site.js', env);
  assert.equal(res.status, 404);
  assert.deepEqual(env.ASSETS.calls, ['/chat-archives/_site/1132669f0ee1/site.js']);
});

test('/chat-archives?<query> is served in place like /chat-archives (the app is no-trailing-slash)', async () => {
  const env = { ASSETS: assets(), SHELL: shell() };
  for (const path of ['/chat-archives?x=1', '/chat-archives?', '/chat-archives?g=rag&y=%20z']) {
    const res = await get(path, env);
    assert.equal(res.status, 200, path);
    assert.equal(res.headers.get('location'), null, `${path}: no redirect`);
    assert.equal(res.headers.get('x-site-shell'), 'test; app=chat', path);
  }
  assert.deepEqual(env.ASSETS.calls, ['/chat-archives', '/chat-archives', '/chat-archives']);
});

test('paths that only start with the prefix go to the origin as the very same Request', async (t) => {
  const seen = [];
  const origin = new Response('origin 404', { status: 404 });
  t.mock.method(globalThis, 'fetch', async (req) => {
    seen.push(req);
    return origin;
  });
  const env = { ASSETS: assets(), SHELL: shell() };
  const paths = ['/chat-archivesfoo', '/chat-archivesfoo?x=1', '/chat-archives.html', '/chat-archives-x/y?x=1', '/chat-archives_site/x'];
  for (const path of paths) {
    const req = new Request(`https://akshayprabhakant.com${path}`, { method: 'POST', body: 'b', headers: { 'x-t': path } });
    const res = await worker.fetch(req, env);
    assert.equal(seen.at(-1), req, `${path}: the incoming Request itself`);
    assert.equal(res, origin, `${path}: the origin's response, unchanged`);
  }
  assert.equal(seen.length, paths.length);
  assert.deepEqual(env.ASSETS.calls, [], 'never ASSETS');
  assert.deepEqual(env.SHELL.calls, { compose: [], fetch: [] }, 'never site-shell');
});

test('the archive\'s own paths and its /_next/* and /vendors/* routes never go to the origin', async (t) => {
  const origin = t.mock.method(globalThis, 'fetch', async () => new Response('origin'));
  const env = { ASSETS: assets() };
  const paths = ['/chat-archives', '/chat-archives?x=1', '/chat-archives/', '/chat-archives/?x=1', '/chat-archives/login', `/chat-archives/${STORY}?x=1`, '/_next/static/x.js', '/vendors/claude.svg?x=1'];
  for (const path of paths) await get(path, env);
  assert.equal(origin.mock.callCount(), 0);
  assert.equal(env.ASSETS.calls.length, paths.length);
});
