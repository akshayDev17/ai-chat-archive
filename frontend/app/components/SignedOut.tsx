import Link from 'next/link';

/**
 * Shown on the front page when the visitor has no identity.
 *
 * It has to explain the split honestly, because the model is unusual: stories
 * are public, shelves are not. "Sign in" alone would read as "this site is
 * private", which is wrong — someone who was sent a story link can still read
 * that story. So the copy says both halves.
 */
export default function SignedOut() {
  return (
    <section className="signedout">
      <div className="kicker">Reader access</div>
      <h1 className="hl">This edition is filed under one reader</h1>
      <p className="standfirst">
        Every story is shelved under the address that imported it, so your edition shows only your
        own imports. Individual stories stay public: anyone you send a story link to can read it
        without signing in.
      </p>
      <div className="signedout-actions">
        <Link href="/chat-archives?login" className="btn">
          Sign in
        </Link>
        <span className="fine">A one-time code is emailed — no password is ever stored.</span>
      </div>
    </section>
  );
}
