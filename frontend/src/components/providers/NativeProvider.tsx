'use client';

/**
 * NativeProvider — Vectorae
 *
 * Single component that boots all native-only side-effects.
 * Imported ONCE in app/layout.tsx — nowhere else.
 *
 * On WEB: every hook inside is a no-op (isNative() returns false, early returns
 * in each hook ensure nothing Capacitor-specific executes).
 *
 * Boot sequence (all in useEffect — safe for static export):
 *   1. SplashScreen.hide()         — removes the launch screen after React mounts
 *   2. useBackButton()             — Android hardware back button
 *   3. useStatusBar()              — status bar color tracks active theme
 *   4. useNetwork()                — populates NetworkContext
 *   5. usePushNotifications()      — FCM token registration (Chunk 3)
 *   6. Deep link listener          — @capacitor/app 'appUrlOpen' → router
 */

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { isNative } from '@/lib/platform';
import { useBackButton } from '@/hooks/useBackButton';
import { useStatusBar } from '@/hooks/useStatusBar';
import { useNetwork } from '@/hooks/useNetwork';
import { usePushNotifications } from '@/hooks/usePushNotifications';
import { parseDeepLink, routeToPath } from '@/lib/deeplinks';

function useDeepLinks() {
  const router = useRouter();

  useEffect(() => {
    if (!isNative()) return;

    let appListener: { remove(): void } | null = null;

    async function init() {
      // TODO: verify @capacitor/app 'appUrlOpen' event API
      const { App } = await import('@capacitor/app');

      // App opened via a deep link while already running
      appListener = await App.addListener('appUrlOpen', (event) => {
        const route = parseDeepLink(event.url);
        if (route) {
          router.push(routeToPath(route));
        }
      });

      // App opened from killed state via a deep link
      // getLaunchUrl() returns the URL that launched the app (if any)
      const launchUrl = await App.getLaunchUrl();
      if (launchUrl?.url) {
        const route = parseDeepLink(launchUrl.url);
        if (route) {
          // Small delay: let the initial render complete before navigating
          setTimeout(() => router.replace(routeToPath(route)), 300);
        }
      }
    }

    // Also handle deep links dispatched by usePushNotifications (notification tap)
    function handlePushDeepLink(e: Event) {
      const url = (e as CustomEvent<{ url: string }>).detail?.url;
      if (!url) return;
      const route = parseDeepLink(url);
      if (route) router.push(routeToPath(route));
    }

    window.addEventListener('ch:deeplink', handlePushDeepLink);
    init();

    return () => {
      appListener?.remove();
      window.removeEventListener('ch:deeplink', handlePushDeepLink);
    };
  }, [router]);
}

function useSplashHide() {
  useEffect(() => {
    if (!isNative()) return;

    async function hideSplash() {
      try {
        // TODO: verify @capacitor/splash-screen API
        const { SplashScreen } = await import('@capacitor/splash-screen');
        // Short delay so the first paint is complete before hiding —
        // prevents a white flash between splash and app content.
        await new Promise((r) => setTimeout(r, 200));
        await SplashScreen.hide({ fadeOutDuration: 300 });
      } catch (err) {
        console.warn('[SplashScreen] Could not hide splash:', err);
      }
    }

    hideSplash();
  }, []);
}

export function NativeProvider() {
  useSplashHide();
  useBackButton();
  useStatusBar();
  useNetwork();        // populates NetworkContext (read by OfflineBanner)
  usePushNotifications();
  useDeepLinks();

  // NativeProvider renders nothing — it is a side-effect-only component
  return null;
}
