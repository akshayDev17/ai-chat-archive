// The static export carries no theme of its own: site-shell owns the boot
// script, the theme tokens and the picker's definition, and adds them at the
// edge (worker.js). What the export must still carry is the picker element and
// the Classic Light fallback for when site-shell is absent.
//
//   npm run build && npm run test:export
import assert from 'node:assert/strict';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { before, test } from 'node:test';
import { fileURLToPath } from 'node:url';

const out = join(dirname(fileURLToPath(import.meta.url)), 'out');
// The shared token names (site-themes' catalogue).
const TOKENS = ['surface', 'surface-2', 'card', 'text', 'text-body', 'text-muted', 'line', 'line-strong', 'card-line',
  'card-line-hover', 'accent', 'focus', 'danger', 'warn', 'ok', 'shadow-rest', 'shadow-hover', 'radius-card',
  'font-serif', 'font-mono'];

const walk = (dir) => readdirSync(dir, { withFileTypes: true })
  .flatMap((e) => (e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)]));
let files;
before(() => {
  assert.ok(existsSync(join(out, 'chat-archives.html')), 'no static export: run `npm run build` first');
  files = walk(out);
});
const pages = () => files.filter((f) => f.endsWith('.html') && relative(out, f).startsWith('chat-archives'));
const sheets = () => files.filter((f) => f.endsWith('.css'));

/** Every rule in a minified stylesheet, as { selector, body }, at-rule blocks flattened. */
function rules(css) {
  const found = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  for (const m of css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(re)) found.push({ selector: m[1].trim(), body: m[2] });
  return found;
}

test('no exported page carries a theme boot script or theme tokens of its own', () => {
  assert.ok(pages().length >= 5, 'the archive pages were exported');
  for (const file of pages()) {
    const html = readFileSync(file, 'utf8');
    const head = html.slice(0, html.indexOf('</head>'));
    assert.equal(html.includes('__siteThemeBoot'), false, `${relative(out, file)}: no bundled boot script`);
    assert.equal(head.includes('data-site-shell'), false, `${relative(out, file)}: site-shell's block is added at the edge, not built in`);
    assert.equal(/<style[^>]*>[^<]*--surface:/.test(head), false, `${relative(out, file)}: no inline theme tokens`);
  }
});

// Login is left out: its masthead renders on the client (inside a Suspense
// boundary for the search params), so its exported HTML has none yet.
test('every prerendered masthead renders the plain <site-theme-picker hide-label> element', () => {
  for (const name of ['chat-archives.html', 'chat-archives/desk.html', 'chat-archives/desk/media.html', 'chat-archives/_story.html']) {
    const html = readFileSync(join(out, name), 'utf8');
    assert.match(html, /<site-theme-picker hide-label="" class="mh-theme"><\/site-theme-picker>/, name);
  }
});

test('the CSS declares the shared tokens only in the :where(:root) Classic Light fallback', () => {
  const all = sheets().flatMap((f) => rules(readFileSync(f, 'utf8')));
  const declaring = all.filter((r) => TOKENS.some((t) => new RegExp(`(^|;)\\s*--${t}\\s*:`).test(r.body)));
  assert.deepEqual([...new Set(declaring.map((r) => r.selector))], [':where(:root)'], 'no theme rule ships in the app CSS');
  const fallback = declaring[0].body;
  for (const t of TOKENS) assert.match(fallback, new RegExp(`(^|;)--${t}:`), `the fallback defines --${t}`);
  assert.match(fallback, /--surface:#EEF1F8/i, 'the fallback is Classic Light');
  assert.equal(all.some((r) => r.selector.includes('[data-theme')), false, 'no per-theme selectors in the app CSS');
});
