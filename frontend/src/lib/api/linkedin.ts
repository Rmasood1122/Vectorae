/** Feature Group 5 — a lead's LinkedIn state (L0: what the USER reported on
 *  their LinkedIn tasks). Vectorae never acts on LinkedIn; there is no
 *  account to connect (removed 2026-11-02). */

import { api } from "./client";

export interface LeadLinkedInState {
  linkedin_url: string | null;
  is_premium: boolean | null;
  connection_status: "pending" | "connected" | null;
  invited_at: string | null;
  connected_at: string | null;
}

export function getLeadLinkedIn(leadId: string): Promise<LeadLinkedInState> {
  return api(`/leads/${leadId}/linkedin`);
}
