/**
 * The report's inline citations are plain text, not links:
 *
 *   body:  ...the agreement was "not imminent."[1][2]
 *   bib:   [1] Reuters. "US-India trade deal not imminent, Greer says." …
 *          https://www.reuters.com/…
 *
 * There is no markdown link syntax and no anchor anywhere in the source, so no
 * renderer can make them clickable. We post-process at render time instead of
 * asking every past report to be regenerated:
 *
 *   [1]        ->  [[1]](#ref-1)                      (inline, becomes a link)
 *   [1] Title  ->  <a id="ref-1"></a>[[1]](#ref-1)    (bibliography, is the target)
 *
 * The injected <a id> needs `rehype-raw` on the ReactMarkdown side.
 */

const BIB_HEADING = /^##\s+Bibliography\s*$/m;

export function linkifyCitations(markdown: string): string {
  const heading = BIB_HEADING.exec(markdown);
  if (!heading) return markdown;

  const body = markdown.slice(0, heading.index);
  const bibliography = markdown.slice(heading.index);

  // Inline markers: [1] and runs like [1][2]. Skip ones already linked.
  const linkedBody = body.replace(
    /\[(\d{1,2})\](?!\()/g,
    (_match, n: string) => `[[${n}]](#ref-${n})`,
  );

  // Bibliography entries become the anchor targets.
  const linkedBibliography = bibliography.replace(
    /^\[(\d{1,2})\]/gm,
    (_match, n: string) => `<a id="ref-${n}"></a>[[${n}]](#ref-${n})`,
  );

  return linkedBody + linkedBibliography;
}
