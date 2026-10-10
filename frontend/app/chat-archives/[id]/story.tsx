'use client';

import { usePathname, useSearchParams } from 'next/navigation';
import Masthead from '../../components/Masthead';
import SessionReader from '../../components/SessionReader';

/**
 * Client half of the story route.
 *
 * Static export has no server to read `params` at request time, so the id is
 * read from the URL here, after hydration. It comes from `usePathname()`, not
 * `useParams()`: a statically-exported dynamic route keeps the
 * `generateStaticParams` value in `useParams()` — i.e. the placeholder `_story`
 * — so `useParams()` would hand `SessionReader` the wrong id and it would fetch
 * `/api/sessions/_story`. `SessionReader` only uses the id inside its fetch
 * effect, so the server-rendered shell ("Opening the story…") is identical for
 * every id and there is no hydration mismatch.
 */
export default function Story() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const id = pathname?.split('/').filter(Boolean).pop() ?? '';
  const chat = searchParams?.get('chat');

  return (
    <>
      <Masthead section="The reading room" />
      <SessionReader id={id} initialMode={chat !== null ? 'chat' : 'report'} />
    </>
  );
}
