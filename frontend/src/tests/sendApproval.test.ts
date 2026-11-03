import { describe, expect, it } from "vitest";

import { parseDeepLink, routeToPath } from "@/lib/deeplinks";

import {
  bannerText,
  buildApprovePayload,
  canApprove,
  gatedChannels,
  initialSelection,
  queuedSummary,
  showBanner,
  statusLabel,
  timezoneNote,
  waitingChannels,
  type ApprovalChannel,
  type SendApprovalState,
} from "@/lib/sendApproval";

function state(overrides: Partial<SendApprovalState> = {}): SendApprovalState {
  return {
    enabled: true,
    date: "2026-10-11",
    timezone: "UTC",
    timezone_source: "utc_fallback",
    valid_until: "2026-10-12T00:00:00+00:00",
    status: "pending",
    approved_channels: [],
    gated_channels: ["email", "linkedin", "whatsapp", "phone"],
    queued: { email: 3, linkedin: 2, whatsapp: 0, phone: 0 },
    queued_total: 5,
    linkedin_risk_acknowledged: false,
    linkedin_warning_version_acknowledged: null,
    decided_at: null,
    decided_by_user_id: null,
    notified_at: null,
    linkedin_warning: {
      version: "li-automation-2026-10-11",
      text: "Vectorae does not send anything on LinkedIn. "
        + "Do you want today's LinkedIn steps added to your LinkedIn tasks?",
      question: "Do you want today's LinkedIn steps added to your LinkedIn tasks?",
    },
    channel_labels: { email: "Email", linkedin: "LinkedIn", whatsapp: "WhatsApp",
                      phone: "AI phone calls" },
    can_decide: true,
    ...overrides,
  };
}

const sel = (...c: ApprovalChannel[]) => new Set<ApprovalChannel>(c);

describe("initialSelection", () => {
  it("starts empty when nothing is approved", () => {
    expect([...initialSelection(state())]).toEqual([]);
  });

  it("never pre-ticks LinkedIn, even when it was approved earlier today", () => {
    const s = state({ status: "approved", approved_channels: ["email", "linkedin"] });
    expect([...initialSelection(s)]).toEqual(["email"]);
  });
});

describe("canApprove", () => {
  it("needs at least one channel", () => {
    expect(canApprove(sel(), false).ok).toBe(false);
  });

  it("refuses LinkedIn without the risk acknowledgement", () => {
    const check = canApprove(sel("email", "linkedin"), false);
    expect(check.ok).toBe(false);
    expect(check.reason).toMatch(/send each LinkedIn message yourself/);
    expect(canApprove(sel("email", "linkedin"), true).ok).toBe(true);
  });

  it("does not ask for the acknowledgement when LinkedIn is not selected", () => {
    expect(canApprove(sel("email"), false).ok).toBe(true);
  });
});

describe("buildApprovePayload", () => {
  it("sends only channels for email-only approval, no LinkedIn fields", () => {
    expect(buildApprovePayload(state(), sel("email"), true)).toEqual({ channels: ["email"] });
  });

  it("carries the acknowledgement and the warning version shown for LinkedIn", () => {
    expect(buildApprovePayload(state(), sel("linkedin", "email"), true)).toEqual({
      channels: ["email", "linkedin"],
      linkedin_risk_acknowledged: true,
      linkedin_warning_version: "li-automation-2026-10-11",
    });
  });
});

describe("banner", () => {
  it("shows while automatic messages wait for a decision", () => {
    expect(showBanner(state())).toBe(true);
    expect(bannerText(state())).toBe("5 automatic messages are waiting for today's approval.");
  });

  it("hides when the flag is off, nothing is queued, or everything queued is approved", () => {
    expect(showBanner(state({ enabled: false }))).toBe(false);
    expect(showBanner(state({ queued: { email: 0, linkedin: 0 } }))).toBe(false);
    expect(showBanner(state({ status: "approved",
                              approved_channels: ["email", "linkedin"] }))).toBe(false);
    expect(showBanner(null)).toBe(false);
  });

  it("keeps showing for a queued channel that was not approved", () => {
    const s = state({ status: "approved", approved_channels: ["email"] });
    expect(waitingChannels(s)).toEqual(["linkedin"]);
    expect(showBanner(s)).toBe(true);
    expect(bannerText(s)).toBe("2 automatic messages are waiting for today's approval.");
  });

  it("says so after a decline", () => {
    expect(bannerText(state({ status: "declined" })))
      .toBe("Automatic sending is off for today. 5 automatic messages are waiting.");
  });
});

describe("labels", () => {
  it("lists gated channels in a fixed order with counts", () => {
    const s = state({ gated_channels: ["linkedin", "email"] });
    expect(gatedChannels(s)).toEqual(["email", "linkedin"]);
    expect(queuedSummary(s)).toBe("Email: 3 · LinkedIn: 2");
  });

  it("explains the UTC fallback", () => {
    expect(timezoneNote(state())).toMatch(/UTC/);
    expect(timezoneNote(state({ timezone: "Asia/Karachi",
                                timezone_source: "calendar_availability" })))
      .toMatch(/Asia\/Karachi/);
  });

  it("names the status", () => {
    expect(statusLabel(state())).toBe("Waiting for your decision");
    expect(statusLabel(state({ status: "approved" }))).toBe("Approved for today");
    expect(statusLabel(state({ enabled: false }))).toMatch(/turned off/);
  });
});

describe("push deep link", () => {
  it("routes the once-a-day push (clienthunter:///send-approval) to the approval page", () => {
    const route = parseDeepLink("clienthunter:///send-approval");
    expect(route).toEqual({ path: "/send-approval", params: {} });
    expect(routeToPath(route!)).toBe("/send-approval");
  });
});
