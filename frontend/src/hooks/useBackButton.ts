'use client';

/**
 * useBackButton — Vectorae
 *
 * Handles Android hardware/gesture back button via @capacitor/app.
 * Required Android UX behaviour — Play Store reviewers check this.
 *
 * Rules:
 *   • At the root of any section (/, /strategies, /campaigns, /leads,
 *     /analytics, /settings) → show "Exit Vectorae?" confirm dialog.
 *   • Inside a section (e.g. /strategies/123) → router.back().
 *   • No-op on web (browser manages its own history).
 *
 * Called ONCE inside NativeProvider.
 * TODO: verify @capacitor/app back button API against current Capacitor docs.
 */

import { useEffect } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { isNative } from '@/lib/platform';

/** Routes that are "roots" — back from here should confirm exit. */
const ROOT_ROUTES = new Set(['/', '/strategies', '/campaigns', '/leads', '/analytics', '/settings']);

function isRootRoute(pathname: string): boolean {
  // Strip trailing slash for comparison
  const clean = pathname.endsWith('/') && pathname !== '/' ? pathname.slice(0, -1) : pathname;
  return ROOT_ROUTES.has(clean);
}

export function useBackButton() {
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!isNative()) return;

    let listener: { remove(): void } | null = null;

    async function init() {
      // TODO: verify @capacitor/app import and backButton event API
      const { App } = await import('@capacitor/app');

      listener = await App.addListener('backButton', ({ canGoBack }) => {
        if (isRootRoute(pathname)) {
          // At root — show exit confirm
          const confirmed = window.confirm('Exit Vectorae?');
          if (confirmed) {
            App.exitApp();
          }
        } else if (canGoBack) {
          // Inside a section — navigate up in browser history
          router.back();
        } else {
          // History empty but not a recognised root — go to dashboard
          router.replace('/');
        }
      });
    }

    init();

    return () => {
      listener?.remove();
    };
  }, [pathname, router]);
}
