/**
 * Combines the two data sources (UptimeRobot, GET /status/public) and the
 * incident files into what the page renders. buildSnapshot is pure; getSnapshot
 * does the fetching. Rules, in plain words:
 *
 *  1. Vectorae's own checks (/status/public) decide API, Background
 *     processing, AI features and Booking pages when they can be read.
 *  2. When they cannot, API, Background processing and Booking pages are
 *     DERIVED from UptimeRobot and labelled "Detail unavailable"; AI features
 *     is never derived (nothing external measures it) and shows "No data".
 *  3. Website & app comes from UptimeRobot only.
 *  4. Nothing is ever shown as Operational unless a check said so. A component
 *     no check covered is "No data", and the banner then cannot say "All
 *     systems operational".
 *  5. An unresolved incident can make a component look worse (or "Under
 *     maintenance" when it is otherwise operational), never better.
 */

import {
  COMPONENTS,
  UPTIME_SOURCES,
  type ComponentId,
  type MonitorRole,
  type StatusEnv,
} from "./config.ts";
import { fetchBackend, type BackendResult } from "./backend.ts";
import { activeIncidents, impactStatus, type Incident } from "./incidents.ts";
import { severity, worse, type DisplayStatus, type PublicStatus } from "./status.ts";
import { fetchUptime, type MonitorState, type UptimeDay, type UptimeResult } from "./uptimerobot.ts";

export const DETAIL_UNAVAILABLE = "Detail unavailable";
export const DERIVED_NOTE = "Detail unavailable — status from external monitoring";
export const STATUS_DATA_UNAVAILABLE = "Status data unavailable";

export interface ComponentView {
  id: ComponentId;
  name: string;
  description: string;
  status: DisplayStatus;
  source: "backend" | "external" | "incident" | "none";
  note: string | null;
  checkedAt: string | null;
  uptime: { days: UptimeDay[]; ratio: number | null } | null;
}

export interface Banner {
  tone: DisplayStatus;
  title: string;
}

export interface Snapshot {
  generatedAt: string;
  overall: Banner;
  dataNotice: { title: string; text: string } | null;
  components: ComponentView[];
  sources: {
    uptimeRobot: UptimeResult["kind"];
    backend: BackendResult["kind"];
  };
}

function monitorStates(uptime: UptimeResult, roles: readonly MonitorRole[]): MonitorState[] {
  if (uptime.kind !== "ok") return roles.map(() => "unknown");
  return roles.map((r) => uptime.monitors[r]?.state ?? "unknown");
}

function role(uptime: UptimeResult, r: MonitorRole): MonitorState {
  return monitorStates(uptime, [r])[0]!;
}

/** Status from a group of monitors: all up, some down, all down, else unknown. */
export function fromMonitors(states: MonitorState[]): DisplayStatus {
  if (states.length === 0) return "unknown";
  const down = states.filter((s) => s === "down").length;
  if (down > 0) return down === states.length ? "major_outage" : "partial_outage";
  return states.every((s) => s === "up") ? "operational" : "unknown";
}

function uptimeFor(uptime: UptimeResult, id: ComponentId): ComponentView["uptime"] {
  const roles = UPTIME_SOURCES[id];
  if (uptime.kind !== "ok" || roles.length === 0) return null;
  const series = roles.map((r) => uptime.monitors[r]).filter((m) => m !== undefined);
  if (series.length === 0) return null;
  const days: UptimeDay[] = series[0]!.days.map((d, i) => {
    const values = series
      .map((s) => s.days[i]?.uptime)
      .filter((v): v is number => typeof v === "number");
    return { date: d.date, uptime: values.length ? Math.min(...values) : null };
  });
  const ratios = series.map((s) => s.ratio).filter((v): v is number => typeof v === "number");
  return { days, ratio: ratios.length ? Math.min(...ratios) : null };
}

interface Measured {
  status: DisplayStatus;
  source: ComponentView["source"];
  note: string | null;
  checkedAt: string | null;
}

function measure(id: ComponentId, uptime: UptimeResult, backend: BackendResult): Measured {
  if (id === "website") {
    const status = fromMonitors(monitorStates(uptime, UPTIME_SOURCES.website));
    return { status, source: status === "unknown" ? "none" : "external", note: null, checkedAt: null };
  }

  const own = backend.kind === "ok" ? backend.components[id] : undefined;
  if (own) {
    let status: PublicStatus = own.status;
    // The public booking page itself is a Vercel page; if the external check
    // says it is down, the backend being fine does not help a prospect.
    if (id === "booking-pages" && role(uptime, "booking") === "down") status = worse(status, "major_outage");
    return { status, source: "backend", note: null, checkedAt: own.checkedAt };
  }

  const unknown: Measured = { status: "unknown", source: "none", note: DETAIL_UNAVAILABLE, checkedAt: null };
  const derived = (status: PublicStatus): Measured => ({
    status,
    source: "external",
    note: DERIVED_NOTE,
    checkedAt: null,
  });
  const apiHttp = role(uptime, "apiHttp");

  switch (id) {
    case "api":
      if (apiHttp === "down") return derived("major_outage");
      if (apiHttp === "up") return derived("operational");
      return unknown;
    case "background-processing": {
      // The keyword monitor matches /health's healthy body, which requires a
      // fresh Celery heartbeat. /health answering but not healthy can only be
      // the heartbeat (a DB or Redis failure makes /health answer 503, which
      // the plain API monitor sees as down). Severity cannot be told apart
      // from outside, so the worse reading is shown, never the better one.
      const keyword = role(uptime, "apiKeyword");
      if (keyword === "up") return derived("operational");
      if (role(uptime, "worker") === "down") return derived("major_outage");
      if (keyword === "down" && apiHttp === "up") return derived("major_outage");
      return unknown;
    }
    case "booking-pages":
      // Booking needs the API; the page itself is on Vercel.
      if (apiHttp === "down" || role(uptime, "booking") === "down") return derived("major_outage");
      return unknown;
    case "ai-features":
    default:
      return unknown;
  }
}

function applyIncidents(id: ComponentId, measured: Measured, active: Incident[]): Measured {
  let floor: PublicStatus | null = null;
  for (const incident of active) {
    if (!incident.components.includes(id)) continue;
    const s = impactStatus(incident.impact);
    if (s) floor = floor ? worse(floor, s) : s;
  }
  if (!floor) return measured;
  if (measured.status === "unknown") {
    return { ...measured, status: floor, source: "incident" };
  }
  if (floor === "maintenance") {
    return measured.status === "operational" ? { ...measured, status: "maintenance", source: "incident" } : measured;
  }
  const status = worse(measured.status, floor);
  return status === measured.status ? measured : { ...measured, status, source: "incident" };
}

export function overallBanner(statuses: DisplayStatus[]): Banner {
  const known = statuses.filter((s): s is PublicStatus => s !== "unknown");
  if (known.length === 0) return { tone: "unknown", title: "Current status unknown" };
  const worst = known.reduce((a, b) => (severity(b) > severity(a) ? b : a));
  switch (worst) {
    case "major_outage":
      return { tone: worst, title: "Major outage" };
    case "partial_outage":
      return { tone: worst, title: "Partial outage" };
    case "degraded":
      return { tone: worst, title: "Degraded performance" };
    case "maintenance":
      return { tone: worst, title: "Maintenance in progress" };
    case "operational":
      return known.length === statuses.length
        ? { tone: "operational", title: "All systems operational" }
        : { tone: "unknown", title: "Some systems could not be checked" };
  }
}

function dataNotice(uptime: UptimeResult, backend: BackendResult): Snapshot["dataNotice"] {
  const ownOk = backend.kind === "ok";
  if (uptime.kind !== "ok") {
    const why =
      uptime.kind === "missing_key"
        ? "External monitoring is not configured for this page"
        : "External monitoring data could not be loaded just now";
    return {
      title: STATUS_DATA_UNAVAILABLE,
      text: ownOk
        ? `${why}, so uptime history is missing and the Website & app component cannot be checked. The other components show Vectorae's own checks.`
        : `${why}, and Vectorae's own checks could not be reached either. Component status is unknown until one of them answers.`,
    };
  }
  if (!ownOk) {
    return {
      title: DETAIL_UNAVAILABLE,
      text: "Vectorae's own component checks could not be reached. Statuses below come from external monitoring where it covers a component; the rest show No data.",
    };
  }
  return null;
}

export function buildSnapshot(input: {
  uptime: UptimeResult;
  backend: BackendResult;
  incidents: Incident[];
  now: Date;
}): Snapshot {
  const active = activeIncidents(input.incidents);
  const components: ComponentView[] = COMPONENTS.map((def) => {
    const m = applyIncidents(def.id, measure(def.id, input.uptime, input.backend), active);
    return {
      id: def.id,
      name: def.name,
      description: def.description,
      status: m.status,
      source: m.source,
      note: m.note,
      checkedAt: m.checkedAt,
      uptime: uptimeFor(input.uptime, def.id),
    };
  });
  return {
    generatedAt: input.now.toISOString().replace(/\.\d{3}Z$/, "Z"),
    overall: overallBanner(components.map((c) => c.status)),
    dataNotice: dataNotice(input.uptime, input.backend),
    components,
    sources: { uptimeRobot: input.uptime.kind, backend: input.backend.kind },
  };
}

export async function getSnapshot(
  env: StatusEnv,
  deps: { fetch: typeof fetch; now: Date; incidents: Incident[] },
): Promise<Snapshot> {
  const [uptime, backend] = await Promise.all([
    fetchUptime(env, { fetch: deps.fetch, now: deps.now }),
    fetchBackend(env, { fetch: deps.fetch }),
  ]);
  return buildSnapshot({ uptime, backend, incidents: deps.incidents, now: deps.now });
}
