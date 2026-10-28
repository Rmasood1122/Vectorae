'use client';

/**
 * useStatusBar — Vectorae
 *
 * Syncs the Android status bar color with the active theme's primary color.
 * Reads the CSS variable --color-primary from the document root, so it
 * automatically follows theme switches made via the M5 ThemeProvider.
 *
 * Called ONCE inside NativeProvider — re-runs whenever the CSS variable changes
 * (driven by a MutationObserver on the <html> style attribute).
 *
 * TODO: verify @capacitor/status-bar API (Style enum, setStyle, setBackgroundColor)
 *       against current Capacitor docs.
 */

import { useEffect } from 'react';
import { isAndroid } from '@/lib/platform';

function getPrimaryColor(): string {
  if (typeof document === 'undefined') return '#0f172a';
  return (
    getComputedStyle(document.documentElement)
      .getPropertyValue('--color-primary')
      .trim() || '#0f172a'
  );
}

/** Determines whether a hex color is dark (needs a light status bar style). */
function isDark(hex: string): boolean {
  const clean = hex.replace('#', '');
  const r = parseInt(clean.substring(0, 2), 16);
  const g = parseInt(clean.substring(2, 4), 16);
  const b = parseInt(clean.substring(4, 6), 16);
  // Perceived luminance (ITU-R BT.709)
  const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return luminance < 128;
}

async function applyStatusBar(color: string) {
  // TODO: verify @capacitor/status-bar import and API shape
  const { StatusBar, Style } = await import('@capacitor/status-bar');
  await StatusBar.setBackgroundColor({ color });
  await StatusBar.setStyle({ style: isDark(color) ? Style.Dark : Style.Light });
}

export function useStatusBar() {
  useEffect(() => {
    if (!isAndroid()) return;

    // Apply immediately on mount
    applyStatusBar(getPrimaryColor());

    // Watch for theme CSS variable changes (ThemeProvider updates --color-primary
    // on the <html> element's style attribute)
    const observer = new MutationObserver(() => {
      applyStatusBar(getPrimaryColor());
    });

    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['style', 'class'],
    });

    return () => observer.disconnect();
  }, []);
}
