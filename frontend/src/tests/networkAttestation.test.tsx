/**
 * L3 (evals/vectorae Phase 5B): the Connections.csv picker stays disabled until
 * the user has read the notice, chosen a lawful basis and ticked the box.
 * Nothing is pre-selected or pre-checked.
 */
import { describe, expect, it, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/api/network", () => ({
  getNetworkStatus: vi.fn(),
  getRecommendations: vi.fn(async () => ({ products: [] })),
  listMessages: vi.fn(async () => ({ messages: [] })),
  eraseNetwork: vi.fn(), draftMessage: vi.fn(), importConnections: vi.fn(),
  saveMessage: vi.fn(),
}));
vi.mock("@/lib/api/linkedinTasks", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/linkedinTasks")>()),
  listTasks: vi.fn(async () => ({ tasks: [], limits: { connection_note: 20, message: 50 },
                                  approval_hours: 24 })),
}));
import { getNetworkStatus } from "@/lib/api/network";
import NetworkPage from "@/app/(app)/network/page";

const consent = {
  scope: "connections_import", version: "2026-11-02.1", purpose: "p",
  text: "By uploading it you confirm that you have a lawful basis.",
  lawful_bases: ["legitimate_interests", "consent"], given: false,
  lawful_basis: null, granted_at: null,
};

describe("L3 attestation on the network page", () => {
  it("locks the file picker until a basis is chosen and the box is ticked", async () => {
    vi.mocked(getNetworkStatus).mockResolvedValue({
      eligible: true, plan: "growth", upgrade_to: null, connection_count: 0,
      last_import_at: null, last_analyzed_at: null, needs_new_export: false,
      max_bytes: 5_000_000, max_rows: 20_000, consent,
    } as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><NetworkPage /></QueryClientProvider>);

    expect(await screen.findByText(/lawful basis\./i)).toBeInTheDocument();
    const file = screen.getByLabelText("Connections.csv");
    const basis = screen.getByLabelText("Lawful basis") as HTMLSelectElement;
    const tick = screen.getByRole("checkbox");
    expect(basis.value).toBe("");
    expect(tick).not.toBeChecked();
    expect(file).toBeDisabled();

    await userEvent.selectOptions(basis, "legitimate_interests");
    expect(file).toBeDisabled();
    await userEvent.click(tick);
    expect(file).toBeEnabled();
  });
});
