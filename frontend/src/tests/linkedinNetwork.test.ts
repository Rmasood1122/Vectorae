import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/client";
import { eraseNetwork, importConnections } from "@/lib/api/network";
import {
  checkExportFile, importErrorMessage, importSummary, safeProfileUrl, scoreLabel,
} from "@/lib/linkedinNetwork";

let calls: { url: string; init: RequestInit }[] = [];
beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
  }));
});
afterEach(() => { vi.unstubAllGlobals(); });

describe("wire", () => {
  it("posts the csv text as a JSON object", async () => {
    await importConnections("First Name,Last Name\nA,B\n",
                            { version: "2026-11-02.1", lawfulBasis: "legitimate_interests" });
    expect(calls[0].url).toContain("/linkedin-network/import");
    expect(calls[0].init.method).toBe("POST");
    expect(JSON.parse(calls[0].init.body as string)).toEqual({
      csv_text: "First Name,Last Name\nA,B\n",
      consent_text_version: "2026-11-02.1",
      lawful_basis: "legitimate_interests",
    });
  });
  it("erases with DELETE on the collection root", async () => {
    await eraseNetwork();
    expect(calls[0].url).toMatch(/\/linkedin-network$/);
    expect(calls[0].init.method).toBe("DELETE");
  });
});

describe("rules", () => {
  it("rejects non-csv, empty and oversized files", () => {
    expect(checkExportFile({ name: "a.pdf", size: 10 }, 100)).not.toBeNull();
    expect(checkExportFile({ name: "a.csv", size: 0 }, 100)).not.toBeNull();
    expect(checkExportFile({ name: "a.csv", size: 101 }, 100)).not.toBeNull();
    expect(checkExportFile({ name: "Connections.CSV", size: 50 }, 100)).toBeNull();
  });
  it("maps 402 to the upgrade message", () => {
    expect(importErrorMessage(new ApiError(402, "x"))).toMatch(/Growth/);
    expect(importErrorMessage(new Error("boom"))).toBe("Import failed. Try again.");
  });
  it("summarises and labels", () => {
    expect(importSummary({ added: 1, skipped: 0, matched: 1 })).toBe("Added 1 connection. 1 match for your products.");
    expect(scoreLabel(80)).toBe("Strong");
    expect(scoreLabel(55)).toBe("Good");
    expect(scoreLabel(31)).toBe("Possible");
  });
  it("only links to https linkedin.com", () => {
    expect(safeProfileUrl("https://www.linkedin.com/in/x")).not.toBeNull();
    expect(safeProfileUrl("javascript:alert(1)")).toBeNull();
    expect(safeProfileUrl("https://evil.com/linkedin.com")).toBeNull();
    expect(safeProfileUrl(null)).toBeNull();
  });
});
