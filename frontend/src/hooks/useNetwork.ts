'use client';

/**
 * useNetwork — Vectorae
 *
 * Monitors network status and populates NetworkContext.
 * Called ONCE inside NativeProvider — do not call elsewhere.
 *
 * Two distinct checks:
 *   1. @capacitor/network → device has a network connection  (online)
 *   2. GET /health ping   → our FastAPI backend is reachable (backendReachable)
 *
 * On web: falls back to navigator.onLine for device network status.
 *
 * TODO: verify @capacitor/network API against current Capacitor docs.
 */

import { useEffect, useRef } from 'react';
import { isNative } from '@/lib/platform';
import { useNetworkDispatch } from '@/contexts/NetworkContext';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? '';
const BACKEND_PING_INTERVAL_MS = 30_000;  // check every 30 s
const BACKEND_PING_TIMEOUT_MS  = 5_000;

async function pingBackend(): Promise<boolean> {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), BACKEND_PING_TIMEOUT_MS);
    const res = await fetch(`${API_URL}/health`, {
      signal: controller.signal,
      cache: 'no-store',
    });
    clearTimeout(timer);
    return res.ok;
  } catch {
    return false;
  }
}

export function useNetwork() {
  const { setOnline, setBackendReachable } = useNetworkDispatch();
  const pingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let networkListener: { remove(): void } | null = null;

    async function init() {
      // ── 1. Device network state ─────────────────────────────────────────
      if (isNative()) {
        // TODO: verify @capacitor/network import path and API shape
        const { Network } = await import('@capacitor/network');

        // Get initial state
        const status = await Network.getStatus();
        setOnline(status.connected);

        // Subscribe to changes
        networkListener = await Network.addListener('networkStatusChange', (s) => {
          setOnline(s.connected);
          if (!s.connected) {
            // Device offline → backend definitely unreachable
            setBackendReachable(false);
          } else {
            // Came back online → re-ping immediately
            pingBackend().then(setBackendReachable);
          }
        });
      } else {
        // Web fallback: navigator.onLine
        const handleOnline  = () => { setOnline(true);  pingBackend().then(setBackendReachable); };
        const handleOffline = () => { setOnline(false); setBackendReachable(false); };
        window.addEventListener('online',  handleOnline);
        window.addEventListener('offline', handleOffline);
        setOnline(navigator.onLine);
      }

      // ── 2. Backend reachability ─────────────────────────────────────────
      // Initial ping
      const reachable = await pingBackend();
      setBackendReachable(reachable);

      // Periodic ping
      pingTimerRef.current = setInterval(async () => {
        const r = await pingBackend();
        setBackendReachable(r);
      }, BACKEND_PING_INTERVAL_MS);
    }

    init();

    return () => {
      networkListener?.remove();
      if (pingTimerRef.current) clearInterval(pingTimerRef.current);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
}
