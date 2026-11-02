/**
 * Product defect #10: the "Delete my uploaded connections" button lived inside
 * the Growth-only upload card, so a downgraded account could not delete its
 * uploaded connections from the app although DELETE /linkedin-network is never
 * plan-gated (app/api/linkedin_network.py::erase). A data-rights issue.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/api/network", () => ({
  getNetworkStatus: vi.fn(),
  getRecommendations: vi.fn(async () => ({ products: [] })),
  listMessages: vi.fn(async () => ({ messages: [] })),
  eraseNetwork: vi.fn(async () => ({ deleted: 3 })),
  draftMessage: vi.fn(), importConnections: vi.fn(),
  markMessageSent: vi.fn(), saveMessage: vi.fn(),
}));
import { eraseNetwork, getNetworkStatus } from "@/lib/api/network";
import NetworkPage from "@/app/(app)/network/page";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><NetworkPage /></QueryClientProvider>);
}

const base = { plan: "starter", upgrade_to: "growth", needs_new_export: false,
               max_bytes: 5_000_000 };

describe("Network page delete", () => {
  beforeEach(() => vi.mocked(eraseNetwork).mockClear());

  it("a downgraded account with stored connections can delete them", async () => {
    vi.mocked(getNetworkStatus).mockResolvedValue(
      { ...base, eligible: false, connection_count: 3 } as never);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    const button = await screen.findByRole("button", { name: /delete my uploaded connections/i });
    await userEvent.click(button);
    expect(eraseNetwork).toHaveBeenCalled();
  });

  it("shows no delete button when nothing is stored", async () => {
    vi.mocked(getNetworkStatus).mockResolvedValue(
      { ...base, eligible: false, connection_count: 0 } as never);
    renderPage();
    await screen.findByText(/included with the Growth plan/i);
    expect(screen.queryByRole("button", { name: /delete my uploaded connections/i })).toBeNull();
  });

  it("an eligible account still sees it", async () => {
    vi.mocked(getNetworkStatus).mockResolvedValue(
      { ...base, plan: "growth", upgrade_to: null, eligible: true, connection_count: 2 } as never);
    renderPage();
    expect(await screen.findByRole("button", { name: /delete my uploaded connections/i }))
      .toBeInTheDocument();
  });
});
