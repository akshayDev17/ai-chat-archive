'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ingestSession, isSignInRequired, listFilings, whoami } from '@/lib/api';
import { AFTER_SIGN_IN } from '@/lib/nav';
import type { Session } from '@/types';
import StoryLink from './StoryLink';

/**
 * The copy desk: where a share link becomes a story in the paper.
 *
 * This is the surface Cloudflare's one-time PIN opens up. It is deliberately
 * NOT the front page — reading and filing are different jobs, and the front
 * page is public while this is not.
 *
 * The page enforces its own gate rather than relying on the router, because
 * the gate is a *server* fact: `GET /api/filings` answers 401 to anyone without
 * an identity. Bouncing to the sign-in screen (with `next` pointing back here)
 * is the friendly rendering of that same fact, not a substitute for it.
 */
export default function CopyDesk() {
  const router = useRouter();
  const [email, setEmail] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);
  const [filings, setFilings] = useState<Session[]>([]);

  const loadFilings = useCallback(async () => {
    try {
      setFilings(await listFilings());
    } catch {
      // A failed filings read must not hide the form — filing is the point of
      // the page, and the form reports its own errors.
      setFilings([]);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    whoami()
      .then((value) => {
        if (cancelled) return;
        if (!value) {
          router.replace(`/chat-archives/login?next=${encodeURIComponent(AFTER_SIGN_IN)}`);
          return;
        }
        setEmail(value);
        setChecking(false);
        void loadFilings();
      })
      .catch(() => {
        if (!cancelled) {
          router.replace(`/chat-archives/login?next=${encodeURIComponent(AFTER_SIGN_IN)}`);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [router, loadFilings]);

  if (checking) return <p className="fine">Opening the copy desk…</p>;

  return (
    <div className="desk">
      <section className="desk-intro">
        {/* The masthead already names the place ("Copy desk"), so the kicker
            names the task instead of repeating it. */}
        <div className="kicker">New filing</div>
        <h1 className="hl">File a session</h1>
        <p className="standfirst">
          Paste a share link and it is set into the paper: the summary report as the article,
          the transcript as the interview. Signed in as <em>{email}</em>.
        </p>
      </section>

      <FileSession onFiled={loadFilings} />

      {filings.length > 0 ? (
        <section className="desk-filings">
          <h2 className="desk-sub">Your recent filings</h2>
          <ul>
            {filings.map((session) => (
              <li key={session.id}>
                <StoryLink session={session} variant="flow" />
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

/** The filing form itself: one field, one button, one honest status line. */
function FileSession({ onFiled }: { onFiled: () => void }) {
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setNote(null);
    try {
      const result = await ingestSession(url);
      setNote({
        text: `“${result.title}” is in the edition — ${result.messages} messages, ${result.citations} citations.`,
        error: false,
      });
      setUrl('');
      onFiled();
    } catch (error) {
      if (isSignInRequired(error)) {
        // The session expired between opening the desk and filing. Access
        // answers a fetch() with a redirect that the browser follows as a GET,
        // dropping the POST body, so the import would otherwise fail silently.
        setNote({ text: 'Your session expired — sign in again to file.', error: true });
      } else {
        setNote({
          text: error instanceof Error ? error.message : 'Import failed',
          error: true,
        });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="file-form" onSubmit={submit}>
      <label htmlFor="share-url">ChatGPT share link</label>
      <div className="file-row">
        <input
          id="share-url"
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://chatgpt.com/share/…"
          required
          autoFocus
        />
        <button type="submit" className="btn" disabled={busy}>
          {busy ? 'Filing…' : 'File it'}
        </button>
      </div>
      {note ? (
        <p className={note.error ? 'file-msg error' : 'file-msg'}>{note.text}</p>
      ) : (
        <p className="fine">
          In the ChatGPT share dialog, choose <em>Share link</em> and paste it here.
        </p>
      )}
    </form>
  );
}
