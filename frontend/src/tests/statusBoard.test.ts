/**
 * Public status page at /status (src/lib/statusBoard.ts): response parsing,
 * overall-state derivation, unreachable handling and refresh decisions.
 * Offline: every fetch here is a fake.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  FETCH_TIMEOUT_MS,
  KNOWN_COMPONENTS,
  OVERALL_HEADLINES,
  RATE_LIMITED_RETRY_MS,
  REFRESH_INTERVAL_MS,
  STATUS_COLORS,
  STATUS_PAGE_BACKGROUND,
  applyIncidents,
  deriveOverall,
  fetchPublicStatus,
  msUntilNextCheck,
  nextRefreshDelay,
  parseStatusBody,
  reduceStatus,
  resultFromResponse,
  statusEndpoint,
  type StatusComponent,
  type StatusResult,
  type StatusView,
} from "@/lib/statusBoard";
import { contrastRatio } from "@/lib/theme/contrast";

const ALL_OK = {
  components: [
    { id: "api", name: "API", status: "operational", checked_at: "2026-10-10T20:01:36Z" },
    { id: "background-processing", name: "Background processing", status: "operational", checked_at: "2026-10-10T20:01:36Z" },
    { id: "ai-features", name: "AI features", status: "operational", checked_at: "2026-10-10T20:01:36Z" },
    { id: "booking-pages", name: "Booking pages", status: "operational", checked_at: "2026-10-10T20:01:36Z" },
  ],
};

function withStatus(id: string, status: string) {
  return { components: ALL_OK.components.map((c) => (c.id === id ? { ...c, status } : c)) };
}

function okComponents(body: unknown): StatusComponent[] {
  const r = parseStatusBody(body);
  if (r.kind !== "ok") throw new Error(`expected ok, got ${JSON.stringify(r)}`);
  return r.components;
}

const NOW = new Date("2026-10-11T12:00:00Z");

describe("statusEndpoint", () => {
  it("joins the API origin and the public status path", () => {
    expect(statusEndpoint("https://api.example.test")).toBe("https://api.example.test/status/public");
    expect(statusEndpoint("https://api.example.test///")).toBe("https://api.example.test/status/public");
  });

  it("is null when the API origin is missing or not http(s)", () => {
    expect(statusEndpoint(undefined)).toBeNull();
    expect(statusEndpoint("")).toBeNull();
    expect(statusEndpoint("   ")).toBeNull();
    expect(statusEndpoint("javascript:alert(1)")).toBeNull();
    expect(statusEndpoint("not a url")).toBeNull();
  });
});

describe("parseStatusBody", () => {
  it("all ok: four components in contract order, all operational", () => {
    const comps = okComponents(ALL_OK);
    expect(comps.map((c) => c.id)).toEqual(KNOWN_COMPONENTS.map((c) => c.id));
    expect(comps.every((c) => c.status === "operational")).toBe(true);
    expect(deriveOverall(comps)).toBe("operational");
  });

  it("degraded component makes the overall state degraded", () => {
    const comps = okComponents(withStatus("background-processing", "degraded"));
    expect(comps.find((c) => c.id === "background-processing")?.status).toBe("degraded");
    expect(deriveOverall(comps)).toBe("degraded");
  });

  it("an unrecognised status value is shown as unknown, never as operational", () => {
    const comps = okComponents(withStatus("ai-features", "on_fire"));
    expect(comps.find((c) => c.id === "ai-features")?.status).toBe("unknown");
    expect(deriveOverall(comps)).toBe("unknown");
  });

  it("a known component missing from the answer is listed as unknown", () => {
    const comps = okComponents({ components: ALL_OK.components.filter((c) => c.id !== "booking-pages") });
    expect(comps.find((c) => c.id === "booking-pages")).toEqual({
      id: "booking-pages",
      name: "Booking pages",
      status: "unknown",
    });
    expect(deriveOverall(comps)).toBe("unknown");
  });

  it("keeps only id, name and status: no other field from the response is carried", () => {
    const comps = okComponents({
      components: [{ ...ALL_OK.components[0], error: "Traceback: psycopg2.OperationalError host=db.internal" }],
    });
    for (const c of comps) expect(Object.keys(c).sort()).toEqual(["id", "name", "status"]);
    expect(JSON.stringify(comps)).not.toContain("Traceback");
  });

  it("falls back to the known name when the name is missing or absurd", () => {
    const comps = okComponents({
      components: [{ id: "api", name: "x".repeat(500), status: "operational" }],
    });
    expect(comps[0]).toEqual({ id: "api", name: "API", status: "operational" });
  });

  it("ignores items with bad ids and duplicate ids", () => {
    const comps = okComponents({
      components: [
        ALL_OK.components[0],
        { id: "api", name: "API again", status: "major_outage" },
        { id: "<script>", name: "x", status: "operational" },
        { id: 42, name: "x", status: "operational" },
        null,
      ],
    });
    expect(comps.filter((c) => c.id === "api")).toEqual([{ id: "api", name: "API", status: "operational" }]);
    expect(comps.map((c) => c.id)).not.toContain("<script>");
  });

  it.each([
    ["null", null],
    ["a string", "ok"],
    ["an array", []],
    ["no components key", { status: "ok" }],
    ["components not an array", { components: "all good" }],
    ["no usable item", { components: [{ id: "api" }, { name: "x" }] }],
  ])("malformed body (%s) counts as unreachable/malformed", (_label, body) => {
    expect(parseStatusBody(body)).toEqual({ kind: "unreachable", reason: "malformed" });
  });
});

describe("resultFromResponse", () => {
  it("parses a 200 JSON body", () => {
    expect(resultFromResponse(200, JSON.stringify(ALL_OK)).kind).toBe("ok");
  });

  it("malformed JSON is unreachable/malformed", () => {
    expect(resultFromResponse(200, "<html>Service Unavailable</html>")).toEqual({
      kind: "unreachable",
      reason: "malformed",
    });
    expect(resultFromResponse(200, "")).toEqual({ kind: "unreachable", reason: "malformed" });
    expect(resultFromResponse(200, '{"components":[')).toEqual({ kind: "unreachable", reason: "malformed" });
  });

  it("5xx (e.g. the host's 502/503 page when the API is down) is unreachable/http_error", () => {
    expect(resultFromResponse(502, "<html>Bad gateway</html>")).toEqual({ kind: "unreachable", reason: "http_error" });
    expect(resultFromResponse(503, JSON.stringify(ALL_OK))).toEqual({ kind: "unreachable", reason: "http_error" });
    expect(resultFromResponse(404, "")).toEqual({ kind: "unreachable", reason: "http_error" });
  });

  it("429 is rate_limited, not an outage: the API answered", () => {
    expect(resultFromResponse(429, '{"detail":"rate limited"}')).toEqual({ kind: "rate_limited" });
  });
});

describe("deriveOverall", () => {
  const c = (status: StatusComponent["status"]): StatusComponent => ({ id: status, name: status, status });

  it("is the worst known status", () => {
    expect(deriveOverall([c("operational"), c("partial_outage"), c("degraded")])).toBe("partial_outage");
    expect(deriveOverall([c("operational"), c("major_outage"), c("unknown")])).toBe("major_outage");
    expect(deriveOverall([c("operational"), c("maintenance")])).toBe("maintenance");
    expect(deriveOverall([c("maintenance"), c("degraded")])).toBe("degraded");
  });

  it("unknown beats operational: never 'all systems operational' for something unchecked", () => {
    expect(deriveOverall([c("operational"), c("unknown")])).toBe("unknown");
    expect(deriveOverall([])).toBe("unknown");
  });
});

describe("fetchPublicStatus", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns unconfigured without fetching when there is no endpoint", async () => {
    const fetch = vi.fn();
    expect(await fetchPublicStatus(null, { fetch })).toEqual({ kind: "unconfigured" });
    expect(fetch).not.toHaveBeenCalled();
  });

  it("all ok: a GET with no credentials, no cache, and an abort signal", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify(ALL_OK), { status: 200 }));
    const r = await fetchPublicStatus("https://api.example.test/status/public", { fetch });
    expect(r.kind).toBe("ok");
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.example.test/status/public");
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("omit");
    expect(init.cache).toBe("no-store");
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("timeout: aborts after FETCH_TIMEOUT_MS and reports unreachable/timeout", async () => {
    vi.useFakeTimers();
    expect(FETCH_TIMEOUT_MS).toBe(8_000);
    const fetch = vi.fn(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
    );
    const pending = fetchPublicStatus("https://api.example.test/status/public", { fetch });
    await vi.advanceTimersByTimeAsync(FETCH_TIMEOUT_MS - 1);
    let settled = false;
    void pending.then(() => (settled = true));
    await Promise.resolve();
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(await pending).toEqual({ kind: "unreachable", reason: "timeout" });
  });

  it("network failure (DNS, CORS, connection refused) is unreachable/network", async () => {
    const fetch = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    expect(await fetchPublicStatus("https://api.example.test/status/public", { fetch })).toEqual({
      kind: "unreachable",
      reason: "network",
    });
  });

  it("malformed JSON from the API is unreachable/malformed", async () => {
    const fetch = vi.fn(async () => new Response("not json at all", { status: 200 }));
    expect(await fetchPublicStatus("https://api.example.test/status/public", { fetch })).toEqual({
      kind: "unreachable",
      reason: "malformed",
    });
  });

  it("degraded component comes through", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify(withStatus("api", "degraded")), { status: 200 }));
    const r = await fetchPublicStatus("https://api.example.test/status/public", { fetch });
    expect(r.kind === "ok" && deriveOverall(r.components)).toBe("degraded");
  });
});

describe("reduceStatus", () => {
  const ok = parseStatusBody(ALL_OK);

  it("all ok -> operational with every component and the check time", () => {
    const v = reduceStatus(null, ok, NOW);
    expect(v.overall).toBe("operational");
    expect(v.components).toHaveLength(4);
    expect(v.checkedAt).toEqual(NOW);
    expect(v.reason).toBeNull();
    expect(v.notice).toBeNull();
    expect(OVERALL_HEADLINES[v.overall]).toBe("All systems operational");
  });

  it("unreachable -> major outage headline; API shown down, the rest 'No data'", () => {
    const v = reduceStatus(null, { kind: "unreachable", reason: "timeout" }, NOW);
    expect(v.overall).toBe("unreachable");
    expect(OVERALL_HEADLINES[v.overall]).toBe("Vectorae API unreachable — major outage");
    expect(v.reason).toBe("timeout");
    expect(v.components.find((c) => c.id === "api")?.status).toBe("major_outage");
    expect(v.components.filter((c) => c.id !== "api").every((c) => c.status === "unknown")).toBe(true);
  });

  it("unreachable after a good check replaces the old good data (no stale 'operational')", () => {
    const prev = reduceStatus(null, ok, new Date(NOW.getTime() - 30_000));
    const v = reduceStatus(prev, { kind: "unreachable", reason: "network" }, NOW);
    expect(v.overall).toBe("unreachable");
    expect(v.components.some((c) => c.status === "operational")).toBe(false);
  });

  it("rate limited after a good check keeps the last result and says when it is from", () => {
    const then = new Date(NOW.getTime() - 30_000);
    const prev = reduceStatus(null, ok, then);
    const v = reduceStatus(prev, { kind: "rate_limited" }, NOW);
    expect(v.overall).toBe("operational");
    expect(v.notice).toBe("rate_limited");
    expect(v.dataFrom).toEqual(then);
    expect(v.checkedAt).toEqual(NOW);
  });

  it("rate limited with no earlier result -> unknown, never operational", () => {
    const v = reduceStatus(null, { kind: "rate_limited" }, NOW);
    expect(v.overall).toBe("unknown");
    expect(v.notice).toBe("rate_limited");
  });

  it("rate limited after an outage does not resurrect old 'operational' data", () => {
    const prev = reduceStatus(null, { kind: "unreachable", reason: "network" }, NOW);
    const v = reduceStatus(prev, { kind: "rate_limited" }, NOW);
    expect(v.overall).toBe("unknown");
  });

  it("unconfigured -> its own state, not an outage", () => {
    const v = reduceStatus(null, { kind: "unconfigured" }, NOW);
    expect(v.overall).toBe("unconfigured");
    expect(v.components.every((c) => c.status === "unknown")).toBe(true);
  });
});

describe("refresh scheduling", () => {
  it("refreshes every 30 s, backs off when rate limited, never when unconfigured", () => {
    expect(REFRESH_INTERVAL_MS).toBe(30_000);
    const results: [StatusResult, number | null][] = [
      [{ kind: "ok", components: [] }, REFRESH_INTERVAL_MS],
      [{ kind: "unreachable", reason: "timeout" }, REFRESH_INTERVAL_MS],
      [{ kind: "rate_limited" }, RATE_LIMITED_RETRY_MS],
      [{ kind: "unconfigured" }, null],
    ];
    for (const [r, delay] of results) expect(nextRefreshDelay(r)).toBe(delay);
    expect(RATE_LIMITED_RETRY_MS).toBeGreaterThan(REFRESH_INTERVAL_MS);
  });

  it("does not schedule while the tab is hidden", () => {
    expect(msUntilNextCheck({ hidden: true, lastAttemptAt: null, delay: REFRESH_INTERVAL_MS, now: NOW })).toBeNull();
    expect(msUntilNextCheck({ hidden: true, lastAttemptAt: NOW, delay: REFRESH_INTERVAL_MS, now: NOW })).toBeNull();
  });

  it("checks at once when visible and never checked, or the last check is older than the delay", () => {
    expect(msUntilNextCheck({ hidden: false, lastAttemptAt: null, delay: REFRESH_INTERVAL_MS, now: NOW })).toBe(0);
    const old = new Date(NOW.getTime() - 5 * 60_000);
    expect(msUntilNextCheck({ hidden: false, lastAttemptAt: old, delay: REFRESH_INTERVAL_MS, now: NOW })).toBe(0);
  });

  it("waits only the remaining time when the tab comes back before the delay is up", () => {
    const recent = new Date(NOW.getTime() - 10_000);
    expect(msUntilNextCheck({ hidden: false, lastAttemptAt: recent, delay: REFRESH_INTERVAL_MS, now: NOW })).toBe(20_000);
  });

  it("never when there is nothing to poll", () => {
    expect(msUntilNextCheck({ hidden: false, lastAttemptAt: null, delay: null, now: NOW })).toBeNull();
  });
});

describe("applyIncidents", () => {
  const comps = (): StatusComponent[] => okComponents(ALL_OK);
  const incident = (impact: "none" | "minor" | "major" | "critical" | "maintenance", resolved = false) => ({
    id: "2026-10-11-test",
    title: "Test",
    impact,
    components: ["background-processing"],
    updates: [
      { status: "investigating" as const, at: "2026-10-11T10:00:00Z", text: "Looking." },
      ...(resolved ? [{ status: "resolved" as const, at: "2026-10-11T11:00:00Z", text: "Fixed." }] : []),
    ],
  });
  const bg = (list: StatusComponent[]) => list.find((c) => c.id === "background-processing")?.status;

  it("an open incident raises listed components to at least its impact", () => {
    expect(bg(applyIncidents(comps(), [incident("minor")]))).toBe("degraded");
    expect(bg(applyIncidents(comps(), [incident("major")]))).toBe("partial_outage");
    expect(bg(applyIncidents(comps(), [incident("critical")]))).toBe("major_outage");
    expect(bg(applyIncidents(comps(), [incident("maintenance")]))).toBe("maintenance");
    expect(bg(applyIncidents(comps(), [incident("none")]))).toBe("operational");
  });

  it("never makes a component look better, and resolved incidents change nothing", () => {
    const down = okComponents(withStatus("background-processing", "major_outage"));
    expect(bg(applyIncidents(down, [incident("minor")]))).toBe("major_outage");
    expect(bg(applyIncidents(down, [incident("maintenance")]))).toBe("major_outage");
    expect(bg(applyIncidents(comps(), [incident("critical", true)]))).toBe("operational");
  });
});

describe("colours on the marketing background", () => {
  it("every status colour meets WCAG AA (4.5:1) as text on the page background", () => {
    for (const [state, hex] of Object.entries(STATUS_COLORS)) {
      expect(contrastRatio(hex, STATUS_PAGE_BACKGROUND), state).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("there is a headline for every overall state", () => {
    const states: StatusView["overall"][] = [
      "operational", "degraded", "partial_outage", "major_outage", "maintenance",
      "unknown", "unreachable", "unconfigured",
    ];
    for (const s of states) expect(OVERALL_HEADLINES[s]).toBeTruthy();
  });
});
