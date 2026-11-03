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
import { fileURLToPath } from 'node:url';

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

let build;
before(() => {
  assert.ok(siteThemes, 'set SITE_THEMES_DIR to a checkout of akshayDev17/site-themes');
  assert.ok(existsSync(SHELL), `no site-shell config at ${SHELL}`);
  assert.ok(existsSync(join(out, 'chat-archives.html')), 'no static export: run `npm run build` first');
  build = JSON.parse(readFileSync(join(siteThemes, 'shell/generated/build.json'), 'utf8'));
});

// Not a failure: globals.css carries a one-time copy of site-shell's Classic
// Light fallback, which only shows while site-shell is down. Say so when the
// copy has drifted from the current one, so it can be refreshed.
test('the Classic Light fallback in globals.css matches site-shell (advisory)', (t) => {
  const block = (text) => text.split('\n').find((l) => l.startsWith(':where(:root){'));
  const ours = block(readFileSync(join(frontend, 'app/globals.css'), 'utf8'));
  const theirs = block(readFileSync(join(siteThemes, 'shell/generated/fallback.css'), 'utf8'));
  assert.ok(ours, 'globals.css has the :where(:root) fallback block');
  if (ours !== theirs) t.diagnostic('globals.css fallback differs from site-themes shell/generated/fallback.css: copy the new block in');
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
/** The exported page plus site-shell's one insertion, right after its leading <meta charset>. */
function composed(file) {
  const page = asBuilt(file);
  assert.equal(page.split(META).length, 2, `${file} has exactly one ${META}`);
  const scripts = build.scripts.map((f) => `<script defer src="/chat-archives/_site/${build.id}/${f}"></script>`).join('');
  return page.replace(META, META + build.inline + scripts);
}

test('bound to site-shell: every page gains the theme head block after <meta charset> and nothing else', async (t) => {
  const s = await session(t, [APP, SHELL]);
  for (const [path, file] of Object.entries(PAGES)) {
    const res = await fetch(`${s.base}${path}`);
    const body = await res.text();
    assert.equal(res.status, 200, path);
    assert.equal(res.headers.get('x-site-shell'), `${build.id}; app=chat`, path);
    assert.equal(res.headers.get('etag'), null, `${path}: composed HTML carries no validator`);
    assert.equal(body, composed(file), `${path}: the exported page plus one insertion`);
    assert.ok(body.indexOf(META) < 1024, `${path}: <meta charset> stays inside the first 1024 bytes`);
    assert.equal(body.match(/data-site-shell/g).length, 2, `${path}: one boot <script> and one tokens <style>`);
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
