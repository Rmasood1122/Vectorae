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

export interface UsagePage {
  period_start: string;
  meetings_booked: number;
  amount_cents: number;
  by_status: Record<string, number>;
  price_per_meeting_cents: number;
  currency: string;
  meetings: BillableMeetingRow[];
  subscription: Subscription | null;
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
  dispute: (id: string, reason: string) =>
    api<BillableMeetingRow>(`/billing/meetings/${id}/dispute`, { body: { reason } }),
  purchaseCredits: (amount_cents: number) =>
    api<CheckoutResult>("/billing/purchase-credits", { body: { amount_cents } }),
};
