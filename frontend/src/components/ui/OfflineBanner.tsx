'use client';

/**
 * OfflineBanner — Vectorae
 *
 * Non-blocking persistent banner shown when connectivity is degraded.
 * Never blocks the UI — users can still read cached React Query data.
 *
 * Two distinct states with different messages:
 *   OFFLINE            → device has no network at all
 *   BACKEND_UNREACHABLE → device has network but our API backend is down
 *
 * Honesty rule (platform-gaps item 5): the banner states only what this
 * device can know.
 *   OFFLINE — losing THIS device's connection does not stop anything:
 *     scheduled sends run on Vectorae's servers, not in the app. True by
 *     construction, so it may be said.
 *   BACKEND_UNREACHABLE — the server did not answer, so the app cannot know
 *     whether sends are going out. It used to say "Your campaigns are still
 *     running", which is exactly what an outage makes unknowable. It now says
 *     so, and points at the status page (when its link is live), which shows
 *     "unknown" for the same situation.
 *
 * Rendered once in app/layout.tsx, outside the page content area.
 */

import { useNetworkState } from '@/contexts/NetworkContext';
import { statusPageLink } from '@/lib/statusPage';

type BannerState = 'offline' | 'backend_unreachable' | 'ok';

function resolveBannerState(online: boolean, backendReachable: boolean): BannerState {
  if (!online) return 'offline';
  if (!backendReachable) return 'backend_unreachable';
  return 'ok';
}

export const MESSAGES: Record<Exclude<BannerState, 'ok'>, { title: string; body: string }> = {
  offline: {
    title: 'No connection',
    body:
      'Scheduled sends run on Vectorae\'s servers, not on this device, so losing your ' +
      'connection does not stop them. You\'ll be back in sync when connected.',
  },
  backend_unreachable: {
    title: 'Server temporarily unreachable',
    body:
      'We can\'t reach Vectorae\'s server right now, so we can\'t confirm whether ' +
      'scheduled sends are going out. We\'ll reconnect automatically.',
  },
};

export function OfflineBanner({ showStatusLink }: { showStatusLink?: boolean } = {}) {
  const { online, backendReachable } = useNetworkState();
  const status = statusPageLink(showStatusLink);
  const state = resolveBannerState(online, backendReachable);

  if (state === 'ok') return null;

  const { title, body } = MESSAGES[state];

  return (
    <div
      role="status"
      aria-live="polite"
      className={[
        'fixed top-0 left-0 right-0 z-50',
        'flex items-start gap-3 px-4 py-3',
        'text-sm',
        state === 'offline'
          ? 'bg-yellow-500/95 text-yellow-950'
          : 'bg-orange-500/95 text-orange-950',
      ].join(' ')}
      style={{ paddingTop: 'max(0.75rem, env(safe-area-inset-top))' }}
    >
      {/* Icon */}
      <svg
        className="mt-0.5 h-4 w-4 shrink-0"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        {state === 'offline' ? (
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M18.364 5.636a9 9 0 010 12.728M15.536 8.464a5 5 0 010 7.072M12 11a1 1 0 100 2 1 1 0 000-2zm-3.536-2.536a5 5 0 000 7.072M5.636 5.636a9 9 0 000 12.728"
          />
        ) : (
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"
          />
        )}
      </svg>

      {/* Text */}
      <div>
        <span className="font-semibold">{title}</span>
        <span className="ml-1 opacity-90">{body}</span>
        {state === 'backend_unreachable' && status && (
          <a href={status.href} className="ml-1 font-semibold underline" data-testid="offline-status-link">
            Check service status
          </a>
        )}
      </div>
    </div>
  );
}
