/** The subject-line card shows pooled rates and no subject text. */
import { describe, expect, it, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/api/client", () => ({ apiClient: { get: vi.fn() } }));
import { apiClient } from "@/lib/api/client";
import { SubjectLineCard } from "@/components/analytics/SubjectLineCard";

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}><SubjectLineCard channel="email" /></QueryClientProvider>,
  );
}

describe("SubjectLineCard", () => {
  it("shows the pattern and its rate, never a subject line", async () => {
    // An older API could still send `example`; the card must not show it.
    vi.mocked(apiClient.get).mockResolvedValue([{
      pattern_type: "question", avg_reply_rate: 0.25, avg_meeting_rate: 0.1,
      sample_size: 40, is_reliable: true, channel: "email",
      example: "Quick question for Zyntrex, Priyanka?",
    }]);
    renderCard();
    expect(await screen.findByText("Ends with a question")).toBeInTheDocument();
    expect(screen.getByText("25.0% reply")).toBeInTheDocument();
    expect(screen.queryByText(/Zyntrex/)).not.toBeInTheDocument();
    expect(screen.getByText(/pooled across every account/)).toBeInTheDocument();
  });
});
