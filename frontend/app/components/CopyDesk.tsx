'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { DEV_AUTH, devSignOut, ingestSession, isSignInRequired, whoami } from '@/lib/api';
import { AFTER_SIGN_IN } from '@/lib/nav';

/**
 * The copy desk: where a share link becomes a story in the paper.
 *
 * This is the surface the sign-in flow opens up. It is deliberately NOT the
 * front page — reading and filing are different jobs, and the front page is
 * public while this is not.
 *
 * The page enforces its own gate rather than relying on the router, because
 * the gate is a *server* fact: `POST /api/desk/ingest` answers 401 to anyone without
 * an identity. Bouncing to the sign-in screen (with `next` pointing back here)
 * is the friendly rendering of that same fact, not a substitute for it.
 *
 * One job, one page: the form. There was briefly a "your recent filings" list
 * underneath, which made a single-purpose page into a dashboard — the filed
 * story is already in the edition, one click away on the front page, and
 * `archive:updated` refreshes it.
 */
export default function CopyDesk() {
  const router = useRouter();
  const [email, setEmail] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);

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
      })
      .catch(() => {
        if (!cancelled) {
          router.replace(`/chat-archives/login?next=${encodeURIComponent(AFTER_SIGN_IN)}`);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  async function signOut() {
    await devSignOut();
    // A full navigation, not router.push: the masthead resolves identity on
    // mount, and the shell needs to re-read it.
    router.replace('/chat-archives');
    router.refresh();
  }

  if (checking) return <p className="fine">Opening the copy desk…</p>;

  return (
    <div className="desk">
      {/* Mirrors the reader's "← All sessions": without it, the desk is a dead
          end — the masthead here has no actions slot by design, so the only way
          back to the paper would be the browser's back button. */}
      <Link href="/chat-archives" className="back">
        ← The edition
      </Link>

      <section className="desk-intro">
        {/* The masthead already names the place ("Copy desk"), so the kicker
            names the task instead of repeating it. */}
        <div className="kicker">New filing</div>
        <h1 className="hl">File a session</h1>
        <p className="standfirst">
          Paste a share link and it is set into the paper: the summary report as the article,
          the transcript as the interview.
        </p>
        <p className="fine desk-who">
          Signed in as <em>{email}</em>
          {DEV_AUTH ? (
            <>
              {' · '}
              {/* Local only. In production, signing out belongs to whatever
                  provider establishes the session, not to this page. */}
              <button type="button" className="linkish" onClick={signOut}>
                Sign out
              </button>
            </>
          ) : null}
        </p>
      </section>

      <FileSession />
    </div>
  );
}

/** The filing form itself: one field, one button, one honest status line. */
function FileSession() {
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; tone: 'ok' | 'warn' | 'error' } | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setNote(null);
    try {
      const result = await ingestSession(url);
      setUrl('');
      setNote({
        // A filed session with no report is not a failure — the transcript is
        // there and the story is in the edition — but it is not what was asked
        // for either, and staying quiet about it is how a missing report went
        // unnoticed until someone opened the story and asked why it was empty.
        tone: result.report_length > 0 ? 'ok' : 'warn',
        text:
          result.report_length > 0
            ? `“${result.title}” is in the edition — ${result.messages} messages, ${result.citations} citations.`
            : `“${result.title}” is in the edition, but no summary report was found in that share — it will read as a transcript only.`,
      });
      // Tells the front page (another tab included) to re-read the edition.
      window.dispatchEvent(new Event('archive:updated'));
    } catch (error) {
      if (isSignInRequired(error)) {
        // The session expired between opening the desk and filing. Access
        // answers a fetch() with a redirect that the browser follows as a GET,
        // dropping the POST body, so the import would otherwise fail silently.
        setNote({ text: 'Your session expired — sign in again to file.', tone: 'error' });
      } else {
        setNote({
          text: error instanceof Error ? error.message : 'Import failed',
          tone: 'error',
        });
      }
    } finally {
      setBusy(false);
    }
  }

  // Both states render the SAME element with the same class. They used to
  // differ — the hint took `.fine` (no top margin) and the result took
  // `.file-msg` — which both left the hint flush against the input's bottom
  // border and made the spacing jump the moment you filed something.
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
      <p
        className={note && note.tone !== 'ok' ? `file-msg ${note.tone}` : 'file-msg'}
        role="status"
      >
        {note ? (
          note.text
        ) : (
          <>
            In the ChatGPT share dialog, choose <em>Share link</em> and paste it here.
          </>
        )}
      </p>
    </form>
  );
}
