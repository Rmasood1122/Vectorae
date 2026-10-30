/**
 * Typed frontend API client for admin endpoints.
 * All methods require an admin JWT (enforced server-side).
 */
import { apiClient } from "./client";

/** Section B: one onboarding identity review (GET /admin/identity-reviews). */
export interface IdentityReview {
  user_id: string;
  email: string;
  personal_country: string | null;
  geo_detected_country: string | null;
  geo_check_status: string | null;
  geo_review_status: "pending" | "cleared" | "confirmed_risk" | null;
  account_type: "individual" | "company" | null;
  company_name: string | null;
  company_country: string | null;
  signup_ip: string | null;
  phone_verified: boolean;
  identity_submitted_at: string | null;
  geo_reviewed_by: string | null;
  geo_reviewed_at: string | null;
  geo_review_note: string | null;
}

/** One account in GET /admin/users. The origin fields are null for accounts
 *  created before the identity step (migration 0039). */
export interface AdminUser {
  id: string;
  email: string;
  is_admin: boolean;
  is_suspended: boolean;
  plan: string | null;
  created_at: string | null;
  strategy_count: number;
  active_campaign_count: number;
  last_active_at: string | null;
  signup_ip: string | null;
  signup_country: string | null;
  declared_country: string | null;
  company_name: string | null;
  company_country: string | null;
}

/** One row of GET /admin/signups: an account created, or a refused attempt. */
export interface SignupRow {
  ts: string;
  event: string;
  outcome: string;
  email: string | null;
  ip: string | null;
  country: string | null;
  user_id: string | null;
}

export const adminApi = {
  listIdentityReviews: async (status = "pending") =>
    apiClient.get<{ pending_count: number; reviews: IdentityReview[] }>(
      `/admin/identity-reviews?status=${encodeURIComponent(status)}`),

  decideIdentityReview: async (userId: string, decision: "cleared" | "confirmed_risk",
                               note: string) =>
    apiClient.post<IdentityReview>(`/admin/identity-reviews/${userId}`, { decision, note }),

  // -------------------------------------------------------------------
  // Users
  // -------------------------------------------------------------------
  listUsers: async (page = 1, pageSize = 50) =>
    apiClient.get<AdminUser[]>(`/admin/users?page=${page}&page_size=${pageSize}`),

  listSignups: async (page = 1, pageSize = 50) =>
    apiClient.get<SignupRow[]>(`/admin/signups?page=${page}&page_size=${pageSize}`),

  getUserDetail: async (userId: string) =>
    apiClient.get(`/admin/users/${userId}`),

  suspendUser: async (userId: string, reason: string) =>
    apiClient.post(`/admin/users/${userId}/suspend`, { reason }),

  unsuspendUser: async (userId: string) =>
    apiClient.post(`/admin/users/${userId}/unsuspend`),

  // -------------------------------------------------------------------
  // Suppression list
  // -------------------------------------------------------------------
  listSuppression: async (page = 1, pageSize = 100) =>
    apiClient
      .get(`/admin/suppression-list?page=${page}&page_size=${pageSize}`)
      ,

  addSuppression: async (email: string, reason: string) =>
    apiClient.post("/admin/suppression-list", { email, reason }),

  removeSuppression: async (email: string) =>
    apiClient.delete(`/admin/suppression-list/${encodeURIComponent(email)}`),

  // -------------------------------------------------------------------
  // Task errors
  // -------------------------------------------------------------------
  listTaskErrors: async (unresolvedOnly = true, page = 1, pageSize = 50) =>
    apiClient
      .get(`/admin/task-errors?unresolved_only=${unresolvedOnly}&page=${page}&page_size=${pageSize}`)
      ,

  resolveTaskError: async (errorId: string, note: string) =>
    apiClient
      .post(`/admin/task-errors/${errorId}/resolve`, { resolution_note: note })
      ,

  // -------------------------------------------------------------------
  // Circuit breakers
  // -------------------------------------------------------------------
  // ── Feature 2/3 admin surfaces ─────────────────────────────────────────
  // Tutorial completions are AGGREGATE ONLY — the payload deliberately
  // carries no user identifier. Support tickets DO carry the requester's
  // email, because a ticket you cannot reply to is useless.
  getTutorialCompletions: async () =>
    apiClient.get<import("./types").AdminTutorialCompletions>(
      "/admin/tutorials/completions"),

  // Task 3: the catalogue is editable. `listTutorials` returns DRAFTS too --
  // that is the difference between this and the user-facing /tutorials.
  listTutorials: async () =>
    apiClient.get<import("./types").AdminTutorialList>("/admin/tutorials"),

  createTutorial: async (body: Record<string, unknown>) =>
    apiClient.post<import("./types").AdminTutorial>("/admin/tutorials", body),

  updateTutorial: async (
    slug: string,
    patch: import("./types").AdminTutorialPatch,
  ) =>
    apiClient.put<import("./types").AdminTutorial>(
      `/admin/tutorials/${encodeURIComponent(slug)}`, patch),

  deleteTutorial: async (slug: string) =>
    apiClient.delete<{ deleted: string; progress_rows_kept: number }>(
      `/admin/tutorials/${encodeURIComponent(slug)}`),

  publishTutorial: async (slug: string) =>
    apiClient.post<import("./types").AdminTutorial>(
      `/admin/tutorials/${encodeURIComponent(slug)}/publish`),

  unpublishTutorial: async (slug: string) =>
    apiClient.post<import("./types").AdminTutorial>(
      `/admin/tutorials/${encodeURIComponent(slug)}/unpublish`),

  listSupportTickets: async (status?: string) =>
    apiClient.get<{ open_count: number;
                    tickets: import("./types").AdminSupportTicket[] }>(
      `/admin/support/tickets${status ? `?status=${status}` : ""}`),

  resolveSupportTicket: async (ticketId: string, note: string) =>
    apiClient.post<{ id: string; status: string }>(
      `/admin/support/tickets/${ticketId}/resolve`, { note }),

  listCircuitBreakers: async () =>
    apiClient.get("/admin/circuit-breakers"),

  resetCircuitBreaker: async (provider: string) =>
    apiClient.post(`/admin/circuit-breakers/${provider}/reset`),

  // -------------------------------------------------------------------
  // Playbook
  // -------------------------------------------------------------------
  listPlaybookScores: async (userId?: string, page = 1, pageSize = 100) => {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (userId) params.set("user_id", userId);
    return apiClient.get(`/admin/playbook/scores?${params}`);
  },

  recomputePlaybook: async () =>
    apiClient.post("/admin/playbook/recompute"),

  // -------------------------------------------------------------------
  // Health
  // -------------------------------------------------------------------
  getSystemHealth: async () =>
    apiClient.get("/health"),

  getLearningLoopHealth: async () =>
    apiClient.get("/health/learning-loop"),

  getChannelHealth: async () =>
    apiClient.get("/health/channels"),

  getCeleryStats: async () =>
    apiClient.get("/admin/celery-stats"),

  // -------------------------------------------------------------------
  // Encryption key rotation
  // -------------------------------------------------------------------
  rotateEncryptionKey: async () =>
    apiClient.post("/admin/rotate-encryption-key"),
};
