"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listStrategies } from "@/lib/api/strategies";
import { useAnalytics } from "@/lib/api/hooks";
import type { AnalyticsPoint } from "@/lib/api/types";
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip,
  Legend, ResponsiveContainer,
} from "recharts";
import { AsyncState } from "@/components/ui/skeleton";
import { BenchmarkPanel } from "@/components/analytics/BenchmarkPanel";
import { ReplyQualityPanel } from "@/components/analytics/ReplyQualityPanel";
import { CompletionPanel } from "@/components/analytics/CompletionPanel";
import { AttributionPanel } from "@/components/analytics/AttributionPanel";
import { MultiVariateResults } from "@/components/strategies/MultiVariateResults";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { historyLabel } from "@/lib/analyticsHistory";
import { Badge } from "@/components/ui/badge";

// CSS-variable-aware recharts theme: reads the current primary/accent
// computed values at render time so every preset recolors charts instantly.
function cssVar(name: string): string {
  if (typeof window === "undefined") return "#000";
  return `rgb(${getComputedStyle(document.documentElement)
    .getPropertyValue(name).trim()})`;
}

function buildSeries(data: AnalyticsPoint[], event: string) {
  // Group by bucket then pivot channels into columns.
  const buckets: Record<string, Record<string, number>> = {};
  for (const p of data.filter(d => d.event === event)) {
    buckets[p.bucket] = buckets[p.bucket] ?? {};
    buckets[p.bucket][p.channel] = p.count;
  }
  return Object.entries(buckets)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([bucket, v]) => ({ bucket, email: v.email ?? 0, whatsapp: v.whatsapp ?? 0 }));
}

export default function AnalyticsPage() {
  const [strategyId, setStrategyId] = useState("");
  const [granularity, setGranularity] = useState("day");
  const { data: strats } = useQuery({ queryKey: ["strategies"], queryFn: listStrategies });
  const effectiveId = strategyId || (strats?.[0]?.id ?? "");
  const { data, isLoading, error } = useAnalytics(effectiveId, granularity);

  // Product defect #18 / review L5: the series covers only the plan's window.
  const historyWindow = historyLabel(data?.history_days);
  const replySeries = data ? buildSeries(data.series, "replied") : [];
  const sentSeries  = data ? buildSeries(data.series, "sent")    : [];
  const variants    = data?.variants ?? {};

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">Analytics</h1>
        <select className="rounded border border-border bg-card px-2 py-1 text-sm"
                value={effectiveId}
                onChange={e => setStrategyId(e.target.value)}
                aria-label="Strategy">
          <option value="">All strategies</option>
          {strats?.map(s => (
            <option key={s.id} value={s.id}>
              {(s as unknown as { product_name?: string }).product_name ?? s.id.slice(0, 8)}
            </option>
          ))}
        </select>
        <select className="rounded border border-border bg-card px-2 py-1 text-sm"
                value={granularity}
                onChange={e => setGranularity(e.target.value)}
                aria-label="Granularity">
          <option value="day">Daily</option>
          <option value="week">Weekly</option>
          <option value="month">Monthly</option>
        </select>
        {/* Feature Group 3 */}
        <Link href="/analytics/revenue"
              className="ml-auto text-sm font-medium text-[rgb(var(--primary))] hover:underline">
          Revenue &amp; ROI →
        </Link>
        {/* F07 Spend Guard */}
        <Link href="/analytics/usage"
              className="text-sm font-medium text-[rgb(var(--primary))] hover:underline">
          Usage &amp; spend →
        </Link>
      </div>

      <AsyncState isLoading={isLoading} error={error}
                  empty={!data || data.series.length === 0}
                  emptyLabel="No outcome data yet — send your first campaign.">

        {/* Part 1 Feature 1: positive reply rate beside the raw reply rate. */}
        <ReplyQualityPanel strategyId={effectiveId || undefined} />

        {/* Part 1 Feature 3: did enrolled prospects get the whole sequence? */}
        <CompletionPanel strategyId={effectiveId || undefined} />

        {/* Part 1 Feature 8: which touch earned each outcome, and how sure. */}
        <AttributionPanel strategyId={effectiveId || undefined} />

        <Card aria-label="Volume over time">
          <CardHeader><CardTitle className="text-sm">Messages sent over time{historyWindow ? ` (${historyWindow})` : ""}</CardTitle></CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={sentSeries}>
                <XAxis dataKey="bucket" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Legend />
                <Line type="monotone" dataKey="email" name="Email"
                      stroke={cssVar("--primary")} dot={false} />
                <Line type="monotone" dataKey="whatsapp" name="WhatsApp"
                      stroke={cssVar("--accent")} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card aria-label="Replies over time">
          <CardHeader><CardTitle className="text-sm">Replies over time{historyWindow ? ` (${historyWindow})` : ""}</CardTitle></CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={replySeries}>
                <XAxis dataKey="bucket" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Legend />
                <Bar dataKey="email" name="Email" fill={cssVar("--primary")} />
                <Bar dataKey="whatsapp" name="WhatsApp" fill={cssVar("--accent")} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        {/* Variant comparison — groundwork for M8 A/B */}
        {Object.keys(variants).length > 0 && (
          <Card aria-label="Variant comparison">
            <CardHeader>
              <CardTitle className="text-sm">Variant comparison (A/B groundwork)</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm" aria-label="A/B variant comparison">
                  <thead>
                    <tr className="border-b border-border text-muted-foreground">
                      <th className="pb-2 text-left">Variant</th>
                      <th className="pb-2 text-right">Sent</th>
                      <th className="pb-2 text-right">Replied</th>
                      <th className="pb-2 text-right">Booked</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(variants).map(([v, counts]) => (
                      <tr key={v} className="border-b border-border/40">
                        <td className="py-1.5 font-medium">{v}</td>
                        <td className="py-1.5 text-right">{counts.sent ?? 0}</td>
                        <td className="py-1.5 text-right">{counts.replied ?? 0}</td>
                        <td className="py-1.5 text-right">{counts.meeting_booked ?? 0}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Multi-variate (3+ variant) results, ranked with significance. The
            plan gate is the endpoint's: without multi_variate it answers 402
            and the panel says which plan includes it. */}
        {effectiveId && (
          <Card aria-label="Multi-variate test results">
            <CardHeader>
              <CardTitle className="text-sm">Multi-variate test results</CardTitle>
            </CardHeader>
            <CardContent>
              <MultiVariateResults strategyId={effectiveId} />
            </CardContent>
          </Card>
        )}

        {/* Feature 6: anonymised benchmarks across Vectorae accounts. */}
        <BenchmarkPanel strategyId={effectiveId || undefined} />

        {/* M8 learning insights placeholder */}
        <Card className="border-dashed">
          <CardContent className="p-gutter">
            <div className="flex items-center gap-2">
              <Badge>Coming in M8</Badge>
              <span className="text-sm font-medium">Learning insights</span>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              Playbook scores, winning patterns, and A/B auto-promotion will
              appear here once the nightly learning loop is running.
            </p>
          </CardContent>
        </Card>
      </AsyncState>
    </div>
  );
}
