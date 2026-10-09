import FrontPage from '../components/FrontPage';
import Masthead from '../components/Masthead';

/**
 * The newspaper front page — one reader's shelf.
 *
 *   /chat-archives         → here
 *   /chat-archives/login   → the sign-in screen (its own path; see that file)
 *   /chat-archives/<uuid>  → one story, public by permalink
 *
 * The sign-in screen is deliberately NOT a branch of this component. When it
 * lived here behind `?login` it made this route's behaviour depend on a query
 * string, which meant its masthead, its width and its auth slot all had to be
 * conditioned in one place — and, more importantly, it could not be given its
 * own Cloudflare Access policy.
 */
export default function ChatArchives() {
  return (
    <main className="frame frame-wide">
      <Masthead actions />
      <FrontPage />
    </main>
  );
}
