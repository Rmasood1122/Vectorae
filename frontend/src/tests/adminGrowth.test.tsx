/** Admin > Growth: live sign-ups and purchases for named viewers only. */
import { describe, expect, it, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/api/admin", () => ({ adminApi: { getGrowth: vi.fn() } }));
import { adminApi, type GrowthDashboard } from "@/lib/api/admin";
import { ApiError } from "@/lib/api/client";
import GrowthPage from "@/app/(admin)/admin/growth/page";
import { sourceFrom } from "@/lib/signupSource";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><GrowthPage /></QueryClientProvider>);
}

const DATA: GrowthDashboard = {
  generated_at: "2026-11-03T12:00:00Z", window_days: 30,
  totals: { accounts: 12, signups_24h: 1, signups_7d: 3, signups_window: 5,
            verified_in_window: 4, refused_attempts_window: 2, paying_subscriptions: 1,
            trialing_subscriptions: 0, purchases_window: 1, canceled_window: 0,
            converted_in_window: 1, mrr_cents_by_currency: { USD: 9900 } },
  top_countries: [["GB", 3]], top_sources: [["linkedin", 2]],
  daily: [{ date: "2026-11-03", signups: 1, purchases: 1 }],
  signups: [{
    signed_up_at: "2026-11-01T09:00:00Z", user_id: "u1", email: "buyer@acme.test",
    full_name: null, ip: "198.51.100.7", ip_country: "GB", declared_country: "GB",
    account_type: "company", company_name: "Acme", company_country: "GB",
    source: "linkedin", utm_source: "linkedin", utm_medium: "social",
    utm_campaign: "nov-launch", referrer_host: null, landing_path: "/pricing",
    email_verified: true, phone_verified: true, identity_review: null,
    is_suspended: false, plan: "growth", subscription_status: "active",
    last_active_at: null, account_deleted: false }],
  purchases: [{
    subscription_id: "s1", user_id: "u1", email: "buyer@acme.test", company_name: "Acme",
    tier: "growth", billing_model: "monthly", status: "active",
    purchased_at: "2026-11-02T09:00:00Z", amount_cents: 9900, currency: "USD",
    interval: "month", monthly_value_cents: 9900, counts_as_revenue: true, is_stub: false,
    trial_started_at: null, trial_ends_at: null, current_period_end: null,
    cancel_at_period_end: false, canceled_at: null, stripe_customer_id: "cus_123",
    signup_ip: "198.51.100.7", signup_country: "GB", signup_source: "linkedin",
    days_signup_to_purchase: 1, needs_attention: false }],
};

describe("Admin Growth page", () => {
  beforeEach(() => { vi.mocked(adminApi.getGrowth).mockReset(); });

  it("shows sign-ups, purchases, where from and revenue", async () => {
    vi.mocked(adminApi.getGrowth).mockResolvedValue(DATA);
    renderPage();
    expect(await screen.findByText("Subscriptions purchased")).toBeInTheDocument();
    expect(screen.getAllByText("buyer@acme.test")).toHaveLength(2);
    expect(screen.getByText("nov-launch")).toBeInTheDocument();
    expect(screen.getByText("cus_123")).toBeInTheDocument();
    expect(screen.getAllByText(/\$99\.00/).length).toBeGreaterThan(0);
    expect(adminApi.getGrowth).toHaveBeenCalledWith(30);
  });

  it("tells a refused viewer plainly and shows no data", async () => {
    vi.mocked(adminApi.getGrowth).mockImplementation(async () => {
      throw new ApiError(403, "growth dashboard access required");
    });
    renderPage();
    expect(await screen.findByText(/limited to named accounts/)).toBeInTheDocument();
    expect(screen.queryByText("Subscriptions purchased")).not.toBeInTheDocument();
  });
});

describe("signup source capture", () => {
  it("keeps UTM tags, the referring host and the landing path", () => {
    expect(sourceFrom("?utm_source=linkedin&utm_campaign=nov&x=1",
                      "https://www.google.com/search?q=secret", "/pricing", "vectorae.app"))
      .toEqual({ utm_source: "linkedin", utm_campaign: "nov",
                 referrer: "www.google.com", landing_path: "/pricing" });
  });

  it("ignores our own site as a referrer", () => {
    expect(sourceFrom("", "https://vectorae.app/about", "/", "vectorae.app"))
      .toEqual({ landing_path: "/" });
  });
});
