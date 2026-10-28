"use client";

/** Feature 6: your rates next to the spread across other Vectorae accounts.
 *
 *  Always labelled as what it is — an observation across this deployment's
 *  accounts, not an industry statistic — and silent rather than approximate
 *  when a bucket has too few accounts to publish. */

import { useQuery } from "@tanstack/react-query";

import {
  getBenchmarks,
  type BenchmarkBucket,
  type BenchmarkChannel,
  type BenchmarkMetric,
} from "@/lib/api/benchmarks";
import { AsyncState } from "@/components/ui/skeleton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const METRICS: { key: BenchmarkMetric; label: string; lowerIsBetter?: boolean }[] = [
  { key: "reply_rate", label: "Reply rate" },
  { key: "meeting_rate", label: "Meeting-booked rate" },
  { key: "bounce_rate", label: "Bounce rate", lowerIsBetter: true },
];

const pct = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`;

function bucketFor(row: BenchmarkChannel): { bucket: BenchmarkBucket; scope: string } | null {
  if (row.industry) return { bucket: row.industry, scope: row.industry.industry };
  if (row.all_industries) return { bucket: row.all_industries, scope: "all industries" };
  return null;
}

export function BenchmarkPanel({ strategyId }: { strategyId?: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["benchmarks", strategyId ?? ""],
    queryFn: () => getBenchmarks(strategyId || undefined),
  });

  return (
    <Card aria-label="Benchmarks">
      <CardHeader>
        <CardTitle className="text-sm">How you compare</CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            {data.note} Last {data.window_days} days. A benchmark appears only once at
            least {data.min_accounts} accounts qualify.
          </p>
        )}
      </CardHeader>
      <CardContent>
        <AsyncState isLoading={isLoading} error={error}
                    empty={!!data && data.channels.length === 0}
                    emptyLabel="No sends in the window yet.">
          {data && (
            <div className="space-y-4">
              {data.channels.map((row) => {
                const match = bucketFor(row);
                return (
                  <div key={row.channel} className="overflow-x-auto">
                    <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      {row.channel}
                      {match ? ` · compared with ${match.scope} (${match.bucket.accounts} accounts)` : ""}
                    </p>
                    {!match && (
                      <p className="text-sm text-muted-foreground">
                        Not enough accounts yet to publish a benchmark for this channel.
                      </p>
                    )}
                    {!row.yours.enough_data && (
                      <p className="text-sm text-muted-foreground">
                        Your {row.yours.dispatched} send(s) are below the {data.min_account_sends} needed
                        for a meaningful rate.
                      </p>
                    )}
                    <table className="w-full text-sm" aria-label={`${row.channel} benchmarks`}>
                      <thead>
                        <tr className="border-b border-border text-muted-foreground">
                          <th className="pb-2 text-left font-normal">Metric</th>
                          <th className="pb-2 text-right font-normal">You</th>
                          <th className="pb-2 text-right font-normal">Median</th>
                          <th className="pb-2 text-right font-normal">Middle half</th>
                        </tr>
                      </thead>
                      <tbody>
                        {METRICS.map((metric) => {
                          const spread = match?.bucket.metrics[metric.key];
                          return (
                            <tr key={metric.key} className="border-b border-border/40">
                              <td className="py-1.5" title={data.definitions[metric.key]}>
                                {metric.label}
                              </td>
                              <td className="py-1.5 text-right font-medium">
                                {row.yours.enough_data ? pct(row.yours[metric.key]) : "—"}
                              </td>
                              <td className="py-1.5 text-right">{pct(spread?.p50)}</td>
                              <td className="py-1.5 text-right text-muted-foreground">
                                {spread ? `${pct(spread.p25)} – ${pct(spread.p75)}` : "—"}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                );
              })}
            </div>
          )}
        </AsyncState>
      </CardContent>
    </Card>
  );
}
