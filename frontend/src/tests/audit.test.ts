import { describe, expect, it } from "vitest";
import { exportFilename, shortHash, verificationSummary } from "@/lib/audit";

const HEAD = "a".repeat(64);

describe("verificationSummary", () => {
  it("is neutral before a check", () => {
    expect(verificationSummary(null).label).toBe("Not verified yet");
  });
  it("reports an intact chain with its head", () => {
    const s = verificationSummary({ valid: true, sealed: 1200, unsealed: 0, head_seq_no: 1200,
                                    head_hash: HEAD, problems: [] });
    expect(s.tone).toBe("success");
    expect(s.detail).toBe("1,200 records, head #1200 aaaaaaaaaaaa…");
  });
  it("reports an empty trail plainly", () => {
    expect(verificationSummary({ valid: true, sealed: 0, unsealed: 0, head_seq_no: 0,
                                 head_hash: "0".repeat(64), problems: [] }).detail)
      .toBe("No activity recorded yet");
  });
  it("groups problems into a readable sentence", () => {
    const s = verificationSummary({
      valid: false, sealed: 3, unsealed: 0, head_seq_no: 3, head_hash: HEAD,
      problems: [{ seq_no: 2, problem: "content_modified" }, { seq_no: 3, problem: "broken_link" },
                 { seq_no: 3, problem: "sequence_gap", expected: 2 }],
    });
    expect(s.tone).toBe("destructive");
    expect(s.label).toBe("Tampering detected");
    expect(s.detail).toContain("1 records whose content was changed");
    expect(s.detail).toContain("1 records missing from the sequence");
  });
});

describe("formatting", () => {
  it("shortens hashes", () => {
    expect(shortHash(HEAD)).toBe("aaaaaaaaaaaa…");
    expect(shortHash("abc")).toBe("abc");
    expect(shortHash(null)).toBe("—");
  });
  it("names exports by date", () => {
    expect(exportFilename("pdf", new Date("2026-09-13T12:00:00Z"))).toBe("vectorae-audit-2026-09-13.pdf");
  });
});
