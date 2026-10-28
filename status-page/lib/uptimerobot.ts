/**
 * UptimeRobot v2 `getMonitors` — the external prober, independent of the
 * Vectorae infrastructure it reports on.
 *
 * Read-only API key, server-side env var UPTIMEROBOT_API_KEY. The key goes in
 * the POST body to UptimeRobot and nowhere else: it is not returned from any
 * function here, so it cannot reach rendered HTML or /api/status.
 *
 * Per-day uptime comes from `custom_uptime_ranges` (one start_end range per
 * UTC day), which works on the free plan; raw logs older than 24 h are a Pro
 * feature, so they are not used. v2 is UptimeRobot's legacy API ("no longer
 * receiving updates", no end date announced as of 2026-10-11).
 */

import {
  MONITOR_NAMES,
  UPTIME_DAYS,
  UPTIMEROBOT_DEFAULT_URL,
  UPTIMEROBOT_TIMEOUT_MS,
  type MonitorRole,
  type StatusEnv,
} from "./config.ts";

export type MonitorState = "up" | "down" | "unknown";

export interface UptimeDay {
  /** UTC calendar day, YYYY-MM-DD. */
  date: string;
  /** Percentage 0-100, or null when the monitor did not exist yet or returned nothing usable. */
  uptime: number | null;
}

export interface MonitorData {
  state: MonitorState;
  days: UptimeDay[];
  /** UptimeRobot's own 90-day uptime ratio, or null. */
  ratio: number | null;
}

export type UptimeResult =
  | { kind: "missing_key" }
  | { kind: "error"; reason: "http" | "timeout" | "invalid_response" | "api_error" }
  | { kind: "ok"; monitors: Partial<Record<MonitorRole, MonitorData>> };

export interface DayRange {
  date: string;
  start: number;
  end: number;
}

const DAY_SECONDS = 86_400;

/** One range per UTC day, oldest first, the last one ending now. */
export function dayRanges(now: Date, days: number = UPTIME_DAYS): DayRange[] {
  const nowSec = Math.floor(now.getTime() / 1000);
  const todayStart = nowSec - (nowSec % DAY_SECONDS);
  const out: DayRange[] = [];
  for (let i = days - 1; i >= 0; i--) {
    const start = todayStart - i * DAY_SECONDS;
    const end = i === 0 ? Math.max(nowSec, start + 1) : start + DAY_SECONDS - 1;
    out.push({ date: new Date(start * 1000).toISOString().slice(0, 10), start, end });
  }
  return out;
}

/** UptimeRobot monitor status codes: 0 paused, 1 not checked yet, 2 up, 8 seems down, 9 down. */
export function monitorState(code: unknown): MonitorState {
  if (code === 2) return "up";
  if (code === 8 || code === 9) return "down";
  return "unknown";
}

interface RawMonitor {
  friendly_name?: unknown;
  status?: unknown;
  create_datetime?: unknown;
  custom_uptime_ranges?: unknown;
  custom_uptime_ratio?: unknown;
}

function parsePercent(raw: string | undefined): number | null {
  if (raw === undefined || raw.trim() === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 && n <= 100 ? n : null;
}

export function parseMonitors(json: unknown, ranges: DayRange[]): UptimeResult {
  if (!json || typeof json !== "object") return { kind: "error", reason: "invalid_response" };
  const body = json as { stat?: unknown; monitors?: unknown };
  if (body.stat !== "ok") return { kind: "error", reason: "api_error" };
  if (!Array.isArray(body.monitors)) return { kind: "error", reason: "invalid_response" };

  const byName = new Map<string, MonitorRole>(
    (Object.entries(MONITOR_NAMES) as [MonitorRole, string][]).map(([role, name]) => [
      name.toLowerCase(),
      role,
    ]),
  );
  const monitors: Partial<Record<MonitorRole, MonitorData>> = {};

  for (const raw of body.monitors as RawMonitor[]) {
    if (!raw || typeof raw.friendly_name !== "string") continue;
    const role = byName.get(raw.friendly_name.trim().toLowerCase());
    if (!role) continue;
    const state = monitorState(raw.status);
    // A paused duplicate must not shadow the active monitor with the same name.
    const existing = monitors[role];
    if (existing && (existing.state !== "unknown" || state === "unknown")) continue;

    const created = typeof raw.create_datetime === "number" ? raw.create_datetime : 0;
    const values =
      typeof raw.custom_uptime_ranges === "string" ? raw.custom_uptime_ranges.split("-") : [];
    const days: UptimeDay[] = ranges.map((range, i) => ({
      date: range.date,
      // Before the monitor existed there is no data, whatever UptimeRobot returns.
      uptime: range.end < created ? null : parsePercent(values[i]),
    }));
    const ratio =
      typeof raw.custom_uptime_ratio === "string" ? parsePercent(raw.custom_uptime_ratio) : null;
    monitors[role] = { state, days, ratio };
  }
  return { kind: "ok", monitors };
}

export interface FetchDeps {
  fetch: typeof fetch;
  now: Date;
}

export async function fetchUptime(env: StatusEnv, deps: FetchDeps): Promise<UptimeResult> {
  const key = (env.UPTIMEROBOT_API_KEY || "").trim();
  if (!key) return { kind: "missing_key" };
  const base = (env.UPTIMEROBOT_API_URL || UPTIMEROBOT_DEFAULT_URL).replace(/\/+$/, "");
  const ranges = dayRanges(deps.now);

  const form = new URLSearchParams({
    api_key: key,
    format: "json",
    custom_uptime_ranges: ranges.map((r) => `${r.start}_${r.end}`).join("-"),
    custom_uptime_ratios: String(UPTIME_DAYS),
  });

  let resp: Response;
  try {
    resp = await deps.fetch(`${base}/getMonitors`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: form.toString(),
      // Deliberately no per-request "no-store" cache option: that would turn the page dynamic and call
      // UptimeRobot (10 requests/minute on the free plan) on every view. The
      // page's `revalidate = 60` bounds this to one call per minute.
      signal: AbortSignal.timeout(UPTIMEROBOT_TIMEOUT_MS),
    });
  } catch {
    return { kind: "error", reason: "timeout" };
  }
  if (!resp.ok) return { kind: "error", reason: "http" };
  let json: unknown;
  try {
    json = await resp.json();
  } catch {
    return { kind: "error", reason: "invalid_response" };
  }
  return parseMonitors(json, ranges);
}
