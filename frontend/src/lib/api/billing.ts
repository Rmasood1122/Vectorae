import { api } from "./client";
import {
  checkoutPayload, type Catalog, type CheckoutModel, type CheckoutTerms,
} from "../pricing";

export interface Subscription {
  billing_model: CheckoutModel;
  tier: string;
  status: "incomplete" | "trialing" | "active" | "past_due" | "canceled";
  grants_plan: boolean;
  trial_ends_at: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  canceled_at: string | null;
  is_stub: boolean;
  /** What the next renewal charges (null for pay per meeting). */
  price_cents?: number | null;
  interval?: "month" | "year" | null;
  /** A billing state a person must resolve (e.g. two subscriptions). */
  needs_support?: boolean;
}

export interface BillingOverview {
  plan: string;
  subscription: Subscription | null;
  stub_mode: boolean;
  checkout_terms?: CheckoutTerms;
  usage: {
    period_start: string;
    meetings_booked: number;
    amount_cents: number;
    by_status: Record<string, number>;
    price_per_meeting_cents: number;
    currency: string;
  };
}

export interface CheckoutResult {
  mode: "stub" | "stripe";
  checkout_url: string | null;
  subscription: Subscription | null;
}

export interface BillableMeetingRow {
  id: string;
  lead_id: string | null;
  strategy_id: string | null;
  source: string;
  amount_cents: number;
  currency: string;
  status: "pending" | "charged" | "waived" | "disputed" | "failed" | "blocked_by_credit_limit";
  occurred_at: string;
  charge_after: string;
  charged_at: string | null;
  is_stub: boolean;
  failure_reason: string | null;
  resolution_note: string | null;
}

export interface UsagePage extends BillingOverview {
  meetings: BillableMeetingRow[];
}

export interface CreditTransactionRow {
  id: string;
  type: "purchase" | "adjustment" | "expiration" | "block";
  amount_cents: number;
  old_limit_cents: number;
  new_limit_cents: number;
  reason: string;
  created_at: string;
}

export interface CreditExpirationInfo {
  monthly_limit_cents: number;
  carryover_cents: number;
  total_available_cents: number;
  expires_at: string | null;
  reset_day: number;
}

export interface MonthlyTrendPoint {
  period: string;
  spent_cents: number;
  transaction_count: number;
}

export interface CreditAnalyticsData {
  transaction_summary: Record<string, { count: number; total_usd: string }>;
  monthly_trend: MonthlyTrendPoint[];
  projected_monthly_burn_cents: number;
  days_until_expiration: number | null;
}

export const billingApi = {
  catalog: () => api<Catalog>("/billing/catalog", { auth: false }),
  overview: () => api<BillingOverview>("/billing"),
  /** The trial a checkout started now would get (the backend decides). */
  checkoutTerms: () => api<CheckoutTerms>("/billing/checkout-terms"),
  checkout: (billing_model: CheckoutModel, tier?: string, acceptRenewalTerms = false) =>
    api<CheckoutResult>("/billing/checkout",
      { body: checkoutPayload(billing_model, tier, acceptRenewalTerms) }),
  cancel: () => api<Subscription>("/billing/cancel", { method: "POST" }),
  meetings: () => api<BillableMeetingRow[]>("/billing/meetings"),
  usage: () => api<UsagePage>("/billing/usage"),
  creditTransactions: (limit?: number, type?: string) => {
    const params = new URLSearchParams();
    if (limit) params.append("limit", String(limit));
    if (type) params.append("transaction_type", type);
    return api<CreditTransactionRow[]>(`/billing/credit-transactions?${params}`);
  },
  creditExpiration: () => api<CreditExpirationInfo>("/billing/credit-expiration"),
  creditAnalytics: () => api<CreditAnalyticsData>("/billing/analytics"),
  dispute: (id: string, reason: string) =>
    api<BillableMeetingRow>(`/billing/meetings/${id}/dispute`, { body: { reason } }),
};
