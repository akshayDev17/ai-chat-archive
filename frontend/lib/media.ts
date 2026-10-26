/**
 * The filer's one-line view of a story's image copies, and where it leads.
 *
 * Shown only to the story's filer — on the story page and on their front-page
 * cards — so the media page is one click away from wherever they are, not only
 * from the copy desk.
 */

export interface MediaCounts {
  total: number;
  copied: number;
  failed: number;
  /** Waiting or mid-copy. */
  pending: number;
}

export function imagesLabel(c: MediaCounts): string {
  if (c.copied === c.total) return `Images · all ${c.total} copied`;
  return `Images · ${c.copied} / ${c.total} copied${c.failed ? ` · ${c.failed} failed` : ''}`;
}

/** The media page, opened on one story. */
export function mediaHref(id: string): string {
  return `/chat-archives/desk/media?id=${encodeURIComponent(id)}`;
}
