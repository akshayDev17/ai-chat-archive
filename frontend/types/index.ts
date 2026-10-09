export interface Session {
  id: string;
  title: string;
  created_at: string;
  source: string;
  /**
   * Raw markdown of the summary report (the "thumbnail").
   *
   * Note there is no `owner_email` here. Provenance is stored server-side and
   * never sent on a listing: `/api/sessions` answers anonymous visitors, so a
   * per-row address would publish every filer's email in the JSON.
   */
  markdown?: string | null;
}

export interface SessionListResponse {
  /** Present only on the owner-scoped `/api/filings` read. */
  owner?: string;
  sessions: Session[];
}

export interface WhoAmI {
  /** `null` when nobody is signed in — an answer, not an error. */
  email: string | null;
}

export interface Message {
  role: string;
  content: string;
}

export interface Citation {
  n: number;
  title: string;
  url: string;
}

/** Full payload for the reader view (report + transcript + citations). */
export interface SessionDetail {
  id: string;
  title: string;
  source: string;
  report: string | null;
  citations: Citation[];
  messages: Message[];
}

export interface IngestResult {
  share_id: string;
  title: string;
  messages: number;
  report_length: number;
  citations: number;
}
