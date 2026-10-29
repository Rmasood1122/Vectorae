import { api } from "./client";

/** Private email network (Growth plan and up; server flag email_network_enabled).
 *  Every call is scoped to the signed-in account on the server; nothing here can
 *  read anyone else's contacts or mailbox. Mailbox data is headers only. */

export interface ExclusionCount {
  reason: string;
  label: string;
  count: number;
}

export interface EmailImport {
  id: string;
  kind: string;
  status: "done" | "queued" | "running" | "failed" | "reconnect_required";
  rows_in_file: number;
  messages_scanned: number;
  people_found: number;
  added: number;
  updated: number;
  excluded: number;
  excluded_by_reason: ExclusionCount[];
  cap_note: string | null;
  error: string | null;
  created_at: string | null;
  finished_at: string | null;
  matched?: number;
  max_messages?: number;
}

export interface GmailState {
  connected: boolean;
  address: string | null;
  /** Scanning Gmail is switched off on every deployment (privacy policy section 3). */
  locked: boolean;
  can_scan: boolean;
  reconnect_required: boolean;
  scan: EmailImport | null;
}

export interface EmailNetworkStatus {
  eligible: boolean;
  plan: string;
  upgrade_to: string | null;
  feature_enabled: boolean;
  contact_count: number;
  last_import: EmailImport | null;
  needs_new_import: boolean;
  gmail: GmailState;
  attestation: { version: string; text: string };
  limits: {
    mbox_max_bytes: number;
    contacts_max_bytes: number;
    max_messages: number;
    max_contact_rows: number;
    gmail_max_messages: number;
  };
}

export interface EmailMatch {
  contact_id: string;
  email: string;
  full_name: string | null;
  company: string | null;
  company_is_guess: boolean;
  title: string | null;
  sent_to_count: number;
  received_from_count: number;
  two_way: boolean;
  first_seen: string | null;
  last_seen: string | null;
  in_contacts_file: boolean;
  casl_block: string | null;
  score: number;
  icp_score: number;
  relationship_score: number;
  segment: string;
  reasons: string[] | null;
}

export interface EmailNetworkProduct {
  product_id: string;
  product_name: string;
  total_matches: number;
  segments: { segment: string; count: number }[];
  matches: EmailMatch[];
}

export interface EmailNetworkMessage {
  id: string;
  contact_id: string;
  product_id: string;
  subject: string;
  body: string;
  status: "draft" | "approved" | "attached" | "used";
  strategy_id: string | null;
  lead_id: string | null;
  campaign_message_status: string | null;
  error: string | null;
}

export interface Campaign {
  strategy_id: string;
  product_name: string;
  created_at: string | null;
  campaign_state: string;
  has_email_first_sequence: boolean;
}

export interface AddResult {
  added: number;
  skipped: { contact_id: string; reason: string }[];
  batch_id: string | null;
  lead_ids: Record<string, string>;
  verification_queued?: boolean;
}

export const getEmailNetworkStatus = () => api<EmailNetworkStatus>("/email-network/status");

export const getEmailRecommendations = () =>
  api<{ products: EmailNetworkProduct[] }>("/email-network/recommendations");

/** One file, sent as the RAW request body (an mbox is streamed by the server,
 *  headers only; nothing is written to disk). */
export const uploadEmailNetworkFile = (
  file: Blob,
  attestationVersion: string,
  ownAddresses: string,
) => {
  const query = new URLSearchParams({ attested: "true", attestation_version: attestationVersion });
  // Your own addresses go in a header, never in the URL (access logs).
  const own = ownAddresses.trim();
  return api<EmailImport>(`/email-network/import?${query.toString()}`, {
    method: "POST",
    raw: { body: file, contentType: "application/octet-stream" },
    ...(own ? { headers: { "X-Own-Addresses": encodeURIComponent(own) } } : {}),
  });
};

export const startGmailScan = (attestationVersion: string, ownAddresses: string[]) =>
  api<EmailImport>("/email-network/gmail-scan", {
    method: "POST",
    body: { attested: true, attestation_version: attestationVersion, own_addresses: ownAddresses },
  });

export const getGmailScan = () => api<GmailState>("/email-network/gmail-scan");

export const eraseEmailNetwork = () =>
  api<Record<string, number>>("/email-network", { method: "DELETE" });

export const deleteEmailContact = (contactId: string) =>
  api<{ deleted_contacts: number }>(`/email-network/contacts/${contactId}`, { method: "DELETE" });

export const listEmailCampaigns = () => api<{ campaigns: Campaign[] }>("/email-network/campaigns");

export const addContactsToCampaign = (strategyId: string, contactIds: string[]) =>
  api<AddResult>("/email-network/add-to-campaign", {
    body: { strategy_id: strategyId, contact_ids: contactIds },
  });

export const listEmailMessages = () =>
  api<{ messages: EmailNetworkMessage[] }>("/email-network/messages");

export const draftEmailMessage = (contactId: string, productId: string) =>
  api<EmailNetworkMessage>("/email-network/messages/draft", {
    body: { contact_id: contactId, product_id: productId },
  });

export const saveEmailMessage = (id: string, subject: string, body: string) =>
  api<EmailNetworkMessage>(`/email-network/messages/${id}`, {
    method: "PUT",
    body: { subject, body },
  });

export const attachEmailMessage = (id: string, strategyId: string) =>
  api<EmailNetworkMessage>(`/email-network/messages/${id}/attach`, {
    body: { strategy_id: strategyId },
  });

export const detachEmailMessage = (id: string) =>
  api<EmailNetworkMessage>(`/email-network/messages/${id}/detach`, { body: {} });
