/**
 * The /status page's live board (components/status/StatusBoard.tsx) rendered
 * with fake fetches: what a visitor sees when the API is fine, degraded,
 * unreachable, or returns something unreadable.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { StatusBoard } from "@/components/status/StatusBoard";
import type { Incident } from "@/lib/statusIncidents";

const BODY = (overrides: Record<string, string> = {}) =>
  JSON.stringify({
    components: [
      ["api", "API"],
      ["background-processing", "Background processing"],
      ["ai-features", "AI features"],
      ["booking-pages", "Booking pages"],
    ].map(([id, name]) => ({
      id,
      name,
      status: overrides[id] ?? "operational",
      checked_at: "2026-10-11T12:00:00Z",
    })),
  });

const ENDPOINT = "https://api.example.test/status/public";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("StatusBoard", () => {
  it("all ok: headline, four components with text labels, last-checked time", async () => {
    const fetchImpl = vi.fn(async () => new Response(BODY(), { status: 200 }));
    render(<StatusBoard endpoint={ENDPOINT} fetchImpl={fetchImpl} incidents={[]} />);
    await waitFor(() => expect(screen.getByTestId("status-overall").textContent).toBe("All systems operational"));
    expect(screen.getByTestId("status-component-api").textContent).toContain("Operational");
    expect(screen.getAllByRole("listitem").filter((li) => li.dataset.status)).toHaveLength(4);
    expect(screen.getByTestId("status-last-checked").textContent).toMatch(/Last checked/);
    expect(screen.getByTestId("status-no-incidents").textContent).toBe("No incidents reported.");
    expect(fetchImpl).toHaveBeenCalledWith(ENDPOINT, expect.objectContaining({ method: "GET" }));
  });

  it("degraded component: degraded headline and label", async () => {
    const fetchImpl = vi.fn(async () => new Response(BODY({ "ai-features": "degraded" }), { status: 200 }));
    render(<StatusBoard endpoint={ENDPOINT} fetchImpl={fetchImpl} incidents={[]} />);
    await waitFor(() => expect(screen.getByTestId("status-overall").textContent).toBe("Degraded performance"));
    expect(screen.getByTestId("status-component-ai-features").textContent).toContain("Degraded performance");
  });

  it("API unreachable: major-outage headline, no internal detail on the page", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("Failed to fetch: getaddrinfo ENOTFOUND leadpilot-api-internal");
    });
    render(<StatusBoard endpoint={ENDPOINT} fetchImpl={fetchImpl} incidents={[]} />);
    await waitFor(() =>
      expect(screen.getByTestId("status-overall").textContent).toBe("Vectorae API unreachable — major outage"),
    );
    expect(screen.getByTestId("status-reason").textContent).toMatch(/could not be reached/);
    expect(screen.getByTestId("status-component-api").dataset.status).toBe("major_outage");
    expect(screen.getByTestId("status-component-booking-pages").textContent).toContain("No data");
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/ENOTFOUND|getaddrinfo|TypeError|internal/);
  });

  it("server error page instead of JSON: treated as unreachable, the HTML is not shown", async () => {
    const fetchImpl = vi.fn(async () => new Response("<h1>502 Bad Gateway</h1><pre>Traceback ...</pre>", { status: 502 }));
    render(<StatusBoard endpoint={ENDPOINT} fetchImpl={fetchImpl} incidents={[]} />);
    await waitFor(() => expect(screen.getByTestId("status-overall").dataset.state).toBe("unreachable"));
    expect(document.body.textContent).not.toMatch(/Traceback|Bad Gateway|502/);
  });

  it("an open incident is listed and raises its component", async () => {
    const incident: Incident = {
      id: "2026-10-11-sends-delayed",
      title: "Scheduled sends delayed",
      impact: "major",
      components: ["background-processing"],
      updates: [{ status: "investigating", at: "2026-10-11T09:00:00Z", text: "We are investigating." }],
    };
    const fetchImpl = vi.fn(async () => new Response(BODY(), { status: 200 }));
    render(<StatusBoard endpoint={ENDPOINT} fetchImpl={fetchImpl} incidents={[incident]} />);
    await waitFor(() => expect(screen.getByTestId("status-overall").textContent).toBe("Partial outage"));
    expect(screen.getByTestId("incident-2026-10-11-sends-delayed").textContent).toContain("2026-10-11 09:00 UTC");
  });

  it("polls again after 30 s while visible", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchImpl = vi.fn(async () => new Response(BODY(), { status: 200 }));
    render(<StatusBoard endpoint={ENDPOINT} fetchImpl={fetchImpl} incidents={[]} />);
    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(30_000);
    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(2));
  });

  it("pauses while the tab is hidden and checks again when it comes back", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let state: DocumentVisibilityState = "visible";
    const spy = vi.spyOn(document, "visibilityState", "get").mockImplementation(() => state);
    try {
      const fetchImpl = vi.fn(async () => new Response(BODY(), { status: 200 }));
      render(<StatusBoard endpoint={ENDPOINT} fetchImpl={fetchImpl} incidents={[]} />);
      await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));

      state = "hidden";
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(5 * 60_000);
      expect(fetchImpl).toHaveBeenCalledTimes(1);

      state = "visible";
      document.dispatchEvent(new Event("visibilitychange"));
      await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(2));
    } finally {
      spy.mockRestore();
    }
  });
});
