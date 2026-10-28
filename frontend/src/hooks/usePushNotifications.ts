'use client';

/**
 * usePushNotifications — Vectorae (M7 Chunk 3)
 *
 * FCM device token lifecycle management on Android.
 *
 * On app startup (after login):
 *   1. Request notification permission (Android 13+ requires explicit grant).
 *   2. If granted: get FCM token → POST /devices/register.
 *   3. Listen for token refresh → re-register automatically.
 *   4. Handle permission denied: set a flag so we can show a one-time
 *      in-app prompt after the first strategy completes.
 *
 * Background + killed-state notifications:
 *   FCM delivers these natively to Android — no JS needed for delivery.
 *   Tapping the notification opens the app; the deep link in the payload
 *   (set by the backend notification service) is handled by useDeepLinks
 *   in NativeProvider.
 *
 * Called ONCE inside NativeProvider after auth is confirmed.
 *
 * TODO: verify correct Capacitor FCM plugin:
 *   Option A — @capacitor/push-notifications (official, wraps FCM on Android)
 *   Option B — @capacitor-firebase/messaging (community, more Firebase features)
 *   This file uses @capacitor/push-notifications. If you switch to Option B,
 *   the import path and API shape will differ — check current docs.
 *
 * TODO: Android 13+ (API 33+) requires POST_NOTIFICATIONS permission at runtime.
 *       Verify that @capacitor/push-notifications handles this automatically
 *       or whether a manual permission request is needed.
 */

import { useEffect } from 'react';
import { isNative } from '@/lib/platform';
import { storage } from '@/lib/storage';
import { getAccessToken } from '@/lib/auth-session';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? '';
const PUSH_PERMISSION_DENIED_KEY = 'ch_push_permission_denied';
const PUSH_PROMPTED_KEY = 'ch_push_reshow_prompted';

async function registerToken(fcmToken: string): Promise<void> {
  const authToken = await getAccessToken();
  if (!authToken) return; // not logged in yet — will be called again after login

  try {
    await fetch(`${API_URL}/devices/register`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`,
      },
      body: JSON.stringify({ token: fcmToken, platform: 'android' }),
    });
  } catch (err) {
    // Non-fatal: push notifications are a convenience feature.
    // Log silently; the next token refresh will retry.
    console.warn('[PushNotifications] Token registration failed:', err);
  }
}

export function usePushNotifications() {
  useEffect(() => {
    if (!isNative()) return;

    // tokenRefreshListener: would hold the 'registrationError' / token-refresh
    // listener once wired. Typed explicitly; see TODO above.
    let tokenRefreshListener: { remove(): void } | null = null;
    void tokenRefreshListener;  // suppress TS2454 never-assigned warning
    // typed as any: @capacitor/push-notifications types require the package in tsc env
    let notificationListener: any = null;

    async function init() {
      // TODO: verify @capacitor/push-notifications import API
      const { PushNotifications } = await import('@capacitor/push-notifications');

      // ── 1. Request permission ───────────────────────────────────────────
      const permResult = await PushNotifications.requestPermissions();

      if (permResult.receive !== 'granted') {
        // Mark as denied so NativeProvider can show a one-time in-app prompt
        // after the first strategy completes (better timing than immediately re-asking)
        await storage.set(PUSH_PERMISSION_DENIED_KEY, 'true');
        console.info('[PushNotifications] Permission denied by user.');
        return;
      }

      // Clear denied flag if they previously denied but now granted
      await storage.remove(PUSH_PERMISSION_DENIED_KEY);

      // ── 2. Register with FCM ────────────────────────────────────────────
      await PushNotifications.register();

      // ── 3. Handle registration (get FCM token) ──────────────────────────
      // TODO: verify 'registration' event name and token field
      await PushNotifications.addListener('registration', async (token) => {
        console.info('[PushNotifications] Registered, token length:', token.value.length);
        await registerToken(token.value);
      });

      // ── 4. Handle token refresh (re-register with new token) ────────────
      // TODO: verify if @capacitor/push-notifications exposes a refresh event,
      // or if registration fires again automatically on token rotation.
      // If not, implement token polling via periodic re-register() calls.

      // ── 5. Foreground notification display ─────────────────────────────
      notificationListener = (await PushNotifications.addListener(
        'pushNotificationReceived',
        (notification) => {
          // App is in foreground: show a non-intrusive in-app toast
          // instead of relying on the system notification drawer.
          // The notification data.deepLink field (set by backend) is
          // available here for in-app navigation if the user taps the toast.
          console.info('[PushNotifications] Foreground notification:', notification.title);
          // TODO: wire to a ToastProvider to display foreground push in-app
        },
      )) as { remove(): void } | null;

      // ── 6. Handle notification tap (app opened via notification) ────────
      await PushNotifications.addListener(
        'pushNotificationActionPerformed',
        (action) => {
          // The deep link is embedded in action.notification.data.deepLink
          // by the backend notification service (see notifications.py).
          const deepLink = action.notification.data?.deepLink as string | undefined;
          if (deepLink && typeof window !== 'undefined') {
            // Dispatch a custom event that useDeepLinks (in NativeProvider) listens for
            window.dispatchEvent(new CustomEvent('ch:deeplink', { detail: { url: deepLink } }));
          }
        },
      );
    }

    init();

    return () => {
      /* eslint-disable */
      (tokenRefreshListener as any)?.remove();
      (notificationListener as any)?.remove();
      /* eslint-enable */
    };
  }, []);
}

/**
 * Exported for use by the Strategy complete page:
 * Show a one-time prompt to enable notifications if the user previously denied.
 */
export async function shouldShowPushRePrompt(): Promise<boolean> {
  const denied = await storage.get(PUSH_PERMISSION_DENIED_KEY);
  const alreadyShown = await storage.get(PUSH_PROMPTED_KEY);
  return denied === 'true' && alreadyShown !== 'true';
}

export async function markPushRePromptShown(): Promise<void> {
  await storage.set(PUSH_PROMPTED_KEY, 'true');
}
