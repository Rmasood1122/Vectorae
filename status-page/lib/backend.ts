/**
 * GET ${LEADPILOT_API_URL}/status/public — Vectorae's own component checks
 * (app/api/status_public.py). 5-second timeout. Anything other than a
 * well-formed answer counts as "unreachable": the page then derives what it
 * can from UptimeRobot and says "detail unavailable".
 */

import {
  BACKEND_COMPONENT_IDS,
  BACKEND_TIMEOUT_MS,
  type ComponentId,
  type StatusEnv,
} from "./config.ts";
import { isPublicStatus, type PublicStatus } from "./status.ts";

export interface BackendComponent {
  status: PublicStatus;
  checkedAt: string;
}

export type BackendResult =
  | { kind: "unconfigured" }
  | { kind: "unreachable" }
  | { kind: "ok"; components: Partial<Record<ComponentId, BackendComponent>> };

const ISO_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/;

export function parseBackend(json: unknown): BackendResult {
  if (!json || typeof json !== "object") return { kind: "unreachable" };
  const list = (json as { components?: unknown }).components;
  if (!Array.isArray(list)) return { kind: "unreachable" };
  const components: Partial<Record<ComponentId, BackendComponent>> = {};
  for (const item of list) {
    if (!item || typeof item !== "object") continue;
    const { id, status, checked_at } = item as Record<string, unknown>;
    if (typeof id !== "string" || !(BACKEND_COMPONENT_IDS as readonly string[]).includes(id)) continue;
    if (!isPublicStatus(status)) continue;
    if (typeof checked_at !== "string" || !ISO_UTC.test(checked_at)) continue;
    components[id as ComponentId] = { status, checkedAt: checked_at };
  }
  if (Object.keys(components).length === 0) return { kind: "unreachable" };
  return { kind: "ok", components };
}

export function backendBaseUrl(env: StatusEnv): string | null {
  const raw = (env.LEADPILOT_API_URL || "").trim().replace(/\/+$/, "");
  if (!raw) return null;
  try {
    const url = new URL(raw);
    return url.protocol === "https:" || url.protocol === "http:" ? raw : null;
  } catch {
    return null;
  }
}

export async function fetchBackend(
  env: StatusEnv,
  deps: { fetch: typeof fetch },
): Promise<BackendResult> {
  const base = backendBaseUrl(env);
  if (!base) return { kind: "unconfigured" };
  try {
    const resp = await deps.fetch(`${base}/status/public`, {
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(BACKEND_TIMEOUT_MS),
    });
    if (!resp.ok) return { kind: "unreachable" };
    return parseBackend(await resp.json());
  } catch {
    return { kind: "unreachable" };
  }
}
