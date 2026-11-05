/**
 * "/" is the marketing home page on the web, and the app's front door in the
 * Android app (Capacitor loads the same export) and an installed PWA
 * (src/lib/homeEntry.ts). The app must still land users in the app, routed
 * exactly as the old redirect-only page did.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";
import type { UserOut } from "@/lib/api/types";
import {
  APP_SHELL_SCRIPT,
  appEntryDestination,
  homeMode,
  isAppBuild,
  signedInAppLink,
  type EntryDeps,
} from "@/lib/homeEntry";

const ready = {
  email: "founder@agency.com",
  email_verified: true,
  identity_required: false,
  identity_complete: true,
  phone_verified: true,
  has_active_plan: true,
} as unknown as UserOut;

const unverified = (e: unknown) => e instanceof ApiError && e.status === 403;

function deps(over: Partial<EntryDeps> = {}): EntryDeps {
  return {
    restoreSession: vi.fn().mockResolvedValue(true),
    me: vi.fn().mockResolvedValue(ready),
    phoneDeferred: () => false,
    isUnverifiedError: unverified,
    ...over,
  };
}

describe("homeMode: who sees marketing", () => {
  it("a browser on the web build sees the home page", () => {
    expect(homeMode({ buildTarget: "web", native: false, standalone: false })).toBe("marketing");
    expect(homeMode({ buildTarget: undefined, native: false, standalone: false })).toBe("marketing");
  });

  it("the Android app is the app, whatever the build target", () => {
    expect(homeMode({ buildTarget: "web", native: true, standalone: false })).toBe("app");
  });

  it("a mobile build is the app even before the runtime check", () => {
    for (const t of ["mobile-prod", "mobile-dev", "MOBILE-prod"]) {
      expect(isAppBuild(t)).toBe(true);
      expect(homeMode({ buildTarget: t, native: false, standalone: false })).toBe("app");
    }
    expect(isAppBuild("web")).toBe(false);
    expect(isAppBuild(undefined)).toBe(false);
  });

  it("an installed PWA (display-mode standalone) is the app", () => {
    expect(homeMode({ buildTarget: "web", native: false, standalone: true })).toBe("app");
  });
});

describe("appEntryDestination: the app routes as the old page did", () => {
  it("no session -> /login, without asking for the user", async () => {
    const d = deps({ restoreSession: vi.fn().mockResolvedValue(false) });
    expect(await appEntryDestination(d)).toBe("/login");
    expect(d.me).not.toHaveBeenCalled();
  });

  // Paywall (founder decision 2026-11-05): a restore now checks the plan too.
  it("a restored session without a plan -> pricing", async () => {
    const noPlan = { ...ready, has_active_plan: false } as UserOut;
    expect(await appEntryDestination(deps({ me: vi.fn().mockResolvedValue(noPlan) }))).toBe("/pricing");
  });

  it("a restored session with a plan -> the dashboard", async () => {
    expect(await appEntryDestination(deps({ me: vi.fn().mockResolvedValue(ready as UserOut) }))).toBe("/pipeline");
  });

  it("an unverified email -> /check-email?email=...", async () => {
    const u = { ...ready, email_verified: false } as UserOut;
    expect(await appEntryDestination(deps({ me: vi.fn().mockResolvedValue(u) })))
      .toBe("/check-email?email=founder%40agency.com");
  });

  it("identity verification still owed -> /onboarding/verify, unless the phone step was deferred", async () => {
    const u = { ...ready, identity_required: true, phone_verified: false } as UserOut;
    expect(await appEntryDestination(deps({ me: vi.fn().mockResolvedValue(u) }))).toBe("/onboarding/verify");
    expect(await appEntryDestination(deps({ me: vi.fn().mockResolvedValue(u), phoneDeferred: () => true })))
      .toBe("/pipeline");
  });

  it("/auth/me refusing as unverified -> /check-email; any other failure -> the dashboard", async () => {
    const refused = new ApiError(403, "EMAIL_NOT_VERIFIED");
    expect(await appEntryDestination(deps({ me: vi.fn().mockRejectedValue(refused) }))).toBe("/check-email");
    expect(await appEntryDestination(deps({ me: vi.fn().mockRejectedValue(new Error("network")) })))
      .toBe("/pipeline");
  });
});

describe("signedInAppLink: the web header never redirects", () => {
  it("anonymous visitor -> null (header shows Sign in)", async () => {
    expect(await signedInAppLink(deps({ restoreSession: vi.fn().mockResolvedValue(false) }))).toBeNull();
  });

  it("a restore that throws -> null, the page still works", async () => {
    expect(await signedInAppLink(deps({ restoreSession: vi.fn().mockRejectedValue(new Error("x")) }))).toBeNull();
  });

  it("a signed-in visitor -> where their session belongs, restoring only once", async () => {
    const d = deps();
    expect(await signedInAppLink(d)).toBe("/pipeline");
    expect(d.restoreSession).toHaveBeenCalledTimes(1);
  });
});

describe("APP_SHELL_SCRIPT: no marketing flash inside the app", () => {
  afterEach(() => {
    document.documentElement.removeAttribute("data-app-shell");
    delete (window as unknown as { Capacitor?: unknown }).Capacitor;
    delete (window as unknown as { androidBridge?: unknown }).androidBridge;
    vi.unstubAllGlobals();
  });

  const run = () => new Function(APP_SHELL_SCRIPT)();

  it("marks <html> in the Android WebView (androidBridge, as @capacitor/core detects it)", () => {
    (window as unknown as { androidBridge: unknown }).androidBridge = { postMessage() {} };
    run();
    expect(document.documentElement.getAttribute("data-app-shell")).toBe("1");
  });

  it("marks <html> when a native window.Capacitor says so", () => {
    (window as unknown as { Capacitor: unknown }).Capacitor = { isNativePlatform: () => true };
    run();
    expect(document.documentElement.getAttribute("data-app-shell")).toBe("1");
  });

  it("marks <html> in an installed PWA", () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q === "(display-mode: standalone)" }));
    run();
    expect(document.documentElement.getAttribute("data-app-shell")).toBe("1");
  });

  it("leaves a normal browser alone, including Capacitor's web shim", () => {
    (window as unknown as { Capacitor: unknown }).Capacitor = { isNativePlatform: () => false };
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    run();
    expect(document.documentElement.hasAttribute("data-app-shell")).toBe(false);
  });

  it("the CSS hides the home page for a marked <html>", async () => {
    const { readFileSync } = await import("node:fs");
    expect(readFileSync("src/app/globals.css", "utf8"))
      .toMatch(/html\[data-app-shell\]\s+\.lp-home\s*\{\s*display:\s*none/);
  });
});
