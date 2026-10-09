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
  /** Present only on the owner-scoped `/api/desk/filings` read. */
  owner?: string;
  sessions: Session[];
}

export interface WhoAmI {
  /** `null` when nobody is signed in — an answer, not an error. */
  email: string | null;
}

/**
 * A web source cited inside one message.
 *
 * `spans` are `[start, end]` offsets into that message's `content`, marking
 * where the citation sits. The source text itself holds only an opaque
 * private-use token there — the token means nothing without these offsets, and
 * the offsets mean nothing without the source. Plural because one source is
 * often cited several times in a single reply.
 */
export interface Source {
  /** 1-based, by order of first appearance in the message. */
  index: number;
  /** `cite` for an inline citation pill, `link` for a link the model wrote. */
  kind: 'cite' | 'link';
  title: string;
  url: string;
  attribution: string;
  pub_date: number | null;
  spans: [number, number][];
}

export interface Message {
  role: string;
  content: string;
  sources: Source[];
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
