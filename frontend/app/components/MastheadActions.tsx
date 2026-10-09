'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { whoami } from '@/lib/api';

/**
 * The masthead's top-right slot — the only place auth is surfaced on the
 * public pages.
 *
 *   signed out → Sign in
 *   signed in  → Copy desk
 *
 * Two things are deliberately absent.
 *
 * There is no import box: filing copy is a different job from reading the
 * paper, and it belongs on its own page. An input wedged into the nameplate
 * was both cramped and permanently visible to people who could not use it.
 *
 * And there is no address. Printing "akshay@akshayprabhakant.com" in the
 * nameplate of a *public* newspaper is the one place an email must never
 * appear — the whole design keeps addresses off the wire and out of URLs, and
 * stamping one into the masthead undoes that for anyone looking over a
 * shoulder, in a screenshot, or on a projector. The desk confirms who you are
 * signed in as, on the one page that requires signing in to see.
 *
 * Sign in is an <a> styled as a button, not a <button>: it navigates, so link
 * semantics (middle-click, open-in-new-tab, screen-reader "link") are the
 * correct ones.
 */
export default function MastheadActions() {
  const [signedIn, setSignedIn] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    whoami()
      .then((email) => {
        if (!cancelled) setSignedIn(Boolean(email));
      })
      .catch(() => {
        // The backend being down is not a reason to hide the sign-in link.
        if (!cancelled) setSignedIn(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Reserve the slot while resolving so the masthead does not reflow.
  if (signedIn === null) return <div className="mh-actions" aria-hidden="true" />;

  return (
    <div className="mh-actions">
      <Link href={signedIn ? '/chat-archives/desk' : '/chat-archives/login'} className="signin">
        {signedIn ? 'Copy desk' : 'Sign in'} <span aria-hidden="true">→</span>
      </Link>
    </div>
  );
}
