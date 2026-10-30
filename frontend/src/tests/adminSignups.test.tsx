/** Admins see who signed up and from where (Admin > Sign-ups, Admin > Users). */
import { describe, expect, it, vi } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/api/admin", () => ({
  adminApi: {
    listSignups: vi.fn(), listUsers: vi.fn(),
    suspendUser: vi.fn(), unsuspendUser: vi.fn(),
  },
}));
import { adminApi } from "@/lib/api/admin";
import SignupsPage from "@/app/(admin)/admin/signups/page";
import UsersPage from "@/app/(admin)/admin/users/page";

function renderWith(Page: React.ComponentType) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><Page /></QueryClientProvider>);
}

describe("Admin Sign-ups page", () => {
  it("lists created accounts and refused attempts with address and country", async () => {
    vi.mocked(adminApi.listSignups).mockResolvedValue([
      { ts: "2026-10-30T09:00:00Z", event: "signup", outcome: "created",
        email: "new@example.com", ip: "198.51.100.7", country: "PK", user_id: "u1" },
      { ts: "2026-10-30T08:00:00Z", event: "signup_blocked_vpn",
        outcome: "refused: VPN, proxy or datacenter address",
        email: "vpn@example.com", ip: "203.0.113.9", country: null, user_id: null },
    ]);
    renderWith(SignupsPage);
    expect(await screen.findByText("new@example.com")).toBeInTheDocument();
    expect(screen.getByText("198.51.100.7")).toBeInTheDocument();
    expect(screen.getByText("PK")).toBeInTheDocument();
    expect(screen.getByText("created")).toBeInTheDocument();
    expect(screen.getByText("refused: VPN, proxy or datacenter address")).toBeInTheDocument();
  });

  it("pages to older sign-ups when a page is full", async () => {
    const row = (i: number) => ({ ts: "2026-10-30T09:00:00Z", event: "signup", outcome: "created",
      email: `u${i}@example.com`, ip: null, country: null, user_id: null });
    vi.mocked(adminApi.listSignups).mockReset();
    vi.mocked(adminApi.listSignups)
      .mockResolvedValueOnce(Array.from({ length: 50 }, (_, i) => row(i)))
      .mockResolvedValueOnce([row(99)]);
    renderWith(SignupsPage);
    await screen.findByText("u0@example.com");
    fireEvent.click(screen.getByText("Older"));
    expect(await screen.findByText("u99@example.com")).toBeInTheDocument();
    await waitFor(() => expect(adminApi.listSignups).toHaveBeenLastCalledWith(2, 50));
    expect(screen.getByText("Older")).toBeDisabled();
  });

  it("says so when nothing is recorded", async () => {
    vi.mocked(adminApi.listSignups).mockResolvedValue([]);
    renderWith(SignupsPage);
    expect(await screen.findByText("No sign-ups recorded yet.")).toBeInTheDocument();
  });
});

describe("Admin Users page", () => {
  it("shows where each account signed up from", async () => {
    vi.mocked(adminApi.listUsers).mockResolvedValue([{
      id: "u1", email: "new@example.com", is_admin: false, is_suspended: false, plan: null,
      created_at: "2026-10-30T09:00:00Z", strategy_count: 0, active_campaign_count: 0,
      last_active_at: null, signup_ip: "198.51.100.7", signup_country: "AE",
      declared_country: "PK", company_name: "Acme", company_country: "GB",
    }]);
    renderWith(UsersPage);
    expect(await screen.findByText("198.51.100.7")).toBeInTheDocument();
    expect(screen.getByText("AE")).toBeInTheDocument();
    expect(screen.getByText("PK")).toBeInTheDocument();
    expect(screen.getByText("Acme (GB)")).toBeInTheDocument();
  });
});
