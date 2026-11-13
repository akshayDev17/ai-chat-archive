// A filer's controls on their own stories, in a real browser: Delete (with its
// bin icon and two-click confirmation) and public/private, on the copy desk's
// list of filings and on the story page — and their absence for everyone else.
//
// Like test:layout, this loads the static export in the installed Google Chrome
// (channel 'chrome'), serving out/ directly and answering the API itself, so
// what is tested is the pages as shipped. The export must be built with
// NEXT_PUBLIC_API_BASE='' (as CI builds it), so the pages ask their own origin.
//
//   NEXT_PUBLIC_API_BASE= npm run build && npm run test:controls
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, extname, join } from 'node:path';
import { after, before, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const out = join(dirname(fileURLToPath(import.meta.url)), 'out');
const ORIGIN = 'http://archive.test';
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' };

const TIDES = '11111111-1111-4111-8111-111111111111';
const NOTE = '22222222-2222-4222-8222-222222222222';
const THEIRS = '55555555-5555-4555-8555-555555555555';
const FRESH = '77777777-7777-4777-8777-777777777777';
const CONFIRM = 'Delete for good? This deletes the transcript, report, sources and images, and cannot be undone.';
const NOT_YOURS = 'You have no filed story with that id';

/** The exported file the Worker serves for a path (worker.js, plus the assets' clean URLs). */
function exported(pathname) {
  if (/^\/chat-archives\/[0-9a-f-]{36}\/?$/.test(pathname)) return 'chat-archives/_story.html';
  const file = decodeURIComponent(pathname).replace(/^\//, '');
  return extname(file) ? file : `${file}.html`;
}

/**
 * A stand-in archive: `me` filed two stories (one private), someone else filed
 * a third. It answers as the backend does, including 404 for what is not the
 * caller's, and records every write it is sent.
 */
function archive({ signedIn = true, failDeletes = false } = {}) {
  const stories = new Map([
    [TIDES, { title: 'Tides and harbours', visibility: 'public', mine: true }],
    [NOTE, { title: 'A private note', visibility: 'private', mine: true }],
    [THEIRS, { title: 'Someone else’s story', visibility: 'public', mine: false }],
  ]);
  const writes = [];
  const detail = (id, story) => ({
    id,
    title: story.title,
    source: 'chatgpt',
    report: `# ${story.title}\n\nThe report of ${story.title}.`,
    citations: [],
    messages: [],
  });
  const listed = (id, story) => ({ id, title: story.title, created_at: '2026-11-06T10:00:00+00:00', source: 'chatgpt', markdown: null });

  function answer(method, pathname, body) {
    const id = pathname.split('/').pop();
    const story = stories.get(id);
    const mine = signedIn && story?.mine ? story : null;
    if (pathname === '/api/sessions') {
      return { json: { sessions: [...stories].filter(([, s]) => s.visibility === 'public').map(([i, s]) => listed(i, s)) } };
    }
    if (pathname.startsWith('/api/sessions/')) {
      return story?.visibility === 'public' ? { json: detail(id, story) } : { status: 404, json: { error: 'Not found' } };
    }
    if (!signedIn) return { status: 401, json: { error: 'sign-in required' } };
    if (pathname === '/api/desk/whoami') return { json: { email: 'me@example.com' } };
    if (pathname === '/api/desk/media') return { json: { filings: [] } };
    if (pathname === '/api/desk/filings') {
      return { json: { owner: 'me@example.com', sessions: [...stories].filter(([, s]) => s.mine).map(([i, s]) => ({ ...listed(i, s), visibility: s.visibility })) } };
    }
    if (pathname === '/api/desk/ingest') {
      // As the backend files: a choice is stored, and without one a new story
      // is public while one filed again keeps what it had.
      const filedId = body.share_url.split('/').pop();
      const before = stories.get(filedId);
      writes.push({ method, id: filedId, body });
      const visibility = body.visibility ?? (before?.mine ? before.visibility : 'public');
      stories.set(filedId, { title: before?.title ?? 'A new story', visibility, mine: true });
      return { json: { share_id: filedId, title: stories.get(filedId).title, messages: 2, report_length: 10, citations: 0, images: 0, visibility } };
    }
    if (pathname.startsWith('/api/desk/stories/')) {
      return mine ? { json: detail(id, mine) } : { status: 404, json: { error: NOT_YOURS } };
    }
    if (pathname.startsWith('/api/desk/sessions/')) {
      if (method === 'GET') return { json: { id, yours: Boolean(mine), visibility: mine?.visibility ?? null, images: null } };
      writes.push({ method, id, body });
      if (!mine || (method === 'DELETE' && failDeletes)) return { status: 404, json: { error: NOT_YOURS } };
      if (method === 'DELETE') {
        stories.delete(id);
        return { json: { id, retracted: true } };
      }
      if (method === 'PATCH') {
        mine.visibility = body.visibility;
        return { json: { id, visibility: body.visibility } };
      }
    }
    return { status: 404, json: { error: 'not found' } };
  }
  return { answer, writes, stories };
}

let browser;
before(async () => {
  assert.ok(existsSync(join(out, 'chat-archives.html')), 'no static export: run `npm run build` first');
  browser = await chromium.launch({ channel: 'chrome' });
});
after(() => browser?.close());

/** A page on the export, with the stand-in archive answering its API calls. */
async function open(t, path, options) {
  const api = archive(options);
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  t.after(() => ctx.close());
  await ctx.route('**/*', (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== ORIGIN) return route.abort();
    if (url.pathname.startsWith('/api/')) {
      return route.fulfill({ status: 200, ...api.answer(request.method(), url.pathname, request.postDataJSON()) });
    }
    const file = join(out, exported(url.pathname));
    if (!existsSync(file)) return route.fulfill({ status: 404, body: '' });
    return route.fulfill({ body: readFileSync(file), contentType: TYPES[extname(file)] ?? 'application/octet-stream' });
  });
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}${path}`);
  return { page, api };
}

/** The filings row holding this story. */
const row = (page, title) => page.locator('.filing-row').filter({ hasText: title });

/** A bin icon, hidden from assistive technology, drawn before the button's word. */
async function assertBinBeside(button, word) {
  const icon = button.locator('svg');
  await icon.waitFor();
  assert.equal(await icon.getAttribute('aria-hidden'), 'true', 'the icon is decoration');
  assert.match(await icon.getAttribute('class'), /lucide-trash/, 'the icon is a bin');
  assert.equal(await icon.evaluate((svg) => getComputedStyle(svg).stroke === getComputedStyle(svg.parentElement).color), true, 'the icon takes the text colour');
  assert.equal((await button.innerText()).trim().toLowerCase(), word.toLowerCase());
  const [iconBox, buttonBox] = [await icon.boundingBox(), await button.boundingBox()];
  assert.ok(iconBox.width >= 12 && iconBox.height >= 12, 'the icon is large enough to see');
  assert.ok(iconBox.x - buttonBox.x < 1, 'the icon comes before the word');
  const middle = (box) => box.y + box.height / 2;
  assert.ok(Math.abs(middle(iconBox) - middle(buttonBox)) <= 1.5, 'the icon is centred on its word');
}

test('the desk lists your filings, each with Delete and its bin', async (t) => {
  const { page } = await open(t, '/chat-archives/desk');
  await row(page, 'Tides and harbours').waitFor();
  assert.equal(await page.locator('.filing-row').count(), 2, 'only your own stories are listed');
  assert.equal(await page.getByText('Someone else’s story').count(), 0);

  const del = page.getByRole('button', { name: 'Delete “Tides and harbours”' });
  await assertBinBeside(del, 'Delete');
  assert.equal(await page.getByText(/retract/i).count(), 0, 'nothing says Retract any more');
});

test('deleting from a row takes two clicks, and the row goes once the server confirms', async (t) => {
  const { page, api } = await open(t, '/chat-archives/desk');
  const tides = row(page, 'Tides and harbours');
  await tides.getByRole('button', { name: /^Delete/ }).click();

  // First click: the plain warning, and nothing sent yet.
  await tides.getByText(CONFIRM).waitFor();
  assert.deepEqual(api.writes, []);
  await assertBinBeside(tides.getByRole('button', { name: 'Yes, delete' }), 'Yes, delete');

  // "Keep it" backs out without deleting.
  await tides.getByRole('button', { name: 'Keep it' }).click();
  await tides.getByRole('button', { name: 'Delete “Tides and harbours”' }).waitFor();
  assert.deepEqual(api.writes, []);

  await tides.getByRole('button', { name: /^Delete/ }).click();
  await tides.getByRole('button', { name: 'Yes, delete' }).click();
  await tides.waitFor({ state: 'detached' });
  assert.deepEqual(api.writes, [{ method: 'DELETE', id: TIDES, body: null }]);
  assert.equal(await page.locator('.filing-row').count(), 1, 'the other row stays');
});

test('a delete the server refuses leaves the row in place with the reason', async (t) => {
  const { page, api } = await open(t, '/chat-archives/desk', { failDeletes: true });
  const tides = row(page, 'Tides and harbours');
  await tides.getByRole('button', { name: /^Delete/ }).click();
  await tides.getByRole('button', { name: 'Yes, delete' }).click();

  const alert = tides.getByRole('alert');
  await alert.waitFor();
  assert.equal(await alert.innerText(), NOT_YOURS);
  assert.equal(api.writes.length, 1);
  assert.equal(await page.locator('.filing-row').count(), 2, 'no row was removed');
  await tides.getByRole('button', { name: 'Yes, delete' }).waitFor();
});

test('a row says Public or Private and switches, once the server confirms', async (t) => {
  const { page, api } = await open(t, '/chat-archives/desk');
  const tides = row(page, 'Tides and harbours');
  const note = row(page, 'A private note');
  await tides.waitFor();
  assert.equal((await tides.locator('.visibility-label').innerText()).toLowerCase(), 'public');
  assert.equal((await note.locator('.visibility-label').innerText()).toLowerCase(), 'private');
  assert.equal(await note.locator('.visibility-label svg').getAttribute('aria-hidden'), 'true');

  await tides.getByRole('button', { name: 'Make private: Tides and harbours' }).click();
  await tides.getByRole('button', { name: 'Make public: Tides and harbours' }).waitFor();
  assert.equal((await tides.locator('.visibility-label').innerText()).toLowerCase(), 'private');
  assert.deepEqual(api.writes, [{ method: 'PATCH', id: TIDES, body: { visibility: 'private' } }]);
});

/** Paste a share link into the filing form, file it, and wait for the result line. */
async function fileLink(page, id, said) {
  await page.getByLabel('ChatGPT share link').fill(`https://chatgpt.com/share/${id}`);
  await page.getByRole('button', { name: 'File it' }).click();
  await page.locator('.file-msg').filter({ hasText: said }).waitFor();
}

test('the filing form offers public or private and picks neither, so a new story is filed public', async (t) => {
  const { page, api } = await open(t, '/chat-archives/desk');
  const choices = page.getByRole('group', { name: 'Who can read it' });
  await choices.waitFor();
  assert.equal(await choices.getByRole('radio', { name: /^Public/ }).isChecked(), false);
  assert.equal(await choices.getByRole('radio', { name: /^Private/ }).isChecked(), false);
  await choices.getByText('Neither chosen: a new story is public, and one filed again keeps what it had.').waitFor();

  await fileLink(page, FRESH, 'is in the edition');
  assert.deepEqual(api.writes, [{ method: 'POST', id: FRESH, body: { share_url: `https://chatgpt.com/share/${FRESH}` } }]);
  assert.equal((await row(page, 'A new story').locator('.visibility-label').innerText()).toLowerCase(), 'public');
});

test('a story filed private and filed again without choosing stays private', async (t) => {
  const { page, api } = await open(t, '/chat-archives/desk');
  const share_url = `https://chatgpt.com/share/${FRESH}`;
  await page.getByRole('radio', { name: /^Private/ }).check();
  await fileLink(page, FRESH, 'is filed as private — only you can read it');

  // Later, on a desk opened afresh: the same link, the choice left alone.
  await page.reload();
  await fileLink(page, FRESH, 'is filed as private — only you can read it');
  assert.deepEqual(api.writes, [
    { method: 'POST', id: FRESH, body: { share_url, visibility: 'private' } },
    { method: 'POST', id: FRESH, body: { share_url } },
  ]);
  assert.equal(api.stories.get(FRESH).visibility, 'private');
  assert.equal((await row(page, 'A new story').locator('.visibility-label').innerText()).toLowerCase(), 'private');

  await page.goto(`${ORIGIN}/chat-archives`);
  await page.getByText('Tides and harbours').first().waitFor();
  assert.equal(await page.getByText('A new story').count(), 0, 'it is not in the public edition');
});

test('a private story filed again with Public chosen is published', async (t) => {
  const { page, api } = await open(t, '/chat-archives/desk');
  await page.getByRole('radio', { name: /^Public/ }).check();
  await fileLink(page, NOTE, 'is in the edition');
  assert.deepEqual(api.writes, [
    { method: 'POST', id: NOTE, body: { share_url: `https://chatgpt.com/share/${NOTE}`, visibility: 'public' } },
  ]);
  assert.equal((await row(page, 'A private note').locator('.visibility-label').innerText()).toLowerCase(), 'public');
});

test('your own story page offers Delete this story with its bin, and deletes in two clicks', async (t) => {
  const { page, api } = await open(t, `/chat-archives/${TIDES}`);
  const del = page.getByRole('button', { name: 'Delete this story' });
  await assertBinBeside(del, 'Delete this story');

  await del.click();
  await page.getByText(CONFIRM).waitFor();
  assert.deepEqual(api.writes, []);
  await page.getByRole('button', { name: 'Yes, delete' }).click();
  await page.waitForURL(`${ORIGIN}/chat-archives`);
  assert.deepEqual(api.writes, [{ method: 'DELETE', id: TIDES, body: null }]);
});

test('your private story opens for you, labelled Private, and can be made public', async (t) => {
  const { page, api } = await open(t, `/chat-archives/${NOTE}`);
  await page.getByText('The report of A private note.').waitFor();
  const tools = page.locator('.reader-tools');
  assert.equal((await tools.locator('.visibility-label').innerText()).toLowerCase(), 'private');

  await tools.getByRole('button', { name: 'Make public' }).click();
  await tools.getByRole('button', { name: 'Make private' }).waitFor();
  assert.equal((await tools.locator('.visibility-label').innerText()).toLowerCase(), 'public');
  assert.deepEqual(api.writes, [{ method: 'PATCH', id: NOTE, body: { visibility: 'public' } }]);
  assert.equal(api.stories.get(NOTE).visibility, 'public');
});

test('someone else’s story has no controls', async (t) => {
  const { page } = await open(t, `/chat-archives/${THEIRS}`);
  await page.getByText('The report of Someone else’s story.').waitFor();
  await page.waitForLoadState('networkidle');
  assert.equal(await page.getByRole('button', { name: /delete/i }).count(), 0);
  assert.equal(await page.getByRole('button', { name: /make (public|private)/i }).count(), 0);
  assert.equal(await page.locator('.visibility-label').count(), 0);
});

test('a private story is not found for a signed-out visitor', async (t) => {
  const { page } = await open(t, `/chat-archives/${NOTE}`, { signedIn: false });
  await page.getByText('Not found').waitFor();
  assert.equal(await page.getByText('A private note').count(), 0);
  assert.equal(await page.getByRole('button').count(), 0);
});
