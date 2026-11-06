// On a phone, no page of the static export lays out wider than the screen.
// One element wider than the viewport makes a phone browser zoom the whole
// page out, which shrinks every line of text with it. Nor does a table cell
// split a word to make it fit: a wide table scrolls inside its own box, and
// a narrow one still spans the column. The test loads each
// exported page in Chrome's phone emulation (the installed Google Chrome,
// channel 'chrome', as test:shell uses), serving out/ directly, and a story
// from a fixture holding what filed stories really carry: bare URLs, a wide
// table, a code block, and a long unbroken token in a user turn.
//
// The export must be built with NEXT_PUBLIC_API_BASE='' (as CI builds it), so
// the reader asks its own origin for the story, which the test answers.
//
//   NEXT_PUBLIC_API_BASE= npm run build && npm run test:layout
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, extname, join } from 'node:path';
import { after, before, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const out = join(dirname(fileURLToPath(import.meta.url)), 'out');
const ORIGIN = 'http://archive.test';
const STORY = '6ac877f9-c690-83ec-8394-61b0727ba5eb';
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' };

const LONG_URL = 'https://www.example.gov/BlobFolder/dynamiccollectorresultitem/prospectuses_18k_report_2021/en/annual-report-appendix-full-text.pdf';
const NARROW_TABLE = ['| Key | Value |', '| --- | --- |', '| State | Delhi |'].join('\n');
const STORY_BODY = {
  id: STORY,
  title: 'A story with wide content',
  source: 'chatgpt',
  report: [
    '# A story with wide content',
    '',
    `A bare link in running text: ${LONG_URL}`,
    '',
    `- A bare link in a list: ${LONG_URL}`,
    '',
    '| Scenario | Existing tariff | Agreed terms | Result | Notes |',
    '| --- | --- | --- | --- | --- |',
    `| Excluded | Existing tariff remains | Tariff reduced/changed according to agreed terms | Unchanged | ${LONG_URL} |`,
    '',
    NARROW_TABLE,
    '',
    '```text',
    'GMR Group  ──  GMR Airports Ltd.  ←  listed  ──  Delhi International Airport Ltd. (DIAL)  ──  64% stake',
    '```',
  ].join('\n'),
  citations: [],
  messages: [
    { role: 'user', content: `[$chat-to-markdown-report](app://plugins_6ac7f272fb048191ac6a4770a335b24d) ${LONG_URL}`, sources: [] },
    {
      role: 'assistant',
      content: [
        `See ${LONG_URL}`,
        '',
        '| Scenario | Existing tariff | Agreed terms | Result | Notes |',
        '| --- | --- | --- | --- | --- |',
        `| Excluded | Existing tariff remains | Tariff reduced/changed according to agreed terms | Unchanged | ${LONG_URL} |`,
        '',
        NARROW_TABLE,
      ].join('\n'),
      sources: [],
    },
  ],
};

const LISTED = { id: STORY, title: STORY_BODY.title, created_at: '2026-11-06T10:00:00+00:00', source: 'chatgpt', markdown: STORY_BODY.report };

// Each page, and the text that shows it has rendered.
const PAGES = {
  '/chat-archives': 'A story with wide content',
  '/chat-archives/login': 'Sign in to the archive',
  '/chat-archives/desk': 'File a session',
  '/chat-archives/desk/media': 'Pictures in your filed stories',
  [`/chat-archives/${STORY}`]: 'A bare link in running text',
  [`/chat-archives/${STORY}?chat`]: 'Verbatim transcript',
};

/** The exported file the Worker serves for a path (worker.js, plus the assets' clean URLs). */
function exported(pathname) {
  if (/^\/chat-archives\/[0-9a-f-]{36}\/?$/.test(pathname)) return 'chat-archives/_story.html';
  const file = decodeURIComponent(pathname).replace(/^\//, '');
  return extname(file) ? file : `${file}.html`;
}

let browser;
before(async () => {
  assert.ok(existsSync(join(out, 'chat-archives.html')), 'no static export: run `npm run build` first');
  browser = await chromium.launch({ channel: 'chrome' });
});
after(() => browser?.close());

async function phone(width, height, colorScheme) {
  const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 3, isMobile: true, hasTouch: true, colorScheme });
  await ctx.route('**/*', (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();
    if (url.pathname === `/api/sessions/${STORY}`) return route.fulfill({ json: STORY_BODY });
    if (url.pathname === '/api/sessions') return route.fulfill({ json: { sessions: [LISTED] } });
    if (url.pathname === '/api/desk/whoami') return route.fulfill({ json: { email: 'reader@example.com' } });
    if (url.pathname === '/api/desk/media') return route.fulfill({ json: { filings: [] } });
    if (url.pathname.startsWith('/api/')) return route.fulfill({ status: 404, json: { error: 'not found' } });
    const file = join(out, exported(url.pathname));
    if (!existsSync(file)) return route.fulfill({ status: 404, body: '' });
    return route.fulfill({ body: readFileSync(file), contentType: TYPES[extname(file)] ?? 'application/octet-stream' });
  });
  return ctx;
}

for (const colorScheme of ['light', 'dark']) {
  for (const [width, height] of [[360, 800], [390, 844], [412, 915], [844, 390], [915, 412]]) {
    test(`at ${width}x${height} (${colorScheme}), every page lays out at exactly the screen width, tables whole`, async () => {
      const ctx = await phone(width, height, colorScheme);
      const page = await ctx.newPage();
      try {
        for (const [path, marker] of Object.entries(PAGES)) {
          await page.goto(`${ORIGIN}${path}`);
          await page.getByText(marker).first().waitFor({ timeout: 10_000 });
          await page.waitForLoadState('networkidle');
          // A phone browser widens innerWidth to fit content wider than the
          // screen, so both are measured against the screen width.
          const { scrollWidth, innerWidth } = await page.evaluate(() => ({
            scrollWidth: document.documentElement.scrollWidth,
            innerWidth: window.innerWidth,
          }));
          assert.deepEqual({ scrollWidth, innerWidth }, { scrollWidth: width, innerWidth: width }, path);
          // A table cell never splits a word across lines: a run of letters
          // and digits drawn as more than one box has been broken mid-word
          // (a line may still end after a hyphen or a space).
          const { cells, split } = await page.evaluate(() => {
            const found = [...document.querySelectorAll('.report th, .report td, .plain th, .plain td')];
            const broken = [];
            for (const cell of found) {
              const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
              for (let node = walker.nextNode(); node; node = walker.nextNode()) {
                for (const word of node.data.matchAll(/[\p{L}\p{N}]+/gu)) {
                  const range = document.createRange();
                  range.setStart(node, word.index);
                  range.setEnd(node, word.index + word[0].length);
                  if (range.getClientRects().length > 1) broken.push(word[0]);
                }
              }
            }
            return { cells: found.length, split: broken };
          });
          if (path.includes(STORY)) assert.ok(cells > 0, `${path}: the story's table did not render`);
          assert.deepEqual(split, [], `${path}: words split inside table cells`);
          // A table that fits still spans the column; a wider one scrolls
          // inside its own box rather than widening the page.
          const tables = await page.evaluate(() =>
            [...document.querySelectorAll('.report table, .plain table')].map((table) => {
              const box = table.parentElement;
              return {
                wide: table.textContent.includes('https://'),
                inBox: box.classList.contains('table-scroll'),
                table: Math.round(table.getBoundingClientRect().width),
                box: box.clientWidth,
                scroll: box.scrollWidth,
                overflowX: getComputedStyle(box).overflowX,
              };
            }),
          );
          if (path.includes(STORY)) {
            assert.ok(tables.some((t) => t.wide) && tables.some((t) => !t.wide), `${path}: the story's tables did not render`);
          }
          for (const t of tables) {
            assert.ok(t.inBox, `${path}: a table is not in its scrolling box`);
            if (t.wide) {
              assert.ok(t.overflowX === 'auto' && t.scroll > t.box, `${path}: the wide table does not scroll inside its box: ${JSON.stringify(t)}`);
            } else {
              assert.ok(Math.abs(t.table - t.box) <= 1, `${path}: the narrow table does not span the column: ${JSON.stringify(t)}`);
            }
          }
        }
      } finally {
        await ctx.close();
      }
    });
  }
}
