/** F05 Guardrails Studio — types and pure helpers (labels, summaries).
 *  The server is what normalises and enforces; nothing here decides whether a
 *  value is excluded. */

export type ExclusionKind = "domain" | "company" | "contact";
export type ExclusionReason =
  | "customer" | "open_deal" | "churned" | "partner" | "competitor" | "do_not_contact" | "other";
export type ExclusionSource = "manual" | "csv" | "crm_local" | "crm_hubspot" | "crm_salesforce";
export type LibraryKind = "approved_claim" | "forbidden_phrase";

export interface Exclusion {
  id: string;
  kind: ExclusionKind;
  value: string;
  display_value: string;
  reason: ExclusionReason;
  source: ExclusionSource;
  external_ref: string | null;
  note: string | null;
  active: boolean;
  active_since: string | null;
  removed_at: string | null;
  last_seen_at: string | null;
  created_at: string | null;
}

export interface ExclusionPage {
  total: number;
  limit: number;
  offset: number;
  items: Exclusion[];
}

export interface GuardrailsStatus {
  enabled: boolean;
  exclusions: Record<ExclusionKind, number>;
  library: Record<LibraryKind, number>;
  kinds: ExclusionKind[];
  reasons: ExclusionReason[];
  sources: ExclusionSource[];
}

export interface ImportResult {
  added: number;
  reactivated: number;
  exists: number;
  rows: number;
  error_count: number;
  errors: { row: number; error: string }[];
}

export interface SyncSourceResult {
  status: "ok" | "error";
  added?: number;
  reactivated?: number;
  exists?: number;
  invalid?: number;
  error?: string;
}

export type CrmSyncResult = Record<string, SyncSourceResult>;

export interface LibraryEntry {
  id: string;
  kind: LibraryKind;
  text: string;
  product_id: string | null;
  source_url: string | null;
  created_at: string | null;
}

export const KIND_LABELS: Record<ExclusionKind, string> = {
  domain: "Domain", company: "Company", contact: "Contact",
};

export const REASON_LABELS: Record<ExclusionReason, string> = {
  customer: "Customer", open_deal: "Open deal", churned: "Churned", partner: "Partner",
  competitor: "Competitor", do_not_contact: "Do not contact", other: "Other",
};

export const SOURCE_LABELS: Record<ExclusionSource, string> = {
  manual: "Manual", csv: "CSV", crm_local: "Vectorae CRM",
  crm_hubspot: "HubSpot", crm_salesforce: "Salesforce",
};

export const KIND_PLACEHOLDERS: Record<ExclusionKind, string> = {
  domain: "acme.com",
  company: "Acme Inc",
  contact: "jane@acme.com, +1 555 010 2000 or a LinkedIn URL",
};

export function reasonLabel(reason: string): string {
  return REASON_LABELS[reason as ExclusionReason] ?? reason.replace(/_/g, " ");
}

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source as ExclusionSource] ?? source.replace(/_/g, " ");
}

/** "3 added, 1 already listed, 2 rows skipped (see below)". */
export function importSummary(result: ImportResult): string {
  const parts = [`${result.added} added`];
  if (result.reactivated) parts.push(`${result.reactivated} re-activated`);
  if (result.exists) parts.push(`${result.exists} already listed`);
  if (result.error_count) {
    parts.push(`${result.error_count} ${result.error_count === 1 ? "row" : "rows"} skipped`);
  }
  return parts.join(", ");
}

const SYNC_SOURCE_NAMES: Record<string, string> = {
  local: "Vectorae CRM", hubspot: "HubSpot", salesforce: "Salesforce",
};

/** One line per source: "HubSpot: 4 added" / "Salesforce: failed — HTTP 401". */
export function syncSummary(result: CrmSyncResult): string[] {
  return Object.entries(result).map(([source, res]) => {
    const name = SYNC_SOURCE_NAMES[source] ?? source;
    if (res.status === "error") return `${name}: failed — ${res.error ?? "unknown error"}`;
    const extra = res.reactivated ? `, ${res.reactivated} re-activated` : "";
    return `${name}: ${res.added ?? 0} added${extra}, ${res.exists ?? 0} already listed`;
  });
}

/** Library entries grouped for display: approved claims first. */
export function splitLibrary(items: LibraryEntry[]): {
  claims: LibraryEntry[];
  phrases: LibraryEntry[];
} {
  return {
    claims: items.filter((i) => i.kind === "approved_claim"),
    phrases: items.filter((i) => i.kind === "forbidden_phrase"),
  };
}
