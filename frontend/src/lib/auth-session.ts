/**
 * auth-session.ts — Vectorae
 *
 * JWT access + refresh token management.
 *
 * M7 MIGRATION NOTE:
 * M5 stored tokens via localStorage.setItem('access_token', ...) / localStorage.setItem('refresh_token', ...).
 * All such calls must be replaced with the functions below. Search for:
 *   localStorage.setItem('access_token'
 *   localStorage.setItem('refresh_token'
 *   localStorage.getItem('access_token'
 *   localStorage.getItem('refresh_token'
 *   localStorage.removeItem('access_token'
 *   localStorage.removeItem('refresh_token'
 * and replace with the corresponding function from this module.
 *
 * On mobile: tokens are stored in OS-level secure storage (Capacitor Preferences).
 * On web: only the short-lived ACCESS token is stored, in sessionStorage. The
 * refresh token never reaches JavaScript on web — the API sets it as an
 * HttpOnly cookie (see lib/api/client.ts), which is what keeps a user signed in
 * across tabs and visits without ever putting a long-lived credential in
 * script-readable storage.
 */

import { storage } from './storage';

const KEYS = {
  ACCESS: 'ch_access_token',
  REFRESH: 'ch_refresh_token',
  USER_EMAIL: 'ch_user_email',
  USER_ID: 'ch_user_id',
} as const;

// The current access token, held in memory as well as in storage. It is what
// makes getAccessTokenSync() correct on native, where Preferences can only be
// read asynchronously, once a session has been saved or restored.
let memoryAccessToken: string | null = null;

// ── Setters ───────────────────────────────────────────────────────────────

export async function setAccessToken(token: string): Promise<void> {
  memoryAccessToken = token;
  await storage.set(KEYS.ACCESS, token);
}

export async function setRefreshToken(token: string): Promise<void> {
  await storage.set(KEYS.REFRESH, token);
}

/** `refreshToken` is omitted on web (it lives in an HttpOnly cookie) and when
 *  a refresh returned an access token only; the stored one is then left as is. */
export async function setSession(params: {
  accessToken: string;
  refreshToken?: string;
  userEmail?: string;
  userId?: string;
}): Promise<void> {
  memoryAccessToken = params.accessToken;
  await Promise.all([
    storage.set(KEYS.ACCESS, params.accessToken),
    params.refreshToken ? storage.set(KEYS.REFRESH, params.refreshToken) : Promise.resolve(),
    params.userEmail ? storage.set(KEYS.USER_EMAIL, params.userEmail) : Promise.resolve(),
    params.userId ? storage.set(KEYS.USER_ID, params.userId) : Promise.resolve(),
  ]);
}

// ── Getters ───────────────────────────────────────────────────────────────

export async function getAccessToken(): Promise<string | null> {
  return storage.get(KEYS.ACCESS);
}

export async function getRefreshToken(): Promise<string | null> {
  return storage.get(KEYS.REFRESH);
}

export async function getUserEmail(): Promise<string | null> {
  return storage.get(KEYS.USER_EMAIL);
}

export async function getUserId(): Promise<string | null> {
  return storage.get(KEYS.USER_ID);
}

export async function hasSession(): Promise<boolean> {
  const token = await storage.get(KEYS.ACCESS);
  return token !== null && token.length > 0;
}

// ── Clear (logout) ────────────────────────────────────────────────────────

/** Drop a refresh token left in storage by a web build that predates the
 *  HttpOnly cookie. Web only — on native, storage IS where it belongs. */
export async function removeStoredRefreshToken(): Promise<void> {
  await storage.remove(KEYS.REFRESH);
}

export async function clearSession(): Promise<void> {
  memoryAccessToken = null;
  await Promise.all([
    storage.remove(KEYS.ACCESS),
    storage.remove(KEYS.REFRESH),
    storage.remove(KEYS.USER_EMAIL),
    storage.remove(KEYS.USER_ID),
  ]);
}

// ── Synchronous shim (for code that hasn't been converted to async yet) ───
// These read from sessionStorage directly on web for backwards-compat with
// M5 synchronous reads. On mobile they return null (caller must use async
// getters above). Mark each call site with TODO: convert to async.

export function getAccessTokenSync(): string | null {
  if (typeof window === 'undefined') return null;
  // The in-memory copy covers native once restoreSession() (lib/api/client.ts)
  // has run; the sessionStorage read covers a web tab reloaded mid-session.
  return memoryAccessToken ?? sessionStorage.getItem(KEYS.ACCESS);
}
