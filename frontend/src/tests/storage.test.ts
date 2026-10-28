/**
 * storage.test.ts — Vectorae (M7 Chunk 4)
 *
 * Tests for src/lib/storage.ts — the platform-aware storage abstraction.
 *
 * Key assertions:
 *   • On native: @capacitor/preferences is used
 *   • On web: sessionStorage is used
 *   • localStorage.setItem is NEVER called during any auth flow
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Mock @capacitor/preferences ───────────────────────────────────────────────
const mockPreferences = {
  get:    vi.fn().mockResolvedValue({ value: null }),
  set:    vi.fn().mockResolvedValue(undefined),
  remove: vi.fn().mockResolvedValue(undefined),
  clear:  vi.fn().mockResolvedValue(undefined),
};

vi.mock('@capacitor/preferences', () => ({
  Preferences: mockPreferences,
}));

// ── Mock @capacitor/core ──────────────────────────────────────────────────────
// Controlled per-test via `mockReturnValue`
const mockIsNativePlatform = vi.fn().mockReturnValue(false);
const mockGetPlatform      = vi.fn().mockReturnValue('web');

vi.mock('@capacitor/core', () => ({
  Capacitor: {
    isNativePlatform: mockIsNativePlatform,
    getPlatform:      mockGetPlatform,
  },
}));

// ── Import AFTER mocks are registered ────────────────────────────────────────
// Dynamic import is used so the mock is in place before the module resolves.
const getStorage = async () => {
  vi.resetModules();
  const { storage } = await import('@/lib/storage');
  return storage;
};

describe('storage — web platform (sessionStorage)', () => {
  beforeEach(() => {
    mockIsNativePlatform.mockReturnValue(false);
    mockGetPlatform.mockReturnValue('web');
    sessionStorage.clear();
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('stores and retrieves a value via sessionStorage', async () => {
    const storage = await getStorage();
    await storage.set('test_key', 'test_value');
    const result = await storage.get('test_key');
    expect(result).toBe('test_value');
  });

  it('removes a value from sessionStorage', async () => {
    const storage = await getStorage();
    await storage.set('rm_key', 'rm_value');
    await storage.remove('rm_key');
    const result = await storage.get('rm_key');
    expect(result).toBeNull();
  });

  it('clears all sessionStorage values', async () => {
    const storage = await getStorage();
    await storage.set('a', '1');
    await storage.set('b', '2');
    await storage.clear();
    expect(await storage.get('a')).toBeNull();
    expect(await storage.get('b')).toBeNull();
  });

  it('NEVER writes to localStorage during storage operations', async () => {
    // NOTE: was previously asserted via vi.spyOn(Storage.prototype,
    // 'setItem') + expect(localStorage.setItem).not.toHaveBeenCalled().
    // That's unreliable in jsdom: localStorage and sessionStorage share
    // the SAME Storage.prototype, so spying the prototype method makes
    // localStorage.setItem and sessionStorage.setItem literally the same
    // spied function — calling sessionStorage.setItem() (correct,
    // intended behavior) makes the "localStorage.setItem" assertion look
    // like it fired too, even though localStorage itself was never
    // touched. Checking the actual stored value is unambiguous.
    const storage = await getStorage();
    await storage.set('ch_access_token', 'some_jwt');
    await storage.get('ch_access_token');

    expect(localStorage.getItem('ch_access_token')).toBeNull();
    expect(sessionStorage.getItem('ch_access_token')).toBe('some_jwt');

    await storage.remove('ch_access_token');
    expect(sessionStorage.getItem('ch_access_token')).toBeNull();
  });
});

describe('storage — native platform (@capacitor/preferences)', () => {
  beforeEach(() => {
    mockIsNativePlatform.mockReturnValue(true);
    mockGetPlatform.mockReturnValue('android');
    vi.clearAllMocks();
    mockPreferences.get.mockResolvedValue({ value: 'native_value' });
  });

  it('delegates get() to Preferences', async () => {
    const storage = await getStorage();
    const result = await storage.get('native_key');
    expect(mockPreferences.get).toHaveBeenCalledWith({ key: 'native_key' });
    expect(result).toBe('native_value');
  });

  it('delegates set() to Preferences', async () => {
    const storage = await getStorage();
    await storage.set('native_key', 'some_value');
    expect(mockPreferences.set).toHaveBeenCalledWith({ key: 'native_key', value: 'some_value' });
  });

  it('delegates remove() to Preferences', async () => {
    const storage = await getStorage();
    await storage.remove('native_key');
    expect(mockPreferences.remove).toHaveBeenCalledWith({ key: 'native_key' });
  });

  it('delegates clear() to Preferences', async () => {
    const storage = await getStorage();
    await storage.clear();
    expect(mockPreferences.clear).toHaveBeenCalled();
  });

  it('NEVER writes to sessionStorage or localStorage on native', async () => {
    // NOTE: was previously asserted via vi.spyOn(...).not.toHaveBeenCalled()
    // — fragile in this describe block since nothing here re-establishes
    // the spy per test the way the web-platform block's beforeEach did.
    // Checking actual stored values is unambiguous and doesn't depend on
    // spy lifecycle.
    sessionStorage.clear();
    localStorage.clear();

    const storage = await getStorage();
    await storage.set('ch_refresh_token', 'refresh_jwt');

    expect(sessionStorage.getItem('ch_refresh_token')).toBeNull();
    expect(localStorage.getItem('ch_refresh_token')).toBeNull();
  });
});

describe('storage — auth-session token flow', () => {
  beforeEach(() => {
    mockIsNativePlatform.mockReturnValue(false);
    mockGetPlatform.mockReturnValue('web');
    sessionStorage.clear();
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('full auth flow: setSession → getAccessToken → clearSession', async () => {
    const { setSession, getAccessToken, getRefreshToken, clearSession } =
      await import('@/lib/auth-session');

    await setSession({
      accessToken: 'access_jwt',
      refreshToken: 'refresh_jwt',
      userEmail: 'user@test.com',
      userId: '42',
    });

    expect(await getAccessToken()).toBe('access_jwt');
    expect(await getRefreshToken()).toBe('refresh_jwt');

    await clearSession();

    expect(await getAccessToken()).toBeNull();
    expect(await getRefreshToken()).toBeNull();
  });

  it('localStorage never receives the tokens during the auth flow', async () => {
    const { setSession, clearSession } = await import('@/lib/auth-session');

    await setSession({ accessToken: 'at', refreshToken: 'rt' });

    expect(localStorage.getItem('ch_access_token')).toBeNull();
    expect(localStorage.getItem('ch_refresh_token')).toBeNull();
    expect(sessionStorage.getItem('ch_access_token')).toBe('at');
    expect(sessionStorage.getItem('ch_refresh_token')).toBe('rt');

    await clearSession();
    expect(sessionStorage.getItem('ch_access_token')).toBeNull();
    expect(sessionStorage.getItem('ch_refresh_token')).toBeNull();
  });
});