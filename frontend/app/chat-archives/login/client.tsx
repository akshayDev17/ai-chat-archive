'use client';

import { useSearchParams } from 'next/navigation';
import AuthFlow from '../../components/AuthFlow';

/**
 * Client half of the sign-in screen. `next` is read from the URL after
 * hydration because static export has no server to read `searchParams`.
 */
export default function LoginClient() {
  const searchParams = useSearchParams();
  const next = searchParams?.get('next');

  return <AuthFlow next={next} />;
}
