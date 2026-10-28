"use client";

/** Feature Group 3: Revenue & ROI.
 *
 *  Revenue per campaign (won deals), what it cost (recorded costs, metered
 *  model spend, AI-call minutes, and account-wide costs spread by sends),
 *  and the two numbers a founder actually steers by: cost per booked meeting
 *  and cost per closed deal. */

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { listStrategies } from "@/lib/api/strategies";
import {
  createCost, deleteCost, getRevenue, listCosts,
  type CostCategory, type CostInput, type RevenueReport,
} from "@/lib/api/revenue";
import { MeetingsLedgerPanel } from "@/components/analytics/MeetingsLedgerPanel";
import { AsyncState } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { Modal } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { StatTile, formatCount, useChartPalette } from "@/components/crm/dashboard/primitives";
import { formatMoney, formatRoi, monthLabel, presetRange, type RangePreset } from "@/lib/revenue";
import { cn } from "@/lib/utils";

const PRESETS: { key: RangePreset; label: string }[] = [
  { key: "30d", label: "30 days" },
  { key: "90d", label: "90 days" },
  { key: "12m", label: "12 months" },
  { key: "ytd", label: "Year to date" },
];

const CATEGORIES: { key: CostCategory; label: string }[] = [
  { key: "data", label: "Data (Apollo, enrichment)" },
  { key: "tools", label: "Tools & subscriptions" },
  { key: "ai", label: "AI (not metered)" },
  { key: "time", label: "Time" },
  { key: "ads", label: "Ads" },
  { key: "other", label: "Other" },
];

const selectClass = "w-full rounded border border-border bg-card px-2 py-1.5 text-sm";

function compactMoney(units: number, currency: string): string {
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency", currency, notation: "compact", maximumFractionDigits: 1,
    }).format(units);
  } catch {
    return String(units);
  }
}

function useTooltipStyle() {
  const palette = useChartPalette();
  return {
    background: "rgb(var(--card))",
    border: `1px solid ${palette.border}`,
    borderRadius: 6,
    fontSize: 12,
  };
}

export default function RevenuePage() {
  const [preset, setPreset] = useState<RangePreset>("90d");
  const range = presetRange(preset);
  const { data, isLoading, error } = useQuery({
    queryKey: ["revenue", range.from, range.to],
    queryFn: () => getRevenue(range.from, range.to),
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <div>
          <Link href="/analytics" className="text-xs text-muted-foreground hover:underline">
            ← Analytics
          </Link>
          <h1 className="text-xl font-semibold">Revenue &amp; ROI</h1>
        </div>
        <div className="ml-auto flex flex-wrap gap-1" role="group" aria-label="Period">
          {PRESETS.map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => setPreset(p.key)}
              aria-pressed={preset === p.key}
              className={cn(
                "rounded border px-2.5 py-1 text-xs font-medium transition-colors",
                preset === p.key
                  ? "border-[rgb(var(--primary))] bg-[rgb(var(--primary))] text-[rgb(var(--primary-foreground))]"
                  : "border-border bg-card text-muted-foreground hover:text-foreground",
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      <AsyncState isLoading={isLoading} error={error}>
        {data && <RevenueBody report={data} />}
      </AsyncState>

      {/* F08: cost per qualified conversation / booked / held meeting. */}
      <MeetingsLedgerPanel dateFrom={range.from} dateTo={range.to} />
      {/* F09: the same numbers, judged against criteria set on day 1. */}
      <p className="text-xs text-muted-foreground">
        Evaluating Vectorae? <Link className="underline" href="/pilot">Run a 30-day pilot</Link>{" "}
        with success criteria you set up front.
      </p>

      <CostsCard currency={data?.currency ?? "USD"} />
    </div>
  );
}

function RevenueBody({ report }: { report: RevenueReport }) {
  const palette = useChartPalette();
  const tooltipStyle = useTooltipStyle();
  const { totals, currency, notes } = report;
  const money = (cents: number | null) => formatMoney(cents, currency);
  const campaignBars = report.campaigns
    .filter((c) => c.revenue_cents || c.total_cost_cents)
    .map((c) => ({ name: c.name, revenue: c.revenue_cents / 100, cost: c.total_cost_cents / 100 }));
  const monthly = report.monthly.map((m) => ({
    month: monthLabel(m.month), revenue: m.revenue_cents / 100, cost: m.cost_cents / 100,
  }));
  const hasMonthly = monthly.some((m) => m.revenue || m.cost);

  return (
    <div className="space-y-6">
      {(notes.excluded_deals > 0 || notes.excluded_costs > 0 || notes.usd_costs_excluded) && (
        <div role="note" className="rounded border border-warning bg-card p-3 text-sm text-muted-foreground">
          Reporting in <span className="font-medium text-foreground">{currency}</span>.
          {notes.excluded_deals > 0 && ` ${notes.excluded_deals} won deal(s) in other currencies are not included.`}
          {notes.excluded_costs > 0 && ` ${notes.excluded_costs} cost(s) in other currencies are not included.`}
          {notes.usd_costs_excluded &&
            ` Model and call costs are priced in USD (${formatMoney(notes.api_cost_usd_cents + notes.voice_cost_usd_cents, "USD")}) and are not included.`}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatTile label="Revenue" value={money(totals.revenue_cents)}
                  sub={`${totals.deals_won} deal${totals.deals_won === 1 ? "" : "s"} won`} />
        <StatTile label="Total cost" value={money(totals.cost_cents)}
                  sub={`${formatCount(totals.sends)} messages sent`} />
        <StatTile label="Cost / meeting" value={money(totals.cost_per_meeting_cents)}
                  sub={`${totals.meetings} meeting${totals.meetings === 1 ? "" : "s"} booked`} />
        <StatTile label="Cost / closed deal" value={money(totals.cost_per_deal_cents)} />
        <StatTile label="ROI" value={formatRoi(totals.roi)}
                  tone={totals.roi === null ? "default" : totals.roi >= 0 ? "positive" : "danger"}
                  sub="(revenue − cost) ÷ cost" />
      </div>
      <p className="-mt-3 text-xs text-muted-foreground">
        Open pipeline {money(totals.open_pipeline_cents)}
        {totals.unattributed_revenue_cents > 0 &&
          ` · ${money(totals.unattributed_revenue_cents)} revenue not linked to a campaign`}
        {totals.unallocated_cost_cents > 0 &&
          ` · ${money(totals.unallocated_cost_cents)} account-wide cost not allocated (no sends in period)`}
      </p>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card aria-label="Revenue and cost by campaign">
          <CardHeader><CardTitle className="text-sm">Revenue vs cost by campaign</CardTitle></CardHeader>
          <CardContent>
            {campaignBars.length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">No revenue or cost in this period.</p>
            ) : (
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={campaignBars} barGap={2} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke={palette.border} strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="name" tick={{ fontSize: 11, fill: palette.muted }} stroke={palette.border}
                         interval={0} tickFormatter={(v: string) => (v.length > 14 ? `${v.slice(0, 13)}…` : v)} />
                  <YAxis tick={{ fontSize: 11, fill: palette.muted }} stroke={palette.border} width={56}
                         tickFormatter={(v: number) => compactMoney(v, currency)} />
                  <Tooltip contentStyle={tooltipStyle} cursor={{ fill: palette.border, opacity: 0.3 }}
                           formatter={(v) => formatMoney(Math.round(Number(v) * 100), currency)} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="revenue" name="Revenue" fill={palette.primary} radius={[4, 4, 0, 0]} maxBarSize={28} />
                  <Bar dataKey="cost" name="Cost" fill={palette.muted} radius={[4, 4, 0, 0]} maxBarSize={28} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>

        <Card aria-label="Monthly revenue and cost">
          <CardHeader><CardTitle className="text-sm">Revenue and cost by month</CardTitle></CardHeader>
          <CardContent>
            {!hasMonthly ? (
              <p className="py-10 text-center text-sm text-muted-foreground">Nothing recorded in this period yet.</p>
            ) : (
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={monthly} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke={palette.border} strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="month" tick={{ fontSize: 11, fill: palette.muted }} stroke={palette.border} />
                  <YAxis tick={{ fontSize: 11, fill: palette.muted }} stroke={palette.border} width={56}
                         tickFormatter={(v: number) => compactMoney(v, currency)} />
                  <Tooltip contentStyle={tooltipStyle}
                           formatter={(v) => formatMoney(Math.round(Number(v) * 100), currency)} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Line type="monotone" dataKey="revenue" name="Revenue" stroke={palette.primary}
                        strokeWidth={2} dot={{ r: 4 }} />
                  <Line type="monotone" dataKey="cost" name="Cost" stroke={palette.muted}
                        strokeWidth={2} strokeDasharray="5 4" dot={{ r: 4 }} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle className="text-sm">By campaign</CardTitle></CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-sm" aria-label="Revenue and cost by campaign">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th className="py-1.5 font-medium">Campaign</th>
                  <th className="text-right font-medium">Sends</th>
                  <th className="text-right font-medium">Meetings</th>
                  <th className="text-right font-medium">Won</th>
                  <th className="text-right font-medium">Revenue</th>
                  <th className="text-right font-medium">Cost</th>
                  <th className="text-right font-medium">Cost / meeting</th>
                  <th className="text-right font-medium">Cost / deal</th>
                  <th className="text-right font-medium">ROI</th>
                </tr>
              </thead>
              <tbody>
                {report.campaigns.map((c) => (
                  <tr key={c.strategy_id} className="border-b border-border/50 tabular-nums">
                    <td className="py-1.5">
                      <Link href={`/campaigns?strategy=${c.strategy_id}`} className="font-medium hover:underline">
                        {c.name}
                      </Link>
                    </td>
                    <td className="text-right">{formatCount(c.sends)}</td>
                    <td className="text-right">{c.meetings}</td>
                    <td className="text-right">{c.deals_won}</td>
                    <td className="text-right">{money(c.revenue_cents)}</td>
                    <td className="text-right"
                        title={`Recorded ${money(c.direct_cost_cents)} · AI ${money(c.api_cost_cents)} · ` +
                               `Calls ${money(c.voice_cost_cents)} · Shared ${money(c.allocated_cost_cents)}`}>
                      {money(c.total_cost_cents)}
                    </td>
                    <td className="text-right">{money(c.cost_per_meeting_cents)}</td>
                    <td className="text-right">{money(c.cost_per_deal_cents)}</td>
                    <td className={cn("text-right",
                                      c.roi !== null && c.roi < 0 && "text-[rgb(var(--destructive))]")}>
                      {formatRoi(c.roi)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[11px] text-muted-foreground">
            Cost = costs recorded against the campaign + metered AI usage + AI-call minutes +
            a share of account-wide costs proportional to the campaign&apos;s sends. AI and call
            costs are estimates from the prices in Admin › System Settings.
          </p>
        </CardContent>
      </Card>

      {report.api_usage.length > 0 && (
        <Card>
          <CardHeader><CardTitle className="text-sm">Metered AI usage</CardTitle></CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[520px] text-sm" aria-label="Metered AI usage">
                <thead>
                  <tr className="border-b border-border text-left text-xs text-muted-foreground">
                    <th className="py-1.5 font-medium">Provider</th>
                    <th className="font-medium">Used for</th>
                    <th className="text-right font-medium">Calls</th>
                    <th className="text-right font-medium">Tokens in / out</th>
                    <th className="text-right font-medium">Est. cost (USD)</th>
                  </tr>
                </thead>
                <tbody>
                  {report.api_usage.map((u) => (
                    <tr key={`${u.provider}-${u.purpose}`} className="border-b border-border/50 tabular-nums">
                      <td className="py-1.5 capitalize">{u.provider}</td>
                      <td className="capitalize">{u.purpose.replace(/_/g, " ")}</td>
                      <td className="text-right">{formatCount(u.calls)}</td>
                      <td className="text-right">
                        {formatCount(u.input_tokens)} / {formatCount(u.output_tokens)}
                      </td>
                      <td className="text-right">{formatMoney(u.cost_usd_cents, "USD")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Costs
// --------------------------------------------------------------------------

function todayIso(): string {
  return presetRange("30d").to;
}

function CostsCard({ currency }: { currency: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const { data: strats } = useQuery({ queryKey: ["strategies"], queryFn: listStrategies });
  const { data, isLoading, error } = useQuery({ queryKey: ["costs"], queryFn: () => listCosts() });
  const names = new Map(
    (strats ?? []).map((s) => [s.id, (s as unknown as { product_name?: string }).product_name ?? s.id.slice(0, 8)]),
  );

  const remove = useMutation({
    mutationFn: (id: string) => deleteCost(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["costs"] });
      qc.invalidateQueries({ queryKey: ["revenue"] });
      qc.invalidateQueries({ queryKey: ["meetings-ledger"] });
      toast("Cost removed", "info");
    },
    onError: (e) => toast((e as Error).message, "error"),
  });

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-3">
        <div>
          <CardTitle className="text-sm">Recorded costs</CardTitle>
          <p className="text-xs text-muted-foreground">
            Data, tools, ads and your time. Leave the campaign empty for account-wide costs.
          </p>
        </div>
        <Button onClick={() => setOpen(true)}>Add cost</Button>
      </CardHeader>
      <CardContent>
        <AsyncState isLoading={isLoading} error={error}
                    empty={!data || data.items.length === 0}
                    emptyLabel="No costs recorded yet.">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-sm" aria-label="Recorded costs">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th className="py-1.5 font-medium">Date</th>
                  <th className="font-medium">Campaign</th>
                  <th className="font-medium">Category</th>
                  <th className="font-medium">Description</th>
                  <th className="text-right font-medium">Amount</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data?.items.map((c) => (
                  <tr key={c.id} className="border-b border-border/50">
                    <td className="py-1.5 tabular-nums">{c.incurred_on}</td>
                    <td>{c.strategy_id ? names.get(c.strategy_id) ?? "Campaign" : "Account-wide"}</td>
                    <td className="capitalize">{c.category}</td>
                    <td className="text-muted-foreground">
                      {c.description || "—"}
                      {c.hours !== null && ` (${c.hours} h)`}
                    </td>
                    <td className="text-right tabular-nums">{formatMoney(c.amount_cents, c.currency)}</td>
                    <td className="text-right">
                      <button type="button" aria-label="Remove cost"
                              className="rounded p-1 text-muted-foreground hover:text-[rgb(var(--destructive))]"
                              onClick={() => remove.mutate(c.id)}>
                        <Trash2 className="h-4 w-4" aria-hidden />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </AsyncState>
      </CardContent>
      <CostDialog open={open} onClose={() => setOpen(false)} currency={currency} names={names} />
    </Card>
  );
}

function CostDialog({ open, onClose, currency, names }: {
  open: boolean;
  onClose: () => void;
  currency: string;
  names: Map<string, string>;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState({
    strategy_id: "", category: "other" as CostCategory, description: "", amount: "",
    hours: "", rate: "", currency, incurred_on: todayIso(),
  });
  const set = (patch: Partial<typeof form>) => setForm((f) => ({ ...f, ...patch }));
  const isTime = form.category === "time";

  const save = useMutation({
    mutationFn: () => {
      const body: CostInput = {
        strategy_id: form.strategy_id || null,
        category: form.category,
        description: form.description,
        currency: form.currency.toUpperCase(),
        incurred_on: form.incurred_on || null,
      };
      if (isTime) {
        body.hours = Number(form.hours);
        body.hourly_rate = Number(form.rate);
      } else {
        body.amount = Number(form.amount);
      }
      return createCost(body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["costs"] });
      qc.invalidateQueries({ queryKey: ["revenue"] });
      qc.invalidateQueries({ queryKey: ["meetings-ledger"] });
      toast("Cost recorded", "success");
      set({ description: "", amount: "", hours: "", rate: "" });
      onClose();
    },
    onError: (e) => toast((e as Error).message, "error"),
  });

  const valid = isTime
    ? Number(form.hours) > 0 && Number(form.rate) >= 0 && form.rate !== ""
    : form.amount !== "" && Number(form.amount) >= 0;

  return (
    <Modal open={open} onClose={onClose} title="Add cost">
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (valid) save.mutate(); }}>
        <div className="space-y-1">
          <Label htmlFor="cost-campaign">Campaign</Label>
          <select id="cost-campaign" className={selectClass} value={form.strategy_id}
                  onChange={(e) => set({ strategy_id: e.target.value })}>
            <option value="">Account-wide (spread by sends)</option>
            {[...names.entries()].map(([id, name]) => (
              <option key={id} value={id}>{name}</option>
            ))}
          </select>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <Label htmlFor="cost-category">Category</Label>
            <select id="cost-category" className={selectClass} value={form.category}
                    onChange={(e) => set({ category: e.target.value as CostCategory })}>
              {CATEGORIES.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="cost-date">Date</Label>
            <Input id="cost-date" type="date" value={form.incurred_on}
                   onChange={(e) => set({ incurred_on: e.target.value })} />
          </div>
        </div>
        <div className="space-y-1">
          <Label htmlFor="cost-description">Description</Label>
          <Input id="cost-description" maxLength={300} value={form.description}
                 placeholder={isTime ? "Reviewing replies, call prep" : "Apollo Basic, September"}
                 onChange={(e) => set({ description: e.target.value })} />
        </div>
        {isTime ? (
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="cost-hours">Hours</Label>
              <Input id="cost-hours" type="number" min="0" step="0.25" value={form.hours}
                     onChange={(e) => set({ hours: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="cost-rate">Hourly rate ({form.currency})</Label>
              <Input id="cost-rate" type="number" min="0" step="1" value={form.rate}
                     onChange={(e) => set({ rate: e.target.value })} />
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="cost-amount">Amount</Label>
              <Input id="cost-amount" type="number" min="0" step="0.01" value={form.amount}
                     onChange={(e) => set({ amount: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="cost-currency">Currency</Label>
              <Input id="cost-currency" maxLength={3} value={form.currency}
                     onChange={(e) => set({ currency: e.target.value.toUpperCase() })} />
            </div>
          </div>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={!valid || save.isPending}>Save</Button>
        </div>
      </form>
    </Modal>
  );
}
