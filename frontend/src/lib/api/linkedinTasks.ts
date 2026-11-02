import { api } from "./client";

/** L0 assisted LinkedIn tasks. Vectorae drafts; YOU act on LinkedIn.
 *  Every task is approved one at a time (approval lasts 24 hours), opened
 *  during business hours within your daily limits, and marked by you. There
 *  is no bulk approve on purpose. */
export type LinkedInTaskStatus =
  | "pending" | "approved" | "opened" | "done" | "skipped" | "replied"
  | "cancelled" | "expired";

export interface LinkedInTask {
  id: string;
  source: "network" | "sequence";
  kind: "connection_note" | "message" | "comment";
  connection_id: string | null;
  lead_id: string | null;
  profile_url: string;
  text: string;
  status: LinkedInTaskStatus;
  approved_at: string | null;
  approval_expires_at: string | null;
  approval_expired: boolean;
  opened_at: string | null;
  completed_at: string | null;
  cancel_reason: string | null;
  created_at: string;
}

export interface TaskList {
  tasks: LinkedInTask[];
  limits: { connection_note: number; message: number };
  approval_hours: number;
}

export interface OpenedTask {
  id: string;
  profile_url: string;
  text: string;
  kind: string;
  instructions: string;
}

export type TaskOutcome = "done" | "skipped" | "replied";

export const listTasks = (status?: string) =>
  api<TaskList>(`/linkedin-tasks${status ? `?status=${encodeURIComponent(status)}` : ""}`);
export const taskFromNetworkMessage = (messageId: string) =>
  api<LinkedInTask>("/linkedin-tasks/from-network-message", { body: { message_id: messageId } });
export const editTask = (id: string, text: string) =>
  api<LinkedInTask>(`/linkedin-tasks/${id}`, { method: "PUT", body: { text } });
export const approveTask = (id: string) =>
  api<LinkedInTask>(`/linkedin-tasks/${id}/approve`, { method: "POST" });
export const openTask = (id: string) =>
  api<OpenedTask>(`/linkedin-tasks/${id}/open`, { method: "POST" });
export const completeTask = (id: string, outcome: TaskOutcome) =>
  api<LinkedInTask>(`/linkedin-tasks/${id}/complete`, { body: { outcome } });

/** Only a plain https://www.linkedin.com/in/<slug>/ link is ever opened. */
export function safeTaskProfileUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  return /^https:\/\/www\.linkedin\.com\/in\/[\w\-%.]{2,100}\/$/.test(url) ? url : null;
}

export function taskStatusLabel(t: LinkedInTask): string {
  if (t.status === "approved" && t.approval_expired) return "Approval expired";
  return {
    pending: "Needs your approval", approved: "Approved", opened: "Opened on LinkedIn",
    done: "Sent by you", skipped: "Skipped", replied: "Replied",
    cancelled: t.cancel_reason === "suppressed" ? "Cancelled: do not contact" : "Cancelled",
    expired: "Approval expired",
  }[t.status];
}
