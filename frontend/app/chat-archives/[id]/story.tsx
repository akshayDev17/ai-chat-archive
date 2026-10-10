'use client';

import { useParams, useSearchParams } from 'next/navigation';
import Masthead from '../../components/Masthead';
import SessionReader from '../../components/SessionReader';

/**
 * Client half of the story route.
 *
 * Static export has no server to read `params` at request time, so the id is
 * resolved from the URL here, after hydration. `SessionReader` only *uses* the
 * id inside its fetch effect, so the server-rendered shell ("Opening the
 * story…") is identical for every id — no hydration mismatch when the serving
 * layer rewrites a real `/chat-archives/<uuid>` onto the one placeholder page
 * that `page.tsx` generated.
 */
export default function Story() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const id = params?.id ?? '';
  const chat = searchParams?.get('chat');

  return (
    <>
      <Masthead section="The reading room" />
      <SessionReader id={id} initialMode={chat !== null ? 'chat' : 'report'} />
    </>
  );
}
