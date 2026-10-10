'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import AuthFlow from '../../components/AuthFlow';
import Masthead from '../../components/Masthead';

/**
 * Client half of the sign-in screen. `next` is read from the URL after
 * hydration because static export has no server to read `searchParams`.
 */
export default function LoginClient() {
  const searchParams = useSearchParams();
  const next = searchParams?.get('next');

  return (
    <>
      <Masthead section="Reader access" />
      <Link href="/chat-archives" className="back">
        ← The edition
      </Link>
      <AuthFlow next={next} />
    </>
  );
}
