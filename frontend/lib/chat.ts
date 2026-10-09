import type { Message, Source } from '@/types';

/**
 * ChatGPT stores its web sources in `message.metadata.content_references`, and
 * leaves only an opaque private-use token behind in the text:
 *
 *   …might speak again soon. \ue200cite\ue202turn0search1\ue202turn0news11\ue201
 *
 * The source's `spans` say exactly which characters that token occupies, so the
 * token can be *replaced* by a real link rather than deleted. It used to be
 * deleted — a comment here claimed the payload gave no token→URL mapping. It
 * does; the mapping is `spans` + `sources`, and discarding it is why replies
 * that cited eighteen URLs showed none.
 */

/**
 * Strip any private-use characters left over after the real spans are done.
 *
 * Two passes matter here. The `url` rule runs first so an unreplaced link token
 * keeps its human label. The general rule then removes *any* other
 * PUA-delimited run **together with the literal text inside it** — because the
 * payload puts machine-readable JSON between those markers, and stripping only
 * the markers leaves the JSON in the prose:
 *
 *   \ue200entity\ue202["turn0business0","Charkop Mangroves"]\ue201
 *     → entity["turn0business0","Charkop Mangroves"]     (markers only)
 *     → (removed entirely)                                (whole token)
 *
 * Those runs are place cards, maps and image carousels — widgets, not sources.
 * Rendering them is the artifact work, deliberately not done yet; dropping them
 * is the honest placeholder, and it is what the trailing catch-all would leave
 * half-done.
 */
function stripTokens(text: string): string {
  return text
    .replace(/\ue200url\ue202([^\ue202\ue201]*)(?:\ue202[^\ue201]*)?\ue201/g, '$1')
    .replace(/\ue200[^\ue201]*\ue201/g, '')
    .replace(/[\ue200-\ue20f]/g, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/** A URL safe to drop inside markdown link parentheses. */
function markdownUrl(url: string): string {
  return url.replace(/[()]/g, (c) => (c === '(' ? '%28' : '%29')).replace(/ /g, '%20');
}

/** Escape the characters that would break a markdown link label. */
function markdownLabel(text: string): string {
  return text.replace(/[[\]]/g, (c) => `\\${c}`);
}

function markerFor(source: Source): string {
  // `link` sources were written by the model with a human label, so keep it.
  // `cite` pills had no label of their own — only a number.
  const label = source.kind === 'link' && source.title ? source.title : `[${source.index}]`;
  return `[${markdownLabel(label)}](${markdownUrl(source.url)})`;
}

/**
 * Replace a message's citation tokens with real links.
 *
 * Only the *markers* are rewritten — the numbered sources themselves are
 * deliberately not appended here. They used to be, as a `**Sources**` block at
 * the foot of the reply, which made the same list appear twice once the panel
 * behind `···` existed, and buried the prose under a bibliography nobody asked
 * for inline. The list lives in `SourcesPanel`; this function's whole job is to
 * make the markers in the text click through to it.
 */
export function resolveCitations(text: string, sources: Source[]): string {
  if (!sources.length) return stripTokens(text);

  const spans = sources
    .flatMap((source) => source.spans.map(([start, end]) => ({ start, end, source })))
    .sort((a, b) => a.start - b.start);

  // Scan forward, copying the text between spans and splicing a marker in at
  // each one. A span that overlaps the previous one, or runs past the end, is
  // dropped: an untrustworthy offset must not be allowed to rewrite the wrong
  // characters, and its token will be stripped below anyway.
  let out = '';
  let cursor = 0;
  for (const { start, end, source } of spans) {
    if (start < cursor || end > text.length || start >= end) continue;
    out += text.slice(cursor, start) + markerFor(source);
    cursor = end;
  }
  out += text.slice(cursor);

  return stripTokens(out);
}

/**
 * The share payload tags some non-conversational notices with `role: "user"`.
 * "Original custom instructions no longer available" is the one we see; it is a
 * system notice, so it should not render as a user bubble.
 */
const SYSTEM_NOTICES = [
  /^original custom instructions no longer available$/i,
  /no longer available$/i,
  /^conversation (started|ended)/i,
];

export function isSystemNotice(text: string): boolean {
  const t = text.trim();
  return SYSTEM_NOTICES.some((re) => re.test(t));
}

/**
 * A user turn can @-mention a skill, e.g. "@Chat to Markdown Report summarise
 * the above". The mention's boundary cannot be inferred reliably (the name
 * contains a lowercase "to"), so we match against the skills we know.
 */
const KNOWN_SKILLS = ['Chat to Markdown Report', 'Chat to Markdown'];

export function splitMention(text: string): { mention: string | null; rest: string } {
  for (const skill of KNOWN_SKILLS) {
    if (text.startsWith(`@${skill}`)) {
      return { mention: skill, rest: text.slice(skill.length + 1).trim() };
    }
  }
  return { mention: null, rest: text };
}

export type ChatItem =
  | { kind: 'user'; content: string }
  | { kind: 'assistant'; content: string; sources: Source[] }
  | { kind: 'system'; content: string }
  | { kind: 'tools'; count: number };

/**
 * Turn the raw message list into render-ready items:
 *  - consecutive redacted tool outputs collapse into one line with a count
 *  - system notices are separated out from real user turns
 *  - an assistant turn carries its sources, for the panel behind its `···`
 */
export function toChatItems(messages: Message[]): ChatItem[] {
  const items: ChatItem[] = [];

  for (const message of messages) {
    const content = resolveCitations(message.content, message.sources ?? []);
    if (!content) continue;

    if (message.role === 'tool') {
      const last = items[items.length - 1];
      if (last?.kind === 'tools') {
        last.count += 1;
        continue;
      }
      items.push({ kind: 'tools', count: 1 });
      continue;
    }

    if (message.role === 'assistant') {
      items.push({ kind: 'assistant', content, sources: message.sources ?? [] });
      continue;
    }

    if (isSystemNotice(content)) {
      items.push({ kind: 'system', content });
      continue;
    }

    items.push({ kind: 'user', content });
  }

  return items;
}
