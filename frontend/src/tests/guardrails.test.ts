/**
 * F05 Guardrails Studio — pure helpers and the API write bodies.
 * The server normalises and enforces; these only label and summarise.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  importSummary,
  reasonLabel,
  sourceLabel,
  splitLibrary,
  syncSummary,
  type LibraryEntry,
} from "@/lib/guardrails";
import {
  addExclusion,
  addLibraryEntry,
  importExclusions,
  listExclusions,
  listLibrary,
  removeLibraryEntry,
  syncExclusionsFromCrm,
} from "@/lib/api/guardrails";
import { categoryLabel, sourceLabel as claimSourceLabel } from "@/lib/claims";

describe("labels", () => {
  it("names reasons and sources, and falls back readably", () => {
    expect(reasonLabel("open_deal")).toBe("Open deal");
    expect(reasonLabel("brand_new")).toBe("brand new");
    expect(sourceLabel("crm_hubspot")).toBe("HubSpot");
    expect(sourceLabel("crm_local")).toBe("Vectorae CRM");
  });

  it("labels sender claims in the claim-check panel", () => {
    expect(categoryLabel("sender_claim")).toBe("Claim about us");
    expect(claimSourceLabel("approved_claim:5b0c")).toBe("Approved claim");
  });
});

describe("importSummary", () => {
  it("reports only what happened", () => {
    expect(importSummary({ added: 3, reactivated: 0, exists: 0, rows: 3, error_count: 0,
                           errors: [] })).toBe("3 added");
    expect(importSummary({ added: 2, reactivated: 1, exists: 4, rows: 8, error_count: 1,
                           errors: [{ row: 5, error: "bad" }] }))
      .toBe("2 added, 1 re-activated, 4 already listed, 1 row skipped");
    expect(importSummary({ added: 0, reactivated: 0, exists: 0, rows: 2, error_count: 2,
                           errors: [] })).toBe("0 added, 2 rows skipped");
  });
});

describe("syncSummary", () => {
  it("has one line per source, failures included", () => {
    expect(syncSummary({
      local: { status: "ok", added: 2, reactivated: 0, exists: 1, invalid: 0 },
      hubspot: { status: "error", error: "HTTP 401" },
      salesforce: { status: "ok", added: 0, reactivated: 1, exists: 3 },
    })).toEqual([
      "Vectorae CRM: 2 added, 1 already listed",
      "HubSpot: failed — HTTP 401",
      "Salesforce: 0 added, 1 re-activated, 3 already listed",
    ]);
  });
});

describe("splitLibrary", () => {
  it("separates approved claims from forbidden phrases", () => {
    const entry = (id: string, kind: LibraryEntry["kind"]): LibraryEntry => ({
      id, kind, text: id, product_id: null, source_url: null, created_at: null });
    const { claims, phrases } = splitLibrary([entry("a", "forbidden_phrase"),
                                              entry("b", "approved_claim"),
                                              entry("c", "forbidden_phrase")]);
    expect(claims.map((c) => c.id)).toEqual(["b"]);
    expect(phrases.map((p) => p.id)).toEqual(["a", "c"]);
  });
});

// ---- the wire: objects, not JSON strings (src/tests/apiWriteBodies.test.ts) --

let calls: { url: string; init: RequestInit }[] = [];

beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify({ items: [] }), {
      status: 200, headers: { "Content-Type": "application/json" },
    });
  }));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function last() {
  return calls[calls.length - 1];
}

function sentBody(): unknown {
  const raw = last().init.body;
  expect(typeof raw).toBe("string");
  return JSON.parse(raw as string);
}

describe("guardrails API helpers", () => {
  it("addExclusion posts an object", async () => {
    await addExclusion({ kind: "domain", value: "acme.com", reason: "customer" });
    expect(last().url).toMatch(/\/guardrails\/exclusions$/);
    expect(last().init.method).toBe("POST");
    expect(sentBody()).toEqual({ kind: "domain", value: "acme.com", reason: "customer" });
  });

  it("importExclusions sends the CSV text and the default reason", async () => {
    await importExclusions("type,value\ndomain,acme.com\n", "churned");
    expect(sentBody()).toEqual({ csv: "type,value\ndomain,acme.com\n",
                                 default_reason: "churned" });
  });

  it("syncExclusionsFromCrm is a POST", async () => {
    await syncExclusionsFromCrm();
    expect(last().init.method).toBe("POST");
    expect(last().url).toMatch(/\/guardrails\/exclusions\/crm-sync$/);
  });

  it("addLibraryEntry posts an object", async () => {
    await addLibraryEntry({ kind: "forbidden_phrase", text: "act now", product_id: "p1" });
    expect(sentBody()).toEqual({ kind: "forbidden_phrase", text: "act now", product_id: "p1" });
  });

  it("builds list query strings", async () => {
    await listExclusions({ kind: "company", q: "acme", includeRemoved: true });
    expect(last().url).toMatch(
      /\/guardrails\/exclusions\?kind=company&q=acme&include_removed=true&limit=200$/);
    await listLibrary("p1");
    expect(last().url).toMatch(/\/guardrails\/library\?product_id=p1$/);
    await listLibrary(null);
    expect(last().url).toMatch(/\/guardrails\/library$/);
  });

  it("removeLibraryEntry is a DELETE", async () => {
    await removeLibraryEntry("e1");
    expect(last().init.method).toBe("DELETE");
    expect(last().url).toMatch(/\/guardrails\/library\/e1$/);
  });
});
