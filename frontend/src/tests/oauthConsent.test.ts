import { afterEach, describe, expect, it } from "vitest";
import {
  consentPath,
  isRequestId,
  isSafeClientRedirect,
  rememberConsentRequest,
  takeConsentReturn,
  withConsentReturn,
} from "@/lib/oauthConsent";

const ID = "3f2b9c1e-1234-4abc-8def-0123456789ab";

afterEach(() => window.sessionStorage.clear());

describe("connector consent return", () => {
  it("accepts only a request id shaped like a UUID", () => {
    expect(isRequestId(ID)).toBe(true);
    expect(isRequestId("../../pricing")).toBe(false);
    expect(isRequestId("https://evil.example")).toBe(false);
    expect(isRequestId(null)).toBe(false);
  });

  it("remembers a pending request once and returns its consent path", () => {
    rememberConsentRequest(ID);
    expect(takeConsentReturn()).toBe(`/oauth/consent/?request=${ID}`);
    expect(takeConsentReturn()).toBeNull();
  });

  it("never stores or returns anything but a request id", () => {
    rememberConsentRequest("//evil.example");
    expect(takeConsentReturn()).toBeNull();
    window.sessionStorage.setItem("lp_oauth_consent_request", "/pricing?x=1");
    expect(takeConsentReturn()).toBeNull();
  });

  it("replaces only the dashboard and pricing destinations", () => {
    const back = consentPath(ID);
    expect(withConsentReturn("/pipeline", back)).toBe(back);
    expect(withConsentReturn("/pricing", back)).toBe(back);
    expect(withConsentReturn("/check-email?email=a%40b.c", back)).toBe("/check-email?email=a%40b.c");
    expect(withConsentReturn("/onboarding/verify", back)).toBe("/onboarding/verify");
    expect(withConsentReturn("/pipeline", null)).toBe("/pipeline");
  });
});

describe("leaving Vectorae after consent", () => {
  it("allows the callbacks the server can issue", () => {
    expect(isSafeClientRedirect("https://claude.ai/api/mcp/auth_callback?code=x&iss=y")).toBe(true);
    expect(isSafeClientRedirect("https://chatgpt.com/connector_platform_oauth_redirect?code=x")).toBe(true);
    expect(isSafeClientRedirect("http://localhost:53682/callback?code=x")).toBe(true);
    expect(isSafeClientRedirect("http://127.0.0.1/callback?error=access_denied")).toBe(true);
  });

  it("refuses anything else", () => {
    expect(isSafeClientRedirect("javascript:alert(1)")).toBe(false);
    expect(isSafeClientRedirect("http://evil.example/callback")).toBe(false);
    expect(isSafeClientRedirect("http://localhost/other")).toBe(false);
    expect(isSafeClientRedirect("not a url")).toBe(false);
  });
});
