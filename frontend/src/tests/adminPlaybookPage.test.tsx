/** Admin Playbook Scores page reads the API's `{scores: [...]}` envelope. */
import { describe, expect, it, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/api/admin", () => ({
  adminApi: { listPlaybookScores: vi.fn(), recomputePlaybook: vi.fn() },
}));
import { adminApi } from "@/lib/api/admin";
import PlaybookPage from "@/app/(admin)/admin/playbook/page";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}><PlaybookPage /></QueryClientProvider>,
  );
}

describe("Admin Playbook Scores page", () => {
  it("lists each row the server returns", async () => {
    vi.mocked(adminApi.listPlaybookScores).mockResolvedValue({
      scores: [{
        user_id: "00000000-0000-0000-0000-000000000001",
        pattern_key: "angle:pain_point", variant: "B",
        reply_rate: 0.125, booking_rate: 0.05, sample_size: 40,
        last_aggregated_at: "2026-10-20T08:00:00Z",
      }],
    });
    renderPage();
    expect(await screen.findByText("angle:pain_point")).toBeInTheDocument();
    expect(screen.getByText("12.5%")).toBeInTheDocument();
    expect(screen.getByText("00000000-0000-0000-0000-000000000001")).toBeInTheDocument();
    expect(screen.getByText(new Date("2026-10-20T08:00:00Z").toLocaleDateString()))
      .toBeInTheDocument();
  });

  it("labels rows written before per-account ownership", async () => {
    vi.mocked(adminApi.listPlaybookScores).mockResolvedValue({
      scores: [{ user_id: null, pattern_key: "angle:roi", variant: "A",
                 reply_rate: 0.1, booking_rate: null, sample_size: 12,
                 last_aggregated_at: null }],
    });
    renderPage();
    expect(await screen.findByText("pooled (before 0093)")).toBeInTheDocument();
  });

  it("shows the empty state when the server has no rows", async () => {
    vi.mocked(adminApi.listPlaybookScores).mockResolvedValue({ scores: [] });
    renderPage();
    expect(await screen.findByText(/No playbook scores yet/)).toBeInTheDocument();
  });
});
