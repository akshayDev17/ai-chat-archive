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

import {
  ApiError,
  SIGN_IN_RETRY_DELAY_MS,
  ingestSession,
  isSignInRequired,
  ownImageSrc,
  readStory,
  retractSession,
  setVisibility,
  whoami,
} from './api.ts';

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

test('a story is read from the public permalink when it is public', async () => {
  const calls = scriptFetch(json({ id: 'abc', title: 'Public' }));
  assert.equal((await readStory('abc')).title, 'Public');
  assert.deepEqual(calls.map((c) => new URL(c.url).pathname), ['/api/sessions/abc']);
});

test('a story absent from the public permalink is read as your own copy', async () => {
  const calls = scriptFetch(
    json({ error: 'Not found' }, 404),
    json({ id: 'abc', title: 'Mine alone' }),
  );
  const story = await readStory('abc');
  assert.equal(story.title, 'Mine alone');
  assert.deepEqual(
    calls.map((c) => new URL(c.url).pathname),
    ['/api/sessions/abc', '/api/desk/stories/abc'],
  );
});

test('a story that is neither public nor yours reports the public answer', async () => {
  scriptFetch(
    json({ error: 'Not found' }, 404),
    json({ error: 'You have no filed story with that id' }, 404),
  );
  await assert.rejects(readStory('abc'), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 404);
    assert.equal(error.message, 'Not found');
    return true;
  });
});

test('changing visibility sends a PATCH naming the new one', async () => {
  const calls = scriptFetch(json({ id: 'abc', visibility: 'private' }));
  await setVisibility('abc', 'private');
  assert.equal(new URL(calls[0].url).pathname, '/api/desk/sessions/abc');
  assert.equal(calls[0].init?.method, 'PATCH');
  assert.deepEqual(JSON.parse(String(calls[0].init?.body)), { visibility: 'private' });
});

test('filing says whether the story is public or private', async () => {
  const calls = scriptFetch(json({ share_id: 'abc', visibility: 'private' }));
  await ingestSession('https://chatgpt.com/share/abc', 'private');
  assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
    share_url: 'https://chatgpt.com/share/abc',
    visibility: 'private',
  });
});

test('filing without a choice says nothing about visibility', async () => {
  const calls = scriptFetch(json({ share_id: 'abc', visibility: 'private' }));
  const result = await ingestSession('https://chatgpt.com/share/abc');
  assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
    share_url: 'https://chatgpt.com/share/abc',
  });
  assert.equal(result.visibility, 'private');
});

test('your own images are asked for on the signed-in route', () => {
  assert.equal(
    new URL(ownImageSrc('chat-archives/abc/msg-1/01.jpg')).pathname,
    '/api/desk/image/chat-archives/abc/msg-1/01.jpg',
  );
});
