import MastheadActions from './MastheadActions';

/**
 * One of this page's own crumbs after the app's name in the site path; the
 * last one is this page. `href` must be a path on this site.
 */
export interface Crumb {
  label: string;
  href?: string;
}

const APP_ROOT = { label: 'The edition', href: '/chat-archives' };

const escapeHtml = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);

/**
 * What the site path slot shows when site-shell does not fill it (site-shell
 * down, or `next dev`): a back link to this page's parent, which is the
 * previous crumb with a link, or the edition. The front page has no parent.
 */
export function sitePathFallback(tail: readonly Crumb[]): string {
  if (!tail.length) return '';
  const parent = tail.slice(0, -1).filter((c) => c.href).pop() ?? APP_ROOT;
  return `<a class="back" href="${escapeHtml(parent.href!)}">← ${escapeHtml(parent.label)}</a>`;
}

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
 * The paper's nameplate. Above it, the site path: `<site-slot name="site-path">`,
 * which site-shell fills with the row of every app on the site and, on its
 * own line under it, a trail ending in `tail`, this page's own crumbs (akshayDev17/site-themes README,
 * "What compose() adds"). Its content is rendered once, as HTML that React
 * never compares or replaces, so hydration keeps what site-shell put there.
 * Where site-shell does not run, the slot keeps the back link from
 * `sitePathFallback`.
 *
 * site-shell fills the slot in the HTML the Worker serves, so a page has it
 * only when it is loaded as a document: links between the archive's pages are
 * plain `<a>` and navigations are `window.location`, never a Next.js
 * client-side navigation, which would render the next page without it.
 *
 * The top-right corner always holds the theme picker, <site-theme-picker>,
 * which site-shell's site.js defines (it stays an empty element where
 * site-shell is absent); `actions` adds the auth-aware links beside it on the
 * front page, and nothing on the sign-in screen — there is no point offering
 * "Sign in" to someone already on the sign-in page.
 */
export default function Masthead({
  section,
  tail = [],
  actions = false,
}: {
  section?: string;
  tail?: readonly Crumb[];
  actions?: boolean;
}) {
  return (
    <header className="masthead">
      <site-slot
        name="site-path"
        data-tail={tail.length ? JSON.stringify(tail) : undefined}
        dangerouslySetInnerHTML={{ __html: sitePathFallback(tail) }}
        suppressHydrationWarning
      />
      <div className="row">
        <div className="mh-left">
          <span className="name">The AI Digest</span>
          <span className="edition">{section ?? edition()}</span>
        </div>
        <div className="mh-actions">
          {actions ? <MastheadActions /> : null}
          <site-theme-picker hide-label="" className="mh-theme" />
        </div>
      </div>
      <div className="tagline">An AI conversation newspaper · the sessions, as stories</div>
    </header>
  );
}
