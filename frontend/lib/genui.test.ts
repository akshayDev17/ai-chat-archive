/**
 * Tests for the GenUI markup parser, offline.
 *
 * ChatGPT writes some replies as component markup — `<box>`, `<row>`, `<icon/>`,
 * `<Cite/>` — and the archive used to hand that text to Markdown as-is: the tags
 * printed verbatim and the indented card bodies became code blocks. The parser
 * turns the markup into a tree the reader can render; these tests pin its
 * grammar against excerpts from real archived replies.
 *
 * Run:  cd frontend && npm run test:lib
 */

import assert from 'node:assert/strict';
import test from 'node:test';

import { dedent, hasGenUI, inlineMarkdown, parseGenUI, stripGenUI, type GenNode } from './genui.ts';
import type { Source } from '../types/index.ts';

// From the archived "Fact check WhatsApp claim" reply (6adf3276-…), verbatim.
const CARD = `<box gap={3}>
  <row align=start gap={3}>
    <icon name="check-circle" color="success" size="xl"/>
    <box flex="1" gap={1}>
      **1. WhatsApp really did make this statement.**

      On **25 April 2024**, WhatsApp's lawyer told the Delhi High Court: "if we are told to break encryption, then WhatsApp goes." <Cite refs={["turn633644search0","turn633644search3"]}/>
    </box>
  </row>
  <divider color="subtle"/>
</box>`;

const BUTTON = `<button block onClick={() => GenUI.openUrl("sandbox:/mnt/data/report.md")}>Download Markdown report</button>`;

function tags(nodes: GenNode[]): string[] {
  return nodes.flatMap((node) => (node.kind === 'tag' ? [node.tag, ...tags(node.children)] : []));
}

function only(nodes: GenNode[], tag: string): Extract<GenNode, { kind: 'tag' }> {
  for (const node of nodes) {
    if (node.kind !== 'tag') continue;
    if (node.tag === tag) return node;
    try {
      return only(node.children, tag);
    } catch {
      // keep looking in siblings
    }
  }
  throw new Error(`no <${tag}>`);
}

test('hasGenUI spots component markup and ignores ordinary prose', () => {
  assert.equal(hasGenUI(CARD), true);
  assert.equal(hasGenUI('Revenue grew <5% while x < y.'), false);
  assert.equal(hasGenUI('Use the `<b>` tag.'), false);
});

test('the card parses into its nested structure', () => {
  const tree = parseGenUI(CARD);
  assert.ok(tree);
  assert.deepEqual(tags(tree), ['box', 'row', 'icon', 'box', 'Cite', 'divider']);
});

test('attribute forms: numbers, bare words, strings, arrays', () => {
  const tree = parseGenUI(CARD)!;
  assert.deepEqual(only(tree, 'box').attrs, { gap: 3 });
  assert.deepEqual(only(tree, 'row').attrs, { align: 'start', gap: 3 });
  assert.deepEqual(only(tree, 'icon').attrs, { name: 'check-circle', color: 'success', size: 'xl' });
  assert.deepEqual(only(tree, 'Cite').attrs, { refs: ['turn633644search0', 'turn633644search3'] });
});

test('a boolean attribute, and an arrow function whose => does not end the tag', () => {
  const tree = parseGenUI(BUTTON)!;
  const button = only(tree, 'button');
  assert.equal(button.attrs.block, true);
  assert.deepEqual(button.attrs.onClick, { raw: '() => GenUI.openUrl("sandbox:/mnt/data/report.md")' });
  assert.deepEqual(button.children, [
    { kind: 'text', text: 'Download Markdown report', start: BUTTON.indexOf('Download') },
  ]);
});

test('start and end are offsets of the whole tag in the original text', () => {
  const tree = parseGenUI(CARD)!;
  const cite = only(tree, 'Cite');
  const tag = '<Cite refs={["turn633644search0","turn633644search3"]}/>';
  assert.equal(cite.start, CARD.indexOf(tag));
  assert.equal(cite.end, CARD.indexOf(tag) + tag.length);

  const outer = tree[0] as Extract<GenNode, { kind: 'tag' }>;
  assert.equal(outer.start, 0);
  assert.equal(outer.end, CARD.length);
});

test('offsets are UTF-16 positions, so an emoji before a tag still lines up', () => {
  const text = `🇺🇸 Fact. <Cite refs={["a"]}/>`;
  const cite = only(parseGenUI(text)!, 'Cite');
  assert.equal(text.slice(cite.start, cite.end), '<Cite refs={["a"]}/>');
});

test('a < that does not begin a tag is text, even in a GenUI reply', () => {
  const text = `<box>Revenue grew <5% while x < y and a <b> stayed put.</box>`;
  const tree = parseGenUI(text)!;
  assert.deepEqual(tags(tree), ['box']);
  const box = only(tree, 'box');
  assert.equal(box.children[0].kind === 'text' && box.children[0].text,
    'Revenue grew <5% while x < y and a <b> stayed put.');
});

test('tags inside a fenced code block are left as text', () => {
  const text = '<box>\nExample:\n\n```html\n<row gap={2}><icon name="x"/></row>\n```\n</box>';
  assert.deepEqual(tags(parseGenUI(text)!), ['box']);
});

test('an unclosed tag makes the parse fail rather than guess', () => {
  assert.equal(parseGenUI('<box gap={3}>\n  <row align=start>\n  text\n</box>'), null);
});

test('an unknown tag still parses, so the reader can render its contents', () => {
  const tree = parseGenUI('<widget foo="1">hello</widget>')!;
  const widget = only(tree, 'widget');
  assert.deepEqual(widget.attrs, { foo: '1' });
  assert.equal(widget.children[0].kind === 'text' && widget.children[0].text, 'hello');
});

test('dedent removes the card indentation that made Markdown see code', () => {
  const inner = only(only(parseGenUI(CARD)!, 'row').children, 'box');
  const firstText = inner.children.find((c) => c.kind === 'text');
  assert.ok(firstText && firstText.kind === 'text');

  const out = dedent(firstText.text);
  assert.ok(out.startsWith('**1. WhatsApp really did make this statement.**'), out);
  for (const line of out.split('\n')) assert.ok(!/^ {4}/.test(line), `still indented: ${line}`);
});

test('dedent keeps relative indentation, so nested lists survive', () => {
  assert.equal(dedent('\n    - a\n      - b\n    - c\n'), '- a\n  - b\n- c');
});

test('stripGenUI leaves readable Markdown and no tags', () => {
  const out = stripGenUI(CARD);
  assert.ok(out.includes('**1. WhatsApp really did make this statement.**'), out);
  assert.ok(!out.includes('<'), out);
  for (const line of out.split('\n')) assert.ok(!/^ {4}/.test(line), `still indented: ${line}`);
});

// -- inline runs: prose with Cite / Entity / Link inside it --------------------

function source(index: number, span: [number, number]): Source {
  return {
    index,
    kind: 'cite',
    title: `Source ${index}`,
    url: `https://example.com/${index}`,
    attribution: '',
    pub_date: null,
    spans: [span],
  };
}

function runOf(text: string): GenNode[] {
  return parseGenUI(text)!;
}

test('a Cite becomes one marker per source cited at that tag', () => {
  const text = 'A claim. <Cite refs={["a","b"]}/>';
  const span: [number, number] = [9, text.length];
  const { markdown } = inlineMarkdown(runOf(text), [source(1, span), source(2, span)]);

  assert.equal(
    markdown,
    'A claim. [\\[1\\]](https://example.com/1)[\\[2\\]](https://example.com/2)',
  );
});

test('an emoji before the Cite still finds its sources', () => {
  const text = '🇺🇸 A claim. <Cite refs={["a"]}/>';
  const start = text.indexOf('<Cite');
  const { markdown } = inlineMarkdown(runOf(text), [source(1, [start, text.length])]);
  assert.ok(markdown.endsWith('[\\[1\\]](https://example.com/1)'), markdown);
});

test('a Cite with no matching source disappears instead of printing', () => {
  const { markdown } = inlineMarkdown(runOf('A claim. <Cite refs={["a"]}/>'), []);
  assert.equal(markdown, 'A claim.');
});

test('an Entity becomes a genui-entity link carrying its index', () => {
  const text =
    'Starlink, operated by <Entity value="SpaceX" category="company" disambig="Aerospace company"/>, provides…';
  const { markdown, entities } = inlineMarkdown(runOf(text), []);

  assert.equal(markdown, 'Starlink, operated by [SpaceX](genui-entity:0), provides…');
  assert.deepEqual(entities, [{ value: 'SpaceX', disambig: 'Aerospace company' }]);
});

test('a Link becomes an ordinary Markdown link', () => {
  const text = 'Sources: <Link url="https://www.arcep.fr/a" title="ARCEP, June 2022"/>';
  const { markdown } = inlineMarkdown(runOf(text), []);
  assert.equal(markdown, 'Sources: [ARCEP, June 2022](https://www.arcep.fr/a)');
});

test('the run is dedented after the inline tags are replaced', () => {
  const text = '\n      **Bold.**\n\n      More. <Cite refs={["a"]}/>\n    ';
  const start = text.indexOf('<Cite');
  const { markdown } = inlineMarkdown(runOf(text), [source(1, [start, start + '<Cite refs={["a"]}/>'.length])]);
  assert.equal(markdown, '**Bold.**\n\nMore. [\\[1\\]](https://example.com/1)');
});

test('a run that starts mid-line still loses the indent of its later lines', () => {
  // `<box>Intro…` — the first line follows the tag, so it has no indent of its
  // own; the paragraph after the blank line must not become a code block.
  assert.equal(dedent('Intro.\n\n      A later paragraph.\n    '), 'Intro.\n\nA later paragraph.');
});

// -- ordinary replies that merely *mention* component names ---------------------

const CODING_ANSWER = [
  'Wrap it in `<Link href="/x">Home</Link>` and done. A `<row>` is not a tag here.',
  '',
  '```jsx',
  '<Link href="/">',
  '  <box>Home</box>',
  '</Link>',
  '```',
].join('\n');

test('a coding answer that mentions <Link> or <box> in code is not GenUI', () => {
  assert.equal(hasGenUI(CODING_ANSWER), false);
});

test('a lone inline component outside any structure is not enough to call it GenUI', () => {
  assert.equal(hasGenUI('See <Link url="https://x.example" title="X"/>.'), false);
});

test('inline code inside a GenUI reply stays text', () => {
  const text = '<box>Use `<Link href="/x">` here, or `<row>`.</box>';
  const tree = parseGenUI(text)!;
  assert.ok(tree, 'an unclosed <row> inside inline code must not fail the parse');
  assert.deepEqual(tags(tree), ['box']);
});

test('stripGenUI leaves fenced code exactly as written', () => {
  const text = '<box>\n  Example:\n\n```jsx\n<Link href="/">\n  <box>Home</box>\n</Link>\n```\n</box>';
  const out = stripGenUI(text);
  assert.ok(out.includes('```jsx\n<Link href="/">\n  <box>Home</box>\n</Link>\n```'), out);
  assert.ok(out.startsWith('Example:'), out);
});

test('old-style citation tokens in a GenUI text run still become markers', () => {
  const token = '\ue200cite\ue202turn0search0\ue201';
  const text = `<box>A claim. ${token}</box>`;
  const start = text.indexOf(token);
  const box = parseGenUI(text)![0] as Extract<GenNode, { kind: 'tag' }>;
  const { markdown } = inlineMarkdown(box.children, [source(1, [start, start + token.length])]);
  assert.equal(markdown, 'A claim. [\\[1\\]](https://example.com/1)');
});

test('a stray token with no source is removed, not printed', () => {
  const box = parseGenUI('<box>A claim. \ue200entity\ue202["x","y"]\ue201 More.</box>')![0] as Extract<GenNode, { kind: 'tag' }>;
  const { markdown } = inlineMarkdown(box.children, []);
  assert.ok(!/[\ue200-\ue20f]|entity\[/.test(markdown), markdown);
});
