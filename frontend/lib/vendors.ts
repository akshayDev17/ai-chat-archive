/**
 * Which vendor made a conversation, and which mark stands for it.
 *
 * The archive is meant to hold more than ChatGPT — `conversations.source`
 * already enumerates `chatgpt | gemini | claude | elicit` — so the vendored
 * marks in `public/vendors/` are keyed by that same value, and adding a vendor
 * is a file plus a row here.
 *
 * A mark is only returned when the file exists. Elicit has a reserved slot and
 * no file (see `public/vendors/README.md`), so it deliberately falls through to
 * the monogram rather than rendering a broken mask.
 */

export type Vendor = 'chatgpt' | 'gemini' | 'claude' | 'elicit';

/** Vendors with a mark in `public/vendors/`. */
const WITH_MARK: Vendor[] = ['chatgpt', 'gemini', 'claude'];

const LABELS: Record<Vendor, string> = {
  chatgpt: 'ChatGPT',
  gemini: 'Gemini',
  claude: 'Claude',
  elicit: 'Elicit',
};

/**
 * Domains that belong to a vendor, so a *source* pointing at one can carry the
 * vendor's mark instead of a monogram. Matched on hostname suffix, so
 * `chat.openai.com` and `www.chatgpt.com` both land on ChatGPT.
 */
const VENDOR_HOSTS: [string, Vendor][] = [
  ['chatgpt.com', 'chatgpt'],
  ['openai.com', 'chatgpt'],
  ['gemini.google.com', 'gemini'],
  ['bard.google.com', 'gemini'],
  ['claude.ai', 'claude'],
  ['anthropic.com', 'claude'],
  ['elicit.com', 'elicit'],
  ['elicit.org', 'elicit'],
];

export function vendorLabel(vendor: string): string {
  return LABELS[vendor as Vendor] ?? vendor;
}

/** The mark URL for a vendor, or null when that vendor has none yet. */
export function vendorMark(vendor: string): string | null {
  return WITH_MARK.includes(vendor as Vendor) ? `/vendors/${vendor}.svg` : null;
}

/** The vendor a source URL belongs to, or null when it belongs to none. */
export function vendorForUrl(url: string): Vendor | null {
  let host: string;
  try {
    host = new URL(url).hostname.replace(/^www\./, '').toLowerCase();
  } catch {
    return null;
  }
  for (const [domain, vendor] of VENDOR_HOSTS) {
    if (host === domain || host.endsWith(`.${domain}`)) return vendor;
  }
  return null;
}

/** The hostname of a source URL, without `www.` — the credit line under a title. */
export function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

/**
 * A stable tint index (0–5) for a domain, so a monogram tile looks deliberate
 * and the same site always gets the same colour. Summing code points rather
 * than hashing keeps it stable across runs, which a hash of a string in JS is
 * not guaranteed to be.
 */
export function domainTint(url: string): number {
  const domain = domainOf(url);
  let sum = 0;
  for (let i = 0; i < domain.length; i += 1) sum += domain.charCodeAt(i);
  return sum % 6;
}
