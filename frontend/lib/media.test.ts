/**
 * Tests for the filer's image-progress line, offline.
 *
 * Shown to a story's filer on the story page and on their front-page cards, it
 * links through to that story on the media page.
 *
 * Run:  cd frontend && npm run test:lib
 */

import assert from 'node:assert/strict';
import test from 'node:test';

import { imagesLabel, mediaHref } from './media.ts';

test('all copied', () => {
  assert.equal(imagesLabel({ total: 14, copied: 14, failed: 0, pending: 0 }), 'Images · all 14 copied');
});

test('still copying', () => {
  assert.equal(imagesLabel({ total: 14, copied: 10, failed: 0, pending: 4 }), 'Images · 10 / 14 copied');
});

test('some failed', () => {
  assert.equal(
    imagesLabel({ total: 12, copied: 11, failed: 1, pending: 0 }),
    'Images · 11 / 12 copied · 1 failed',
  );
});

test('the link opens that story on the media page', () => {
  assert.equal(mediaHref('6adf3276-41cc'), '/chat-archives/desk/media?id=6adf3276-41cc');
});
