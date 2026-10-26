'use client';

import { useState } from 'react';
import { isSignInRequired, retractSession } from '@/lib/api';

/**
 * Take one of your own stories back out of the edition.
 *
 * Two clicks, not one: a retraction deletes the transcript, report and sources,
 * and there is no undo short of filing the link again. The confirmation is
 * inline rather than a `window.confirm()` so it reads as part of the page.
 *
 * Rendered only for the story's filer — the server enforces the same rule, so
 * a stray click from anyone else would 404, not delete.
 */
export default function Retract({
  id,
  label = 'Retract this story',
  onRetracted,
}: {
  id: string;
  label?: string;
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
          ? 'Your session expired — sign in again to retract.'
          : e instanceof Error
            ? e.message
            : 'Retraction failed',
      );
      setStage('confirm');
    }
  }

  if (stage === 'idle') {
    return (
      <button type="button" className="retract" onClick={() => setStage('confirm')}>
        {label}
      </button>
    );
  }

  const busy = stage === 'busy';
  return (
    <span className="retract-confirm" role="group" aria-label="Confirm retraction">
      <span>Retract for good?</span>
      <button type="button" className="retract danger" disabled={busy} onClick={retract}>
        {busy ? 'Retracting…' : 'Yes, retract'}
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
