/**
 * deeplinks.ts - Vectorae
 *
 * Deep link handling for the Capacitor Android app.
 *
 * Supported schemes:
 *   clienthunter://                    (custom scheme - works without domain ownership)
 *   https://app.clienthunter.com       (Android App Links - requires domain + assetlinks.json)
 *
 * This module:
 *   1. Parses incoming URLs from either scheme into a { route, params } object.
 *   2. Exports URL builders used by the BACKEND to generate notification payloads
 *      and by Gmail outreach templates to embed "view in app" links.
 *
 * TODO: confirm production domain - replace 'app.clienthunter.com' with the real domain
 * before going live. This constant is referenced in:
 *   - AndroidManifest.xml (host in intent-filter)
 *   - public/.well-known/assetlinks.json (App Links verification)
 *   - backend/app/services/notifications.py (deep link URLs in push payloads)
 */

// -- Constants ----------------------------------------------------------

/** Custom scheme. Works on any Android device without domain verification. */
export const DEEP_LINK_SCHEME = 'clienthunter';

/**
 * App Links host. Requires:
 *   1. Domain ownership
 *   2. /.well-known/assetlinks.json served from this domain (see public/.well-known/)
 *   3. SHA-256 fingerprint of the signing certificate in assetlinks.json (Chunk 4)
 * TODO: confirm production domain before Chunk 4 / Play Store release.
 */
export const APP_LINKS_HOST = 'app.clienthunter.com'; // TODO: confirm production domain

// -- Route map ------------------------------------------------------------

export type DeepLinkRoute =
  | { path: '/'; params: Record<string, never> }
  | { path: '/strategies/[id]'; params: { id: string } }
  | { path: '/strategies'; params: Record<string, never> }
  | { path: '/campaigns/[id]'; params: { id: string } }
  | { path: '/campaigns'; params: { strategy?: string; tab?: string } }
  | { path: '/leads/[id]'; params: { id: string } }
  | { path: '/leads/detail'; params: { id: string; tab?: string } }
  | { path: '/leads'; params: Record<string, never> }
  | { path: '/settings'; params: { tab?: string } }
  | { path: '/team'; params: Record<string, never> }
  | { path: '/analytics'; params: Record<string, never> }
  | { path: '/send-approval'; params: Record<string, never> }
  | null; // unrecognised link - do nothing

/**
 * Parse a deep link URL (either scheme) into a Next.js route descriptor.
 * Returns null for unrecognised paths - callers should silently ignore null.
 */
export function parseDeepLink(url: string): DeepLinkRoute {
  let pathname: string;
  let search: URLSearchParams;

  try {
    // Normalise both schemes to a URL object by rewriting the custom scheme.
    // Match the 3-slash "no authority" form (`clienthunter:///path`) that
    // buildCustomSchemeLink() produces - matching only 2 slashes here left
    // the 3rd slash stuck onto the front of the remaining path, producing
    // a double-slash pathname ("//strategies/123") that no route regex
    // below could ever match, so every non-root deep link silently failed.
    const normalised = url.startsWith(`${DEEP_LINK_SCHEME}:///`)
      ? url.replace(`${DEEP_LINK_SCHEME}:///`, 'https://host/')
      : url;

    const parsed = new URL(normalised);
    pathname = parsed.pathname.replace(/\/$/, '') || '/';
    search = parsed.searchParams;
  } catch {
    return null;
  }

  // Feature expansion: the lead detail page. Every notification the event
  // bus sends about a lead (meeting prep ready, reminders, deal won) links
  // here, with an optional tab. Matched BEFORE the /leads/[id] regex below,
  // which would otherwise read "detail" as a lead id.
  if (pathname === '/leads/detail') {
    const id = search.get('id');
    if (!id) return null;
    const tab = search.get('tab');
    return { path: '/leads/detail', params: tab ? { id, tab } : { id } };
  }

  // -- Route matching -------------------------------------------------
  if (pathname === '/' || pathname === '') {
    return { path: '/', params: {} };
  }

  const strategyMatch = pathname.match(/^\/strategies\/([^/]+)$/);
  if (strategyMatch) {
    return { path: '/strategies/[id]', params: { id: strategyMatch[1] } };
  }

  if (pathname === '/strategies') {
    return { path: '/strategies', params: {} };
  }

  const campaignMatch = pathname.match(/^\/campaigns\/([^/]+)$/);
  if (campaignMatch) {
    return { path: '/campaigns/[id]', params: { id: campaignMatch[1] } };
  }

  if (pathname === '/campaigns') {
    // Feature Group 3: campaign alerts (objection spike) link to a campaign
    // and tab via query params -- the static export has no /campaigns/<id>.
    const params: { strategy?: string; tab?: string } = {};
    const strategy = search.get('strategy');
    const tab = search.get('tab');
    if (strategy) params.strategy = strategy;
    if (tab) params.tab = tab;
    return { path: '/campaigns', params };
  }

  const leadMatch = pathname.match(/^\/leads\/([^/]+)$/);
  if (leadMatch) {
    return { path: '/leads/[id]', params: { id: leadMatch[1] } };
  }

  if (pathname === '/leads') {
    return { path: '/leads', params: {} };
  }

  if (pathname === '/analytics') return { path: '/analytics', params: {} };
  // Feature Groups 8/9: deliverability alerts open Settings on their tab;
  // approval notices and invitations lead to the team page.
  if (pathname === '/settings') {
    const tab = search.get('tab');
    return { path: '/settings', params: tab ? { tab } : {} };
  }
  if (pathname === '/team') return { path: '/team', params: {} };
  // Daily send approval: the once-a-day "approve today's automatic sending"
  // push opens the approval page.
  if (pathname === '/send-approval') return { path: '/send-approval', params: {} };

  return null;
}

/**
 * Convert a DeepLinkRoute into a Next.js router path string for `router.push`.
 */
export function routeToPath(route: NonNullable<DeepLinkRoute>): string {
  if (route.path === '/strategies/[id]') return `/strategies/detail?id=${route.params.id}`;
  // `/campaigns/<id>` is a dynamic segment the static export cannot serve
  // (the same 404 the lead links had); the campaigns page reads ?strategy=.
  if (route.path === '/campaigns/[id]') {
    return `/campaigns?strategy=${encodeURIComponent(route.params.id)}`;
  }
  if (route.path === '/campaigns') {
    const q = new URLSearchParams();
    if (route.params.strategy) q.set('strategy', route.params.strategy);
    if (route.params.tab) q.set('tab', route.params.tab);
    const qs = q.toString();
    return qs ? `/campaigns?${qs}` : '/campaigns';
  }
  // `/leads/[id]` used to map to `/leads/<id>` -- a dynamic segment the
  // static export (output: 'export', required by Capacitor) cannot serve, so
  // every "new reply" push opened a 404. Both forms now land on the
  // query-param detail page, like /strategies/detail and /meetings/detail.
  if (route.path === '/settings') {
    return route.params.tab ? `/settings?tab=${encodeURIComponent(route.params.tab)}` : '/settings';
  }
  if (route.path === '/leads/[id]') return `/leads/detail?id=${route.params.id}`;
  if (route.path === '/leads/detail') {
    const tab = route.params.tab ? `&tab=${encodeURIComponent(route.params.tab)}` : '';
    return `/leads/detail?id=${encodeURIComponent(route.params.id)}${tab}`;
  }
  return route.path;
}

// -- URL builders (used by backend notification service + email templates) --
// The backend imports the constants below to build deep link URLs embedded
// in push notification payloads and Gmail outreach status emails.
// These are the ONLY two URL forms the backend should generate -
// custom scheme for push notifications, App Links for emails.

/** Build a custom-scheme deep link (for push notification data payloads).
 *
 * Produces the 3-slash "no authority" form (`clienthunter:///path`) - the
 * correct URI shape for a custom scheme with an empty host, and what
 * AndroidManifest.xml's intent-filter and parseDeepLink() below both
 * expect. Was previously only 2 slashes (`clienthunter://path`), which
 * put the first path segment in the URL's host/authority position
 * instead of the path - breaking deep link parsing on device.
 */
export function buildCustomSchemeLink(path: string): string {
  const clean = path.startsWith('/') ? path : `/${path}`;
  return `${DEEP_LINK_SCHEME}://${clean}`;
}

/** Build an App Links URL (for email "view in app" links - also works in browser). */
export function buildAppLink(path: string): string {
  const clean = path.startsWith('/') ? path : `/${path}`;
  return `https://${APP_LINKS_HOST}${clean}`;
}

// Convenience builders - these are what the backend notification service uses:
export const deepLinks = {
  strategy: (id: string | number) => ({
    custom: buildCustomSchemeLink(`/strategies/detail?id=${id}`),
    web: buildAppLink(`/strategies/detail?id=${id}`),
  }),
  campaign: (id: string | number) => ({
    custom: buildCustomSchemeLink(`/campaigns/${id}`),
    web: buildAppLink(`/campaigns?strategy=${id}`),
  }),
  lead: (id: string | number) => ({
    custom: buildCustomSchemeLink(`/leads/${id}`),
    web: buildAppLink(`/leads/detail?id=${id}`),
  }),
  leadTab: (id: string | number, tab: string) => ({
    custom: buildCustomSchemeLink(`/leads/detail?id=${id}&tab=${tab}`),
    web: buildAppLink(`/leads/detail?id=${id}&tab=${tab}`),
  }),
  dashboard: () => ({
    custom: buildCustomSchemeLink('/'),
    web: buildAppLink('/'),
  }),
};