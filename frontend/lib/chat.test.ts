/**
 * Tests for turning citation tokens into real links, offline.
 *
 * Companion to backend/test/test_parser.py. The parser proves the sources are
 * extracted from the payload; this proves the reader does something useful with
 * them. The regression behind both is the same: the payload's citation tokens
 * were being deleted, so replies that cited eighteen URLs rendered with none,
 * and the docstring blamed the payload for a mapping it actually provides.
 *
 * Run:  cd frontend && npm run test:lib
 */

import assert from 'node:assert/strict';
import test from 'node:test';

import { resolveCitations } from '../lib/chat.ts';
import type { Source } from '../types/index.ts';

const CITE = '\ue200cite\ue202turn0search0\ue201';
const LONG_CITE = '\ue200cite\ue202turn0search0\ue202turn0search4\ue201';
const URL_REF = '\ue200url\ue202Read the statement\ue202turn0search0\ue201';

function cite(overrides: Partial<Source> = {}): Source {
  return {
    index: 1,
    kind: 'cite',
    title: 'A Source',
    url: 'https://example.com/a',
    attribution: 'Example',
    pub_date: null,
    spans: [],
    ...overrides,
  };
}

test('an inline citation token becomes a numbered link', () => {
  const text = `The deal was "not imminent."${CITE}`;
  const out = resolveCitations(text, [cite({ spans: [[26, 26 + CITE.length]] })]);

  assert.ok(out.includes('[\\[1\\]](https://example.com/a)'), out);
  assert.ok(!out.includes('\ue200'), 'the token must not survive');
});

test('the sources list is NOT appended inline any more', () => {
  // The list lives behind the reply's `···`. When it was also appended here the
  // same sources appeared twice, and the prose carried a bibliography nobody
  // asked for inline.
  const text = `Body.${CITE}`;
  const out = resolveCitations(text, [cite({ spans: [[5, 5 + CITE.length]] })]);

  assert.ok(!out.includes('**Sources**'), out);
  assert.ok(!out.includes('1. [A Source]'), out);
  assert.equal(out, 'Body.[\\[1\\]](https://example.com/a)');
});

test('one source cited several times keeps one number and every marker', () => {
  // Collapsing repeats into a single span would leave the second marker with
  // nothing to point at, and it would vanish with the token stripper.
  const text = `First${CITE} and again${CITE}.`;
  const spans: [number, number][] = [
    [5, 5 + CITE.length],
    [5 + CITE.length + 10, 5 + CITE.length + 10 + CITE.length],
  ];
  const out = resolveCitations(text, [cite({ spans })]);

  assert.equal(out.match(/\[\\\[1\\\]\]\(https:\/\/example\.com\/a\)/g)?.length, 2, out);
});

test('a multi-turn citation token is replaced as a whole', () => {
  const text = `Claim.${LONG_CITE}`;
  const out = resolveCitations(text, [cite({ spans: [[6, 6 + LONG_CITE.length]] })]);
  assert.ok(out.startsWith('Claim.[\\[1\\]](https://example.com/a)'), out);
  assert.ok(!out.includes('turn0search'), out);
});

test('a model-written link keeps its own label', () => {
  const text = `See ${URL_REF} for details.`;
  const out = resolveCitations(text, [
    cite({ kind: 'link', title: 'Read the statement', spans: [[4, 4 + URL_REF.length]] }),
  ]);
  assert.ok(out.includes('[Read the statement](https://example.com/a)'), out);
});

test('a source with no trusted span leaves the text alone', () => {
  // Bad offsets must not corrupt the text. The source is still carried on the
  // message, so it appears in the panel — it just has no marker to splice.
  const out = resolveCitations('No tokens here.', [cite({ spans: [] })]);
  assert.equal(out, 'No tokens here.');
});

test('overlapping offsets are skipped rather than splicing the wrong text', () => {
  const text = `Body${CITE} tail`;
  const out = resolveCitations(text, [
    cite({ index: 1, spans: [[4, 4 + CITE.length], [6, 20]] }),
  ]);
  assert.ok(out.includes('Body[\\[1\\]](https://example.com/a)'), out);
});

test('a message with no sources still has its tokens stripped', () => {
  const out = resolveCitations(`Body.${CITE}`, []);
  assert.equal(out, 'Body.');
});

test('parentheses in a url do not break the markdown link', () => {
  // Wikipedia URLs are full of them, and an unescaped `)` closes the markdown
  // link early and dumps the rest into the prose.
  const text = `See x.${CITE}`;
  const at = text.indexOf(CITE);
  const out = resolveCitations(text, [
    cite({ url: 'https://en.wikipedia.org/wiki/A_(b)', spans: [[at, at + CITE.length]] }),
  ]);
  assert.ok(out.includes('https://en.wikipedia.org/wiki/A_%28b%29'), out);
});
