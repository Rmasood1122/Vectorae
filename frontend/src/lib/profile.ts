/** Pure rules for the Profile page. The server re-checks everything. */
import { ApiError } from "@/lib/api/client";

export const MIN_PASSWORD = 8;
export const MAX_NAME = 120;

export function validateName(raw: string): string | null {
  const v = raw.trim();
  if (v.length > MAX_NAME) return `Name must be ${MAX_NAME} characters or fewer.`;
  // eslint-disable-next-line no-control-regex
  if (/[\x00-\x1f\x7f]/.test(v)) return "Name contains invalid characters.";
  return null;
}

export function validatePasswordChange(
  current: string, next: string, confirm: string,
): string | null {
  if (!current) return "Enter your current password.";
  if (next.length < MIN_PASSWORD) return `New password must be at least ${MIN_PASSWORD} characters.`;
  if (next === current) return "New password must be different from the current one.";
  if (next !== confirm) return "The two new passwords do not match.";
  return null;
}

export function passwordErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 429) return "Too many attempts. Wait a while and try again.";
    if (err.status === 400 || err.status === 422) return err.message;
  }
  return "Could not change the password. Try again.";
}

const LABELS: Record<string, string> = {
  free: "Free", starter: "Starter", growth: "Growth", scale: "Scale", pro: "Pro",
  enterprise: "Enterprise", pay_per_meeting: "Pay per meeting",
};
export const planLabel = (plan: string): string => LABELS[plan] ?? plan;
/** Enterprise is the top tier: there is nothing to upgrade to. */
export const canUpgrade = (plan: string): boolean => plan !== "enterprise";

/** File name for the account export, e.g. vectorae-account-export-2026-10-10.json */
export function accountExportFilename(now: Date = new Date()): string {
  return `vectorae-account-export-${now.toISOString().slice(0, 10)}.json`;
}

export function exportErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403 && err.detail === "INVALID_PASSWORD") return "That password is not right.";
    if (err.status === 429) return "Too many attempts. Wait a while and try again.";
  }
  return "Could not prepare your data. Try again.";
}

/* ---- Delete account (GDPR Art. 17) — DELETE /me ------------------------- */

/** What the person types to confirm. Must equal account_erasure.CONFIRMATION_PHRASE. */
export const DELETE_CONFIRMATION = "DELETE";
/** Must equal account_erasure.BILLING_RETENTION_YEARS (tests/test_privacy_docs_truth.py). */
export const BILLING_RETENTION_YEARS = 7;

/** The danger-zone copy: exactly what is erased and what is kept (decisions
 *  E1/E2, docs/features/gdpr-erasure.md). Pinned by profile.test.ts. */
export const DELETE_ACCOUNT_COPY = {
  erased:
    "Your profile, products, strategies, prospects, messages, replies, meetings, CRM records, " +
    "connected accounts (Gmail, LinkedIn, CRM and other integrations), API keys, sessions and " +
    "uploaded files are erased at once, and LinkedIn accounts are disconnected at Unipile. Any " +
    "scheduled message is cancelled and any paid plan is cancelled immediately, with no further " +
    "charges — if the payment provider cannot confirm that, nothing is deleted. This cannot be undone.",
  kept: [
    `Billing records — amounts, dates and payment-provider reference numbers; our copy has no ` +
      `name or email — for ${BILLING_RETENTION_YEARS} years, for tax and accounting.`,
    "The consent ledger for your prospects (every opt-in, opt-out and erasure, with the address " +
      "it concerns) and the do-not-contact list, so anyone who asked not to be contacted stays protected.",
    "A security record that the account was deleted, without your IP address, device or email.",
  ],
  outside: [
    "Stripe keeps its own customer record of you (name, email, payment details).",
    "Access you gave Vectorae inside Google, HubSpot, Salesforce or Slack is not revoked on " +
      "their side; remove it there.",
    "Cached answers from contact-finding providers expire within 30 days; hosting logs with IP " +
      "addresses are kept for the period the hosting provider sets.",
  ],
  workspace:
    "If you own a workspace, it is deleted and its members lose access; their own accounts stay.",
} as const;

/** The button is enabled only with a password and the exact phrase. */
export function canDeleteAccount(password: string, typed: string): boolean {
  return password.length > 0 && typed === DELETE_CONFIRMATION;
}

export function deleteAccountErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403 && err.detail === "INVALID_PASSWORD") return "That password is not right.";
    if (err.status === 422 && err.detail === "CONFIRMATION_REQUIRED") {
      return `Type ${DELETE_CONFIRMATION} in capital letters to confirm.`;
    }
    if (err.status === 429) return "Too many attempts. Wait a while and try again.";
    if (err.status === 409 || err.status === 502) return err.message;
  }
  return "Could not delete the account. Try again.";
}
