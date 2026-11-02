import { ThemePicker } from 'site-themes/react';
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
 * The paper's nameplate. The top-right corner always holds the theme picker;
 * `actions` adds the auth-aware links beside it on the front page, and nothing
 * on the sign-in screen — there is no point offering "Sign in" to someone
 * already on the sign-in page.
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
        <div className="mh-actions">
          {actions ? <MastheadActions /> : null}
          <ThemePicker hideLabel className="mh-theme" />
        </div>
      </div>
      <div className="tagline">An AI conversation newspaper · the sessions, as stories</div>
    </header>
  );
}
