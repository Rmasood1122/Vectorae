/**
 * storage.ts — Vectorae
 *
 * Platform-aware key-value storage abstraction.
 *
 * ┌─────────────────────────────────────────────────────────────────────┐
 * │  On MOBILE (Capacitor):                                             │
 * │  Uses @capacitor/preferences, which is backed by:                  │
 * │    • Android: SharedPreferences (encrypted at rest by the OS on    │
 * │      Android 6+ with hardware-backed key storage where available)  │
 * │    • iOS: NSUserDefaults (protected by iOS data protection)        │
 * │  Tokens survive app close but are NOT readable by other apps.      │
 * │                                                                     │
 * │  On WEB (browser):                                                  │
 * │  Uses sessionStorage — intentionally NON-persistent. Tokens        │
 * │  are cleared when the browser tab closes. Do NOT switch this to    │
 * │  localStorage — there is no OS-level protection for localStorage   │
 * │  on web and tokens would persist indefinitely in plaintext.        │
 * └─────────────────────────────────────────────────────────────────────┘
 *
 * Usage: import { storage } from '@/lib/storage' — the default export
 * automatically routes to the correct backend.
 */

import { isNative, isServer } from './platform';

export interface StorageAdapter {
  get(key: string): Promise<string | null>;
  set(key: string, value: string): Promise<void>;
  remove(key: string): Promise<void>;
  clear(): Promise<void>;
}

// ── Native adapter (@capacitor/preferences) ───────────────────────────────

const nativeAdapter: StorageAdapter = {
  async get(key: string): Promise<string | null> {
    const { Preferences } = await import('@capacitor/preferences');
    const { value } = await Preferences.get({ key });
    return value;
  },

  async set(key: string, value: string): Promise<void> {
    const { Preferences } = await import('@capacitor/preferences');
    await Preferences.set({ key, value });
  },

  async remove(key: string): Promise<void> {
    const { Preferences } = await import('@capacitor/preferences');
    await Preferences.remove({ key });
  },

  async clear(): Promise<void> {
    const { Preferences } = await import('@capacitor/preferences');
    await Preferences.clear();
  },
};

// ── Web adapter (sessionStorage) ──────────────────────────────────────────
// NOTE: sessionStorage is intentionally chosen over localStorage.
// Tokens do NOT persist across browser sessions on web — this is correct
// behaviour. Mobile users get persistence through Preferences (above).

const webAdapter: StorageAdapter = {
  async get(key: string): Promise<string | null> {
    if (isServer()) return null;
    return sessionStorage.getItem(key);
  },

  async set(key: string, value: string): Promise<void> {
    if (isServer()) return;
    sessionStorage.setItem(key, value);
  },

  async remove(key: string): Promise<void> {
    if (isServer()) return;
    sessionStorage.removeItem(key);
  },

  async clear(): Promise<void> {
    if (isServer()) return;
    sessionStorage.clear();
  },
};

// ── Null adapter (SSR / build context) ────────────────────────────────────

const nullAdapter: StorageAdapter = {
  async get(): Promise<null> { return null; },
  async set(): Promise<void> { /* no-op */ },
  async remove(): Promise<void> { /* no-op */ },
  async clear(): Promise<void> { /* no-op */ },
};

// ── Export ────────────────────────────────────────────────────────────────

function resolveAdapter(): StorageAdapter {
  if (isServer()) return nullAdapter;
  if (isNative()) return nativeAdapter;
  return webAdapter;
}

export const storage: StorageAdapter = {
  get: (key) => resolveAdapter().get(key),
  set: (key, value) => resolveAdapter().set(key, value),
  remove: (key) => resolveAdapter().remove(key),
  clear: () => resolveAdapter().clear(),
};
