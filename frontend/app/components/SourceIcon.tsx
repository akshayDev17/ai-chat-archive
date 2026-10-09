import { domainOf, domainTint, vendorForUrl, vendorMark } from '@/lib/vendors';

/**
 * The square badge that sits to the left of every source row.
 *
 * Three cases, in order:
 *
 *  1. **A vendor we have a mark for** — ChatGPT, Gemini, Claude — rendered as a
 *     CSS `mask` so the single-path SVG takes `currentColor`. One file serves
 *     the paper, the ink and the accent without three variants.
 *  2. **A vendor without a mark yet** (Elicit) and **any other site** — a
 *     monogram: the domain's first letter on a tint picked from the domain, so
 *     a given site always looks the same and the list does not read as random.
 *  3. Nothing at all if the URL is unparseable — the row still renders.
 *
 * Deliberately **not** a remote favicon service. Fetching `google.com/s2/…` for
 * every source would hand a third party the reading list of a private archive,
 * and would break the moment that endpoint changed. When real favicons are
 * wanted, they belong fetched once at ingest and stored — not requested from
 * every reader's browser, forever.
 */
export default function SourceIcon({ url, size = 24 }: { url: string; size?: number }) {
  const vendor = vendorForUrl(url);
  const mark = vendor ? vendorMark(vendor) : null;

  if (mark) {
    return (
      <span
        className="src-icon src-icon-vendor"
        style={{
          width: size,
          height: size,
          maskImage: `url(${mark})`,
          WebkitMaskImage: `url(${mark})`,
        }}
        role="img"
        aria-label={vendor ?? undefined}
      />
    );
  }

  const domain = domainOf(url);
  return (
    <span
      className="src-icon src-icon-mono"
      data-tint={domainTint(url)}
      style={{ width: size, height: size, fontSize: size * 0.5 }}
      aria-hidden="true"
    >
      {domain.charAt(0).toUpperCase()}
    </span>
  );
}
