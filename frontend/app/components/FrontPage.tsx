'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { listSessions } from '@/lib/api';
import type { Session } from '@/types';
import StoryLink from './StoryLink';

const LEAD_SLOTS = 2; // two leads above the fold; the third heads the flow

/**
 * The edition. Public, and the same for everyone: there is no signed-out branch
 * here any more, because there is nothing to hide — the front page is the
 * newspaper and every filed story appears in it.
 */
export default function FrontPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setSessions(await listSessions());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load the edition');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // The copy desk announces this after a successful filing, so the edition
  // picks up a story filed in another tab without a reload.
  useEffect(() => {
    const onUpdated = () => void refresh();
    window.addEventListener('archive:updated', onUpdated);
    return () => window.removeEventListener('archive:updated', onUpdated);
  }, [refresh]);

  const leads = sessions.slice(0, LEAD_SLOTS);
  const flowLead = sessions[LEAD_SLOTS];

  // Round-robin the remainder across three columns so each ends at its own
  // depth — the ragged bottom a printed front page has.
  const columns = useMemo(() => {
    const cols: Session[][] = [[], [], []];
    sessions.slice(LEAD_SLOTS + 1).forEach((session, i) => cols[i % 3].push(session));
    return cols;
  }, [sessions]);

  if (loading) return <p className="fine">Printing the edition…</p>;
  if (error) return <div className="status error">{error}</div>;
  if (sessions.length === 0) {
    return (
      <div className="empty">
        No stories yet. Sign in and file a share link to set the first edition.
      </div>
    );
  }
  if (sessions.length === 0) {
    return (
      <div className="empty">
        No stories yet. Paste a ChatGPT share link above to set the first edition.
      </div>
    );
  }

  return (
    <div className="frontpage">
      <div className={leads.length > 1 ? 'leads leads-2' : 'leads'}>
        {leads.map((session) => (
          <StoryLink key={session.id} session={session} variant="lead" />
        ))}
      </div>

      {flowLead ? <StoryLink session={flowLead} variant="flow-lead" /> : null}

      {columns.some((col) => col.length > 0) ? (
        <div className="flow">
          {columns.map((col, i) => (
            <div className="fcol" key={i}>
              {col.map((session) => (
                <StoryLink key={session.id} session={session} variant="flow" />
              ))}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
