import type { IngestResult, Session, SessionDetail, SessionListResponse } from '@/types';

// Point this at the Python Worker's origin (e.g. the wrangler dev server in
// development, or the deployed `*.workers.dev` / custom domain in production).
const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://127.0.0.1:8787';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  const data = (await res.json().catch(() => ({}))) as T & { error?: string };
  if (!res.ok) {
    throw new Error(data.error ?? `Request failed: HTTP ${res.status}`);
  }
  return data;
}

export function listSessions(): Promise<Session[]> {
  return request<SessionListResponse>('/api/sessions').then((d) => d.sessions);
}

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
