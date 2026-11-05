/** Where a signed-in user belongs — pure, so every rule is unit-tested in
 *  src/tests/post-login.test.ts.
 *
 *  PAYWALL (founder decision 2026-11-05): an account with no purchased plan is
 *  sent to /pricing on sign-in AND on every app load (planGateRedirect, run by
 *  components/shell/Shell.tsx). The server enforces the same rule with 402
 *  SUBSCRIPTION_REQUIRED (app/services/auth.py::check_plan_active); this is the
 *  routing half, so a no-plan user sees pricing instead of a page of errors.
 *  The SERVER decides the facts (email_verified, has_active_plan); this only
 *  turns them into a route. */

import type { UserOut } from "./api/types";
import { needsVerification } from "./identity";

export const DASHBOARD_PATH = "/pipeline";
export const PRICING_PATH = "/pricing";

type RoutingUser = Pick<UserOut,
  "email" | "email_verified" | "identity_required" | "identity_complete" |
  "phone_verified" | "has_active_plan">;

export function postLoginDestination(
  user: RoutingUser,
  opts: { phoneDeferred?: boolean; afterLogin?: boolean } = {},
): string {
  if (user.email_verified === false) {
    return `/check-email?email=${encodeURIComponent(user.email)}`;
  }
  if (needsVerification(user, opts.phoneDeferred ?? false)) return "/onboarding/verify";
  // Only an explicit `false` gates: a response without the field is from a
  // backend that predates it, and must not send anyone to pricing.
  // `afterLogin` is accepted for callers but no longer changes the answer.
  if (user.has_active_plan === false) return PRICING_PATH;
  return DASHBOARD_PATH;
}

/** App pages an account WITHOUT a plan may still open: its profile (plan
 *  card, data export, account deletion), identity verification, and a team
 *  invite (a member of a paying owner gets access). Matches the server's
 *  exempt list in app/services/auth.py. */
const NO_PLAN_PATHS = ["/profile", "/onboarding/verify", "/invite"];

/** Where Shell must send a signed-in user before rendering PATHNAME, or null.
 *  `search` is the query string: the Stripe success page
 *  (/settings?billing=success) is let through so a just-paid customer is not
 *  bounced to pricing while the payment webhook is still on its way. */
export function planGateRedirect(
  user: Pick<UserOut, "has_active_plan">,
  pathname: string,
  search = "",
): string | null {
  if (user.has_active_plan !== false) return null;
  const path = pathname.replace(/\/+$/, "") || "/";
  if (NO_PLAN_PATHS.some((p) => path === p || path.startsWith(`${p}/`))) return null;
  if (path === "/settings" && new URLSearchParams(search).get("billing") === "success") return null;
  return PRICING_PATH;
}
