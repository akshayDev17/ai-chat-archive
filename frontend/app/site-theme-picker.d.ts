// <site-theme-picker>: the shared theme picker, a custom element defined by
// site-shell's /_site/<build>/site.js (akshayDev17/site-themes, shell/).
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
    }
  }
}
