import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { dayRanges, fetchUptime, monitorState, parseMonitors } from "../lib/uptimerobot.ts";
import { backendBaseUrl, fetchBackend, parseBackend } from "../lib/backend.ts";
import { API, KEY, NOW, UR, allMonitors, backendBody, fakeFetch, json, monitor } from "./helpers.ts";

describe("UptimeRobot", () => {
  it("builds 90 UTC day ranges, the last ending now", () => {
    const ranges = dayRanges(NOW);
    assert.equal(ranges.length, 90);
    assert.equal(ranges[89]!.date, "2026-10-11");
    assert.equal(ranges[89]!.end, Math.floor(NOW.getTime() / 1000));
    assert.equal(ranges[0]!.date, "2026-07-14");
    assert.equal(ranges[0]!.end - ranges[0]!.start, 86_399);
  });

  it("maps status codes: 2 up, 8/9 down, paused and unchecked unknown", () => {
    assert.equal(monitorState(2), "up");
    assert.equal(monitorState(8), "down");
    assert.equal(monitorState(9), "down");
    assert.equal(monitorState(0), "unknown");
    assert.equal(monitorState(1), "unknown");
  });

  it("matches monitors by friendly name, ignoring unrelated ones", () => {
    const body = allMonitors({ worker: 9 });
    body.monitors.push({ ...monitor("worker", 2), friendly_name: "Someone else's site" });
    const result = parseMonitors(body, dayRanges(NOW));
    assert.equal(result.kind, "ok");
    if (result.kind !== "ok") return;
    assert.equal(result.monitors.worker!.state, "down");
    assert.equal(Object.keys(result.monitors).length, 6);
  });

  it("a paused duplicate never shadows the active monitor", () => {
    const ranges = dayRanges(NOW);
    for (const order of [[0, 2], [2, 0]]) {
      const body = { stat: "ok", monitors: order.map((s) => monitor("apiHttp", s)) };
      const r = parseMonitors(body, ranges);
      assert.equal(r.kind === "ok" && r.monitors.apiHttp!.state, "up");
    }
  });

  it("days before the monitor existed have no data", () => {
    const created = Math.floor(Date.parse("2026-10-09T08:00:00Z") / 1000);
    const r = parseMonitors({ stat: "ok", monitors: [monitor("website", 2, { created, uptime: "0.000" })] }, dayRanges(NOW));
    assert.equal(r.kind, "ok");
    if (r.kind !== "ok") return;
    const days = r.monitors.website!.days;
    assert.equal(days[86]!.uptime, null); // 2026-10-08
    assert.equal(days[87]!.uptime, 0); // 2026-10-09, monitor created that day
  });

  it("missing key: no request at all", async () => {
    const { fetch, calls } = fakeFetch({});
    assert.deepEqual(await fetchUptime({ UPTIMEROBOT_API_KEY: "  " }, { fetch, now: NOW }), { kind: "missing_key" });
    assert.equal(calls.length, 0);
  });

  it("POSTs the read-only key and 90 ranges to getMonitors", async () => {
    const { fetch, calls } = fakeFetch({ [UR]: () => json(allMonitors()) });
    const r = await fetchUptime({ UPTIMEROBOT_API_KEY: KEY }, { fetch, now: NOW });
    assert.equal(r.kind, "ok");
    const form = new URLSearchParams(String(calls[0]!.init!.body));
    assert.equal(form.get("api_key"), KEY);
    assert.equal(form.get("custom_uptime_ranges")!.split("-").length, 90);
    assert.equal(form.get("custom_uptime_ratios"), "90");
  });

  it("honours UPTIMEROBOT_API_URL (used by the local drills)", async () => {
    const { fetch, calls } = fakeFetch({ "http://127.0.0.1:9/v2/getMonitors": () => json(allMonitors()) });
    await fetchUptime({ UPTIMEROBOT_API_KEY: KEY, UPTIMEROBOT_API_URL: "http://127.0.0.1:9/v2/" }, { fetch, now: NOW });
    assert.equal(calls[0]!.url, "http://127.0.0.1:9/v2/getMonitors");
  });
});

describe("Vectorae /status/public", () => {
  it("accepts the backend contract and drops anything unexpected", () => {
    const body = backendBody();
    body.components.push({ id: "secret-queue", name: "x", status: "operational", checked_at: "2026-10-11T11:59:40Z" });
    body.components.push({ id: "api", name: "API", status: "on fire", checked_at: "2026-10-11T11:59:40Z" });
    const r = parseBackend(body);
    assert.equal(r.kind, "ok");
    if (r.kind !== "ok") return;
    assert.deepEqual(Object.keys(r.components).sort(), ["ai-features", "api", "background-processing", "booking-pages"]);
    assert.equal(r.components.api!.status, "operational");
  });

  it("an empty or foreign body is unreachable", () => {
    assert.equal(parseBackend({ components: [] }).kind, "unreachable");
    assert.equal(parseBackend({ detail: "Not Found" }).kind, "unreachable");
    assert.equal(parseBackend(null).kind, "unreachable");
  });

  it("only http(s) base URLs are used; trailing slashes are trimmed", () => {
    assert.equal(backendBaseUrl({ LEADPILOT_API_URL: "https://api.example.test/" }), "https://api.example.test");
    assert.equal(backendBaseUrl({ LEADPILOT_API_URL: "ftp://x" }), null);
    assert.equal(backendBaseUrl({ LEADPILOT_API_URL: "not a url" }), null);
    assert.equal(backendBaseUrl({}), null);
  });

  it("unconfigured makes no request", async () => {
    const { fetch, calls } = fakeFetch({});
    assert.deepEqual(await fetchBackend({}, { fetch }), { kind: "unconfigured" });
    assert.equal(calls.length, 0);
  });

  it("fetches /status/public under the configured base", async () => {
    const { fetch, calls } = fakeFetch({ [API]: () => json(backendBody()) });
    assert.equal((await fetchBackend({ LEADPILOT_API_URL: API }, { fetch })).kind, "ok");
    assert.equal(calls[0]!.url, `${API}/status/public`);
  });
});
