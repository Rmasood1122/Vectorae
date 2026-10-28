/** Pure rules for the forgot-password and reset-password pages
 *  (docs/features/password-reset.md). The server re-checks everything. */
import { ApiError } from "@/lib/api/client";
import { MIN_PASSWORD } from "@/lib/profile";

/** Same ceiling the API applies (PasswordResetConfirmIn.new_password). */
export const MAX_PASSWORD = 256;

/** The confirmation shown after a reset request, whatever the address. The
 *  API never says whether an account exists, so neither may the page. */
export const REQUEST_SENT_MESSAGE =
  "If that address has a Vectorae account, we have emailed it a link to reset " +
  "the password. The link works once and expires after a short time. Check " +
  "your spam folder if it does not arrive within a few minutes.";

/** The token from the emailed link: `/reset-password#token=<token>`.
 *
 *  It travels in the URL FRAGMENT, which browsers never send to a server
 *  (not to the static host, not in a Referer header). A `?token=` query is
 *  accepted too, in case a mail client rewrote the link. */
export function readResetToken(hash: string, search = ""): string | null {
  for (const raw of [hash.replace(/^#/, ""), search.replace(/^\?/, "")]) {
    const token = new URLSearchParams(raw).get("token");
    if (token && /^[A-Za-z0-9_-]{20,512}$/.test(token)) return token;
  }
  return null;
}

export function validateNewPassword(next: string, confirm: string): string | null {
  if (next.length < MIN_PASSWORD) return `Password must be at least ${MIN_PASSWORD} characters.`;
  if (next.length > MAX_PASSWORD) return `Password must be ${MAX_PASSWORD} characters or fewer.`;
  if (next !== confirm) return "The two passwords do not match.";
  return null;
}

export function requestErrorMessage(err: unknown): string {
  if (err instanceof ApiError && err.status === 429) {
    return "Too many requests. Wait a few minutes and try again.";
  }
  if (err instanceof ApiError && err.status === 422) return "Enter a valid email address.";
  return "Could not send the email. Try again shortly.";
}

export function resetErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 429) return "Too many attempts. Wait a few minutes and try again.";
    if (err.status === 400) {
      return "This reset link is invalid or has expired. Request a new one.";
    }
    if (err.status === 422) return err.detail;
  }
  return "Could not reset the password. Try again.";
}
