"use client";
/** Admin > Growth: who signed up, from where, and who bought what -- live.
 *  GET /admin/growth (app/services/growth_dashboard.py). Only the addresses on
 *  GROWTH_DASHBOARD_VIEWERS that are also admins; everyone else gets 403 and
 *  this page says so. Refreshes every 15 seconds while the tab is visible. */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { adminApi, type GrowthDashboard } from "@/lib/api/admin";
import { ApiError } from "@/lib/api/client";
import { Badge } from "@/components/ui/badge";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";

const REFRESH_MS = 15_000;
const WINDOWS = [1, 7, 30, 90, 365];

function when(value: string | null) {
  return value ? new Date(value).toLocaleString() : "—";
}

function money(cents: number | null, currency: string | null) {
  if (cents == null) return "—";
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: currency || "USD" })
      .format(cents / 100);
  } catch {
    return `${(cents / 100).toFixed(2)} ${currency ?? ""}`.trim();
  }
}

function Tile({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded-lg border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}

function Daily({ data }: { data: GrowthDashboard["daily"] }) {
  const peak = Math.max(1, ...data.map((d) => Math.max(d.signups, d.purchases)));
  return (
    <div className="rounded-lg border p-3">
      <div className="mb-2 flex gap-4 text-xs text-muted-foreground">
        <span><span className="inline-block h-2 w-2 rounded-sm bg-primary" /> Sign-ups</span>
        <span><span className="inline-block h-2 w-2 rounded-sm bg-emerald-500" /> Purchases</span>
      </div>
      <div className="flex h-24 items-end gap-px" aria-label="Sign-ups and purchases per day">
        {data.map((d) => (
          <div key={d.date} className="flex flex-1 items-end gap-px"
               title={`${d.date}: ${d.signups} sign-ups, ${d.purchases} purchases`}>
            <div className="flex-1 bg-primary" style={{ height: `${(d.signups / peak) * 100}%` }} />
            <div className="flex-1 bg-emerald-500"
                 style={{ height: `${(d.purchases / peak) * 100}%` }} />
          </div>
        ))}
      </div>
    </div>
  );
}

function Ranked({ title, rows }: { title: string; rows: [string, number][] }) {
  return (
    <div className="rounded-lg border p-3">
      <div className="mb-2 text-sm font-medium">{title}</div>
      {rows.length === 0 ? <div className="text-sm text-muted-foreground">None yet.</div> : (
        <ul className="space-y-1 text-sm">
          {rows.map(([name, count]) => (
            <li key={name} className="flex justify-between">
              <span>{name}</span><span className="tabular-nums">{count}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function AdminGrowthPage() {
  const [days, setDays] = useState(30);
  const { data, error, isLoading, dataUpdatedAt, isFetching } = useQuery({
    queryKey: ["admin", "growth", days],
    queryFn: () => adminApi.getGrowth(days),
    refetchInterval: REFRESH_MS,
    retry: (count, err) => !(err instanceof ApiError && err.status === 403) && count < 2,
  });

  if (error instanceof ApiError && error.status === 403) {
    return (
      <div className="space-y-2">
        <h1 className="text-2xl font-bold">Growth</h1>
        <p className="text-muted-foreground">
          This dashboard is limited to named accounts. Yours is not one of them.
        </p>
      </div>
    );
  }

  const t = data?.totals;
  const mrr = t ? Object.entries(t.mrr_cents_by_currency) : [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Growth</h1>
          <p className="text-sm text-muted-foreground">
            Sign-ups and subscription purchases, live. Updates every 15 seconds.
            {dataUpdatedAt > 0 && <> Last update {new Date(dataUpdatedAt).toLocaleTimeString()}
              {isFetching ? " (refreshing…)" : ""}.</>}
          </p>
        </div>
        <label className="text-sm">
          Period{" "}
          <select className="rounded-md border bg-background px-2 py-1" value={days}
                  onChange={(e) => setDays(Number(e.target.value))} aria-label="Period">
            {WINDOWS.map((d) => (
              <option key={d} value={d}>{d === 1 ? "Last 24 hours" : `Last ${d} days`}</option>
            ))}
          </select>
        </label>
      </div>

      {isLoading || !data || !t ? (
        <div className="text-muted-foreground">{error ? "Could not load the dashboard." : "Loading…"}</div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Tile label="Sign-ups (period)" value={t.signups_window}
                  hint={`${t.signups_24h} in 24h · ${t.signups_7d} in 7 days`} />
            <Tile label="Email verified" value={t.verified_in_window}
                  hint={`${t.refused_attempts_window} refused attempts`} />
            <Tile label="Purchases (period)" value={t.purchases_window}
                  hint={`${t.converted_in_window} of this period's sign-ups bought`} />
            <Tile label="Paying now" value={t.paying_subscriptions}
                  hint={`${t.trialing_subscriptions} trialing · ${t.canceled_window} cancelled`} />
            <Tile label="Monthly recurring revenue"
                  value={mrr.length === 0 ? "—" : mrr.map(([c, v]) => money(v, c)).join(" + ")}
                  hint="Stripe-billed, active and past due" />
            <Tile label="All accounts" value={t.accounts} />
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            <div className="md:col-span-1"><Daily data={data.daily} /></div>
            <Ranked title="Top countries (sign-up address)" rows={data.top_countries} />
            <Ranked title="Top sources" rows={data.top_sources} />
          </div>

          <section className="space-y-2">
            <h2 className="text-lg font-semibold">Subscriptions purchased</h2>
            {data.purchases.length === 0 ? (
              <div className="text-sm text-muted-foreground">No purchases in this period.</div>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>When</TableHead><TableHead>Account</TableHead>
                      <TableHead>Plan</TableHead><TableHead>Price</TableHead>
                      <TableHead>Status</TableHead><TableHead>Trial ends</TableHead>
                      <TableHead>Renews</TableHead><TableHead>Signed up from</TableHead>
                      <TableHead>Source</TableHead><TableHead>Days to buy</TableHead>
                      <TableHead>Stripe customer</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.purchases.map((p) => (
                      <TableRow key={p.subscription_id}>
                        <TableCell className="text-sm whitespace-nowrap">{when(p.purchased_at)}</TableCell>
                        <TableCell className="font-medium">
                          {p.email ?? "deleted account"}
                          {p.company_name && <div className="text-xs text-muted-foreground">{p.company_name}</div>}
                        </TableCell>
                        <TableCell className="text-sm">{p.tier} · {p.billing_model}</TableCell>
                        <TableCell className="text-sm whitespace-nowrap">
                          {money(p.amount_cents, p.currency)}{p.interval ? ` / ${p.interval}` : ""}
                        </TableCell>
                        <TableCell>
                          <Badge variant={p.counts_as_revenue ? "secondary" : "outline"}>
                            {p.is_stub ? "test (no Stripe)" : p.status}
                            {p.cancel_at_period_end ? " · ending" : ""}
                          </Badge>
                          {p.needs_attention && <Badge variant="destructive" className="ml-1">check</Badge>}
                        </TableCell>
                        <TableCell className="text-sm whitespace-nowrap">{when(p.trial_ends_at)}</TableCell>
                        <TableCell className="text-sm whitespace-nowrap">{when(p.current_period_end)}</TableCell>
                        <TableCell className="text-sm">
                          {p.signup_country ?? "—"}
                          <div className="text-xs text-muted-foreground">{p.signup_ip ?? ""}</div>
                        </TableCell>
                        <TableCell className="text-sm">{p.signup_source}</TableCell>
                        <TableCell className="text-sm tabular-nums">{p.days_signup_to_purchase ?? "—"}</TableCell>
                        <TableCell className="text-xs">{p.stripe_customer_id ?? "—"}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </section>

          <section className="space-y-2">
            <h2 className="text-lg font-semibold">Sign-ups</h2>
            {data.signups.length === 0 ? (
              <div className="text-sm text-muted-foreground">No sign-ups in this period.</div>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>When</TableHead><TableHead>Email</TableHead>
                      <TableHead>Address</TableHead><TableHead>Country (address)</TableHead>
                      <TableHead>Country (stated)</TableHead><TableHead>Company</TableHead>
                      <TableHead>Source</TableHead><TableHead>Campaign</TableHead>
                      <TableHead>Landing page</TableHead><TableHead>Verified</TableHead>
                      <TableHead>Plan</TableHead><TableHead>Last active</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.signups.map((s, i) => (
                      <TableRow key={`${s.user_id ?? "gone"}-${s.signed_up_at}-${i}`}>
                        <TableCell className="text-sm whitespace-nowrap">{when(s.signed_up_at)}</TableCell>
                        <TableCell className="font-medium">
                          {s.account_deleted ? "deleted account" : s.email}
                          {s.full_name && <div className="text-xs text-muted-foreground">{s.full_name}</div>}
                        </TableCell>
                        <TableCell className="text-sm">{s.ip ?? "—"}</TableCell>
                        <TableCell className="text-sm">{s.ip_country ?? "—"}</TableCell>
                        <TableCell className="text-sm">
                          {s.declared_country ?? "—"}
                          {s.identity_review === "pending" && (
                            <Badge variant="destructive" className="ml-1">mismatch</Badge>)}
                        </TableCell>
                        <TableCell className="text-sm">
                          {s.company_name ?? (s.account_type === "individual" ? "individual" : "—")}
                          {s.company_country && <span className="text-muted-foreground"> ({s.company_country})</span>}
                        </TableCell>
                        <TableCell className="text-sm">
                          {s.source}
                          {s.utm_medium && <div className="text-xs text-muted-foreground">{s.utm_medium}</div>}
                        </TableCell>
                        <TableCell className="text-sm">{s.utm_campaign ?? "—"}</TableCell>
                        <TableCell className="text-xs">{s.landing_path ?? "—"}</TableCell>
                        <TableCell className="text-sm">
                          {s.email_verified ? "email" : "—"}{s.phone_verified ? " · phone" : ""}
                        </TableCell>
                        <TableCell className="text-sm">
                          {s.plan ?? "—"}{s.subscription_status ? ` (${s.subscription_status})` : ""}
                        </TableCell>
                        <TableCell className="text-sm whitespace-nowrap">{when(s.last_active_at)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
