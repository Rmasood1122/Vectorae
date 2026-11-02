import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as tasks from "@/lib/api/linkedinTasks";
import { approveTask, completeTask, openTask, safeTaskProfileUrl, taskStatusLabel,
  type LinkedInTask } from "@/lib/api/linkedinTasks";

let calls: { url: string; init: RequestInit }[] = [];
beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
  }));
});
afterEach(() => { vi.unstubAllGlobals(); });

describe("L0 task client", () => {
  it("approves, opens and completes ONE task per call", async () => {
    await approveTask("t1");
    await openTask("t1");
    await completeTask("t1", "done");
    expect(calls.map((c) => c.url.replace(/^.*\/linkedin-tasks/, ""))).toEqual(
      ["/t1/approve", "/t1/open", "/t1/complete"]);
    expect(JSON.parse(calls[2].init.body as string)).toEqual({ outcome: "done" });
  });

  it("exposes no bulk action", () => {
    expect(Object.keys(tasks).filter((k) => /bulk|all/i.test(k))).toEqual([]);
  });

  it("only ever opens a plain member profile link", () => {
    expect(safeTaskProfileUrl("https://www.linkedin.com/in/ana-lopez/")).toBe(
      "https://www.linkedin.com/in/ana-lopez/");
    for (const bad of ["javascript:alert(1)", "https://evil.example/in/ana/",
                       "https://www.linkedin.com/company/acme/", null, ""]) {
      expect(safeTaskProfileUrl(bad)).toBeNull();
    }
  });

  it("labels an expired approval and a suppression cancel", () => {
    const base = { status: "approved", approval_expired: true, cancel_reason: null } as LinkedInTask;
    expect(taskStatusLabel(base)).toBe("Approval expired");
    expect(taskStatusLabel({ ...base, status: "cancelled", cancel_reason: "suppressed" }))
      .toBe("Cancelled: do not contact");
  });
});
