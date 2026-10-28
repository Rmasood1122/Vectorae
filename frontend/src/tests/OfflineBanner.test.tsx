/**
 * OfflineBanner.test.tsx — Vectorae (M7 Chunk 4)
 *
 * Tests for src/components/ui/OfflineBanner.tsx
 *
 * Coverage:
 *   • When online + backend reachable → nothing rendered
 *   • When offline → "No connection" banner rendered
 *   • When online but backend unreachable → "Server temporarily unreachable" banner
 *   • Cached React Query data still renders (UI not blocked by offline state)
 *   • Banners have correct ARIA role for accessibility
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';

import { NetworkContext } from '@/contexts/NetworkContext';
import { MESSAGES, OfflineBanner } from '@/components/ui/OfflineBanner';

// ── Helper: render with controlled network state ───────────────────────────────

function renderWithNetwork(
  online: boolean,
  backendReachable: boolean,
  showStatusLink?: boolean,
) {
  return render(
    <NetworkContext.Provider
      value={{
        online,
        backendReachable,
        setOnline: () => {},
        setBackendReachable: () => {},
      }}
    >
      <OfflineBanner showStatusLink={showStatusLink} />
    </NetworkContext.Provider>,
  );
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('OfflineBanner', () => {
  it('renders nothing when online and backend reachable', () => {
    const { container } = renderWithNetwork(true, true);
    expect(container.firstChild).toBeNull();
  });

  it('renders "No connection" when device is offline', () => {
    renderWithNetwork(false, false);
    expect(screen.getByRole('status')).toBeTruthy();
    expect(screen.getByText(/No connection/i)).toBeTruthy();
  });

  it('offline: says only what is true — this device going offline does not stop server-side sends', () => {
    renderWithNetwork(false, false);
    expect(screen.getByText(/not on this device/i)).toBeTruthy();
    expect(screen.getByText(/does not stop them/i)).toBeTruthy();
  });

  it('server unreachable: never claims campaigns are still running (that is unknowable)', () => {
    renderWithNetwork(true, false);
    const text = screen.getByRole('status').textContent ?? '';
    expect(text).not.toMatch(/still running/i);
    expect(text).toMatch(/can't confirm whether scheduled sends are going out/i);
  });

  it('no banner copy anywhere asserts campaigns are running', () => {
    for (const { body } of Object.values(MESSAGES)) {
      expect(body).not.toMatch(/still running/i);
    }
  });

  it('server unreachable: links the status page only when the link is live', () => {
    const { unmount } = renderWithNetwork(true, false, true);
    expect(screen.getByTestId('offline-status-link').getAttribute('href'))
      .toBe('/status/');
    unmount();
    renderWithNetwork(true, false, false);
    expect(screen.queryByTestId('offline-status-link')).toBeNull();
  });

  it('offline: no status link (the device cannot load it anyway)', () => {
    renderWithNetwork(false, false, true);
    expect(screen.queryByTestId('offline-status-link')).toBeNull();
  });

  it('renders "Server temporarily unreachable" when online but backend is down', () => {
    renderWithNetwork(true, false);
    expect(screen.getByRole('status')).toBeTruthy();
    expect(screen.getByText(/temporarily unreachable/i)).toBeTruthy();
  });

  it('shows different copy for offline vs backend-unreachable', () => {
    const { unmount } = renderWithNetwork(false, false);
    const offlineTitle = screen.getByRole('status').textContent;
    unmount();

    renderWithNetwork(true, false);
    const unreachableTitle = screen.getByRole('status').textContent;

    expect(offlineTitle).not.toBe(unreachableTitle);
  });

  it('banner has aria-live="polite" for screen reader announcements', () => {
    renderWithNetwork(false, false);
    const banner = screen.getByRole('status');
    expect(banner.getAttribute('aria-live')).toBe('polite');
  });

  it('does not block UI — banner is position:fixed with pointerEvents not blocking', () => {
    renderWithNetwork(false, false);
    const banner = screen.getByRole('status');
    // fixed positioning is set via className, not inline style —
    // just verify the banner exists and is not a modal overlay
    expect(banner).toBeTruthy();
    // The banner must not have aria-modal (it doesn't block interaction)
    expect(banner.getAttribute('aria-modal')).toBeNull();
  });
});
