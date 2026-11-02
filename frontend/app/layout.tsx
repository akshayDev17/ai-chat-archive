import type { Metadata } from 'next';
import { Newsreader, IBM_Plex_Mono } from 'next/font/google';
import { bootScript } from 'site-themes';
// The package's tokens first; globals.css aliases the app's names onto them.
import 'site-themes/themes.css';
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
    // suppressHydrationWarning: the boot script sets data-theme on <html> before
    // React hydrates, so the server markup never carries it.
    <html lang="en" className={`${newsreader.variable} ${plexMono.variable}`} suppressHydrationWarning>
      <head>
        {/* Inline, never a src: it picks the stored theme before first paint
            with no request, so the first frame is already in the right theme. */}
        <script dangerouslySetInnerHTML={{ __html: bootScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
