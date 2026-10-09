'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { whoami } from '@/lib/api';

/**
 * The masthead's top-right slot — the only place auth is surfaced on the
 * public pages.
 *
 *   signed out → Sign in
 *   signed in  → Copy desk, plus who you are
 *
 * There is deliberately no import box here any more. Filing copy is a
 * different job from reading the paper, and it belongs on its own page: an
 * input wedged into the nameplate was both cramped and permanently visible to
 * people who could not use it.
 *
 * Sign in is an <a> styled as a button, not a <button>: it navigates, so link
 * semantics (middle-click, open-in-new-tab, screen-reader "link") are the
 * correct ones.
 */
export default function MastheadActions() {
  const [email, setEmail] = useState<string | null>(null);
  const [resolved, setResolved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    whoami()
      .then((value) => {
        if (!cancelled) setEmail(value);
      })
      .catch(() => {
        // The backend being down is not a reason to hide the sign-in link.
        if (!cancelled) setEmail(null);
      })
      .finally(() => {
        if (!cancelled) setResolved(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Reserve the slot while resolving so the masthead does not reflow.
  if (!resolved) return <div className="mh-actions" aria-hidden="true" />;

  if (!email) {
    return (
      <div className="mh-actions">
        <Link href="/chat-archives/login" className="signin">
          Sign in <span aria-hidden="true">→</span>
        </Link>
      </div>
    );
  }

  return (
    <div className="mh-actions">
      <span className="reader" title={email}>
        {email}
      </span>
      <Link href="/chat-archives/desk" className="signin">
        Copy desk <span aria-hidden="true">→</span>
      </Link>
    </div>
  );
}
