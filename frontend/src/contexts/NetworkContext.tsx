'use client';

/**
 * NetworkContext — Vectorae
 *
 * Provides two distinct network states to the entire component tree:
 *
 *   online          — the device has a network connection (WiFi/cell)
 *   backendReachable — the API backend is responding
 *
 * These are intentionally separate:
 *   • offline + !backendReachable  → "No connection" (device offline)
 *   • online  + !backendReachable  → "Server unreachable" (our cloud backend is down)
 *
 * The banner says only what the device can know: going offline here does not
 * stop server-side sends; an unreachable server means their state is unknown
 * (see components/ui/OfflineBanner.tsx).
 *
 * Populated by useNetwork (called once in NativeProvider).
 * Consumed by OfflineBanner.
 */

import { createContext, useContext, useState, ReactNode } from 'react';

export interface NetworkState {
  online: boolean;
  backendReachable: boolean;
  setOnline: (v: boolean) => void;
  setBackendReachable: (v: boolean) => void;
}

export const NetworkContext = createContext<NetworkState>({
  online: true,
  backendReachable: true,
  setOnline: () => {},
  setBackendReachable: () => {},
});

export function NetworkProvider({ children }: { children: ReactNode }) {
  const [online, setOnline] = useState(true);
  const [backendReachable, setBackendReachable] = useState(true);

  return (
    <NetworkContext.Provider value={{ online, backendReachable, setOnline, setBackendReachable }}>
      {children}
    </NetworkContext.Provider>
  );
}

/** Hook for reading network state in any component (e.g. OfflineBanner). */
export function useNetworkState(): Pick<NetworkState, 'online' | 'backendReachable'> {
  const { online, backendReachable } = useContext(NetworkContext);
  return { online, backendReachable };
}

/** Hook for updating network state (used only inside useNetwork hook). */
export function useNetworkDispatch(): Pick<NetworkState, 'setOnline' | 'setBackendReachable'> {
  const { setOnline, setBackendReachable } = useContext(NetworkContext);
  return { setOnline, setBackendReachable };
}
