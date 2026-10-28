/**
 * Everything the status page knows about Vectorae's shape. The UptimeRobot
 * monitor names below are a contract with the founders' account: they must
 * match the friendly names in docs/program/STATUS_PAGE.md exactly (case is
 * ignored). The backend component ids are a contract with
 * app/api/status_public.py.
 *
 * Files under lib/ import each other with explicit `.ts` extensions so that
 * Node can run them directly (tests, incident-lint, drills) without a build.
 */

export type ComponentId =
  | "website"
  | "api"
  | "background-processing"
  | "ai-features"
  | "booking-pages";

export interface ComponentDef {
  id: ComponentId;
  name: string;
  description: string;
}

export const COMPONENTS: readonly ComponentDef[] = [
  {
    id: "website",
    name: "Website & app",
    description: "calendarharvest.com and the Vectorae web app",
  },
  {
    id: "api",
    name: "API",
    description: "The service the web app, mobile app and integrations talk to",
  },
  {
    id: "background-processing",
    name: "Background processing",
    description: "Research pipelines, scheduled sends and other queued work",
  },
  {
    id: "ai-features",
    name: "AI features",
    description: "Strategy generation, personalization and the Help assistant",
  },
  {
    id: "booking-pages",
    name: "Booking pages",
    description: "Public pages where prospects book a meeting",
  },
];

export const COMPONENT_IDS: readonly ComponentId[] = COMPONENTS.map((c) => c.id);

/** Backend ids that GET /status/public reports (no "website": it cannot see Vercel). */
export const BACKEND_COMPONENT_IDS: readonly ComponentId[] = [
  "api",
  "background-processing",
  "ai-features",
  "booking-pages",
];

/** Roles of the UptimeRobot monitors and the friendly name each must have. */
export const MONITOR_NAMES = {
  website: "Vectorae - Website",
  appLogin: "Vectorae - App login",
  booking: "Vectorae - Booking page",
  apiHttp: "Vectorae - API",
  apiKeyword: "Vectorae - API healthy",
  worker: "Vectorae - Worker",
} as const;

export type MonitorRole = keyof typeof MONITOR_NAMES;

/** Which monitors draw each component's 90-day bars (worst value per day). */
export const UPTIME_SOURCES: Record<ComponentId, readonly MonitorRole[]> = {
  website: ["website", "appLogin"],
  api: ["apiHttp"],
  "background-processing": ["worker"],
  "ai-features": [],
  "booking-pages": ["booking"],
};

export const UPTIMEROBOT_DEFAULT_URL = "https://api.uptimerobot.com/v2";
export const DEFAULT_SITE_URL = "https://status.calendarharvest.com";

export const BACKEND_TIMEOUT_MS = 5_000;
export const UPTIMEROBOT_TIMEOUT_MS = 8_000;
/** Page and data revalidation interval. Keep in sync with `revalidate` exports. */
export const REVALIDATE_SECONDS = 60;
export const UPTIME_DAYS = 90;
export const RECENT_INCIDENT_DAYS = 14;

export interface StatusEnv {
  UPTIMEROBOT_API_KEY?: string;
  UPTIMEROBOT_API_URL?: string;
  LEADPILOT_API_URL?: string;
}

/** Server-side env only. None of these carry the public-env prefix Next.js inlines into browser bundles. */
export function readEnv(source: Record<string, string | undefined> = process.env): StatusEnv {
  return {
    UPTIMEROBOT_API_KEY: source.UPTIMEROBOT_API_KEY,
    UPTIMEROBOT_API_URL: source.UPTIMEROBOT_API_URL,
    LEADPILOT_API_URL: source.LEADPILOT_API_URL,
  };
}

export function siteUrl(source: Record<string, string | undefined> = process.env): string {
  return (source.STATUS_SITE_URL || DEFAULT_SITE_URL).replace(/\/+$/, "");
}
