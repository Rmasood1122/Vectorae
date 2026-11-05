import { describe, expect, it } from "vitest";
import { planGateRedirect, postLoginDestination } from "@/lib/post-login";

const ready = {
  email: "founder@agency.com",
  email_verified: true,
  identity_required: false,
  identity_complete: true,
  phone_verified: true,
  has_active_plan: true,
};
const afterLogin = { afterLogin: true };

describe("postLoginDestination — right after sign-in", () => {
  it("sends an account with a plan to the dashboard", () => {
    expect(postLoginDestination(ready, afterLogin)).toBe("/pipeline");
  });

  it("sends an account that never bought a plan to pricing", () => {
    expect(postLoginDestination({ ...ready, has_active_plan: false }, afterLogin)).toBe("/pricing");
  });

  it("does not gate on a response that predates has_active_plan", () => {
    expect(postLoginDestination({ ...ready, has_active_plan: undefined }, afterLogin)).toBe("/pipeline");
  });

  it("asks for email verification before anything else", () => {
    expect(postLoginDestination({ ...ready, email_verified: false, has_active_plan: false }, afterLogin))
      .toBe("/check-email?email=founder%40agency.com");
  });

  it("finishes identity verification before pricing (checkout needs a verified phone)", () => {
    const user = { ...ready, identity_required: true, identity_complete: false, has_active_plan: false };
    expect(postLoginDestination(user, afterLogin)).toBe("/onboarding/verify");
  });

  it("respects a deferred phone step", () => {
    const user = { ...ready, identity_required: true, phone_verified: false, has_active_plan: false };
    expect(postLoginDestination(user, afterLogin)).toBe("/onboarding/verify");
    expect(postLoginDestination(user, { ...afterLogin, phoneDeferred: true })).toBe("/pricing");
  });
});

describe("postLoginDestination — silent session restore", () => {
  // Paywall (founder decision 2026-11-05): replaces "never sends a no-plan
  // user to pricing on app load" -- a no-plan account no longer gets the app.
  it("sends a no-plan user to pricing on app load too", () => {
    expect(postLoginDestination({ ...ready, has_active_plan: false })).toBe("/pricing");
  });

  it("sends a paying user to the dashboard", () => {
    expect(postLoginDestination(ready)).toBe("/pipeline");
  });

  it("still enforces email and identity verification", () => {
    expect(postLoginDestination({ ...ready, email_verified: false }))
      .toBe("/check-email?email=founder%40agency.com");
    expect(postLoginDestination({ ...ready, identity_required: true, identity_complete: false }))
      .toBe("/onboarding/verify");
  });
});

describe("planGateRedirect — the paywall on every app page", () => {
  const noPlan = { has_active_plan: false };

  it.each(["/pipeline", "/leads", "/strategies", "/inbox", "/settings", "/crm/dashboard/overview"])(
    "sends a no-plan user from %s to pricing", (path) => {
      expect(planGateRedirect(noPlan, path)).toBe("/pricing");
    });

  it.each(["/profile", "/profile/", "/onboarding/verify", "/invite"])(
    "lets a no-plan user open %s (plan card, data export, delete, verify, invite)", (path) => {
      expect(planGateRedirect(noPlan, path)).toBeNull();
    });

  it("lets a just-paid user land on the Stripe success page", () => {
    expect(planGateRedirect(noPlan, "/settings", "?billing=success")).toBeNull();
    expect(planGateRedirect(noPlan, "/settings", "?billing=other")).toBe("/pricing");
  });

  it("does not match look-alike paths", () => {
    expect(planGateRedirect(noPlan, "/profiles")).toBe("/pricing");
    expect(planGateRedirect(noPlan, "/inviteX")).toBe("/pricing");
  });

  it("never gates a paying user or a backend that predates the field", () => {
    expect(planGateRedirect({ has_active_plan: true }, "/pipeline")).toBeNull();
    expect(planGateRedirect({ has_active_plan: undefined }, "/pipeline")).toBeNull();
  });
});
