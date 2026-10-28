/**
 * app/layout.tsx — Vectorae root layout
 *
 * M7 additions (marked with ── M7 ──):
 *   • NetworkProvider wraps everything (enables OfflineBanner context)
 *   • NativeProvider boots all Capacitor side-effects (no-op on web)
 *   • OfflineBanner renders persistently over all content when offline
 *   • body gets safe-area padding via CSS (globals.css)
 *   • Viewport meta updated: viewport-fit=cover for status bar overlap handling
 */

import type { Metadata, Viewport } from 'next';
import { Inter } from 'next/font/google';
import { Toaster } from 'sonner';
import './globals.css';

// M5 providers
import { ThemeProvider } from '@/components/providers/ThemeProvider';
import { QueryProvider }  from '@/components/providers/QueryProvider';

// ── M7 additions ──────────────────────────────────────────────────────────
import { NetworkProvider } from '@/contexts/NetworkContext';
import { NativeProvider }  from '@/components/providers/NativeProvider';
import { OfflineBanner }   from '@/components/ui/OfflineBanner';

// ── Vercel Speed Insights ──────────────────────────────────────────────────
import { SpeedInsights } from '@vercel/speed-insights/next';

const inter = Inter({ subsets: ['latin'], display: 'swap' });

export const metadata: Metadata = {
  title: 'Vectorae',
  description: 'AI-powered end-to-end client acquisition',
  // manifest for PWA (also used by Capacitor as the web app manifest)
  manifest: '/manifest.json',
};

// ── M7: viewport-fit=cover ────────────────────────────────────────────────
// Required for safe-area-inset-* CSS env() values to work on Android
// (handles the status bar and navigation bar overlap areas).
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 1,          // prevent zoom on input focus (mobile UX)
  userScalable: false,
  viewportFit: 'cover',     // ← M7 addition for safe area support
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#ffffff' },
    { media: '(prefers-color-scheme: dark)',  color: '#0f172a' },
  ],
};

/**
 * No-flash theme script: reads the saved theme from sessionStorage / Preferences
 * before the first paint and applies CSS variables to <html>.
 * This runs synchronously before hydration — no theme flash on load.
 *
 * Works correctly in the Capacitor WebView (identical to browser JS execution).
 */
const noFlashScript = `
(function(){
  try {
    var t = sessionStorage.getItem('ch_theme');
    if (!t) return;
    var theme = JSON.parse(t);
    var root = document.documentElement;
    Object.entries(theme.cssVars || {}).forEach(function([k,v]){ root.style.setProperty(k,v); });
    if (theme.darkMode) root.classList.add('dark');
  } catch(e) {}
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* No-flash theme script must be the very first script in <head> */}
        {/* eslint-disable-next-line @next/next/no-sync-scripts */}
        <script dangerouslySetInnerHTML={{ __html: noFlashScript }} />
      </head>
      <body className={inter.className}>
        {/*
          Provider nesting order:
            QueryProvider    → React Query cache (outermost, all queries need it)
              ThemeProvider  → CSS variable theming (must wrap all UI)
                NetworkProvider → online/backendReachable state (M7)
                  NativeProvider  → Capacitor side-effects, no-op on web (M7)
                  OfflineBanner   → reads NetworkContext, fixed position (M7)
                  {children}      → page content
        */}
        <QueryProvider>
          <ThemeProvider>
            <NetworkProvider>
              <NativeProvider />
              <OfflineBanner />
              {/* sonner's toast() is called from a dozen pages but nothing
                  rendered a Toaster, so every toast was silently dropped. */}
              <Toaster position="top-center" richColors closeButton />
              {children}
              <SpeedInsights />
            </NetworkProvider>
          </ThemeProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
