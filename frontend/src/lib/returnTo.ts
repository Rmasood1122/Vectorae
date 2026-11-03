/** "Take me back where I was going" after sign-in. Pure helpers, unit-tested
 *  in src/tests/returnTo.test.ts.
 *
 *  A signed-out visitor who opens a protected page (an /admin/... link, a
 *  bookmarked app page) is sent to /login; the page they asked for is kept in
 *  sessionStorage (this tab only) and sign-in goes back to it once.
 *
 *  Only a same-site PATH is ever stored or returned -- never a full URL, a
 *  protocol-relative "//host" or a backslash form browsers treat as one -- so
 *  this can never become an open redirect. Auth pages are never stored. */

const KEY = "lp_return_to";
const MAX_LENGTH = 512;
const AUTH_PAGES = ["/login", "/signup", "/check-email", "/forgot-password",
                    "/reset-password", "/oauth"];

export function isSafeReturnPath(value: string | null | undefined): value is string {
  if (typeof value !== "string" || value.length === 0 || value.length > MAX_LENGTH) return false;
  if (!value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return false;
  // Control characters (tabs, newlines) are stripped by browsers in URLs and
  // can turn "/\t/evil.com" into "//evil.com".
  if (/[\u0000-\u001f\u007f]/.test(value)) return false;
  const path = value.split(/[?#]/, 1)[0];
  if (path === "/") return false;
  return !AUTH_PAGES.some((page) => path === page || path.startsWith(`${page}/`));
}

export function rememberReturnTo(path: string): void {
  if (!isSafeReturnPath(path)) return;
  try {
    window.sessionStorage.setItem(KEY, path);
  } catch {
    // Storage blocked: sign-in falls back to the dashboard.
  }
}

/** The page to return to after sign-in, once; null if none. */
export function takeReturnTo(): string | null {
  try {
    const path = window.sessionStorage.getItem(KEY);
    window.sessionStorage.removeItem(KEY);
    return isSafeReturnPath(path) ? path : null;
  } catch {
    return null;
  }
}

/** Sign-in routing with a remembered page: anything the person must do first
 *  (verify email or identity, choose a plan) still wins; only the default
 *  dashboard destination is replaced. */
export function withReturnTo(destination: string, returnTo: string | null): string {
  if (!returnTo) return destination;
  return destination === "/pipeline" ? returnTo : destination;
}

/** The current page as a path (+ query), for a guard about to send to /login. */
export function currentPath(): string {
  try {
    return `${window.location.pathname}${window.location.search}`;
  } catch {
    return "";
  }
}
