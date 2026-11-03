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

export const metadata: Metadata = {
  title: 'The AI Digest',
  description: 'Your AI chat sessions and their reports, as a newspaper.',
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
