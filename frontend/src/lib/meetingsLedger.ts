/** F08 — pure helpers for the meetings-per-dollar ledger. No React, no
 *  fetching; tested in tests/meetingsLedger.test.ts. Types are structural so
 *  this file does not import the API module (which imports it). */

export interface LedgerQuery {
  dateFrom: string;
  dateTo: string;
  strategyId?: string;
}

export type LedgerExportFormat = "csv" | "pdf";
export type LedgerExportView = "campaigns" | "events";

export const DISABLED_STATUS = 409;

export function ledgerQueryString(query: LedgerQuery): string {
  const q = new URLSearchParams({ date_from: query.dateFrom, date_to: query.dateTo });
  if (query.strategyId) q.set("strategy_id", query.strategyId);
  return q.toString();
}

export function ledgerExportPath(
  query: LedgerQuery,
  format: LedgerExportFormat,
  view: LedgerExportView = "campaigns",
): string {
  const q = new URLSearchParams(ledgerQueryString(query));
  q.set("format", format);
  if (format === "csv" && view !== "campaigns") q.set("view", view);
  return `/analytics/meetings-ledger/export?${q}`;
}

export function exportFilename(
  query: LedgerQuery,
  format: LedgerExportFormat,
  view: LedgerExportView = "campaigns",
): string {
  const suffix = format === "csv" && view === "events" ? "-events" : "";
  return `vectorae-meetings-ledger-${query.dateFrom}-${query.dateTo}${suffix}.${format}`;
}

/** True when the API said the feature is switched off (not an error to show). */
export function isDisabledError(error: unknown): boolean {
  return typeof error === "object" && error !== null
    && (error as { status?: number }).status === DISABLED_STATUS;
}

interface MixLike {
  meetings_booked: number;
  booked_certain: number;
  booked_by_method: Record<string, number>;
}

/** Same rule as the attribution panel: under half confirmed by a reply ->
 *  say so above the numbers. Pending bookings are named separately. */
export function certaintyCaveat(figures: MixLike | null | undefined): string | null {
  if (!figures || figures.meetings_booked === 0) return null;
  const pending = figures.booked_by_method.pending ?? 0;
  const parts: string[] = [];
  const share = figures.booked_certain / figures.meetings_booked;
  if (share < 0.5) {
    parts.push(`Only ${Math.round(share * 100)}% of booked meetings are confirmed by a reply — `
      + "the rest are credited to the last message sent before them, which is a guess.");
  }
  if (pending > 0) {
    parts.push(`${pending} booking${pending === 1 ? " is" : "s are"} not attributed yet `
      + "(the attribution sweep runs every 15 minutes).");
  }
  return parts.length ? parts.join(" ") : null;
}

/** "3 confirmed · 1 thread · 2 guessed · 1 pending" — zeros left out. */
export function methodMixLabel(mix: Record<string, number>): string {
  const labels: [string, string][] = [
    ["direct_reply", "confirmed"], ["thread_match", "thread"], ["last_touch", "guessed"],
    ["none", "no touch"], ["pending", "pending"],
  ];
  const parts = labels
    .filter(([key]) => (mix[key] ?? 0) > 0)
    .map(([key, label]) => `${mix[key]} ${label}`);
  return parts.length ? parts.join(" · ") : "—";
}

export function infrastructureLabel(infra: {
  cents_per_1k_sends: number;
  included: boolean;
  excluded_non_usd_cents: number;
}): string {
  if (infra.cents_per_1k_sends <= 0) {
    return "Infrastructure not included — the admin rate is 0.";
  }
  if (!infra.included) {
    return "Infrastructure is priced in USD and is not included in this currency.";
  }
  const dollars = (infra.cents_per_1k_sends / 100).toFixed(2);
  return `Infrastructure included at $${dollars} per 1,000 sends (admin setting).`;
}

/** One line on the reconciliation block, for the footer. */
export function reconciliationLabel(rec: {
  balanced: boolean;
  decomposition_balanced: boolean;
  difference_cents: number;
  booked_outcome_leads: number;
  ledger_meetings_booked: number;
}): { ok: boolean; text: string } {
  const ok = rec.balanced && rec.decomposition_balanced
    && rec.booked_outcome_leads === rec.ledger_meetings_booked;
  if (ok) {
    return { ok, text: "Reconciled to the raw cost and outcome rows." };
  }
  return {
    ok,
    text: `Does not reconcile: cost differs by ${rec.difference_cents} cents; `
      + `${rec.ledger_meetings_booked} booked vs ${rec.booked_outcome_leads} in the raw outcomes.`,
  };
}
