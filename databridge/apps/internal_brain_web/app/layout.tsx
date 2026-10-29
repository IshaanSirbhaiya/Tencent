import '@fontsource-variable/inter';
import type { Metadata, Viewport } from 'next';
import { Providers } from '@/components/shell/Providers';
import { SkipLink } from '@/components/shell/SkipLink';
import { PresenterRoot } from '@/components/presenter/PresenterRoot';
import { TourOverlay } from '@/components/tour/Tour';
import { PREFS_INIT_SCRIPT } from '@/lib/prefs';
import './globals.css';

export const metadata: Metadata = {
  title: 'Walnut DataBridge',
  description: "Answers only from what you're allowed to see — every access audited",
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: [
    { media: '(prefers-color-scheme: dark)', color: '#0a0b0d' },
    { media: '(prefers-color-scheme: light)', color: '#fafafb' },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark" data-mode="user" data-text="100" suppressHydrationWarning>
      <head>
        {/* Theme, text size, contrast, motion, language and mode before first paint (no flash). */}
        <script dangerouslySetInnerHTML={{ __html: PREFS_INIT_SCRIPT }} />
      </head>
      <body className="min-h-screen bg-bg font-sans text-body text-fg antialiased">
        <Providers>
          {/* First Tab stop; inside Providers so it follows the chosen language. */}
          <SkipLink />
          {children}
          <TourOverlay />
          <PresenterRoot />
        </Providers>
      </body>
    </html>
  );
}
