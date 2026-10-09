import MastheadActions from './MastheadActions';

function edition(): string {
  const now = new Date();
  const date = now.toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
    year: 'numeric',
  });
  return `Pune · ${date}`;
}

/**
 * The paper's nameplate. `actions` decides what sits in the top-right corner:
 * the auth-aware slot on the front page, nothing on the sign-in screen — there
 * is no point offering "Sign in" to someone already on the sign-in page.
 */
export default function Masthead({
  section,
  actions = false,
}: {
  section?: string;
  actions?: boolean;
}) {
  return (
    <header className="masthead">
      <div className="row">
        <div className="mh-left">
          <span className="name">The AI Digest</span>
          <span className="edition">{section ?? edition()}</span>
        </div>
        {actions ? <MastheadActions /> : null}
      </div>
      <div className="tagline">An AI conversation newspaper · the sessions, as stories</div>
    </header>
  );
}
