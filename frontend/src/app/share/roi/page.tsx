"use client";

/** Feature A6 — the PUBLIC, read-only ROI dashboard: `/share/roi?token=…`.
 *
 * No login. Outside the (app) route group, like /book, so it gets the root
 * providers and none of the Shell's session guard or navigation. A query
 * parameter, not a path segment, for the static-export reason /book documents.
 * The token is a credential: it is never logged here and the API answers
 * no-store + noindex. */

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { ApiError } from "@/lib/api/client";
import { getPublicDashboard } from "@/lib/api/shareLinks";
import { formatAmount, readToken, stageScale, weeklyMeetings } from "@/lib/shareLinks";
import { LogoMark } from "@/components/ui/Logo";

export default function SharedRoiPage() {
  return (
    <Suspense fallback={<p className="p-6 text-sm text-muted-foreground">Loading…</p>}>
      <Dashboard />
    </Suspense>
  );
}

function Dashboard() {
  const params = useSearchParams();
  const token = readToken(`?${params.toString()}`);
  const query = useQuery({
    queryKey: ["public-roi", token],
    queryFn: () => getPublicDashboard(token as string),
    enabled: !!token,
    retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
  });

  if (!token || query.isError) {
    const message = query.error instanceof ApiError && query.error.status === 429
      ? "Too many requests. Please try again in a few minutes."
      : "This dashboard link is invalid, has expired or was revoked. Ask the sender for a new one.";
    return <main className="mx-auto max-w-lg p-8 text-center text-muted-foreground">{message}</main>;
  }
  if (query.isLoading || !query.data) {
    return <main className="p-8 text-center text-sm text-muted-foreground">Loading dashboard…</main>;
  }

  const d = query.data;
  const weekly = weeklyMeetings(d.trend);
  const scale = stageScale(d.pipeline);
  const tiles = [
    { label: "Meetings booked", value: String(d.totals.meetings_booked) },
    { label: "Revenue attributed", value: formatAmount(d.totals.revenue_attributed, d.currency) },
    { label: "Pipeline value", value: formatAmount(d.totals.pipeline_value, d.currency),
      note: "open + won, as of today" },
    { label: "Messages sent", value: d.totals.messages_sent.toLocaleString("en-US") },
    { label: "Reply rate", value: `${d.totals.reply_rate}%` },
    { label: "Hours saved", value: `${d.totals.time_saved_hours}h` },
  ];

  return (
    <main className="min-h-dvh bg-background">
      <div className="mx-auto max-w-5xl space-y-6 p-4 md:p-8">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            {d.brand?.logo_url
              ? <img src={d.brand.logo_url} alt="" className="h-8 w-auto" />
              : <LogoMark size={28} />}
            <div>
              <h1 className="text-xl font-semibold">{d.title}</h1>
              <p className="text-sm text-muted-foreground">
                {d.brand?.name ? `${d.brand.name} · ` : ""}
                {d.scope.type === "campaign" ? d.scope.campaign_name : `${d.scope.campaigns} campaigns`}
                {" · "}{d.period.date_from} to {d.period.date_to}
              </p>
            </div>
          </div>
          <span className="rounded border border-border px-2 py-1 text-xs text-muted-foreground">
            Read-only · link expires {new Date(d.expires_at).toLocaleDateString()}
          </span>
        </header>

        <section aria-label="Results" className="grid grid-cols-2 gap-3 md:grid-cols-3">
          {tiles.map((tile) => (
            <div key={tile.label} className="rounded-lg border border-border bg-card p-4">
              <p className="text-2xl font-bold tabular-nums">{tile.value}</p>
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{tile.label}</p>
              {tile.note && <p className="text-xs text-muted-foreground">{tile.note}</p>}
            </div>
          ))}
        </section>

        <div className="grid gap-4 md:grid-cols-2">
          <section aria-label="Pipeline" className="rounded-lg border border-border bg-card p-4">
            <h2 className="mb-3 text-sm font-semibold">Pipeline</h2>
            <ul className="space-y-2">
              {d.pipeline.map((stage) => (
                <li key={stage.key} className="text-sm">
                  <div className="flex justify-between">
                    <span>{stage.label}</span><span className="tabular-nums">{stage.count}</span>
                  </div>
                  <div className="mt-1 h-2 rounded bg-muted" aria-hidden="true">
                    <div className="h-2 rounded bg-[rgb(var(--primary))]"
                         style={{ width: `${(stage.count / scale) * 100}%` }} />
                  </div>
                </li>
              ))}
            </ul>
          </section>

          <section aria-label="Meetings per week" className="rounded-lg border border-border bg-card p-4">
            <h2 className="mb-3 text-sm font-semibold">Meetings booked per week</h2>
            {weekly.length ? (
              <div className="h-48">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={weekly}>
                    <XAxis dataKey="week" tick={{ fontSize: 11 }} />
                    <YAxis allowDecimals={false} tick={{ fontSize: 11 }} width={28} />
                    <Tooltip />
                    <Bar dataKey="meetings" fill="rgb(var(--primary))" radius={[3, 3, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No daily results recorded yet.</p>
            )}
          </section>
        </div>

        <footer className="text-center text-xs text-muted-foreground">
          Generated {new Date(d.generated_at).toLocaleString()} · Powered by Vectorae
        </footer>
      </div>
    </main>
  );
}
