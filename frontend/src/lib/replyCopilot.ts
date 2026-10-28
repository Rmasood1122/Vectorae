/** F04 Reply Copilot — pure helpers for the draft queue in the inbox.
 *
 *  A draft is never a send. An approved EMAIL draft is handed to the same send
 *  path as every message (suppression, unsubscribe footer, send window, caps,
 *  review); a LinkedIn draft is copy-only. Behind `reply_copilot_enabled`
 *  (default off): the API answers 404, and the panel renders nothing. */

export type CopilotIntent = "positive" | "objection" | "question";
export type CopilotStatus = "pending" | "approved" | "copied" | "discarded" | "superseded";
export type DeliveryState = "queued" | "sent" | "held_for_review" | "blocked";

export interface ClaimFlag {
  claim: string;
  verdict: string;
  category: string;
  replacement: string | null;
}

export interface ReplyDraft {
  id: string;
  reply_id: string;
  lead_id: string;
  lead: { full_name: string | null; company: string | null } | null;
  channel: "email" | "linkedin";
  intent: CopilotIntent;
  meeting_time_request: boolean;
  status: CopilotStatus;
  draft_subject: string | null;
  draft_body: string;
  final_subject: string | null;
  final_body: string | null;
  edited: boolean;
  auto_sent: boolean;
  claim_flags: ClaimFlag[];
  grounding: { playbook_steps?: string[]; approved_claim_ids?: string[];
               booking_url?: string | null; forbidden_phrases?: number };
  reply_preview: string | null;
  reply_received_at: string | null;
  decided_at: string | null;
  created_at: string | null;
  delivery: { state: DeliveryState; status: string; error: string | null;
              sent_at: string | null;
              /** Held by a guard, not failed (backend send_holds.hold_of). */
              hold?: { kind: string; label: string; reason: string } | null } | null;
  send_result: string | null;
}

export interface DraftPage {
  total: number;
  limit: number;
  offset: number;
  status: string;
  items: ReplyDraft[];
}

export interface CopilotSettings {
  auto_send_meeting_time: boolean;
  auto_send_allowed: boolean;
  /** Per-account stop attribution (default true). False = strict: an answer
   *  to a prospect's later reply is cancelled when their earlier reply is
   *  what stopped the sequence. Absent from an older API = true. */
  answer_earlier_replies?: boolean;
  enabled: boolean;
}

export interface CopilotMetrics {
  window_days: number;
  drafts: number;
  pending: number;
  approved: number;
  copied: number;
  discarded: number;
  superseded: number;
  auto_sent: number;
  approvals_measured: number;
  median_reply_to_approval_seconds: number | null;
  sent: number;
  sent_unedited: number;
  unedited_share: number | null;
}

/** The API answers 404 for every route while the feature is off. */
export function isDisabledError(error: unknown): boolean {
  return typeof error === "object" && error !== null
    && (error as { status?: number }).status === 404;
}

export function intentLabel(intent: CopilotIntent): string {
  return { positive: "Interested", objection: "Objection", question: "Question" }[intent]
    ?? intent;
}

export function intentTone(intent: CopilotIntent): "success" | "warning" | "default" {
  return intent === "positive" ? "success" : intent === "objection" ? "warning" : "default";
}

/** Copy-only channels: Vectorae never sends these. */
export function isCopyOnly(draft: Pick<ReplyDraft, "channel">): boolean {
  return draft.channel !== "email";
}

function norm(text: string | null | undefined): string {
  return (text ?? "").split(/\s+/).filter(Boolean).join(" ");
}

/** Mirrors the backend's `edited` rule: whitespace-only changes are not an edit. */
export function isEdited(draft: Pick<ReplyDraft, "draft_body">, text: string): boolean {
  return norm(text) !== norm(draft.draft_body);
}

export function approveLabel(draft: Pick<ReplyDraft, "channel" | "draft_body">,
                             text: string): string {
  if (isCopyOnly(draft)) return isEdited(draft, text) ? "Copy edited" : "Copy";
  return isEdited(draft, text) ? "Send edited" : "Approve & send";
}

/** What happened to a decided draft, in words. */
export function deliveryLabel(draft: Pick<ReplyDraft, "status" | "delivery" | "auto_sent">):
  string {
  if (draft.status === "pending") return "Waiting for you";
  if (draft.status === "discarded") return "Discarded";
  if (draft.status === "superseded") return "Superseded by a newer reply";
  if (draft.status === "copied") return "Copied — send it from LinkedIn";
  const prefix = draft.auto_sent ? "Auto-sent: " : "";
  const delivery = draft.delivery;
  if (!delivery) return `${prefix}queued`;
  switch (delivery.state) {
    case "sent": return `${prefix}sent`;
    case "queued": return `${prefix}queued (waits for the send window and daily cap)`;
    case "held_for_review": return `${prefix}held in the send-review queue`;
    default:
      if (delivery.hold) return `${prefix}on hold: ${delivery.hold.label} — ${delivery.hold.reason}`;
      return `${prefix}not sent${delivery.error ? ` — ${delivery.error}` : ""}`;
  }
}

/** "—" (never "0") when there is nothing to measure. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = seconds / 60;
  if (minutes < 60) return `${Math.round(minutes)} min`;
  const hours = minutes / 60;
  if (hours < 48) return `${hours.toFixed(1)} h`;
  return `${(hours / 24).toFixed(1)} days`;
}

export function formatShare(share: number | null | undefined): string {
  if (share === null || share === undefined) return "—";
  return `${Math.round(share * 100)}%`;
}

/** Why the auto-send toggle is disabled, or null when it can be changed. */
export function autoSendBlockedReason(settings: CopilotSettings | undefined): string | null {
  if (!settings) return "Loading…";
  if (!settings.auto_send_allowed && !settings.auto_send_meeting_time) {
    return "An administrator has not allowed auto-send on this deployment.";
  }
  return null;
}

/** The account's stop-attribution setting; missing means the default (on). */
export function answersEarlierReplies(settings: CopilotSettings | undefined): boolean {
  return settings?.answer_earlier_replies !== false;
}
