import type {
  Filing,
  IngestResult,
  MediaFiling,
  MediaStory,
  Session,
  SessionDetail,
  SessionListResponse,
  Visibility,
  WhoAmI,
} from '@/types';
import type { MediaCounts } from '@/lib/media';

// Point this at the Python Worker's origin (e.g. the local dev server while
// developing, or the same origin in production once the Worker and the site
// share a hostname).
const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://127.0.0.1:8787';

/**
 * An HTTP failure carrying its status, because the UI must tell two very
 * different situations apart: "you are not signed in" (offer the sign-in page)
 * and "the archive is broken" (offer a retry). Collapsing both into one
 * `Error` is how a 401 ends up rendered as a scary red outage banner.
 */
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export function isSignInRequired(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

/** How long to wait before asking a second time after a 401. */
export const SIGN_IN_RETRY_DELAY_MS = 250;

/**
 * One request, asked twice if the first answer is a 401.
 *
 * A signed-in reader's `/api/desk/*` calls intermittently 401 while their
 * siblings, carrying the same cookies, succeed: the Worker's identity lookup
 * sometimes comes back empty. One stray 401 must not flip the masthead to
 * "Sign in", so a 401 is asked again once, after a short pause, before it is
 * believed. A retried POST, PATCH or DELETE is safe: the Worker checks identity
 * before doing anything, so a 401 means nothing was done.
 */
async function fetchRetryingSignIn(url: string, init: RequestInit): Promise<Response> {
  const res = await fetch(url, init);
  if (res.status !== 401) return res;
  await new Promise((resolve) => setTimeout(resolve, SIGN_IN_RETRY_DELAY_MS));
  return fetch(url, init);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetchRetryingSignIn(`${API_BASE}${path}`, {
    // Send the auth cookie once the Worker sets one; harmless before that.
    credentials: 'include',
    ...init,
  });
  const data = (await res.json().catch(() => ({}))) as T & { error?: string };
  if (!res.ok) {
    throw new ApiError(data.error ?? `Request failed: HTTP ${res.status}`, res.status);
  }
  return data;
}

/**
 * The signed-in reader's email, or `null` when nobody is signed in.
 *
 * It asks on a **`/api/desk/`** path on purpose. `ctx.access` is populated only
 * where Cloudflare Access actually runs, and the Access app covers
 * `/api/desk/*` — so on a public path this answered "nobody" even for a
 * signed-in reader, and the copy desk bounced straight back to the sign-in
 * screen. Anonymous now fails (Access redirects the call, or it 401s), which
 * every caller already treats as "signed out".
 */
export async function whoami(): Promise<string | null> {
  return (await request<WhoAmI>('/api/desk/whoami')).email ?? null;
}

/**
 * The public edition — every public story, from every filer.
 *
 * No identity required: the front page is a newspaper, so it reads the same for
 * everyone. Ownership is provenance (internal), not a filter. Stories their
 * filers keep private are not in it.
 */
export function listSessions(): Promise<Session[]> {
  return request<SessionListResponse>('/api/sessions').then((d) => d.sessions);
}

/** Your own filings, newest first — public and private, each saying which. */
export function listFilings(): Promise<Filing[]> {
  return request<{ sessions: Filing[] }>('/api/desk/filings').then((d) => d.sessions);
}

/**
 * The media page's reads and actions — your filings' image copies.
 *
 * All under `/api/desk/`, so they are signed-in only and scoped to stories you
 * filed. The copying itself runs in the background (a once-a-minute Cron
 * Trigger); "Copy now" just runs one batch for one story immediately.
 */
export function mediaSummary(): Promise<MediaFiling[]> {
  return request<{ filings: MediaFiling[] }>('/api/desk/media').then((d) => d.filings);
}

export function mediaFor(id: string): Promise<MediaStory> {
  return request<MediaStory>(`/api/desk/media/${encodeURIComponent(id)}`);
}

/** Put a story's failed images back in the queue; returns how many. */
export function retryMedia(id: string): Promise<number> {
  return request<{ reset: number }>(`/api/desk/media/${encodeURIComponent(id)}/retry`, {
    method: 'POST',
  }).then((d) => d.reset);
}

export function copyMediaNow(id: string): Promise<{ copied: number; failed: number }> {
  return request<{ copied: number; failed: number }>(
    `/api/desk/media/${encodeURIComponent(id)}/copy`,
    { method: 'POST' },
  );
}

/** Where a copied image is served from (`src` comes from the story payload). */
export function mediaSrc(src: string): string {
  return `${API_BASE}${src}`;
}

/**
 * One of your own copied images, by its storage key.
 *
 * The signed-in route, not the public `/api/media/`: a private story's images
 * are served nowhere else, and the media page shows only your own.
 */
export function ownImageSrc(key: string): string {
  return `${API_BASE}/api/desk/image/${key}`;
}

/** A single public story, by permalink: no sign-in required. */
export function getSession(id: string): Promise<SessionDetail> {
  return request<SessionDetail>(`/api/sessions/${encodeURIComponent(id)}`);
}

/**
 * A story as whoever is asking may read it.
 *
 * The public permalink first. A story its filer keeps private is not there —
 * it answers 404 to everyone — so the reader's own copy is asked for next, on
 * the signed-in desk route; that answers only its filer. When neither does, the
 * public answer is the one reported: from where the reader stands, there is no
 * such story.
 */
export function readStory(id: string): Promise<SessionDetail> {
  return getSession(id).catch((notPublic: unknown) =>
    request<SessionDetail>(`/api/desk/stories/${encodeURIComponent(id)}`).catch(() => {
      throw notPublic;
    }),
  );
}

/**
 * File a share link. `visibility` is the filer's own choice, sent only when
 * they made one: left out, a new story is public and one filed again keeps
 * what it had.
 */
export function ingestSession(shareUrl: string, visibility?: Visibility): Promise<IngestResult> {
  return request<IngestResult>('/api/desk/ingest', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ share_url: shareUrl, visibility }),
  });
}

export interface FilingStatus {
  yours: boolean;
  /** Public or private, when it is yours; `null` otherwise. */
  visibility: Visibility | null;
  /** How the story's image copies stand; `null` when it has no images. */
  images: MediaCounts | null;
}

/**
 * Whether the signed-in reader filed this story — so the reader can offer them
 * its controls (delete, public or private) and a link to its images. Not theirs
 * for anyone else, and for anonymous visitors: Access bounces their
 * `/api/desk/` call (or it 401s), which is the same answer.
 */
export function filingStatus(id: string): Promise<FilingStatus> {
  return request<FilingStatus>(`/api/desk/sessions/${encodeURIComponent(id)}`).catch(() => ({
    yours: false,
    visibility: null,
    images: null,
  }));
}

/** Make one of your own stories public or private. Someone else's copy is untouched. */
export function setVisibility(id: string, visibility: Visibility): Promise<void> {
  return request<{ visibility: Visibility }>(`/api/desk/sessions/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ visibility }),
  }).then(() => undefined);
}

/**
 * Delete one of your own stories: its transcript, report, sources and images.
 * Someone else's copy of the same link stays.
 */
export function retractSession(id: string): Promise<void> {
  return request<{ retracted: boolean }>(`/api/desk/sessions/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  }).then(() => undefined);
}

/**
 * Whether a real sign-in provider is wired up yet.
 *
 * It is not: the one-time-PIN provider is still undecided (Cloudflare sending
 * the code, or the Worker sending it via an email service). Until that is
 * chosen, `AuthFlow` cannot complete a production sign-in, so it only *tries*
 * when explicitly told it may — see `devSignIn` below.
 */
export const DEV_AUTH = process.env.NEXT_PUBLIC_DEV_AUTH === '1';

/**
 * Local-only sign-in: sets the `archive_dev_email` cookie.
 *
 * This stands in for the identity provider so the flow you actually asked for
 * is walkable on the only machine it can be run on: public edition → Sign in →
 * login → desk. The endpoint exists solely in `backend/local_server.py`; the
 * deployed Worker has no equivalent, which is why this must stay behind
 * `DEV_AUTH` rather than being called unconditionally.
 */
export function devSignIn(email: string): Promise<{ email: string }> {
  return request<{ email: string }>('/api/dev/session', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email }),
  });
}

/** Local-only sign-out, so the signed-out state is reachable again. */
export function devSignOut(): Promise<void> {
  return request<Record<string, never>>('/api/dev/session', { method: 'DELETE' }).then(
    () => undefined,
  );
}

/**
 * End the session.
 *
 * In production the session belongs to Cloudflare Access, and its logout
 * endpoint has **no return target**: `<domain>/cdn-cgi/access/logout` is
 * documented, but nothing lets you say where to land afterwards (`?returnTo=`
 * is rejected with a 400, `?redirect_url=` is ignored). So a top-level
 * navigation to it strands the reader on Cloudflare's own sign-in page.
 *
 * So we ask it to clear the cookie **in the background** — a same-origin
 * `fetch()` still gets its `Set-Cookie` processed — and let the caller navigate
 * to wherever a signed-out reader belongs.
 *
 * If the background call did not take (Access normally expects a navigation),
 * the verification below falls back to that navigation, which always works.
 */
export async function signOut(): Promise<void> {
  if (DEV_AUTH) {
    await devSignOut();
    return;
  }

  const logoutUrl = `${API_BASE}/cdn-cgi/access/logout`;
  try {
    await fetch(logoutUrl, { credentials: 'include', redirect: 'manual' });
  } catch {
    // Leaving is the reader's intent; a failed call must not trap them here.
  }

  const stillSignedIn = await whoami().catch(() => null);
  if (stillSignedIn) window.location.href = logoutUrl;
}

/**
 * The URL the sign-in screen navigates to in order to authenticate.
 *
 * **This must be a top-level navigation, not a `fetch()`.** Cloudflare Access
 * serves its one-time-PIN screen from `<team>.cloudflareaccess.com` — a
 * different domain — and only ever redirects a *navigation* there. A protected
 * `fetch()` instead receives a 302 that the browser follows as a GET, dropping
 * the request body; that is the silent filing failure described in
 * `CopyDesk.tsx`.
 *
 * In production the API is same-origin, so the `CF_Authorization` cookie —
 * which is scoped to the domain, not the path — is attached on the way back.
 */
export function signInUrl(next = '/chat-archives/desk'): string {
  return `${API_BASE}/api/desk/enter?next=${encodeURIComponent(next)}`;
}
