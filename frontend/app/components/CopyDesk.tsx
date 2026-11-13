'use client';

import { useCallback, useEffect, useState } from 'react';
import { ingestSession, isSignInRequired, listFilings, signOut, whoami } from '@/lib/api';
import { AFTER_SIGN_IN, AFTER_SIGN_OUT } from '@/lib/nav';
import { headline } from '@/lib/story';
import type { Filing, Visibility } from '@/types';
import Retract from './Retract';
import VisibilityControl from './VisibilityControl';

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
 * The form first, then what you have filed. The list is here because the desk
 * is the one place that knows which stories are yours: a private story is in no
 * edition, so without it there would be nowhere to find one again, and it is
 * where each story's own controls — public or private, delete — sit together.
 */
export default function CopyDesk() {
  const [email, setEmail] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let cancelled = false;
    whoami()
      .then((value) => {
        if (cancelled) return;
        if (!value) {
          window.location.replace(`/chat-archives/login?next=${encodeURIComponent(AFTER_SIGN_IN)}`);
          return;
        }
        setEmail(value);
        setChecking(false);
      })
      .catch(() => {
        if (!cancelled) {
          window.location.replace(`/chat-archives/login?next=${encodeURIComponent(AFTER_SIGN_IN)}`);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSignOut() {
    await signOut();
    // A full navigation, not router.push: the masthead resolves identity on
    // mount, and the shell has to re-read it now the cookie is gone.
    window.location.href = AFTER_SIGN_OUT;
  }

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
          the transcript as the interview.
        </p>
        <p className="fine desk-who">
          Signed in as <em>{email}</em>
          {' · '}
          <a href="/chat-archives/desk/media" className="linkish">
            Your images
          </a>
          {' · '}
          <button type="button" className="linkish" onClick={handleSignOut}>
            Sign out
          </button>
        </p>
      </section>

      <DeskWork />
    </div>
  );
}

/** What the form last did, shown in its status line. */
interface Note {
  /** The whole line — or, when it announces a story, what follows who can read it. */
  text: string;
  tone: 'ok' | 'warn' | 'error';
  /**
   * The story this note announces, so it can be deleted from here. The line
   * says who can read it from `visibility` as it is now, not as it was filed.
   */
  filed?: { id: string; title: string; visibility: Visibility };
  /** Images in it, queued for copying — linked to their progress. */
  images?: number;
}

function deletedNote(title: string): Note {
  return { text: `“${title}” was deleted — the transcript, report, sources and images are gone.`, tone: 'ok' };
}

/**
 * The form and the list under it, sharing what they both change: filing adds a
 * row, and deleting a story — from the form's status line or from its row —
 * removes the row and takes back the line that announced it.
 */
function DeskWork() {
  const [note, setNote] = useState<Note | null>(null);
  const [filings, setFilings] = useState<Filing[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const loadFilings = useCallback(() => {
    listFilings()
      .then((rows) => {
        setFilings(rows);
        setListError(null);
      })
      .catch((e) =>
        setListError(
          isSignInRequired(e)
            ? 'Your session expired — sign in again to see your filings.'
            : e instanceof Error
              ? e.message
              : 'Could not load your filings',
        ),
      );
  }, []);
  useEffect(loadFilings, [loadFilings]);

  // Called only after the server confirmed the delete (see `Retract`).
  function forget(id: string, title: string) {
    setFilings((rows) => rows?.filter((row) => row.id !== id) ?? rows);
    setNote((current) => (current?.filed?.id === id ? deletedNote(title) : current));
  }

  function changeVisibility(id: string, visibility: Visibility) {
    setFilings((rows) => rows?.map((row) => (row.id === id ? { ...row, visibility } : row)) ?? rows);
    setNote((current) =>
      current?.filed?.id === id ? { ...current, filed: { ...current.filed, visibility } } : current,
    );
  }

  return (
    <>
      <FileSession note={note} onNote={setNote} onFiled={loadFilings} onDeleted={forget} />
      <Filings
        filings={filings}
        error={listError}
        onDeleted={forget}
        onVisibility={changeVisibility}
      />
    </>
  );
}

/** The filing form itself: one field, one choice, one button, one honest status line. */
function FileSession({
  note,
  onNote,
  onFiled,
  onDeleted,
}: {
  note: Note | null;
  onNote: (note: Note | null) => void;
  onFiled: () => void;
  onDeleted: (id: string, title: string) => void;
}) {
  const [url, setUrl] = useState('');
  // Unset until the filer picks one, and sent only then: filing a link again
  // must not change who can read it unless they say so here.
  const [visibility, setVisibility] = useState<Visibility>();
  const [busy, setBusy] = useState(false);
  const filed = note?.filed;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    onNote(null);
    try {
      const result = await ingestSession(url, visibility);
      setUrl('');
      setVisibility(undefined);
      onNote({
        filed: { id: result.share_id, title: result.title, visibility: result.visibility },
        images: result.images,
        // A filed session with no report is not a failure — the transcript is
        // there and the story is filed — but it is not what was asked for
        // either, and staying quiet about it is how a missing report went
        // unnoticed until someone opened the story and asked why it was empty.
        tone: result.report_length > 0 ? 'ok' : 'warn',
        text:
          result.report_length > 0
            ? ` — ${result.messages} messages, ${result.citations} citations.`
            : ', but no summary report was found in that share — it will read as a transcript only.',
      });
      onFiled();
      // Tells the front page (another tab included) to re-read the edition.
      window.dispatchEvent(new Event('archive:updated'));
    } catch (error) {
      if (isSignInRequired(error)) {
        // The session expired between opening the desk and filing. Access
        // answers a fetch() with a redirect that the browser follows as a GET,
        // dropping the POST body, so the import would otherwise fail silently.
        onNote({ text: 'Your session expired — sign in again to file.', tone: 'error' });
      } else {
        onNote({
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
      <fieldset className="file-visibility">
        <legend>Who can read it</legend>
        {(
          [
            ['public', 'Public', 'in the edition, for everyone'],
            ['private', 'Private', 'only you, signed in'],
          ] as const
        ).map(([value, name, meaning]) => (
          <label key={value} className="file-choice">
            <input
              type="radio"
              name="visibility"
              value={value}
              checked={visibility === value}
              onChange={() => setVisibility(value)}
            />
            <span>
              <strong>{name}</strong> — {meaning}
            </span>
          </label>
        ))}
        <span className="file-unchosen">
          Neither chosen: a new story is public, and one filed again keeps what it had.
        </span>
      </fieldset>
      <p
        className={note && note.tone !== 'ok' ? `file-msg ${note.tone}` : 'file-msg'}
        role="status"
      >
        {note ? (
          <>
            {filed
              ? `“${filed.title}” ${
                  filed.visibility === 'private'
                    ? 'is filed as private — only you can read it'
                    : 'is in the edition'
                }`
              : null}
            {note.text}
            {note.images ? (
              <>
                {' '}
                <a
                  href={`/chat-archives/desk/media?id=${encodeURIComponent(note.filed?.id ?? '')}`}
                  className="linkish"
                >
                  {note.images} image{note.images === 1 ? '' : 's'} copying — watch progress
                </a>
              </>
            ) : null}
            {/* The wrong link, or a share made before the report was written:
                both are noticed here, the moment the filing lands, so the way
                to take it back sits right beside the news. */}
            {filed ? (
              <>
                {' '}
                <Retract
                  key={filed.id}
                  id={filed.id}
                  label="Delete it"
                  onRetracted={() => onDeleted(filed.id, filed.title)}
                />
              </>
            ) : null}
          </>
        ) : (
          <>
            In the ChatGPT share dialog, choose <em>Share link</em> and paste it here.
          </>
        )}
      </p>
    </form>
  );
}

/**
 * Your filings, newest first, each with its own controls.
 *
 * A row leaves the list only when `onDeleted` runs, which `Retract` calls after
 * the server has confirmed; if the delete fails the row stays, with the reason
 * shown beside its confirmation.
 */
function Filings({
  filings,
  error,
  onDeleted,
  onVisibility,
}: {
  filings: Filing[] | null;
  error: string | null;
  onDeleted: (id: string, title: string) => void;
  onVisibility: (id: string, visibility: Visibility) => void;
}) {
  return (
    <section className="filings" aria-labelledby="filings-heading">
      <h2 className="kicker" id="filings-heading">
        Your filings
      </h2>
      {error ? <div className="status error">{error}</div> : null}
      {!filings && !error ? <p className="fine">Reading your filings…</p> : null}
      {filings && !filings.length ? (
        <p className="fine">Nothing filed yet. A story you file appears here.</p>
      ) : null}
      {filings && filings.length ? (
        <ul className="filing-list">
          {filings.map((filing) => {
            const title = headline(filing);
            return (
              <li key={filing.id} className="filing-row">
                <a className="filing-title" href={`/chat-archives/${encodeURIComponent(filing.id)}`}>
                  {title}
                </a>
                <div className="filing-tools">
                  {filing.created_at ? (
                    <span className="filing-date">{filing.created_at.slice(0, 10)}</span>
                  ) : null}
                  <VisibilityControl
                    id={filing.id}
                    visibility={filing.visibility}
                    name={title}
                    onChanged={(visibility) => onVisibility(filing.id, visibility)}
                  />
                  <Retract
                    id={filing.id}
                    label="Delete"
                    name={`Delete “${title}”`}
                    onRetracted={() => onDeleted(filing.id, title)}
                  />
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}
