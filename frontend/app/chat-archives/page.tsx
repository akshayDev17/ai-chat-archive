import AuthFlow from '../components/AuthFlow';
import FrontPage from '../components/FrontPage';
import Masthead from '../components/Masthead';

/**
 * The archive lives here, not at `/`.
 *
 *   /chat-archives         → the newspaper front page
 *   /chat-archives?login   → the sign-in flow
 *
 * `login` is read as a query parameter (not a path segment), so nothing is
 * reserved under /chat-archives/ and a plain <share-id> segment can never
 * collide with it.
 */
export default async function ChatArchives({
  searchParams,
}: {
  searchParams: Promise<{ login?: string }>;
}) {
  const { login } = await searchParams;
  const isLogin = login !== undefined;

  return (
    <main className={isLogin ? 'frame' : 'frame frame-wide'}>
      <Masthead actions={!isLogin} section={isLogin ? 'Reader access' : undefined} />
      {isLogin ? <AuthFlow /> : <FrontPage />}
    </main>
  );
}
