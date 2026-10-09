import type { IngestResult, Session, SessionDetail, SessionListResponse, WhoAmI } from '@/types';

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
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

/** The signed-in reader's email, or `null` when nobody is signed in. */
export async function whoami(): Promise<string | null> {
  try {
    return (await request<WhoAmI>('/api/whoami')).email ?? null;
  } catch (error) {
    if (isSignInRequired(error)) return null;
    throw error;
  }
}

/** The signed-in reader's own shelf. 401s when signed out — see FrontPage. */
export function listSessions(): Promise<Session[]> {
  return request<SessionListResponse>('/api/sessions').then((d) => d.sessions);
}

/** A single story, public by permalink: no sign-in required. */
export function getSession(id: string): Promise<SessionDetail> {
  return request<SessionDetail>(`/api/sessions/${encodeURIComponent(id)}`);
}

export function ingestSession(shareUrl: string): Promise<IngestResult> {
  return request<IngestResult>('/api/ingest', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ share_url: shareUrl }),
  });
}

/**
 * The URL the sign-in screen navigates to in order to authenticate.
 *
 * **This must be a top-level navigation, not a `fetch()`.** Cloudflare Access
 * serves its one-time-PIN screen from `<team>.cloudflareaccess.com` — a
 * different domain — and only ever redirects a *navigation* there. A protected
 * `fetch()` instead receives a 302 that the browser follows as a GET, dropping
 * the request body; that is the silent import failure described in
 * `UploadInline.tsx`.
 *
 * In production the API is same-origin, so the `CF_Authorization` cookie —
 * which is scoped to the domain, not the path — is attached on the way back.
 */
export function signInUrl(next = '/chat-archives'): string {
  return `${API_BASE}/api/session/start?next=${encodeURIComponent(next)}`;
}
