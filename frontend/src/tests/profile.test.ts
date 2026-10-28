import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/client";
import { changePassword, deleteMyAccount, exportMyData, saveName } from "@/lib/api/profile";
import {
  BILLING_RETENTION_YEARS, DELETE_ACCOUNT_COPY, DELETE_CONFIRMATION, accountExportFilename,
  canDeleteAccount, canUpgrade, deleteAccountErrorMessage, exportErrorMessage,
  passwordErrorMessage, planLabel, validateName, validatePasswordChange,
} from "@/lib/profile";
import { NAV } from "@/components/shell/nav";

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
  it("PATCHes the name", async () => {
    await saveName("Ada");
    expect(calls[0].url).toMatch(/\/auth\/me$/);
    expect(calls[0].init.method).toBe("PATCH");
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ full_name: "Ada" });
  });
  it("POSTs the password change with both fields", async () => {
    await changePassword("old-pass-1", "new-pass-2");
    expect(calls[0].url).toMatch(/\/auth\/change-password$/);
    expect(calls[0].init.method).toBe("POST");
    expect(JSON.parse(calls[0].init.body as string)).toEqual({
      current_password: "old-pass-1", new_password: "new-pass-2",
    });
  });
});

describe("data export (GDPR)", () => {
  it("POSTs the password to /me/data-export", async () => {
    await exportMyData("my-pass-1");
    expect(calls[0].url).toMatch(/\/me\/data-export$/);
    expect(calls[0].init.method).toBe("POST");
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ password: "my-pass-1" });
  });
  it("names the file by date", () => {
    expect(accountExportFilename(new Date("2026-10-10T13:00:00Z")))
      .toBe("vectorae-account-export-2026-10-10.json");
  });
  it("maps export errors", () => {
    expect(exportErrorMessage(new ApiError(403, "INVALID_PASSWORD"))).toMatch(/password is not right/);
    expect(exportErrorMessage(new ApiError(429, "x"))).toMatch(/Too many/);
    expect(exportErrorMessage(new Error("boom"))).toMatch(/Try again/);
  });
});

describe("delete account (GDPR Art. 17)", () => {
  it("sends DELETE /me with the password and the typed phrase", async () => {
    await deleteMyAccount("my-pass-1", "DELETE");
    expect(calls[0].url).toMatch(/\/me$/);
    expect(calls[0].init.method).toBe("DELETE");
    expect(JSON.parse(calls[0].init.body as string)).toEqual({
      password: "my-pass-1", confirm: "DELETE",
    });
  });
  it("enables the button only with a password and the exact phrase", () => {
    expect(DELETE_CONFIRMATION).toBe("DELETE");
    expect(canDeleteAccount("pw", "DELETE")).toBe(true);
    expect(canDeleteAccount("", "DELETE")).toBe(false);
    for (const typed of ["", "delete", "Delete", "DELETE ", " DELETE", "DELET"]) {
      expect(canDeleteAccount("pw", typed)).toBe(false);
    }
  });
  it("maps the server's contracts to plain messages", () => {
    expect(deleteAccountErrorMessage(new ApiError(403, "INVALID_PASSWORD"))).toMatch(/password is not right/);
    expect(deleteAccountErrorMessage(new ApiError(422, "CONFIRMATION_REQUIRED"))).toMatch(/Type DELETE/);
    expect(deleteAccountErrorMessage(new ApiError(429, "x"))).toMatch(/Too many/);
    expect(deleteAccountErrorMessage(new ApiError(502, "Could not reach the payment provider")))
      .toMatch(/payment provider/);
    expect(deleteAccountErrorMessage(new ApiError(409, "An administrator account cannot be deleted")))
      .toMatch(/administrator/);
    expect(deleteAccountErrorMessage(new Error("boom"))).toMatch(/Try again/);
  });
  it("says exactly what is kept (E1 billing 7 years, E2 opt-outs, security record)", () => {
    expect(BILLING_RETENTION_YEARS).toBe(7);
    expect(DELETE_ACCOUNT_COPY.kept).toHaveLength(3);
    expect(DELETE_ACCOUNT_COPY.kept[0]).toMatch(/Billing records/);
    expect(DELETE_ACCOUNT_COPY.kept[0]).toMatch(/our copy has no name or email/);
    // Review fix 5: what Stripe and other providers keep is said, not hidden.
    expect(DELETE_ACCOUNT_COPY.outside.join(" ")).toMatch(/Stripe keeps its own customer record/);
    expect(DELETE_ACCOUNT_COPY.erased).toMatch(/nothing is deleted/);
    expect(DELETE_ACCOUNT_COPY.kept[0]).toContain(`${BILLING_RETENTION_YEARS} years`);
    expect(DELETE_ACCOUNT_COPY.kept[1]).toMatch(/opt-in, opt-out and erasure/);
    expect(DELETE_ACCOUNT_COPY.kept[1]).toMatch(/do-not-contact list/);
    expect(DELETE_ACCOUNT_COPY.kept[2]).toMatch(/security record/);
    expect(DELETE_ACCOUNT_COPY.erased).toMatch(/cannot be undone/);
    expect(DELETE_ACCOUNT_COPY.erased).toMatch(/no further charges/);
  });
});

describe("rules", () => {
  it("validates names", () => {
    expect(validateName("Rehan Masood")).toBeNull();
    expect(validateName("")).toBeNull();
    expect(validateName("x".repeat(121))).not.toBeNull();
    expect(validateName("A\u0000B")).not.toBeNull();
  });
  it("validates a password change", () => {
    expect(validatePasswordChange("", "newpass12", "newpass12")).not.toBeNull();
    expect(validatePasswordChange("old", "short", "short")).not.toBeNull();
    expect(validatePasswordChange("samepass1", "samepass1", "samepass1")).not.toBeNull();
    expect(validatePasswordChange("oldpass12", "newpass12", "different1")).not.toBeNull();
    expect(validatePasswordChange("oldpass12", "newpass12", "newpass12")).toBeNull();
  });
  it("maps errors", () => {
    expect(passwordErrorMessage(new ApiError(429, "x"))).toMatch(/Too many/);
    expect(passwordErrorMessage(new ApiError(400, "Current password is incorrect."))).toBe("Current password is incorrect.");
    expect(passwordErrorMessage(new Error("boom"))).toMatch(/Try again/);
  });
  it("labels plans; enterprise has no upgrade", () => {
    expect(planLabel("enterprise")).toBe("Enterprise");
    expect(canUpgrade("enterprise")).toBe(false);
    expect(canUpgrade("free")).toBe(true);
  });
});

describe("shell", () => {
  it("has a Profile entry", () => {
    expect(NAV.some((n) => n.href === "/profile")).toBe(true);
  });
});
