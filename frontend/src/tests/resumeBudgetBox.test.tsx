/**
 * The "New budget $" box must be offered for every resumable status. It used
 * to appear only for `budget_exhausted`, so a strategy left in `verifying`
 * (its run died mid-verification) could be resumed but never given more
 * budget, though POST /resume accepts max_budget_usd from any status.
 */
import { describe, expect, it, vi } from "vitest";
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { StrategyOut } from "@/lib/api/types";

const resumeStrategy = vi.fn(async () => ({}));
vi.mock("@/lib/api/strategies", () => ({
  resumeStrategy: (...a: unknown[]) => resumeStrategy(...(a as [])),
  resumeStrategyErrorMessage: () => "error",
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { ResumeStrategyButton, RESUMABLE_STATUSES } from "@/components/strategy/ResumeStrategyButton";

function open(status: StrategyOut["status"]) {
  const strategy = { id: "s1", status } as StrategyOut;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ResumeStrategyButton strategy={strategy} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: /resume/i }));
}

describe("Resume budget box", () => {
  it.each(RESUMABLE_STATUSES)("is offered when the status is %s", (status) => {
    open(status);
    expect(screen.getByLabelText("New budget in dollars")).toBeTruthy();
  });

  it("sends the new budget when resuming a stuck `verifying` strategy", async () => {
    resumeStrategy.mockClear();
    open("verifying");
    fireEvent.change(screen.getByLabelText("New budget in dollars"), { target: { value: "25" } });
    fireEvent.click(screen.getByRole("button", { name: /confirm resume/i }));
    await vi.waitFor(() => expect(resumeStrategy).toHaveBeenCalledWith("s1", 25));
  });
});
