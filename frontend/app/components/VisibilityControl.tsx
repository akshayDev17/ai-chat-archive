'use client';

import { Globe, Lock } from 'lucide-react';
import { useState } from 'react';
import { isSignInRequired, setVisibility } from '@/lib/api';
import type { Visibility } from '@/types';

/** The label a story carries for its filer: a padlock and "Private", or "Public". */
export function VisibilityLabel({ visibility }: { visibility: Visibility }) {
  const Icon = visibility === 'private' ? Lock : Globe;
  return (
    <span className={`visibility-label visibility-${visibility}`}>
      <Icon className="tool-icon" aria-hidden="true" />
      {visibility === 'private' ? 'Private' : 'Public'}
    </span>
  );
}

/**
 * Whether one of your own stories is public or private, and the switch.
 *
 * One click, unlike delete: either way round it can be switched straight back.
 * `onChanged` runs only once the server has confirmed; a failure leaves the
 * label as it was, with the reason shown.
 *
 * Rendered only for the story's filer — the server enforces the same rule.
 */
export default function VisibilityControl({
  id,
  visibility,
  name,
  onChanged,
}: {
  id: string;
  visibility: Visibility;
  /** The story's title, for the switch's accessible name in a list of stories. */
  name?: string;
  onChanged: (visibility: Visibility) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const next: Visibility = visibility === 'private' ? 'public' : 'private';
  const action = next === 'private' ? 'Make private' : 'Make public';

  async function change() {
    setBusy(true);
    setError(null);
    try {
      await setVisibility(id, next);
      // The edition gains or loses the story: tell the front page to re-read.
      window.dispatchEvent(new Event('archive:updated'));
      onChanged(next);
    } catch (e) {
      setError(
        isSignInRequired(e)
          ? 'Your session expired — sign in again to change this.'
          : e instanceof Error
            ? e.message
            : 'That did not work',
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="visibility-control">
      <VisibilityLabel visibility={visibility} />
      <button
        type="button"
        className="tool"
        disabled={busy}
        aria-label={name ? `${action}: ${name}` : undefined}
        onClick={change}
      >
        {busy ? 'Saving…' : action}
      </button>
      {error ? (
        <span className="retract-error" role="alert">
          {error}
        </span>
      ) : null}
    </span>
  );
}
