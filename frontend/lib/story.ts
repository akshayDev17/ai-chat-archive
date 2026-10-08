import type { Session } from '@/types';

/** The report's first `# heading`, used when the session has no title. */
export function headline(session: Session): string {
  if (session.title && session.title !== 'Untitled') return session.title;
  const first = session.markdown?.match(/^#\s+(.+)$/m)?.[1];
  return first ?? 'Untitled session';
}

/**
 * The report's standfirst: its opening paragraph (usually the italic deck under
 * the title), stripped of markup and cut at a word boundary.
 *
 * Note the character classes use `[ \t]`, not `\s` — with the `m` flag `\s`
 * also matches newlines, which silently splices separate paragraphs together.
 */
export function teaser(session: Session, limit = 220): string {
  if (!session.markdown) return '';

  const body = session.markdown
    .replace(/^#{1,6} .*$/gm, '')            // headings
    .replace(/^[ \t]*[-–—*]\s+/gm, '')       // bullet marks
    .replace(/^[ \t]*>\s?/gm, '')            // quote marks
    .replace(/[*_`]/g, '')                   // emphasis / code marks
    .trim();

  const paragraph =
    body
      .split(/\n\s*\n/)
      .map((p) => p.replace(/\s+/g, ' ').trim())
      .find((p) => p.length > 0) ?? '';

  if (paragraph.length <= limit) return paragraph;

  const cut = paragraph.slice(0, limit);
  const lastSpace = cut.lastIndexOf(' ');
  return `${cut.slice(0, lastSpace > 40 ? lastSpace : limit).trim()}…`;
}

/** Count the report's footnoted sources. */
export function citationCount(markdown?: string | null): number {
  if (!markdown) return 0;
  const at = markdown.search(/^##\s+Bibliography\s*$/m);
  if (at === -1) return 0;
  return (markdown.slice(at).match(/^\[\d+\]/gm) ?? []).length;
}

/** CHATGPT · 2026-10-09 · 8 citations */
export function byline(session: Session): string {
  const parts = [session.source];
  if (session.created_at) parts.push(session.created_at.slice(0, 10));
  const citations = citationCount(session.markdown);
  if (citations) parts.push(`${citations} citations`);
  return parts.join(' · ');
}
