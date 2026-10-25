/**
 * F-SEC-05: WhatsApp templates belong to the whole install, so only an admin
 * manages them. A non-admin sees the approved list with no create, generate,
 * edit, submit or sync controls (the API refuses those calls with 403 anyway).
 */
import { describe, expect, it, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/api/campaigns", () => ({
  listTemplates: vi.fn(async () => ({ templates: [
    { id: "d1", name: "draft_t", language: "en_US", status: "draft", body: "Hi" },
    { id: "s1", name: "sub_t", language: "en_US", status: "submitted", body: "Hi" },
  ] })),
  generateTemplates: vi.fn(),
  submitTemplate: vi.fn(),
  syncTemplate: vi.fn(),
  createTemplate: vi.fn(),
  updateTemplate: vi.fn(),
}));

import { TemplatesPanel } from "@/components/campaigns/TemplatesPanel";

function renderPanel(canManage: boolean) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TemplatesPanel strategyId="s-1" canManage={canManage} />
    </QueryClientProvider>,
  );
}

const CONTROLS = [/\+ New draft/, /Generate with AI/, /^Edit$/, /^Submit$/, /Sync status/];

describe("TemplatesPanel", () => {
  it("gives an admin every management control", async () => {
    renderPanel(true);
    await screen.findByText("draft_t");
    for (const name of CONTROLS) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("gives a non-admin none, and says why", async () => {
    renderPanel(false);
    await screen.findByText("draft_t");
    for (const name of CONTROLS) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
    expect(screen.getByText(/an admin creates and submits them/)).toBeInTheDocument();
  });
});
