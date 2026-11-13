'use client';

import { Trash2 } from 'lucide-react';
import { useState } from 'react';
import { isSignInRequired, retractSession } from '@/lib/api';

/**
 * Delete one of your own stories.
 *
 * Two clicks, not one: deleting removes the transcript, report, sources and
 * images, and there is no undo short of filing the link again. The
 * confirmation is inline rather than a `window.confirm()` so it reads as part
 * of the page, and it says so in as many words.
 *
 * The bin beside the word is decoration — the label is the name — so it is
 * hidden from assistive technology. `name` is for where the label alone does
 * not say *what* is deleted (a row in a list of stories).
 *
 * `onRetracted` runs only once the server has confirmed. A failure leaves the
 * control on its confirmation with the reason shown, so whatever the caller
 * renders around it — a row, the story itself — stays put.
 *
 * Rendered only for the story's filer — the server enforces the same rule, so
 * a stray click from anyone else would 404, not delete.
 */
export default function Retract({
  id,
  label = 'Delete this story',
  name,
  onRetracted,
}: {
  id: string;
  label?: string;
  /** The accessible name, when it should say more than the visible label. */
  name?: string;
  onRetracted: () => void;
}) {
  const [stage, setStage] = useState<'idle' | 'confirm' | 'busy'>('idle');
  const [error, setError] = useState<string | null>(null);

  async function retract() {
    setStage('busy');
    setError(null);
    try {
      await retractSession(id);
      // Same signal filing sends: the front page (another tab included)
      // re-reads the edition.
      window.dispatchEvent(new Event('archive:updated'));
      onRetracted();
    } catch (e) {
      setError(
        isSignInRequired(e)
          ? 'Your session expired — sign in again to delete.'
          : e instanceof Error
            ? e.message
            : 'Delete failed',
      );
      setStage('confirm');
    }
  }

  if (stage === 'idle') {
    return (
      <button
        type="button"
        className="retract"
        aria-label={name}
        onClick={() => setStage('confirm')}
      >
        <Trash2 className="tool-icon" aria-hidden="true" />
        {label}
      </button>
    );
  }

  const busy = stage === 'busy';
  return (
    <span className="retract-confirm" role="group" aria-label={name ? `Confirm: ${name}` : 'Confirm deletion'}>
      <span className="retract-ask">
        Delete for good? This deletes the transcript, report, sources and images, and cannot be
        undone.
      </span>
      <button type="button" className="retract danger" disabled={busy} onClick={retract}>
        <Trash2 className="tool-icon" aria-hidden="true" />
        {busy ? 'Deleting…' : 'Yes, delete'}
      </button>
      <button type="button" className="retract" disabled={busy} onClick={() => setStage('idle')}>
        Keep it
      </button>
      {error ? (
        <span className="retract-error" role="alert">
          {error}
        </span>
      ) : null}
    </span>
  );
}
