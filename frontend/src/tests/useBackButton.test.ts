/**
 * useBackButton.test.ts — Vectorae (M7 Chunk 4)
 *
 * Tests for src/hooks/useBackButton.ts
 *
 * Coverage:
 *   • Root screen (/, /strategies, etc.) → window.confirm called + App.exitApp on "OK"
 *   • Root screen → "Cancel" on confirm → App.exitApp NOT called
 *   • Nested screen (/strategies/123) → router.back() called
 *   • Non-native (web) → no listener registered, no-op
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

// ── Mock next/navigation ───────────────────────────────────────────────────────
const mockRouterBack = vi.fn();
const mockRouterReplace = vi.fn();
let currentPathname = '/';

vi.mock('next/navigation', () => ({
  useRouter:   vi.fn(() => ({ back: mockRouterBack, replace: mockRouterReplace })),
  usePathname: vi.fn(() => currentPathname),
}));

// ── Mock @capacitor/app ───────────────────────────────────────────────────────
let backButtonCallback: ((data: { canGoBack: boolean }) => void) | null = null;

const mockExitApp = vi.fn().mockResolvedValue(undefined);
const mockAddListener = vi.fn().mockImplementation(async (event: string, cb: any) => {
  if (event === 'backButton') backButtonCallback = cb;
  return { remove: vi.fn() };
});

vi.mock('@capacitor/app', () => ({
  App: {
    addListener: mockAddListener,
    exitApp: mockExitApp,
    getLaunchUrl: vi.fn().mockResolvedValue(null),
  },
}));

// ── Mock platform: native ─────────────────────────────────────────────────────
vi.mock('@/lib/platform', () => ({
  isNative: vi.fn().mockReturnValue(true),
  isAndroid: vi.fn().mockReturnValue(true),
  isServer:  vi.fn().mockReturnValue(false),
}));

import { useBackButton } from '@/hooks/useBackButton';

// ── Helpers ───────────────────────────────────────────────────────────────────

function fireBackButton(canGoBack = false) {
  if (backButtonCallback) {
    act(() => { backButtonCallback!({ canGoBack }); });
  }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('useBackButton', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    backButtonCallback = null;
    currentPathname = '/';
    vi.spyOn(window, 'confirm');
  });

  const ROOT_ROUTES = ['/', '/strategies', '/campaigns', '/leads', '/analytics', '/settings'];

  ROOT_ROUTES.forEach((route) => {
    it(`shows exit confirm dialog on root route: ${route}`, async () => {
      currentPathname = route;
      vi.spyOn(window, 'confirm').mockReturnValue(false);

      const { unmount } = renderHook(() => useBackButton());
      await act(async () => { await new Promise(r => setTimeout(r, 10)); });

      fireBackButton(false);

      expect(window.confirm).toHaveBeenCalledWith('Exit Vectorae?');
      unmount();
    });
  });

  it('calls App.exitApp() when user confirms exit on root route', async () => {
    currentPathname = '/';
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    const { unmount } = renderHook(() => useBackButton());
    await act(async () => { await new Promise(r => setTimeout(r, 10)); });

    fireBackButton(false);

    expect(mockExitApp).toHaveBeenCalled();
    unmount();
  });

  it('does NOT call App.exitApp() when user cancels exit dialog', async () => {
    currentPathname = '/';
    vi.spyOn(window, 'confirm').mockReturnValue(false);

    const { unmount } = renderHook(() => useBackButton());
    await act(async () => { await new Promise(r => setTimeout(r, 10)); });

    fireBackButton(false);

    expect(mockExitApp).not.toHaveBeenCalled();
    unmount();
  });

  it('calls router.back() on nested route when canGoBack is true', async () => {
    currentPathname = '/strategies/123';

    const { unmount } = renderHook(() => useBackButton());
    await act(async () => { await new Promise(r => setTimeout(r, 10)); });

    fireBackButton(true);

    expect(mockRouterBack).toHaveBeenCalled();
    expect(window.confirm).not.toHaveBeenCalled();
    unmount();
  });

  it('calls router.replace("/") on nested route with empty history', async () => {
    currentPathname = '/strategies/123';

    const { unmount } = renderHook(() => useBackButton());
    await act(async () => { await new Promise(r => setTimeout(r, 10)); });

    fireBackButton(false);   // canGoBack = false despite nested route

    expect(mockRouterReplace).toHaveBeenCalledWith('/');
    unmount();
  });

  it('registers the back button listener on mount', async () => {
    const { unmount } = renderHook(() => useBackButton());
    await act(async () => { await new Promise(r => setTimeout(r, 10)); });

    expect(mockAddListener).toHaveBeenCalledWith('backButton', expect.any(Function));
    unmount();
  });

  it('is a no-op on web (non-native)', async () => {
    const { isNative } = await import('@/lib/platform');
    vi.mocked(isNative).mockReturnValue(false);

    const { unmount } = renderHook(() => useBackButton());
    await act(async () => { await new Promise(r => setTimeout(r, 10)); });

    expect(mockAddListener).not.toHaveBeenCalled();
    unmount();

    vi.mocked(isNative).mockReturnValue(true);   // restore
  });
});
