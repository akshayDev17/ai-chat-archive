/**
 * Tests for the vendor lookup behind the bot mark and the source badges.
 *
 * Worth pinning because it is suffix matching on hostnames, which is the kind
 * of thing that either over-matches (`notopenai.com` becoming ChatGPT) or
 * silently stops matching (`chat.openai.com` after a rename). Neither shows up
 * as an error — a missed match just renders a monogram, which looks fine and is
 * wrong.
 *
 * Run:  cd frontend && npm run test:lib
 */

import assert from 'node:assert/strict';
import test from 'node:test';

import { domainOf, domainTint, vendorForUrl, vendorLabel, vendorMark } from '../lib/vendors.ts';

test('a vendor domain is recognised, bare or with www', () => {
  assert.equal(vendorForUrl('https://chatgpt.com/share/abc'), 'chatgpt');
  assert.equal(vendorForUrl('https://www.chatgpt.com/c/1'), 'chatgpt');
  assert.equal(vendorForUrl('https://gemini.google.com/app'), 'gemini');
  assert.equal(vendorForUrl('https://claude.ai/chat/1'), 'claude');
});

test('a vendor subdomain is recognised', () => {
  assert.equal(vendorForUrl('https://chat.openai.com/x'), 'chatgpt');
  assert.equal(vendorForUrl('https://www.anthropic.com/news'), 'claude');
});

test('a hostname that merely ends with a vendor name is not a vendor', () => {
  // The reason this is a suffix match on a dot boundary, not `endsWith` on the
  // raw string: otherwise a squatter's domain would wear the real logo.
  assert.equal(vendorForUrl('https://notopenai.com/x'), null);
  assert.equal(vendorForUrl('https://fakechatgpt.com/x'), null);
  assert.equal(vendorForUrl('https://chatgpt.com.evil.example/x'), null);
});

test('an unknown site is not a vendor', () => {
  assert.equal(vendorForUrl('https://www.reuters.com/x'), null);
  assert.equal(vendorForUrl('https://en.wikipedia.org/wiki/A_(b)'), null);
});

test('a vendor with no mark resolves to null, not a broken url', () => {
  // Elicit's slot is reserved and empty. Returning a path that 404s would show
  // an invisible mask rather than the monogram fallback.
  assert.equal(vendorForUrl('https://elicit.com/notebooks'), 'elicit');
  assert.equal(vendorMark('elicit'), null);
  assert.equal(vendorMark('chatgpt'), '/vendors/chatgpt.svg');
});

test('an unparseable url degrades instead of throwing', () => {
  assert.equal(vendorForUrl('not a url'), null);
  assert.equal(domainOf('not a url'), 'not a url');
  assert.equal(typeof domainTint('not a url'), 'number');
});

test('a domain always gets the same tint', () => {
  // Same site, same colour, across runs — the tile is a visual index, not noise.
  assert.equal(domainTint('https://www.reuters.com/a'), domainTint('https://reuters.com/b'));
  assert.ok(domainTint('https://reuters.com/a') >= 0);
  assert.ok(domainTint('https://reuters.com/a') < 6);
});

test('labels are human names', () => {
  assert.equal(vendorLabel('chatgpt'), 'ChatGPT');
  assert.equal(vendorLabel('something-new'), 'something-new');
});
