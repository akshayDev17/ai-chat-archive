'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { whoami } from '@/lib/api';
import UploadInline from './UploadInline';

/**
 * The masthead's top-right slot, which is *state dependent*:
 *
 *   signed out → a single Sign in affordance
 *   signed in  → the import box, plus who you are
 *
 * The import box is hidden when signed out rather than shown-and-rejected,
 * because "paste a link" is a promise this page cannot keep without an
 * identity. An <a> styled as a button is used for Sign in — it navigates, so
 * link semantics (middle-click, open-in-new-tab, screen-reader "link") are the
 * correct ones; a <button> that navigates is a small lie to the browser.
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
        // A backend that is down is not a reason to hide the sign-in link;
        // treat it as signed out and let the page body report the real problem.
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
      <UploadInline />
      <span className="reader" title={email}>
        {email}
      </span>
    </div>
  );
}
