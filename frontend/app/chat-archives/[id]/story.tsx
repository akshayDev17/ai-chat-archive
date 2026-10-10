'use client';

import { useState } from 'react';
import { usePathname } from 'next/navigation';
import Masthead from '../../components/Masthead';
import SessionReader, { type ReaderMode } from '../../components/SessionReader';

/**
 * Client half of the story route.
 *
 * The id comes from `usePathname()`, not `useParams()`: a statically-exported
 * dynamic route keeps the `generateStaticParams` value in `useParams()` (the
 * placeholder `_story`), so `useParams()` would hand `SessionReader` the wrong
 * id and it would fetch `/api/sessions/_story`.
 *
 * The mode is read from the URL **once**, in a lazy state initialiser, and
 * deliberately NOT through `useSearchParams()`. That hook lives under a
 * Suspense boundary, so a search-param change (which is what toggling
 * Report/Chat does) re-rendered — and re-mounted — this subtree, re-fetching
 * the whole session on every toggle. Reading `window.location.search` once
 * keeps the toggle a pure state change: one `GET /api/sessions/<id>` per story,
 * covering both views, and `?chat` links still work on load.
 *
 * The server prerender has no `window`, so it initialises to `report`. That is
 * not a hydration mismatch in practice: until the session arrives the reader
 * renders "Opening the story…", which does not depend on the mode.
 */
export default function Story() {
  const pathname = usePathname();
  const id = pathname?.split('/').filter(Boolean).pop() ?? '';
  const [initialMode] = useState<ReaderMode>(() =>
    typeof window === 'undefined'
      ? 'report'
      : new URLSearchParams(window.location.search).has('chat')
        ? 'chat'
        : 'report',
  );

  return (
    <>
      <Masthead section="The reading room" />
      <SessionReader id={id} initialMode={initialMode} />
    </>
  );
}
