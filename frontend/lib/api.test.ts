/**
 * Tests for the API helper's single retry on a 401, offline.
 *
 * A signed-in reader's desk calls intermittently 401 while their siblings
 * succeed, so one stray 401 is asked again before the reader is shown as
 * signed out.
 *
 * Run:  cd frontend && npm run test:lib
 */

import assert from 'node:assert/strict';
import test from 'node:test';

import { ApiError, SIGN_IN_RETRY_DELAY_MS, isSignInRequired, retractSession, whoami } from './api.ts';

const realFetch = globalThis.fetch;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

/** Answer each call with the next response; record what was asked. */
function scriptFetch(...responses: Response[]) {
  const calls: { url: string; init?: RequestInit; at: number }[] = [];
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    calls.push({ url, init, at: Date.now() });
    const next = responses.shift();
    if (!next) throw new Error(`unexpected fetch ${url}`);
    return next;
  }) as typeof fetch;
  return calls;
}

test.afterEach(() => {
  globalThis.fetch = realFetch;
});

test('a single stray 401 is retried once and the reader stays signed in', async () => {
  const calls = scriptFetch(json({ error: 'sign-in required' }, 401), json({ email: 'reader@example.com' }));
  assert.equal(await whoami(), 'reader@example.com');
  assert.equal(calls.length, 2);
  assert.equal(calls[1].url, calls[0].url);
  assert.equal(calls[1].init?.credentials, 'include');
  assert.ok(calls[1].at - calls[0].at >= SIGN_IN_RETRY_DELAY_MS - 5, 'waits before asking again');
});

test('two 401s in a row mean signed out, after exactly one retry', async () => {
  const calls = scriptFetch(json({ error: 'sign-in required' }, 401), json({ error: 'sign-in required' }, 401));
  await assert.rejects(whoami(), (e: unknown) => isSignInRequired(e) && (e as ApiError).status === 401);
  assert.equal(calls.length, 2);
});

test('a first-time answer is not asked again', async () => {
  const calls = scriptFetch(json({ email: 'reader@example.com' }));
  assert.equal(await whoami(), 'reader@example.com');
  assert.equal(calls.length, 1);
});

test('other failures are not retried', async () => {
  const calls = scriptFetch(json({ error: 'broken' }, 500));
  await assert.rejects(whoami(), (e: unknown) => e instanceof ApiError && e.status === 500);
  assert.equal(calls.length, 1);
});

test('a retried write is sent again with its method', async () => {
  const calls = scriptFetch(json({ error: 'sign-in required' }, 401), json({ retracted: true }));
  await retractSession('abc');
  assert.equal(calls.length, 2);
  assert.equal(calls[1].init?.method, 'DELETE');
});
