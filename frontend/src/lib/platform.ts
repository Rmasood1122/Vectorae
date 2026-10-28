/**
 * platform.ts - Vectorae
 *
 * Single source of truth for platform detection.
 * All conditional native/web code in the app imports from here - never
 * check `Capacitor.isNativePlatform()` directly outside this module.
 *
 * typeof window guard: Next.js static export still runs code server-side
 * during `next build`. All functions return safe defaults (web) in that
 * context so Capacitor's native bridge is never touched in a Node
 * environment.
 *
 * Uses a static ESM import (not require()) - @capacitor/core's `Capacitor`
 * export is safe to import at module scope even during SSR/build (it only
 * touches window inside its methods, not at import time). A previous
 * version used a lazy require() here, reasoning it would "avoid Node-side
 * execution during static build" - but require() bypasses Vitest's
 * vi.mock() interception (which only intercepts ESM import statements),
 * so every test that mocked @capacitor/core silently got the real,
 * unmocked module instead. That was the root cause of all 9 failures in
 * storage.test.ts. The typeof window guards below do the actual SSR-safety
 * job require() was (incorrectly) being used for.
 */

import { Capacitor } from '@capacitor/core';

/** True when running inside a Capacitor native shell (Android / iOS). */
export function isNative(): boolean {
  if (typeof window === 'undefined') return false;
  return Capacitor.isNativePlatform();
}

/** True when running in an Android Capacitor shell. */
export function isAndroid(): boolean {
  if (typeof window === 'undefined') return false;
  return Capacitor.getPlatform() === 'android';
}

/** True when running in an iOS Capacitor shell. */
export function isIOS(): boolean {
  if (typeof window === 'undefined') return false;
  return Capacitor.getPlatform() === 'ios';
}

/** True when running in a browser (web app, not Capacitor shell). */
export function isWeb(): boolean {
  return !isNative();
}

/** True when code is running server-side (Next.js build / SSR context). */
export function isServer(): boolean {
  return typeof window === 'undefined';
}