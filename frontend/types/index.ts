export interface Session {
  id: string;
  title: string;
  created_at: string;
  source: string;
  /** Raw markdown of the summary report (the "thumbnail"). */
  markdown?: string | null;
}

export interface SessionListResponse {
  sessions: Session[];
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
