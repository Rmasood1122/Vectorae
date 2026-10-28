/** Feature Group 9 — deliverability health and the compliance audit log. */

import { api } from "./client";

export interface DomainHealth {
  domain: string;
  address: string;
  health: {
    score: number;
    source: string;
    checked_at: string;
    details: {
      spf: boolean;
      dmarc: boolean;
      dmarc_policy: string | null;
      dkim: boolean;
      bounce_rate: number | null;
      reasons: string[];
    };
  } | null;
  blacklist: { listed_on: string[]; unknown: string[]; source: string; checked_at: string } | null;
  history: { checked_at: string; score: number }[];
}

export interface DeliverabilityStatus {
  threshold: number;
  monitoring_enabled: boolean;
  domains: DomainHealth[];
}

export interface AuditRow {
  id: string;
  lead_id: string | null;
  message_id: string | null;
  channel: string;
  region: string | null;
  regime: string | null;
  decision: string;
  checks: Record<string, unknown> | null;
  ts: string | null;
}

export const getDeliverability = (): Promise<DeliverabilityStatus> => api("/deliverability");
export const runDeliverabilityCheck = (): Promise<DeliverabilityStatus> =>
  api("/deliverability/check", { method: "POST" });
export const listComplianceAudit = (limit = 50): Promise<AuditRow[]> =>
  api(`/compliance/audit?limit=${limit}`);

/** Score -> tone, with the threshold the admin configured. */
export function healthTone(score: number | null | undefined, threshold: number):
  "positive" | "warning" | "danger" | "default" {
  if (score === null || score === undefined) return "default";
  if (score >= threshold) return "positive";
  return score < 40 ? "danger" : "warning";
}

export const DECISION_LABELS: Record<string, string> = {
  sent: "Sent",
  blocked_suppressed: "Blocked — suppressed",
  blocked_no_address: "Blocked — no address",
  skipped_no_consent: "Skipped — no consent",
};

/* ---------------------------------------------------------------------------
 * Part 1 Feature 4 — per-MAILBOX health, and the auto-throttle it drives.
 *
 * Separate from DomainHealth above on purpose: the domain is the wrong grain
 * to act on. Two mailboxes on one domain can have very different reputations,
 * and only the one in trouble should be slowed down.
 * ------------------------------------------------------------------------ */

export type MailboxState = "healthy" | "throttled" | "paused";
export type MailboxBand = "good" | "at_risk" | "bad" | "unchecked";

export interface MailboxHealth {
  mailbox_ref: string;
  channel: string;
  address: string | null;
  domain: string | null;
  score: number | null;
  band: MailboxBand;
  state: MailboxState;
  throttle_cap: number | null;
  daily_cap: number | null;
  reason: string | null;
  reasons: string[];
  auth: { spf: boolean | null; dkim: boolean | null; dmarc: boolean | null;
          dmarc_policy: string | null };
  complaint_rate: number | null;
  /** Always "proxy" today — Vectorae has no feedback-loop feed. */
  complaint_source: string;
  bounce_rate: number | null;
  sends_today: number;
  sends_7d: number;
  paused_at: string | null;
  resumed_at: string | null;
  checked_at: string | null;
}

export interface MailboxHealthStatus {
  thresholds: { throttle_below: number; pause_below: number; throttle_fraction: number };
  complaint_note: string;
  mailboxes: MailboxHealth[];
}

export const getMailboxHealth = (): Promise<MailboxHealthStatus> =>
  api("/deliverability/mailboxes");
export const refreshMailboxHealth = (): Promise<MailboxHealthStatus> =>
  api("/deliverability/mailboxes/refresh", { method: "POST" });
export const resumeMailbox = (ref: string): Promise<MailboxHealthStatus> =>
  api(`/deliverability/mailboxes/${encodeURIComponent(ref)}/resume`, { method: "POST" });
