"use client";

/**
 * Live part of the public status page (/status, src/app/status/page.tsx).
 *
 * Runs in the visitor's browser: GET ${NEXT_PUBLIC_API_URL}/status/public on
 * load and every 30 s while the tab is visible (paused when hidden, checked
 * again on return if the interval has passed), 8 s timeout. All decisions are
 * in src/lib/statusBoard.ts; this file only schedules and renders.
 *
 * Accessibility: the summary is a polite live region; every state has a text
 * label and its own icon shape, so nothing depends on colour alone.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  HelpCircle,
  MinusCircle,
  RefreshCw,
  WifiOff,
  Wrench,
  XOctagon,
  type LucideIcon,
} from "lucide-react";

import {
  COMPONENT_STATUS_LABELS,
  OVERALL_HEADLINES,
  STATUS_COLORS,
  UNREACHABLE_DETAILS,
  applyIncidents,
  deriveOverall,
  fetchPublicStatus,
  msUntilNextCheck,
  nextRefreshDelay,
  overallColor,
  reduceStatus,
  statusEndpoint,
  REFRESH_INTERVAL_MS,
  type ComponentStatus,
  type FetchDeps,
  type OverallState,
  type StatusView,
} from "@/lib/statusBoard";
import {
  INCIDENTS,
  INCIDENT_UPDATE_LABELS,
  isResolved,
  sortIncidents,
  type Incident,
} from "@/lib/statusIncidents";

const STATUS_ICONS: Record<ComponentStatus, LucideIcon> = {
  operational: CheckCircle2,
  degraded: AlertTriangle,
  partial_outage: MinusCircle,
  major_outage: XOctagon,
  maintenance: Wrench,
  unknown: HelpCircle,
};

function overallIcon(state: OverallState): LucideIcon {
  if (state === "unreachable") return WifiOff;
  if (state === "unconfigured") return HelpCircle;
  return STATUS_ICONS[state];
}

function formatLocalTime(d: Date): string {
  return d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  });
}

/** Deterministic (UTC) so the statically exported HTML matches hydration. */
function formatUtc(iso: string): string {
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
}

export interface StatusBoardProps {
  endpoint?: string | null;
  incidents?: readonly Incident[];
  fetchImpl?: FetchDeps["fetch"];
}

export function StatusBoard({
  endpoint = statusEndpoint(process.env.NEXT_PUBLIC_API_URL),
  incidents = INCIDENTS,
  fetchImpl,
}: StatusBoardProps) {
  const [view, setView] = useState<StatusView | null>(null);
  const [checking, setChecking] = useState(false);
  const checkRef = useRef<() => void>(() => {});

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let inFlight = false;
    let lastAttemptAt: Date | null = null;
    let delay: number | null = REFRESH_INTERVAL_MS;

    const schedule = () => {
      if (timer) clearTimeout(timer);
      timer = null;
      const ms = msUntilNextCheck({
        hidden: document.visibilityState === "hidden",
        lastAttemptAt,
        delay,
        now: new Date(),
      });
      if (ms !== null) timer = setTimeout(check, ms);
    };

    const check = async () => {
      if (inFlight || !alive) return;
      inFlight = true;
      setChecking(true);
      const result = await fetchPublicStatus(endpoint, { fetch: fetchImpl });
      inFlight = false;
      if (!alive) return;
      const now = new Date();
      lastAttemptAt = now;
      delay = nextRefreshDelay(result);
      setView((prev) => reduceStatus(prev, result, now));
      setChecking(false);
      schedule();
    };

    checkRef.current = () => void check();
    document.addEventListener("visibilitychange", schedule);
    void check();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", schedule);
    };
  }, [endpoint, fetchImpl]);

  const sorted = useMemo(() => sortIncidents(incidents), [incidents]);
  const active = sorted.filter((i) => !isResolved(i));
  const past = sorted.filter((i) => isResolved(i));

  const components = useMemo(
    () => (view ? applyIncidents(view.components, incidents) : []),
    [view, incidents],
  );
  const overall: OverallState | null = view
    ? view.overall === "unreachable" || view.overall === "unconfigured"
      ? view.overall
      : deriveOverall(components)
    : null;

  const onCheckNow = useCallback(() => checkRef.current(), []);

  const OverallIcon = overall ? overallIcon(overall) : RefreshCw;

  return (
    <div className="space-y-10" data-testid="status-board">
      {/* Summary */}
      <section aria-labelledby="status-summary-heading">
        <h2 id="status-summary-heading" className="sr-only">
          Current status
        </h2>
        <div className="rounded-xl border border-white/10 bg-white/[0.03] p-5 sm:p-6">
          <div role="status" aria-live="polite" aria-atomic="true">
            <p className="flex items-start gap-3 text-xl font-semibold sm:text-2xl">
              <OverallIcon
                aria-hidden="true"
                className={overall ? "mt-1 h-6 w-6 shrink-0" : "mt-1 h-6 w-6 shrink-0 animate-spin text-gray-400"}
                style={overall ? { color: overallColor(overall) } : undefined}
              />
              <span data-testid="status-overall" data-state={overall ?? "checking"}>
                {overall ? OVERALL_HEADLINES[overall] : "Checking status…"}
              </span>
            </p>
            {view?.reason && (
              <p className="mt-3 text-sm text-gray-300" data-testid="status-reason">
                {UNREACHABLE_DETAILS[view.reason]} Campaign sending, replies and booking may be affected.
                This page checks again automatically.
              </p>
            )}
            {view?.notice === "rate_limited" && (
              <p className="mt-3 text-sm text-gray-300" data-testid="status-rate-limited">
                Too many status checks from your network right now, so the API asked us to slow down.
                {view.dataFrom
                  ? ` Showing the result from ${formatLocalTime(view.dataFrom)}.`
                  : " No result to show yet."}{" "}
                Checking again in 2 minutes.
              </p>
            )}
            {view?.overall === "unconfigured" && (
              <p className="mt-3 text-sm text-gray-300">
                This copy of the site was built without an API address, so it cannot check live status.
              </p>
            )}
          </div>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm text-gray-400">
            <p data-testid="status-last-checked">
              {view ? (
                <>
                  Last checked{" "}
                  <time dateTime={view.checkedAt.toISOString()}>{formatLocalTime(view.checkedAt)}</time>
                  {" · "}refreshes every 30 seconds while this tab is open
                </>
              ) : (
                "Contacting the Vectorae API…"
              )}
            </p>
            <button
              type="button"
              onClick={onCheckNow}
              disabled={checking || view?.overall === "unconfigured"}
              className="inline-flex items-center gap-2 rounded-md border border-white/20 px-3 py-1.5 text-sm text-white hover:bg-white/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <RefreshCw aria-hidden="true" className={checking ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
              {checking ? "Checking…" : "Check now"}
            </button>
          </div>
        </div>
        <noscript>
          <p className="mt-4 text-sm text-gray-300">
            This page checks Vectorae&apos;s live status with JavaScript. Turn JavaScript on to see it.
          </p>
        </noscript>
      </section>

      {/* Components */}
      <section aria-labelledby="status-components-heading">
        <h2 id="status-components-heading" className="text-lg font-semibold">
          Components
        </h2>
        <ul className="mt-4 divide-y divide-white/10 rounded-xl border border-white/10" data-testid="status-components">
          {(view ? components : []).map((c) => {
            const Icon = STATUS_ICONS[c.status];
            return (
              <li
                key={c.id}
                className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-3 sm:px-5"
                data-testid={`status-component-${c.id}`}
                data-status={c.status}
              >
                <span className="font-medium text-white">{c.name}</span>
                <span className="flex items-center gap-2 text-sm" style={{ color: STATUS_COLORS[c.status] }}>
                  <Icon aria-hidden="true" className="h-4 w-4 shrink-0" />
                  {COMPONENT_STATUS_LABELS[c.status]}
                </span>
              </li>
            );
          })}
          {!view && <li className="px-4 py-3 text-sm text-gray-400 sm:px-5">Checking…</li>}
        </ul>
        {view?.overall === "unreachable" && (
          <p className="mt-3 text-sm text-gray-400">
            &ldquo;No data&rdquo; means the component could not be checked because the API did not answer,
            not that it is working.
          </p>
        )}
      </section>

      {/* Incidents */}
      {active.length > 0 && (
        <section aria-labelledby="status-active-heading">
          <h2 id="status-active-heading" className="text-lg font-semibold">
            Active incidents
          </h2>
          <ul className="mt-4 space-y-4">{active.map((i) => <IncidentItem key={i.id} incident={i} />)}</ul>
        </section>
      )}
      <section aria-labelledby="status-past-heading">
        <h2 id="status-past-heading" className="text-lg font-semibold">
          Past incidents
        </h2>
        {past.length === 0 ? (
          <p className="mt-4 text-sm text-gray-400" data-testid="status-no-incidents">
            No incidents reported.
          </p>
        ) : (
          <ul className="mt-4 space-y-4">{past.map((i) => <IncidentItem key={i.id} incident={i} />)}</ul>
        )}
      </section>
    </div>
  );
}

function IncidentItem({ incident }: { incident: Incident }) {
  const updates = [...incident.updates].reverse(); // newest first
  return (
    <li className="rounded-xl border border-white/10 bg-white/[0.03] p-4 sm:p-5" data-testid={`incident-${incident.id}`}>
      <h3 className="font-semibold text-white">{incident.title}</h3>
      <ol className="mt-3 space-y-3">
        {updates.map((u) => (
          <li key={`${u.at}-${u.status}`} className="text-sm">
            <p className="text-gray-400">
              <span className="font-medium text-gray-200">{INCIDENT_UPDATE_LABELS[u.status]}</span>
              {" · "}
              <time dateTime={u.at}>{formatUtc(u.at)}</time>
            </p>
            <p className="mt-1 text-gray-300">{u.text}</p>
          </li>
        ))}
      </ol>
    </li>
  );
}
