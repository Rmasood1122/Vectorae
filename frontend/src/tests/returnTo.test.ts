/** Sign-in returns to the page the visitor asked for (lib/returnTo.ts). */
import { beforeEach, describe, expect, it } from "vitest";
import {
  isSafeReturnPath, rememberReturnTo, takeReturnTo, withReturnTo,
} from "@/lib/returnTo";

describe("isSafeReturnPath", () => {
  it.each(["/admin/growth", "/admin/growth?days=7", "/leads/123#notes", "/pipeline"])(
    "accepts the same-site path %s", (path) => expect(isSafeReturnPath(path)).toBe(true));

  it.each([
    "https://evil.example/x", "//evil.example", "/\\evil.example", "\\\\evil.example",
    "/\t/evil.example", "/\n/evil.example", "javascript:alert(1)", "admin/growth", "",
    "/", "/login", "/login?next=/x", "/signup", "/check-email?email=a", "/oauth/consent/",
    `/${"a".repeat(600)}`,
  ])("refuses %j", (path) => expect(isSafeReturnPath(path)).toBe(false));

  it("refuses non-strings", () => {
    expect(isSafeReturnPath(null)).toBe(false);
    expect(isSafeReturnPath(undefined)).toBe(false);
  });
});

describe("remember / take", () => {
  beforeEach(() => { window.sessionStorage.clear(); });

  it("returns the remembered page exactly once", () => {
    rememberReturnTo("/admin/growth?days=7");
    expect(takeReturnTo()).toBe("/admin/growth?days=7");
    expect(takeReturnTo()).toBeNull();
  });

  it("never stores an unsafe target", () => {
    rememberReturnTo("//evil.example");
    expect(takeReturnTo()).toBeNull();
  });

  it("never returns a tampered stored value", () => {
    window.sessionStorage.setItem("lp_return_to", "https://evil.example");
    expect(takeReturnTo()).toBeNull();
  });
});

describe("withReturnTo", () => {
  it("replaces the default dashboard destination", () => {
    expect(withReturnTo("/pipeline", "/admin/growth")).toBe("/admin/growth");
  });

  it.each(["/check-email?email=a%40b.c", "/onboarding/verify", "/pricing"])(
    "never skips a step the person must do first: %s", (dest) =>
      expect(withReturnTo(dest, "/admin/growth")).toBe(dest));

  it("does nothing without a remembered page", () => {
    expect(withReturnTo("/pipeline", null)).toBe("/pipeline");
  });
});
