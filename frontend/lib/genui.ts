/**
 * ChatGPT's GenUI markup, parsed into a tree the reader can render.
 *
 * Some replies are written as components rather than plain Markdown:
 *
 *   <box gap={3}>
 *     <row align=start gap={3}>
 *       <icon name="check-circle" color="success" size="xl"/>
 *       <box flex="1" gap={1}>
 *         **1. WhatsApp really did make this statement.** … <Cite refs={["turn6…"]}/>
 *
 * Handed to Markdown as-is, every tag printed verbatim and the indented bodies
 * became code blocks (four spaces of indentation is code in Markdown). This
 * module parses the markup; `GenUI.tsx` renders the tree through a registry.
 *
 * The syntax is JSX-like and the scanner is hand-written for it, because the
 * easy regex — `<[^>]*>` — ends `onClick={() => GenUI.openUrl(…)}` at the `>` of
 * its arrow. Attribute values are read, never evaluated.
 *
 * Offsets (`start`/`end`) are UTF-16 positions in the original text — the same
 * coordinate space as the wire's source spans and media `tag_start` — so a
 * `<Cite/>` finds its sources and an `<AsyncImage/>` its copied image by offset.
 */

import { markdownLabel, markdownUrl, markersAt, resolveCitationsIn } from './chat.ts';
import type { Source } from '@/types';

export type AttrValue = string | number | boolean | string[] | { raw: string };

export type GenNode =
  | {
      kind: 'tag';
      tag: string;
      attrs: Record<string, AttrValue>;
      children: GenNode[];
      start: number;
      end: number;
    }
  | { kind: 'text'; text: string; start: number };

type TagNode = Extract<GenNode, { kind: 'tag' }>;

/** Components that sit inside a line of prose rather than forming a block. */
export const INLINE_TAGS = new Set(['Cite', 'Entity', 'Link']);

/** Every component seen in archived replies. An unclosed one is malformed markup. */
const KNOWN_TAGS = new Set([
  'box', 'row', 'grid', 'grid-item', 'text', 'title', 'icon', 'divider', 'button',
  'AsyncImage', 'AsyncImageGroup', ...INLINE_TAGS,
]);

/**
 * Tags that only GenUI markup produces. `Link`, `Entity`, `icon` and `divider`
 * are deliberately absent: they only ever appear inside these, and `<Link` is
 * everywhere in ordinary coding answers (Next.js, React Router).
 */
const GENUI_SIGNAL = /<(?:box|row|grid|Cite|AsyncImage)\b/;

/**
 * Whether a reply is GenUI markup at all. Plain replies keep today's path.
 *
 * Code does not count: a reply *explaining* `<box>` in a code block or an
 * inline `` `<row>` `` is a coding answer, and routing it here used to swallow
 * its code and drop its citations.
 */
export function hasGenUI(text: string): boolean {
  let prose = '';
  let cursor = 0;
  for (const [start, end] of codeRanges(text)) {
    prose += text.slice(cursor, start);
    cursor = end;
  }
  prose += text.slice(cursor);
  return GENUI_SIGNAL.test(prose);
}

interface ScannedTag {
  name: string;
  attrs: Record<string, AttrValue>;
  closing: boolean;
  selfClosing: boolean;
  /** Index just past the tag's final `>`. */
  end: number;
}

const NAME = /[A-Za-z][\w-]*/y;
const ATTR_NAME = /[A-Za-z_][\w-]*/y;
const BARE_VALUE = /[^\s>/]+/y;
const MAX_TAG_LENGTH = 4000;

function matchAt(re: RegExp, text: string, at: number): string | null {
  re.lastIndex = at;
  const m = re.exec(text);
  return m ? m[0] : null;
}

function skipSpace(text: string, at: number): number {
  while (at < text.length && /\s/.test(text[at])) at++;
  return at;
}

/** Index just past the `}` matching the `{` at `at`, skipping quoted strings. */
function braceEnd(text: string, at: number): number {
  let depth = 0;
  for (let i = at; i < text.length && i - at < MAX_TAG_LENGTH; i++) {
    const ch = text[i];
    if (ch === '"' || ch === "'" || ch === '`') {
      i = quoteEnd(text, i) - 1;
      if (i < 0) return -1;
    } else if (ch === '{') {
      depth++;
    } else if (ch === '}') {
      depth--;
      if (depth === 0) return i + 1;
    }
  }
  return -1;
}

/** Index just past the quote closing the one at `at`, honouring backslashes. */
function quoteEnd(text: string, at: number): number {
  const quote = text[at];
  for (let i = at + 1; i < text.length && i - at < MAX_TAG_LENGTH; i++) {
    if (text[i] === '\\') i++;
    else if (text[i] === quote) return i + 1;
  }
  return -1;
}

function braceValue(inner: string): AttrValue {
  const trimmed = inner.trim();
  if (/^-?\d+(?:\.\d+)?$/.test(trimmed)) return Number(trimmed);
  if (trimmed.startsWith('[')) {
    try {
      const parsed: unknown = JSON.parse(trimmed);
      if (Array.isArray(parsed) && parsed.every((v) => typeof v === 'string')) return parsed;
    } catch {
      // not JSON — keep it raw
    }
  }
  return { raw: trimmed };
}

/** Read one tag starting at the `<` at `at`, or `null` if it is not one. */
function scanTag(text: string, at: number): ScannedTag | null {
  let i = at + 1;
  const closing = text[i] === '/';
  if (closing) i++;

  const name = matchAt(NAME, text, i);
  if (!name) return null;
  i += name.length;

  if (closing) {
    i = skipSpace(text, i);
    return text[i] === '>' ? { name, attrs: {}, closing, selfClosing: false, end: i + 1 } : null;
  }

  const attrs: Record<string, AttrValue> = {};
  while (i < text.length && i - at < MAX_TAG_LENGTH) {
    const before = i;
    i = skipSpace(text, i);
    if (text.startsWith('/>', i)) return { name, attrs, closing, selfClosing: true, end: i + 2 };
    if (text[i] === '>') return { name, attrs, closing, selfClosing: false, end: i + 1 };
    if (i === before) return null; // attributes must be separated by whitespace

    const attr = matchAt(ATTR_NAME, text, i);
    if (!attr) return null;
    i += attr.length;
    const afterName = skipSpace(text, i);
    if (text[afterName] !== '=') {
      attrs[attr] = true;
      continue;
    }
    i = skipSpace(text, afterName + 1);

    if (text[i] === '"' || text[i] === "'") {
      const end = quoteEnd(text, i);
      if (end < 0) return null;
      attrs[attr] = text.slice(i + 1, end - 1);
      i = end;
    } else if (text[i] === '{') {
      const end = braceEnd(text, i);
      if (end < 0) return null;
      attrs[attr] = braceValue(text.slice(i + 1, end - 1));
      i = end;
    } else {
      const bare = matchAt(BARE_VALUE, text, i);
      if (!bare) return null;
      attrs[attr] = bare;
      i += bare.length;
    }
  }
  return null;
}

/**
 * `[start, end)` ranges of code — fenced blocks and inline code spans — whose
 * contents are never markup, in order.
 */
function codeRanges(text: string): [number, number][] {
  const fences = fencedRanges(text);
  const ranges: [number, number][] = [];
  let fence = 0;
  const inline = /`+/g;
  for (let m = inline.exec(text); m; m = inline.exec(text)) {
    while (fence < fences.length && fences[fence][1] <= m.index) fence++;
    if (fences[fence] && fences[fence][0] <= m.index) {
      inline.lastIndex = fences[fence][1];
      continue;
    }
    // An inline span closes at the next run of the same length, within its
    // paragraph (a blank line ends it, as in Markdown).
    const tick = m[0];
    const close = new RegExp(`(?<!\`)${tick}(?!\`)`, 'g');
    close.lastIndex = m.index + tick.length;
    const end = close.exec(text);
    const paragraphEnd = text.indexOf('\n\n', m.index);
    if (end && (paragraphEnd < 0 || end.index < paragraphEnd)) {
      ranges.push([m.index, end.index + tick.length]);
      inline.lastIndex = end.index + tick.length;
    }
  }
  return [...ranges, ...fences].sort((a, b) => a[0] - b[0]);
}

/** `[start, end)` ranges of fenced code blocks. */
function fencedRanges(text: string): [number, number][] {
  const ranges: [number, number][] = [];
  const fence = /^[ \t]*(`{3,}|~{3,})[^\n]*$/gm;
  let open: { at: number; marker: string } | null = null;
  for (let m = fence.exec(text); m; m = fence.exec(text)) {
    if (!open) open = { at: m.index, marker: m[1] };
    else if (m[1].startsWith(open.marker[0]) && m[1].length >= open.marker.length) {
      ranges.push([open.at, m.index + m[0].length]);
      open = null;
    }
  }
  if (open) ranges.push([open.at, text.length]);
  return ranges;
}

/**
 * Parse GenUI markup into a tree, or `null` when the markup is malformed
 * (an unclosed or mismatched tag) — the reader then falls back to
 * `stripGenUI` rather than rendering a guess.
 *
 * A `<` only starts a tag when what follows parses as one. An opening tag that
 * is not a known component and has no closing tag anywhere later (`<b>` in
 * prose) is text; an unclosed *known* component is malformed markup.
 */
export function parseGenUI(text: string): GenNode[] | null {
  const root: GenNode[] = [];
  const stack: TagNode[] = [];
  const fences = codeRanges(text);
  let fenceIndex = 0;

  const children = () => (stack.length ? stack[stack.length - 1].children : root);
  const appendText = (from: number, to: number) => {
    if (to <= from) return;
    const list = children();
    const last = list[list.length - 1];
    if (last && last.kind === 'text' && last.start + last.text.length === from) {
      last.text += text.slice(from, to);
    } else {
      list.push({ kind: 'text', text: text.slice(from, to), start: from });
    }
  };

  let textStart = 0;
  let i = 0;
  while (i < text.length) {
    while (fenceIndex < fences.length && fences[fenceIndex][1] <= i) fenceIndex++;
    const fence = fences[fenceIndex];
    if (fence && fence[0] <= i) {
      i = fence[1];
      continue;
    }

    if (text[i] !== '<') {
      i++;
      continue;
    }
    const tag = scanTag(text, i);
    const isOrphanOpen =
      tag &&
      !tag.closing &&
      !tag.selfClosing &&
      !KNOWN_TAGS.has(tag.name) &&
      !text.includes(`</${tag.name}>`, tag.end);
    if (!tag || isOrphanOpen) {
      i++;
      continue;
    }

    appendText(textStart, i);
    if (tag.closing) {
      const open = stack.pop();
      if (!open || open.tag !== tag.name) return null;
      open.end = tag.end;
    } else {
      const node: TagNode = {
        kind: 'tag',
        tag: tag.name,
        attrs: tag.attrs,
        children: [],
        start: i,
        end: tag.end,
      };
      children().push(node);
      if (!tag.selfClosing) stack.push(node);
    }
    i = tag.end;
    textStart = i;
  }
  appendText(textStart, text.length);
  return stack.length ? null : root;
}

/**
 * Remove the indentation a text run inherited from the markup around it.
 *
 * The first line usually follows a tag on the same line, so it is trimmed on
 * its own and does not count towards the common indent; the rest lose their
 * shared leading whitespace, which keeps nested list structure intact.
 */
export function dedent(text: string): string {
  const lines = text.replace(/^\s*\n/, '').split('\n');
  const rest = lines.slice(1).filter((line) => line.trim());
  const indent = rest.length
    ? Math.min(...rest.map((line) => line.match(/^[ \t]*/)![0].length))
    : 0;
  return lines
    .map((line, n) => (n === 0 ? line.trimStart() : line.slice(indent)))
    .join('\n')
    .replace(/\s+$/, '');
}

/**
 * The markup with every tag removed and every line unindented — the reader's
 * fallback for malformed markup, and the text the copy button copies.
 */
export function stripGenUI(text: string): string {
  // Tags go; code — fenced or inline — is copied through untouched.
  let out = '';
  let i = 0;
  for (const [start, end] of [...codeRanges(text), [text.length, text.length] as [number, number]]) {
    while (i < start) {
      const tag = text[i] === '<' ? scanTag(text, i) : null;
      if (tag && tag.end <= start) {
        i = tag.end;
      } else {
        out += text[i];
        i++;
      }
    }
    out += text.slice(Math.max(i, start), end);
    i = Math.max(i, end);
  }

  // Unindent prose lines, but never the lines inside a fenced block.
  let inFence = false;
  return out
    .split('\n')
    .map((line) => {
      const fence = /^\s*(`{3,}|~{3,})/.test(line);
      if (fence) inFence = !inFence;
      return inFence && !fence ? line : line.trimStart();
    })
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

export interface EntityRef {
  value: string;
  disambig: string;
}

function stringAttr(node: TagNode, name: string): string {
  const value = node.attrs[name];
  return typeof value === 'string' ? value : '';
}

/**
 * A run of prose — text plus the inline components inside it — as one Markdown
 * string, so a sentence with a `<Cite/>` in the middle stays one paragraph.
 *
 *   Cite   → the archive's `[n]` markers for the sources spanning that tag
 *            (none, if nothing resolved: the tag simply goes)
 *   Entity → `[value](genui-entity:<i>)`, `i` indexing the returned `entities`
 *   Link   → `[title](url)`
 *
 * The result is dedented, because the run's text carries the indentation of
 * the markup around it.
 */
export function inlineMarkdown(
  nodes: GenNode[],
  sources: Source[],
): { markdown: string; entities: EntityRef[] } {
  const entities: EntityRef[] = [];
  let markdown = '';
  for (const node of nodes) {
    if (node.kind === 'text') {
      // Older-style citation tokens can sit in the prose too; their spans are
      // message offsets, resolved against this piece.
      markdown += resolveCitationsIn(node.text, node.start, sources);
    } else if (node.tag === 'Cite') {
      markdown += markersAt(node.start, node.end, sources);
    } else if (node.tag === 'Entity') {
      const value = stringAttr(node, 'value');
      markdown += `[${markdownLabel(value)}](genui-entity:${entities.length})`;
      entities.push({ value, disambig: stringAttr(node, 'disambig') });
    } else if (node.tag === 'Link') {
      const url = stringAttr(node, 'url');
      const title = stringAttr(node, 'title') || url;
      markdown += url ? `[${markdownLabel(title)}](${markdownUrl(url)})` : markdownLabel(title);
    }
  }
  return { markdown: dedent(markdown), entities };
}
