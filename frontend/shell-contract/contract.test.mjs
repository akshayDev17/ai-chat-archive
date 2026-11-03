// The frontend Worker against the real site-shell, in local workerd: each test
// is one `wrangler dev` session over the static export, with no Cloudflare
// account. It needs the export (`npm run build`, which writes out/) and a
// checkout of akshayDev17/site-themes:
//
//   SITE_THEMES_DIR=/path/to/site-themes npm run test:shell
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { after, before, test } from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { chromium } from 'playwright-core';
import { experimental_readRawConfig as readWranglerConfig } from 'wrangler';

const here = dirname(fileURLToPath(import.meta.url));
const frontend = join(here, '..');
const out = join(frontend, 'out');
const wrangler = join(frontend, 'node_modules/.bin/wrangler');
const siteThemes = process.env.SITE_THEMES_DIR && resolve(process.env.SITE_THEMES_DIR);

const APP = join(frontend, 'wrangler.jsonc');
const UNBOUND = join(here, 'unbound.jsonc');
const THROWING_SHELL = join(here, 'throwing-shell.jsonc');
const SHELL = siteThemes && join(siteThemes, 'shell/wrangler.jsonc');

const STORY = '6ac877f9-c690-83ec-8394-61b0727ba5eb';
const META = '<meta charSet="utf-8"/>';
// Each path the Worker serves as a page, and the exported file behind it.
const PAGES = {
  '/chat-archives': 'chat-archives.html',
  [`/chat-archives/${STORY}`]: 'chat-archives/_story.html',
  '/chat-archives/login': 'chat-archives/login.html',
  '/chat-archives/desk': 'chat-archives/desk.html',
  '/chat-archives/desk/media': 'chat-archives/desk/media.html',
};
// Each page's own crumbs in the site path after "AI Digest" (Masthead's `tail`).
const TAILS = {
  '/chat-archives': [],
  [`/chat-archives/${STORY}`]: ['The reading room'],
  '/chat-archives/login': ['Reader access'],
  '/chat-archives/desk': ['Copy desk'],
  '/chat-archives/desk/media': ['Copy desk', 'Media'],
};
// The site path slot, whose content site-shell replaces.
const SLOT = /(<site-slot name="site-path"[^>]*>)([\s\S]*?)(<\/site-slot>)/;

let build;
// The listed apps, from the site-themes checkout's app list (its committed dist/).
let listedApps;
// Where composed pages load site-shell's files from: site-shell's ASSET_BASE
// var (the zone route akshayprabhakant.com/_site/*) when its config sets one,
// which overrides the assetBase this app passes; otherwise the app's own
// forwarded prefix.
let assetBase;
before(async () => {
  assert.ok(siteThemes, 'set SITE_THEMES_DIR to a checkout of akshayDev17/site-themes');
  assert.ok(existsSync(SHELL), `no site-shell config at ${SHELL}`);
  assert.ok(existsSync(join(out, 'chat-archives.html')), 'no static export: run `npm run build` first');
  build = JSON.parse(readFileSync(join(siteThemes, 'shell/generated/build.json'), 'utf8'));
  listedApps = (await import(pathToFileURL(join(siteThemes, 'dist/apps.js')).href)).APPS.filter((a) => a.listed);
  assetBase = readWranglerConfig({ config: SHELL }).rawConfig.vars?.ASSET_BASE ?? '/chat-archives/_site/';
});

const scratch = mkdtempSync(join(tmpdir(), 'chat-shell-contract-'));
after(() => rmSync(scratch, { recursive: true, force: true }));
let nextPort = 18900;

/**
 * Starts one wrangler dev session and stops it when the test ends. The dev
 * registry is private to this run: a site-shell left running by any other
 * `wrangler dev` on the machine would otherwise answer this session's binding.
 */
async function session(t, configs) {
  const port = nextPort++;
  const args = ['dev', ...configs.flatMap((c) => ['-c', c]), '--ip', '127.0.0.1', '--port', String(port),
    '--inspector-port', String(port + 1000), '--persist-to', join(scratch, 'state'), '--log-level', 'info'];
  const env = { ...process.env, WRANGLER_SEND_METRICS: 'false', WRANGLER_REGISTRY_PATH: join(scratch, 'registry') };
  const child = spawn(wrangler, args, { cwd: frontend, env, stdio: ['ignore', 'pipe', 'pipe'] });
  let log = '';
  child.stdout.on('data', (d) => (log += d));
  child.stderr.on('data', (d) => (log += d));
  const exited = new Promise((r) => child.on('exit', r));
  t.after(() => {
    child.kill('SIGTERM');
    return exited;
  });
  for (let i = 0; i < 120 && !/Ready on/.test(log); i++) await new Promise((r) => setTimeout(r, 500));
  if (!/Ready on/.test(log)) throw new Error(`wrangler dev did not start:\n${log}`);
  return { base: `http://127.0.0.1:${port}`, log: () => log };
}

/** Worker console output reaches wrangler's stdout a moment after the response. */
async function logMatching(s, re) {
  for (let i = 0; i < 40 && !re.test(s.log()); i++) await new Promise((r) => setTimeout(r, 100));
  return s.log();
}

const asBuilt = (file) => readFileSync(join(out, file), 'utf8');
/** The exported page plus site-shell's head insertion, right after its leading <meta charset>. */
function composedHead(file) {
  const page = asBuilt(file);
  assert.equal(page.split(META).length, 2, `${file} has exactly one ${META}`);
  const scripts = build.scripts.map((f) => `<script defer src="${assetBase}${build.id}/${f}"></script>`).join('');
  return page.replace(META, META + build.inline + scripts);
}

/** The page with the site path slot's content cut out, and that content. */
function splitSlot(html, label) {
  const m = html.match(SLOT);
  assert.ok(m, `${label}: has a site path slot`);
  return { outside: html.replace(SLOT, '$1$3'), inner: m[2] };
}

test('bound to site-shell: every page gains the theme head block and the site path, and nothing else', async (t) => {
  const s = await session(t, [APP, SHELL]);
  for (const [path, file] of Object.entries(PAGES)) {
    const res = await fetch(`${s.base}${path}`);
    const body = await res.text();
    assert.equal(res.status, 200, path);
    assert.equal(res.headers.get('x-site-shell'), `${build.id}; app=chat`, path);
    assert.equal(res.headers.get('etag'), null, `${path}: composed HTML carries no validator`);
    const got = splitSlot(body, path);
    const want = splitSlot(composedHead(file), file);
    assert.equal(got.outside, want.outside, `${path}: outside the slot, the exported page plus the head insertion`);
    assert.notEqual(got.inner, want.inner, `${path}: site-shell replaced the slot's fallback`);
    assert.ok(got.inner.startsWith('<style data-site-shell>'), `${path}: the slot carries its own style`);
    assert.match(got.inner, /<nav class="sp" aria-label="Site path">/, `${path}: the slot holds the site path`);
    assert.ok(body.indexOf(META) < 1024, `${path}: <meta charset> stays inside the first 1024 bytes`);
    assert.equal(body.match(/data-site-shell/g).length, 3, `${path}: the boot <script>, the tokens <style> and the slot's <style>`);
  }

  // Non-HTML passes through untouched, validators and 304 included.
  const page = asBuilt('chat-archives.html');
  const css = page.match(/href="(\/_next\/static\/css\/[^"]+\.css)"/)[1];
  const sheet = await fetch(`${s.base}${css}`);
  assert.equal(sheet.status, 200);
  assert.equal(sheet.headers.get('x-site-shell'), null);
  const etag = sheet.headers.get('etag');
  assert.ok(etag, 'the stylesheet keeps its ETag');
  assert.equal((await fetch(`${s.base}${css}`, { headers: { 'if-none-match': etag } })).status, 304);
  assert.match(await sheet.text(), /:where\(:root\)\{color-scheme:light;--surface:#EEF1F8/, 'the Classic Light fallback ships in the CSS');

  // site-shell's own files through this app's route: immutable; a missing build is never cached.
  const js = await fetch(`${s.base}/chat-archives/_site/${build.id}/site.js`);
  assert.equal(js.status, 200);
  assert.equal(js.headers.get('cache-control'), 'public, max-age=31536000, immutable');
  assert.match(await js.text(), /site-theme-picker/);
  const missing = await fetch(`${s.base}/chat-archives/_site/000000000000/site.js`);
  assert.equal(missing.status, 404);
  assert.equal(missing.headers.get('cache-control'), 'no-store');
});

// In the installed Google Chrome (channel 'chrome'; GitHub's Ubuntu runners
// ship it), so CI downloads no browser. Paths outside this Worker's routes
// (wrangler.jsonc) are answered by others in production, the API Worker and
// GitHub Pages (/favicon.ico), so their failed loads here are not counted.
// /_site/* is site-shell's zone route; here the app Worker forwards it.
const ROUTED = /^\/(chat-archives|_next|vendors|_site)(\/|$)/;

test('bound to site-shell, in a browser: every page hydrates with no console error and upgrades <site-theme-picker>', async (t) => {
  const s = await session(t, [APP, SHELL]);
  const browser = await chromium.launch({ channel: 'chrome' });
  t.after(() => browser.close());
  for (const path of Object.keys(PAGES)) {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', (err) => errors.push(String(err)));
    page.on('console', (msg) => {
      const from = msg.location().url;
      if (msg.type() === 'error' && (!from || ROUTED.test(new URL(from).pathname))) errors.push(msg.text());
    });
    await page.goto(`${s.base}${path}`, { waitUntil: 'networkidle' });
    await page.waitForSelector('site-theme-picker:defined', { state: 'attached', timeout: 10_000 });
    assert.deepEqual(errors, [], `${path}: no hydration or console error`);
    await page.close();
  }
});

/** What the site path shows once the page has hydrated, read from the live DOM. */
function readSitePath(page) {
  return page.evaluate(() => {
    const visible = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
    const nav = document.querySelector('header.masthead > site-slot[name="site-path"] > nav.sp');
    if (!nav) return null;
    const current = nav.querySelector('.sp-apps .sp-app');
    const pill = nav.querySelector('.sp-pill');
    const pillBox = pill.getBoundingClientRect();
    const at = (y) => document.elementFromPoint(pillBox.left + pillBox.width / 2, y);
    return {
      appsShown: visible(nav.querySelector('.sp-apps')),
      appLinks: [...nav.querySelectorAll('.sp-apps a')].map((a) => a.getAttribute('href')),
      current: current && { text: current.querySelector('.sp-label').textContent, ariaCurrent: current.getAttribute('aria-current') },
      crumbs: [...nav.querySelectorAll('.sp-crumb')].filter(visible).map((c) => {
        const el = c.querySelector('a, .sp-here');
        return { text: el.textContent, href: el.getAttribute('href'), ariaCurrent: el.getAttribute('aria-current') };
      }),
      // Every visible link and button in the path, with its height.
      targets: [...nav.querySelectorAll('a, .sp-here, button')].filter(visible)
        .map((el) => ({ text: el.textContent, height: el.getBoundingClientRect().height })),
      pill: visible(pill) && {
        text: pill.firstChild.textContent,
        // Its tap area reaches 5 px past the 34 px it is drawn at, top and bottom.
        tapArea44: at(pillBox.top - 4.5) === pill && at(pillBox.bottom + 4.5) === pill,
      },
    };
  });
}

/**
 * A page signed in as far as the archive can tell: the desk and media pages
 * send anyone else to sign in. The API is another Worker in production, so
 * here its answers are stubbed in the browser.
 */
async function signedInPage(browser, viewport) {
  const page = await browser.newPage({ viewport });
  await page.route('**/api/desk/whoami', (r) => r.fulfill({ json: { email: 'reader@example.com' } }));
  await page.route('**/api/desk/media', (r) => r.fulfill({ json: { filings: [] } }));
  return page;
}

test('bound to site-shell, in a browser: every page shows the site path, with AI Digest current and its own crumbs', async (t) => {
  const s = await session(t, [APP, SHELL]);
  const browser = await chromium.launch({ channel: 'chrome' });
  t.after(() => browser.close());
  const appPaths = listedApps.map((a) => a.path);
  for (const [path, tail] of Object.entries(TAILS)) {
    for (const viewport of [{ width: 1440, height: 900 }, { width: 1280, height: 800 }, { width: 844, height: 390 }]) {
      const page = await signedInPage(browser, viewport);
      await page.goto(`${s.base}${path}`, { waitUntil: 'networkidle' });
      const at = `${path} at ${viewport.width}x${viewport.height}`;
      assert.equal(new URL(page.url()).pathname, path, `${at}: stayed on the page`);
      const sp = await readSitePath(page);
      assert.ok(sp, `${at}: the composed site path survives hydration`);
      assert.ok(sp.appsShown, `${at}: the apps sit inline`);
      assert.equal(sp.pill, false, `${at}: no pill where the apps fit`);
      assert.deepEqual(sp.appLinks.sort(), appPaths.filter((p) => p !== '/chat-archives' || tail.length).sort(),
        `${at}: a link to every listed app (AI Digest is plain text at its root)`);
      assert.deepEqual(sp.current, { text: 'AI Digest', ariaCurrent: tail.length ? 'true' : 'page' }, `${at}: AI Digest is current`);
      assert.deepEqual(sp.crumbs.map((c) => c.text), tail, `${at}: the page's own crumbs`);
      if (tail.length) assert.equal(sp.crumbs.at(-1).ariaCurrent, 'page', `${at}: the last crumb is this page`);
      for (const { text, height } of sp.targets) assert.ok(height >= 44, `${at}: "${text}" is ${height}px tall, under 44px`);
      await page.close();
    }

    // Portrait phone: the apps fold into the pill, which opens the bottom sheet.
    const page = await signedInPage(browser, { width: 390, height: 844 });
    await page.goto(`${s.base}${path}`, { waitUntil: 'networkidle' });
    const sp = await readSitePath(page);
    assert.ok(sp, `${path} on a phone: the composed site path survives hydration`);
    assert.equal(sp.appsShown, false, `${path} on a phone: the apps fold away`);
    assert.deepEqual(sp.pill, { text: 'AI Digest', tapArea44: true }, `${path} on a phone: the AI Digest pill, 44px to tap`);
    assert.deepEqual(sp.crumbs.map((c) => c.text), tail.slice(-1), `${path} on a phone: only the last crumb`);
    await page.click('.sp-pill');
    const sheet = page.locator('dialog.sp-sheet');
    await sheet.waitFor({ state: 'visible' });
    const sheetLinks = await sheet.locator('ul a').evaluateAll((as) => as.map((a) => ({ href: a.getAttribute('href'), current: a.getAttribute('aria-current') })));
    assert.deepEqual(sheetLinks.map((l) => l.href), appPaths, `${path} on a phone: the sheet lists every listed app`);
    assert.deepEqual(sheetLinks.filter((l) => l.current).map((l) => l.href), ['/chat-archives'], `${path} on a phone: AI Digest is marked`);
    await sheet.getByRole('button', { name: 'Close' }).click();
    await sheet.waitFor({ state: 'hidden' });
    await page.close();
  }
});

// site-shell fills the slot only in a page loaded as a document, so a link
// between the archive's pages must not be a Next.js client-side navigation.
test('bound to site-shell, in a browser: following the masthead\'s Sign in lands on a page with the site path', async (t) => {
  const s = await session(t, [APP, SHELL]);
  const browser = await chromium.launch({ channel: 'chrome' });
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.goto(`${s.base}/chat-archives`, { waitUntil: 'networkidle' });
  await Promise.all([page.waitForURL('**/chat-archives/login'), page.click('.mh-actions .signin')]);
  await page.waitForLoadState('networkidle');
  const sp = await readSitePath(page);
  assert.ok(sp, 'the sign-in screen shows the composed site path');
  assert.deepEqual(sp.crumbs.map((c) => c.text), ['Reader access']);
});

test('unbound: with no SHELL binding every page is served exactly as built', async (t) => {
  const s = await session(t, [UNBOUND]);
  for (const [path, file] of Object.entries(PAGES)) {
    const res = await fetch(`${s.base}${path}`);
    assert.equal(res.status, 200, path);
    assert.equal(res.headers.get('x-site-shell'), null, path);
    assert.equal(await res.text(), asBuilt(file), path);
  }
});

test('bound, site-shell not running: compose rejects and the page is fetched again as built', async (t) => {
  const s = await session(t, [APP]);
  for (const [path, file] of Object.entries(PAGES)) {
    const res = await fetch(`${s.base}${path}`);
    assert.equal(res.status, 200, path);
    assert.equal(await res.text(), asBuilt(file), path);
  }
  assert.match(await logMatching(s, /site-shell: compose failed/), /site-shell: compose failed/);
});

test('site-shell throws: fail open to the page as built', async (t) => {
  const s = await session(t, [APP, THROWING_SHELL]);
  for (const [path, file] of Object.entries(PAGES)) {
    const res = await fetch(`${s.base}${path}`);
    assert.equal(res.status, 200, path);
    assert.equal(res.headers.get('x-site-shell'), null, path);
    assert.equal(await res.text(), asBuilt(file), path);
  }
  assert.match(await logMatching(s, /fault: compose threw/), /fault: compose threw/);
});
