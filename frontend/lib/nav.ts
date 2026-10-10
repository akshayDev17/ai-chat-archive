/**
 * Where to go, and where it is safe to go.
 *
 * The sign-in screen accepts a `next` parameter so that a visitor bounced from
 * the copy desk lands back on the copy desk rather than on the front page. That
 * value becomes a `router.push()` target, so it is sanitized the same way the
 * backend sanitizes its `Location` header (`chat_archive/urls.py`): a same-site
 * absolute path, or nothing.
 *
 * The backend guards its own copy of this for the `/api/desk/enter`
 * redirect. Duplicating the rule on both sides is deliberate — they are
 * different sinks (a `Location` header versus a client-side navigation) reached
 * by different code paths, and either one being unguarded is a phishing bug.
 */

/** Where a freshly signed-in reader should land: the copy desk, ready to file. */
export const AFTER_SIGN_IN = '/chat-archives/desk';

/**
 * Where a reader lands after signing out: the public edition, signed out.
 *
 * Not Access's own logout page. That endpoint has no return target, so
 * navigating to it strands the reader on Cloudflare's sign-in screen; the
 * session is cleared in the background instead and the reader is sent here.
 */
export const AFTER_SIGN_OUT = '/chat-archives';

export function safeNext(raw: string | null | undefined, fallback = AFTER_SIGN_IN): string {
  if (!raw || !raw.startsWith('/') || raw.startsWith('//')) return fallback;
  return raw;
}
