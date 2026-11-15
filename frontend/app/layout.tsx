import type { Metadata } from 'next';
import { Newsreader, IBM_Plex_Mono } from 'next/font/google';
// The shared theme (boot script, tokens, picker) is not bundled: site-shell
// adds it to every page's <head> at the edge (worker.js). globals.css aliases
// the app's names onto the shared tokens and carries the Classic Light fallback.
import './globals.css';

const newsreader = Newsreader({
  subsets: ['latin'],
  weight: ['400', '600', '700'],
  style: ['normal', 'italic'],
  variable: '--font-newsreader',
});

const plexMono = IBM_Plex_Mono({
  subsets: ['latin'],
  weight: ['400', '500'],
  variable: '--font-plex-mono',
});

// The tab icon: a folded newspaper in the site's red, switching to the dark
// theme's red in a dark browser. Inline as a data URI so it needs no route of
// its own: this app's paths are /chat-archives/* plus the site-wide /api/*,
// /_next/* and /vendors/*, and a file there would be one more route.
const TAB_ICON =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">' +
  '<style>.g{stroke:#B00020}@media (prefers-color-scheme:dark){.g{stroke:#FF8A95}}</style>' +
  '<g class="g" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M10 6h18v18a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V12h6"/>' +
  '<path d="M10 6v18M15.5 12.5h7M15.5 19.5h7"/>' +
  '</g></svg>';

export const metadata: Metadata = {
  title: 'The AI Digest',
  description: 'Your AI chat sessions and their reports, as a newspaper.',
  icons: { icon: `data:image/svg+xml,${encodeURIComponent(TAB_ICON)}` },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // suppressHydrationWarning: site-shell's boot script sets data-theme on
    // <html> before React hydrates, so the exported markup never carries it.
    <html lang="en" className={`${newsreader.variable} ${plexMono.variable}`} suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}
