import type { Metadata, Viewport } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Walnut Internal Brain',
  description: "Answers only from what you're allowed to see — every access audited",
};

export const viewport: Viewport = { width: 'device-width', initialScale: 1 };

// Apply the stored theme before first paint (no flash). Dark is the default.
const themeInit = `try{var t=localStorage.getItem('ib-theme');if(t==='light'||t==='dark'){document.documentElement.setAttribute('data-theme',t)}}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
      </head>
      <body className="min-h-screen bg-bg font-sans text-fg antialiased">
        <a href="#main" className="skip-link">
          Skip to main content
        </a>
        {children}
      </body>
    </html>
  );
}
