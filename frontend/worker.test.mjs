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
