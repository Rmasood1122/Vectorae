/**
 * Pure logic behind the public status page at /status
 * (www.calendarharvest.com/status/; src/app/status/page.tsx).
 *
 * The page is part of the static export served by Vercel, so it keeps loading
 * when the API on Render is down — which is the point. The visitor's browser
 * calls `${NEXT_PUBLIC_API_URL}/status/public` (app/api/status_public.py) on
 * load and every 30 s while the tab is visible, with an 8 s timeout.
 *
 * Rules this module enforces (tests: src/tests/statusBoard.test.ts):
 *   - Never claim "Operational" for something not checked: an unrecognised or
 *     missing component is "unknown" ("No data"), and unknown beats
 *     operational in the overall state.
 *   - Cannot reach the API (network error, timeout, 5xx, unreadable body)
 *     -> "Vectorae API unreachable — major outage". Old good data is dropped,
 *     never shown as current.
 *   - 429 means the API answered; it is not an outage. The last good result
 *     stays on screen, marked with its time, and polling backs off.
 *   - Only id, name and status are read from the response. No other field —
 *     and no error text of any kind — reaches the page.
 */

import type { Incident, IncidentImpact } from "@/lib/statusIncidents";
import { isResolved } from "@/lib/statusIncidents";

export const REFRESH_INTERVAL_MS = 30_000;
export const RATE_LIMITED_RETRY_MS = 120_000;
export const FETCH_TIMEOUT_MS = 8_000;

export const PUBLIC_STATUSES = [
  "operational",
  "degraded",
  "partial_outage",
  "major_outage",
  "maintenance",
] as const;
export type PublicStatus = (typeof PUBLIC_STATUSES)[number];
/** `unknown` is the page's own: nothing checked it. Never in the API contract. */
export type ComponentStatus = PublicStatus | "unknown";
export type OverallState = ComponentStatus | "unreachable" | "unconfigured";

/** The ids and names GET /status/public always returns, in its order. */
export const KNOWN_COMPONENTS: readonly { id: string; name: string }[] = [
  { id: "api", name: "API" },
  { id: "background-processing", name: "Background processing" },
  { id: "ai-features", name: "AI features" },
  { id: "booking-pages", name: "Booking pages" },
];

export const COMPONENT_STATUS_LABELS: Record<ComponentStatus, string> = {
  operational: "Operational",
  degraded: "Degraded performance",
  partial_outage: "Partial outage",
  major_outage: "Major outage",
  maintenance: "Under maintenance",
  unknown: "No data",
};

export const OVERALL_HEADLINES: Record<OverallState, string> = {
  operational: "All systems operational",
  degraded: "Degraded performance",
  partial_outage: "Partial outage",
  major_outage: "Major outage",
  maintenance: "Maintenance in progress",
  unknown: "Some systems could not be checked",
  unreachable: "Vectorae API unreachable — major outage",
  unconfigured: "Live status checks are not configured on this site",
};

export type UnreachableReason = "timeout" | "network" | "http_error" | "malformed";

/** Customer-facing explanations. No status codes, hosts or error text. */
export const UNREACHABLE_DETAILS: Record<UnreachableReason, string> = {
  timeout: `The Vectorae API did not answer within ${FETCH_TIMEOUT_MS / 1000} seconds.`,
  network: "The Vectorae API could not be reached from your browser.",
  http_error: "The Vectorae API answered with an error instead of its status.",
  malformed: "The Vectorae API's answer could not be read.",
};

/**
 * Text colours on the marketing background (#0a0a0a), all >= 4.5:1
 * (tested). Status is never shown by colour alone: every state also has a
 * text label and its own icon shape (components/status/StatusBoard.tsx).
 */
export const STATUS_PAGE_BACKGROUND = "#0a0a0a";
export const STATUS_COLORS: Record<ComponentStatus, string> = {
  operational: "#4ade80",
  degraded: "#facc15",
  partial_outage: "#fb923c",
  major_outage: "#f87171",
  maintenance: "#60a5fa",
  unknown: "#9ca3af",
};

export function overallColor(state: OverallState): string {
  if (state === "unreachable") return STATUS_COLORS.major_outage;
  if (state === "unconfigured") return STATUS_COLORS.unknown;
  return STATUS_COLORS[state];
}

export interface StatusComponent {
  id: string;
  name: string;
  status: ComponentStatus;
}

export type StatusResult =
  | { kind: "ok"; components: StatusComponent[] }
  | { kind: "unreachable"; reason: UnreachableReason }
  | { kind: "rate_limited" }
  | { kind: "unconfigured" };

export interface StatusView {
  overall: OverallState;
  components: StatusComponent[];
  /** When the browser last tried. */
  checkedAt: Date;
  /** When the components shown were actually fetched (differs only when rate limited). */
  dataFrom: Date | null;
  reason: UnreachableReason | null;
  notice: "rate_limited" | null;
}

// ---------------------------------------------------------------------------

export function isPublicStatus(value: unknown): value is PublicStatus {
  return typeof value === "string" && (PUBLIC_STATUSES as readonly string[]).includes(value);
}

/** `${base}/status/public`, or null when the base is missing or not http(s). */
export function statusEndpoint(apiBase: string | null | undefined): string | null {
  const raw = (apiBase ?? "").trim().replace(/\/+$/, "");
  if (!raw) return null;
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
  } catch {
    return null;
  }
  return `${raw}/status/public`;
}

const ID_RE = /^[a-z0-9][a-z0-9-]{0,47}$/;
const MAX_NAME = 60;
const MAX_COMPONENTS = 20;
const KNOWN_NAME = new Map(KNOWN_COMPONENTS.map((c) => [c.id, c.name]));

/** The JSON body of GET /status/public -> components, or malformed. */
export function parseStatusBody(json: unknown): StatusResult {
  const malformed: StatusResult = { kind: "unreachable", reason: "malformed" };
  if (!json || typeof json !== "object" || Array.isArray(json)) return malformed;
  const list = (json as { components?: unknown }).components;
  if (!Array.isArray(list)) return malformed;

  const found = new Map<string, StatusComponent>();
  for (const item of list) {
    if (found.size >= MAX_COMPONENTS) break;
    if (!item || typeof item !== "object") continue;
    const { id, name, status } = item as Record<string, unknown>;
    if (typeof id !== "string" || !ID_RE.test(id) || found.has(id)) continue;
    if (status === undefined) continue; // an item without a status says nothing
    const cleanName =
      typeof name === "string" && name.trim() && name.trim().length <= MAX_NAME ? name.trim() : null;
    found.set(id, {
      id,
      name: KNOWN_NAME.get(id) ?? cleanName ?? id,
      status: isPublicStatus(status) ? status : "unknown",
    });
  }
  if (found.size === 0) return malformed;

  // Contract order first; a known component the API left out is shown as
  // "No data" rather than silently disappearing. Extras keep response order.
  const components: StatusComponent[] = KNOWN_COMPONENTS.map(
    (k) => found.get(k.id) ?? { id: k.id, name: k.name, status: "unknown" as const },
  );
  for (const [id, c] of found) if (!KNOWN_NAME.has(id)) components.push(c);
  return { kind: "ok", components };
}

/** HTTP status + raw body text -> result. */
export function resultFromResponse(httpStatus: number, bodyText: string): StatusResult {
  if (httpStatus === 429) return { kind: "rate_limited" };
  if (httpStatus < 200 || httpStatus > 299) return { kind: "unreachable", reason: "http_error" };
  let json: unknown;
  try {
    json = JSON.parse(bodyText);
  } catch {
    return { kind: "unreachable", reason: "malformed" };
  }
  return parseStatusBody(json);
}

export interface FetchDeps {
  fetch?: (input: string, init?: RequestInit) => Promise<Response>;
  timeoutMs?: number;
}

/** One check. Never throws. */
export async function fetchPublicStatus(endpoint: string | null, deps: FetchDeps = {}): Promise<StatusResult> {
  if (!endpoint) return { kind: "unconfigured" };
  const doFetch = deps.fetch ?? ((input: string, init?: RequestInit) => fetch(input, init));
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, deps.timeoutMs ?? FETCH_TIMEOUT_MS);
  try {
    // A CORS "simple" GET: no credentials, no custom headers, so no preflight.
    const resp = await doFetch(endpoint, {
      method: "GET",
      credentials: "omit",
      cache: "no-store",
      signal: controller.signal,
    });
    const text = await resp.text();
    return resultFromResponse(resp.status, text);
  } catch {
    return { kind: "unreachable", reason: timedOut ? "timeout" : "network" };
  } finally {
    clearTimeout(timer);
  }
}

const SEVERITY: Record<PublicStatus, number> = {
  operational: 0,
  maintenance: 1,
  degraded: 2,
  partial_outage: 3,
  major_outage: 4,
};

/** Worst known status; unknown wins over operational; empty -> unknown. */
export function deriveOverall(components: readonly StatusComponent[]): ComponentStatus {
  if (components.length === 0) return "unknown";
  let worst: PublicStatus = "operational";
  let anyUnknown = false;
  for (const c of components) {
    if (c.status === "unknown") anyUnknown = true;
    else if (SEVERITY[c.status] > SEVERITY[worst]) worst = c.status;
  }
  return worst === "operational" && anyUnknown ? "unknown" : worst;
}

const IMPACT_FLOOR: Record<IncidentImpact, PublicStatus | null> = {
  none: null,
  minor: "degraded",
  major: "partial_outage",
  critical: "major_outage",
  maintenance: "maintenance",
};

/**
 * Open incidents raise the components they list to at least their impact.
 * Never better than the checks; maintenance only replaces Operational.
 */
export function applyIncidents(
  components: readonly StatusComponent[],
  incidents: readonly Incident[],
): StatusComponent[] {
  const open = incidents.filter((i) => !isResolved(i));
  return components.map((c) => {
    let status = c.status;
    for (const incident of open) {
      if (!incident.components.includes(c.id)) continue;
      const floor = IMPACT_FLOOR[incident.impact];
      if (!floor) continue;
      if (floor === "maintenance") {
        if (status === "operational") status = "maintenance";
      } else if (status === "unknown" || SEVERITY[floor] > SEVERITY[status]) {
        status = floor;
      }
    }
    return status === c.status ? c : { ...c, status };
  });
}

function placeholderComponents(apiStatus: ComponentStatus): StatusComponent[] {
  return KNOWN_COMPONENTS.map((k) => ({
    id: k.id,
    name: k.name,
    status: k.id === "api" ? apiStatus : "unknown",
  }));
}

/** Fold one check result into what the page shows. */
export function reduceStatus(prev: StatusView | null, result: StatusResult, now: Date): StatusView {
  switch (result.kind) {
    case "ok":
      return {
        overall: deriveOverall(result.components),
        components: result.components,
        checkedAt: now,
        dataFrom: now,
        reason: null,
        notice: null,
      };
    case "unreachable":
      return {
        overall: "unreachable",
        // From the visitor's side the API is down; nothing else could be checked.
        components: placeholderComponents("major_outage"),
        checkedAt: now,
        dataFrom: null,
        reason: result.reason,
        notice: null,
      };
    case "rate_limited":
      if (prev && prev.dataFrom && prev.overall !== "unreachable" && prev.overall !== "unconfigured") {
        return { ...prev, checkedAt: now, notice: "rate_limited", reason: null };
      }
      return {
        overall: "unknown",
        components: placeholderComponents("unknown"),
        checkedAt: now,
        dataFrom: null,
        reason: null,
        notice: "rate_limited",
      };
    case "unconfigured":
      return {
        overall: "unconfigured",
        components: placeholderComponents("unknown"),
        checkedAt: now,
        dataFrom: null,
        reason: null,
        notice: null,
      };
  }
}

/** Delay before the next automatic check; null = never. */
export function nextRefreshDelay(result: StatusResult): number | null {
  if (result.kind === "unconfigured") return null;
  if (result.kind === "rate_limited") return RATE_LIMITED_RETRY_MS;
  return REFRESH_INTERVAL_MS;
}

/**
 * Milliseconds until the next check: null while the tab is hidden or when
 * there is nothing to poll; 0 when a check is due now (first load, or the tab
 * came back after the delay passed).
 */
export function msUntilNextCheck(args: {
  hidden: boolean;
  lastAttemptAt: Date | null;
  delay: number | null;
  now: Date;
}): number | null {
  const { hidden, lastAttemptAt, delay, now } = args;
  if (hidden || delay === null) return null;
  if (!lastAttemptAt) return 0;
  const remaining = lastAttemptAt.getTime() + delay - now.getTime();
  return remaining > 0 ? remaining : 0;
}
