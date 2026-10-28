/** Connector consent (docs/features/mcp-connector.md): pure helpers, unit-
 *  tested in src/tests/oauthConsent.test.ts.
 *
 *  Claude or ChatGPT sends the browser to /oauth/consent/?request=<id>. A
 *  person who is not signed in goes to /login first; the pending request id
 *  is kept in sessionStorage (this tab only) so sign-in can bring them back.
 *  Only a well-formed id is ever stored or returned, so the "return to" path
 *  can never be pointed anywhere else. */

const KEY = "lp_oauth_consent_request";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const CONSENT_PATH = "/oauth/consent/";

export function isRequestId(value: string | null | undefined): value is string {
  return typeof value === "string" && UUID.test(value);
}

export function consentPath(requestId: string): string {
  return `${CONSENT_PATH}?request=${encodeURIComponent(requestId)}`;
}

export function rememberConsentRequest(requestId: string): void {
  if (!isRequestId(requestId)) return;
  try {
    window.sessionStorage.setItem(KEY, requestId);
  } catch {
    // Storage blocked: the person returns to Claude and connects again.
  }
}

/** The consent page to return to after sign-in, once; null if none. */
export function takeConsentReturn(): string | null {
  try {
    const id = window.sessionStorage.getItem(KEY);
    window.sessionStorage.removeItem(KEY);
    return isRequestId(id) ? consentPath(id) : null;
  } catch {
    return null;
  }
}

/** Sign-in routing with a pending consent: the routes that need the person
 *  to act first (verify email, verify identity) still win; otherwise the
 *  consent page replaces the dashboard or pricing destination. */
export function withConsentReturn(destination: string, consentReturn: string | null): string {
  if (!consentReturn) return destination;
  if (destination === "/pipeline" || destination === "/pricing") return consentReturn;
  return destination;
}

/** The browser leaves Vectorae only for a URI the server could have
 *  issued: https, or an http loopback callback (Claude Code). */
export function isSafeClientRedirect(target: string): boolean {
  let url: URL;
  try {
    url = new URL(target);
  } catch {
    return false;
  }
  if (url.protocol === "https:") return true;
  return url.protocol === "http:" && (url.hostname === "localhost" || url.hostname === "127.0.0.1")
    && url.pathname === "/callback";
}
