// The elements site-shell (akshayDev17/site-themes, shell/) gives meaning to:
// <site-theme-picker>, the shared theme picker, a custom element defined by
// site-shell's /_site/<build>/site.js; and <site-slot>, which site-shell's
// compose() fills as the page streams out (README, "What compose() adds").
import type { DetailedHTMLProps, HTMLAttributes } from 'react';

declare module 'react' {
  namespace JSX {
    interface IntrinsicElements {
      'site-theme-picker': DetailedHTMLProps<HTMLAttributes<HTMLElement>, HTMLElement> & {
        /** Keep the "Theme" label for screen readers only. */
        'hide-label'?: '';
        /** Visible label text (default "Theme"). */
        label?: string;
      };
      'site-slot': DetailedHTMLProps<HTMLAttributes<HTMLElement>, HTMLElement> & {
        /** Which slot: 'site-path' (the switcher) or 'site-apps'. */
        name: 'site-path' | 'site-apps';
        /** site-path only: this page's crumbs after the app name, as JSON. */
        'data-tail'?: string;
      };
    }
  }
}
