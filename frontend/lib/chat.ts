import type { Message } from '@/types';

/**
 * ChatGPT stores its web-source citations as private-use-area tokens inside the
 * message text, e.g.
 *
 *   …might speak again soon. \ue200cite\ue202turn0search1\ue202turn0news11\ue201
 *
 * The web app renders those as source pills; as raw text they leak into the
 * transcript as "cite turn0search1" noise. The share payload does not give us
 * the token -> URL mapping, so we strip them rather than invent links.
 */
export function cleanMessage(text: string): string {
  return text
    .replace(/\ue200cite(?:\ue202[^\ue202\ue201]+)+\ue201/g, '')
    .replace(/[\ue200-\ue20f]/g, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
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
  | { kind: 'assistant'; content: string }
  | { kind: 'system'; content: string }
  | { kind: 'tools'; count: number };

/**
 * Turn the raw message list into render-ready items:
 *  - consecutive redacted tool outputs collapse into one line with a count
 *  - system notices are separated out from real user turns
 */
export function toChatItems(messages: Message[]): ChatItem[] {
  const items: ChatItem[] = [];

  for (const message of messages) {
    const content = cleanMessage(message.content);
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
      items.push({ kind: 'assistant', content });
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
