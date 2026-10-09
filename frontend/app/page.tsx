import Link from 'next/link';
import Masthead from './components/Masthead';

/**
 * `/` is deliberately not the archive — it belongs to the personal site.
 * The archive lives at /chat-archives.
 */
export default function Home() {
  return (
    <main className="frame">
      <Masthead />
      <div className="kicker">Elsewhere on this site</div>
      <h1 className="hl">The archive has moved</h1>
      <p className="standfirst">
        The AI Digest lives at <Link href="/chat-archives">/chat-archives</Link>.
        This root path belongs to the personal site.
      </p>
    </main>
  );
}
