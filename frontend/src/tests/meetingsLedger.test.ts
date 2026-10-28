import { describe, expect, it } from "vitest";
import {
  DISABLED_STATUS,
  certaintyCaveat,
  exportFilename,
  infrastructureLabel,
  isDisabledError,
  ledgerExportPath,
  ledgerQueryString,
  methodMixLabel,
  reconciliationLabel,
} from "@/lib/meetingsLedger";

const query = { dateFrom: "2026-09-01", dateTo: "2026-09-30" };

function mix(overrides: Record<string, number> = {}) {
  return { direct_reply: 0, thread_match: 0, last_touch: 0, none: 0, pending: 0, ...overrides };
}

describe("query and export URLs", () => {
  it("carries the period and an optional campaign", () => {
    expect(ledgerQueryString(query)).toBe("date_from=2026-09-01&date_to=2026-09-30");
    expect(ledgerQueryString({ ...query, strategyId: "s1" }))
      .toBe("date_from=2026-09-01&date_to=2026-09-30&strategy_id=s1");
  });
  it("builds the export path, adding view only for the events CSV", () => {
    expect(ledgerExportPath(query, "csv"))
      .toBe("/analytics/meetings-ledger/export?date_from=2026-09-01&date_to=2026-09-30&format=csv");
    expect(ledgerExportPath(query, "csv", "events")).toContain("view=events");
    expect(ledgerExportPath(query, "pdf", "events")).not.toContain("view=");
  });
  it("names files after the period", () => {
    expect(exportFilename(query, "pdf")).toBe("vectorae-meetings-ledger-2026-09-01-2026-09-30.pdf");
    expect(exportFilename(query, "csv", "events"))
      .toBe("vectorae-meetings-ledger-2026-09-01-2026-09-30-events.csv");
  });
});

describe("isDisabledError", () => {
  it("recognises the flag-off 409 and nothing else", () => {
    expect(isDisabledError({ status: DISABLED_STATUS })).toBe(true);
    expect(isDisabledError({ status: 404 })).toBe(false);
    expect(isDisabledError(new Error("x"))).toBe(false);
    expect(isDisabledError(null)).toBe(false);
  });
});

describe("certaintyCaveat", () => {
  it("is silent with no bookings or when most are confirmed", () => {
    expect(certaintyCaveat(null)).toBeNull();
    expect(certaintyCaveat({ meetings_booked: 0, booked_certain: 0, booked_by_method: mix() })).toBeNull();
    expect(certaintyCaveat({ meetings_booked: 2, booked_certain: 1,
                             booked_by_method: mix({ direct_reply: 1, last_touch: 1 }) })).toBeNull();
  });
  it("warns when under half are confirmed by a reply", () => {
    const text = certaintyCaveat({ meetings_booked: 4, booked_certain: 1,
                                   booked_by_method: mix({ direct_reply: 1, last_touch: 3 }) });
    expect(text).toContain("Only 25%");
    expect(text).toContain("guess");
  });
  it("names pending bookings separately", () => {
    const text = certaintyCaveat({ meetings_booked: 2, booked_certain: 1,
                                   booked_by_method: mix({ direct_reply: 1, pending: 1 }) });
    expect(text).toBe("1 booking is not attributed yet (the attribution sweep runs every 15 minutes).");
  });
});

describe("methodMixLabel", () => {
  it("lists non-zero methods in a fixed order", () => {
    expect(methodMixLabel(mix({ pending: 1, direct_reply: 3, last_touch: 2 })))
      .toBe("3 confirmed · 2 guessed · 1 pending");
    expect(methodMixLabel(mix())).toBe("—");
  });
});

describe("infrastructureLabel", () => {
  it("says plainly when infrastructure is not included", () => {
    expect(infrastructureLabel({ cents_per_1k_sends: 0, included: false, excluded_non_usd_cents: 0 }))
      .toBe("Infrastructure not included — the admin rate is 0.");
    expect(infrastructureLabel({ cents_per_1k_sends: 250, included: false, excluded_non_usd_cents: 3 }))
      .toContain("not included in this currency");
  });
  it("states the rate when it is included", () => {
    expect(infrastructureLabel({ cents_per_1k_sends: 250, included: true, excluded_non_usd_cents: 0 }))
      .toBe("Infrastructure included at $2.50 per 1,000 sends (admin setting).");
  });
});

describe("reconciliationLabel", () => {
  const ok = { balanced: true, decomposition_balanced: true, difference_cents: 0,
               booked_outcome_leads: 4, ledger_meetings_booked: 4 };
  it("is ok only when cost and bookings both reconcile", () => {
    expect(reconciliationLabel(ok)).toEqual({ ok: true, text: "Reconciled to the raw cost and outcome rows." });
    expect(reconciliationLabel({ ...ok, balanced: false, difference_cents: 12 }).ok).toBe(false);
    expect(reconciliationLabel({ ...ok, decomposition_balanced: false }).ok).toBe(false);
    const counts = reconciliationLabel({ ...ok, ledger_meetings_booked: 3 });
    expect(counts.ok).toBe(false);
    expect(counts.text).toContain("3 booked vs 4");
  });
});
