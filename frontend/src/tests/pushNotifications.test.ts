/**
 * pushNotifications.test.ts — Vectorae (M7 Chunk 4)
 *
 * Tests for src/hooks/usePushNotifications.ts
 *
 * Coverage:
 *   • Permission granted → POST /devices/register called with FCM token
 *   • Permission denied → flag written to storage, no API call
 *   • shouldShowPushRePrompt → true after denial, false if already shown
 *   • Token refresh → re-registers with new token
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

// ── Mock platform: always native ──────────────────────────────────────────────
vi.mock('@/lib/platform', () => ({
  isNative: vi.fn().mockReturnValue(true),
  isAndroid: vi.fn().mockReturnValue(true),
  isIOS: vi.fn().mockReturnValue(false),
  isWeb: vi.fn().mockReturnValue(false),
  isServer: vi.fn().mockReturnValue(false),
}));

// ── Mock storage ───────────────────────────────────────────────────────────────
const mockStorageMap = new Map<string, string>();
vi.mock('@/lib/storage', () => ({
  storage: {
    get:    vi.fn(async (k: string) => mockStorageMap.get(k) ?? null),
    set:    vi.fn(async (k: string, v: string) => { mockStorageMap.set(k, v); }),
    remove: vi.fn(async (k: string) => { mockStorageMap.delete(k); }),
    clear:  vi.fn(async () => { mockStorageMap.clear(); }),
  },
}));

// ── Mock auth-session ──────────────────────────────────────────────────────────
vi.mock('@/lib/auth-session', () => ({
  getAccessToken: vi.fn().mockResolvedValue('mock_access_token'),
}));

// ── Mock fetch ────────────────────────────────────────────────────────────────
const mockFetch = vi.fn().mockResolvedValue({
  ok: true,
  json: async () => ({ token: 'fcm_abc', registered: true }),
});
global.fetch = mockFetch;

// ── Mock @capacitor/push-notifications ────────────────────────────────────────
let onRegistration: ((data: { value: string }) => void) | null = null;
let onNotificationTap: ((action: any) => void) | null = null;

const mockRequestPermissions = vi.fn();
const mockRegister = vi.fn().mockResolvedValue(undefined);
const mockAddListener = vi.fn().mockImplementation(async (event: string, cb: any) => {
  if (event === 'registration') onRegistration = cb;
  if (event === 'pushNotificationActionPerformed') onNotificationTap = cb;
  return { remove: vi.fn() };
});

vi.mock('@capacitor/push-notifications', () => ({
  PushNotifications: {
    requestPermissions: mockRequestPermissions,
    register: mockRegister,
    addListener: mockAddListener,
  },
}));

// ── Import after mocks ─────────────────────────────────────────────────────────
import {
  usePushNotifications,
  shouldShowPushRePrompt,
  markPushRePromptShown,
} from '@/hooks/usePushNotifications';

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('usePushNotifications', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockStorageMap.clear();
    onRegistration = null;
    onNotificationTap = null;
  });

  it('registers FCM token when permission is granted', async () => {
    mockRequestPermissions.mockResolvedValue({ receive: 'granted' });

    const { unmount } = renderHook(() => usePushNotifications());

    // Wait for async effects to run
    await act(async () => {
      await new Promise(r => setTimeout(r, 10));
      // Simulate FCM registration callback
      if (onRegistration) onRegistration({ value: 'fcm_token_granted_123' });
      await new Promise(r => setTimeout(r, 10));
    });

    expect(mockRegister).toHaveBeenCalled();
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/devices/register'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ token: 'fcm_token_granted_123', platform: 'android' }),
      }),
    );

    unmount();
  });

  it('writes denied flag to storage and does NOT call register API when permission denied', async () => {
    mockRequestPermissions.mockResolvedValue({ receive: 'denied' });

    const { unmount } = renderHook(() => usePushNotifications());
    await act(async () => { await new Promise(r => setTimeout(r, 20)); });

    expect(mockRegister).not.toHaveBeenCalled();
    expect(mockFetch).not.toHaveBeenCalled();
    expect(mockStorageMap.get('ch_push_permission_denied')).toBe('true');

    unmount();
  });

  it('clears denied flag when permission is subsequently granted', async () => {
    // Pre-set denied flag
    mockStorageMap.set('ch_push_permission_denied', 'true');
    mockRequestPermissions.mockResolvedValue({ receive: 'granted' });

    const { unmount } = renderHook(() => usePushNotifications());
    await act(async () => { await new Promise(r => setTimeout(r, 20)); });

    expect(mockStorageMap.get('ch_push_permission_denied')).toBeUndefined();

    unmount();
  });

  it('re-registers when a new FCM token is received (token refresh)', async () => {
    mockRequestPermissions.mockResolvedValue({ receive: 'granted' });

    const { unmount } = renderHook(() => usePushNotifications());
    await act(async () => { await new Promise(r => setTimeout(r, 10)); });

    // First token
    await act(async () => {
      if (onRegistration) onRegistration({ value: 'token_v1' });
      await new Promise(r => setTimeout(r, 10));
    });

    // Second token (rotation)
    await act(async () => {
      if (onRegistration) onRegistration({ value: 'token_v2' });
      await new Promise(r => setTimeout(r, 10));
    });

    expect(mockFetch).toHaveBeenCalledTimes(2);
    const secondCall = mockFetch.mock.calls[1];
    expect(secondCall[1].body).toContain('token_v2');

    unmount();
  });
});

// ── shouldShowPushRePrompt ────────────────────────────────────────────────────

describe('shouldShowPushRePrompt', () => {
  beforeEach(() => { mockStorageMap.clear(); });

  it('returns true when user denied and has not been re-prompted', async () => {
    mockStorageMap.set('ch_push_permission_denied', 'true');
    expect(await shouldShowPushRePrompt()).toBe(true);
  });

  it('returns false when user never denied', async () => {
    expect(await shouldShowPushRePrompt()).toBe(false);
  });

  it('returns false after markPushRePromptShown is called', async () => {
    mockStorageMap.set('ch_push_permission_denied', 'true');
    await markPushRePromptShown();
    expect(await shouldShowPushRePrompt()).toBe(false);
  });
});
