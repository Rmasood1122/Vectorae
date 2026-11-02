/** Daily send approval — today's "yes" to automatic sending, shaped for the UI.
 *  Pure; tested in src/tests/sendApproval.test.ts.
 *  Spec: docs/features/daily-send-approval.md (founder decision 2026-10-11). */

export type ApprovalChannel = "email" | "linkedin" | "whatsapp" | "phone";
export type ApprovalStatus = "pending" | "approved" | "declined";

export interface LinkedInWarning {
  version: string;
  text: string;
  question: string;
}

export interface SendApprovalState {
  enabled: boolean;
  date: string;
  timezone: string;
  timezone_source: "calendar_availability" | "utc_fallback";
  valid_until: string;
  status: ApprovalStatus;
  approved_channels: ApprovalChannel[];
  gated_channels: ApprovalChannel[];
  queued: Partial<Record<ApprovalChannel, number>>;
  queued_total: number;
  linkedin_risk_acknowledged: boolean;
  linkedin_warning_version_acknowledged: string | null;
  decided_at: string | null;
  decided_by_user_id: string | null;
  notified_at: string | null;
  linkedin_warning: LinkedInWarning;
  channel_labels: Record<string, string>;
  can_decide: boolean;
  released?: number;
  /** LinkedIn network (bulk) messages queued again by this approval. */
  released_network?: number;
}

export interface ApprovePayload {
  channels: ApprovalChannel[];
  linkedin_risk_acknowledged?: boolean;
  linkedin_warning_version?: string;
}

export const CHANNEL_ORDER: ApprovalChannel[] = ["email", "linkedin", "whatsapp", "phone"];

const FALLBACK_LABELS: Record<ApprovalChannel, string> = {
  email: "Email", linkedin: "LinkedIn", whatsapp: "WhatsApp", phone: "AI phone calls",
};

export function channelLabel(state: Pick<SendApprovalState, "channel_labels"> | null,
                             channel: ApprovalChannel): string {
  return state?.channel_labels?.[channel] ?? FALLBACK_LABELS[channel];
}

/** The channels to show, in a fixed order. */
export function gatedChannels(state: SendApprovalState): ApprovalChannel[] {
  return CHANNEL_ORDER.filter((c) => state.gated_channels.includes(c));
}

/** What the checkboxes start as. LinkedIn is NEVER pre-ticked, even when it
 *  was approved earlier today: approving it is always a separate, explicit
 *  choice. Other channels start ticked only if already approved today. */
export function initialSelection(state: SendApprovalState): Set<ApprovalChannel> {
  return new Set(
    gatedChannels(state).filter((c) => c !== "linkedin" && state.approved_channels.includes(c)),
  );
}

export interface SubmitCheck {
  ok: boolean;
  reason: string | null;
}

/** Can the Approve button submit? Mirrors the API's 422 rules. */
export function canApprove(selected: Set<ApprovalChannel>, linkedinAcknowledged: boolean): SubmitCheck {
  if (selected.size === 0) {
    return { ok: false, reason: "Choose at least one channel, or decline for today." };
  }
  if (selected.has("linkedin") && !linkedinAcknowledged) {
    return {
      ok: false,
      reason: "To approve LinkedIn, confirm you understand that you send each LinkedIn message yourself.",
    };
  }
  return { ok: true, reason: null };
}

export function buildApprovePayload(state: SendApprovalState, selected: Set<ApprovalChannel>,
                                    linkedinAcknowledged: boolean): ApprovePayload {
  const channels = CHANNEL_ORDER.filter((c) => selected.has(c));
  const payload: ApprovePayload = { channels };
  if (selected.has("linkedin")) {
    payload.linkedin_risk_acknowledged = linkedinAcknowledged === true;
    payload.linkedin_warning_version = state.linkedin_warning.version;
  }
  return payload;
}

/** Gated channels with something queued today that are NOT approved. */
export function waitingChannels(state: SendApprovalState): ApprovalChannel[] {
  if (state.status === "approved") {
    return gatedChannels(state).filter(
      (c) => (state.queued[c] ?? 0) > 0 && !state.approved_channels.includes(c));
  }
  return gatedChannels(state).filter((c) => (state.queued[c] ?? 0) > 0);
}

/** Show the app-wide banner? Only while the gate is on, something automatic
 *  is waiting, and today's decision does not already cover it. */
export function showBanner(state: SendApprovalState | null | undefined): boolean {
  if (!state || !state.enabled) return false;
  return waitingChannels(state).length > 0;
}

export function queuedSummary(state: SendApprovalState): string {
  const parts = gatedChannels(state).map((c) => `${channelLabel(state, c)}: ${state.queued[c] ?? 0}`);
  return parts.join(" · ");
}

export function bannerText(state: SendApprovalState): string {
  const waiting = waitingChannels(state);
  const total = waiting.reduce((n, c) => n + (state.queued[c] ?? 0), 0);
  const noun = total === 1 ? "automatic message is" : "automatic messages are";
  if (state.status === "declined") {
    return `Automatic sending is off for today. ${total} ${noun} waiting.`;
  }
  return `${total} ${noun} waiting for today's approval.`;
}

export function statusLabel(state: SendApprovalState): string {
  if (!state.enabled) return "Not required (turned off by an admin)";
  if (state.status === "approved") return "Approved for today";
  if (state.status === "declined") return "Declined for today";
  return "Waiting for your decision";
}

export function statusTone(state: SendApprovalState): "success" | "warning" | "default" {
  if (!state.enabled) return "default";
  if (state.status === "approved") return "success";
  return "warning";
}

export function timezoneNote(state: SendApprovalState): string {
  if (state.timezone_source === "utc_fallback") {
    return "Your day is counted in UTC because no timezone is set on your calendar availability.";
  }
  return `Your day is counted in ${state.timezone} (from your calendar availability).`;
}
