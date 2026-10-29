/**
 * The Network page's "Email contacts" tab: LinkedIn stays the default tab; the
 * upload is disabled until the "my own business contacts" box is ticked; the
 * summary shows exclusion reasons; delete is never plan-gated; flag off is
 * explained. API modules are mocked (wire tests: emailNetwork.test.ts).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const LIMITS = { mbox_max_bytes: 300 * 1048576, contacts_max_bytes: 20 * 1048576 };

vi.mock("@/lib/api/network", () => ({
  getNetworkStatus: vi.fn(async () => ({ eligible: true, plan: "growth", upgrade_to: null,
    connection_count: 0, needs_new_export: false, max_bytes: 5_000_000 })),
  getRecommendations: vi.fn(async () => ({ products: [] })),
  listMessages: vi.fn(async () => ({ messages: [] })),
  eraseNetwork: vi.fn(), bulkSend: vi.fn(), draftMessage: vi.fn(), importConnections: vi.fn(),
  markMessageSent: vi.fn(), saveMessage: vi.fn(), sendMessageNow: vi.fn(),
}));

const IMPORT_FOR_MOCK = {
  id: "i1", kind: "mbox", status: "done", rows_in_file: 1240, messages_scanned: 1240,
  people_found: 1240, added: 600, updated: 30, excluded: 610,
  excluded_by_reason: [
    { reason: "automated", label: "newsletters / automated senders", count: 300 },
  ],
  cap_note: null, error: null, created_at: null, finished_at: null, matched: 42,
};

vi.mock("@/lib/api/emailNetwork", async (orig) => {
  const real = await orig<typeof import("@/lib/api/emailNetwork")>();
  return {
    ...real,
    getEmailNetworkStatus: vi.fn(),
    getEmailRecommendations: vi.fn(async () => ({ products: [] })),
    listEmailMessages: vi.fn(async () => ({ messages: [] })),
    listEmailCampaigns: vi.fn(async () => ({ campaigns: [] })),
    eraseEmailNetwork: vi.fn(async () => ({ deleted_contacts: 2 })),
    deleteEmailContact: vi.fn(async () => ({ deleted_contacts: 1 })),
    uploadEmailNetworkFile: vi.fn(async () => ({ ...IMPORT_FOR_MOCK })),
  };
});

import * as emailApi from "@/lib/api/emailNetwork";
import NetworkPage from "@/app/(app)/network/page";

const STATUS = {
  eligible: true, plan: "growth", upgrade_to: null, feature_enabled: true, contact_count: 0,
  last_import: null, needs_new_import: true,
  gmail: { connected: false, address: null, locked: false, can_scan: false, reconnect_required: false, scan: null },
  attestation: { version: "1", text: "These are my own business contacts." },
  limits: { ...LIMITS, max_messages: 50000, max_contact_rows: 50000, gmail_max_messages: 5000 },
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><NetworkPage /></QueryClientProvider>);
}

describe("Network page: Email contacts tab", () => {
  beforeEach(() => { vi.mocked(emailApi.uploadEmailNetworkFile).mockClear(); });

  it("opens on LinkedIn and does not load email contacts until the tab is chosen", async () => {
    vi.mocked(emailApi.getEmailNetworkStatus).mockResolvedValue(STATUS as never);
    renderPage();
    expect(await screen.findByText("Your LinkedIn network")).toBeInTheDocument();
    expect(emailApi.getEmailNetworkStatus).not.toHaveBeenCalled();
  });

  it("needs the attestation tick before a file can be chosen, then shows the summary", async () => {
    vi.mocked(emailApi.getEmailNetworkStatus).mockResolvedValue(STATUS as never);
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "Email contacts" }));
    const input = await screen.findByLabelText("Contacts or mailbox file");
    expect(input).toBeDisabled();
    await userEvent.click(screen.getByLabelText("These are my own business contacts"));
    expect(input).not.toBeDisabled();
    await userEvent.upload(input, new File(["BEGIN:VCARD\n"], "contacts.vcf"));
    expect(emailApi.uploadEmailNetworkFile).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/610 excluded: 300 newsletters/)).toBeInTheDocument();
  });

  it("explains the flag being off and still lets a downgraded account delete", async () => {
    vi.mocked(emailApi.getEmailNetworkStatus).mockResolvedValue(
      { ...STATUS, eligible: false, plan: "starter", feature_enabled: true, contact_count: 2 } as never);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "Email contacts" }));
    expect(await screen.findByText(/included with the Growth plan/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /delete my email contacts/i }));
    expect(emailApi.eraseEmailNetwork).toHaveBeenCalled();
    // the confirm says what happens to first emails already attached to a campaign
    expect(vi.mocked(window.confirm).mock.calls[0][0]).toMatch(/review queue/);
    expect(screen.queryByLabelText("Contacts or mailbox file")).toBeNull();
  });

  it("never offers the Gmail scan while it is locked, even with Gmail connected", async () => {
    vi.mocked(emailApi.getEmailNetworkStatus).mockResolvedValue({ ...STATUS, gmail: {
      ...STATUS.gmail, connected: true, address: "me@acme.test", locked: true } } as never);
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "Email contacts" }));
    expect(await screen.findByText(/Scanning Gmail is switched off/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /scan my connected gmail/i })).toBeNull();
  });

  it("says when the feature is switched off", async () => {
    vi.mocked(emailApi.getEmailNetworkStatus).mockResolvedValue(
      { ...STATUS, feature_enabled: false } as never);
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "Email contacts" }));
    expect(await screen.findByText(/not switched on/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Contacts or mailbox file")).toBeNull();
  });
});
