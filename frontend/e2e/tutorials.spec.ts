/**
 * E2E — Feature 2, the Learn Vectorae tutorial section.
 *
 * The backend is mocked with page.route(), matching this directory's
 * convention. The mock does REAL filtering on ?q= and ?level= rather than
 * returning a fixed list, so a search test that passes actually proves the
 * page sent the query and rendered what came back — a stub that ignored the
 * parameters would let a completely broken search box pass.
 *
 * Backend behaviour is separately proven for real in tests/test_tutorials.py
 * (50 tests).
 */

import { test, expect, type Page } from "@playwright/test";

test.beforeEach(({}, testInfo) => {
  // Same reasoning as email-verification.spec.ts: WebKit hydrating a static
  // export at workers:2 needs more than Playwright's 30s default.
  testInfo.setTimeout(90_000);
});

const ACCESS = "test-access-token";
const REFRESH = "test-refresh-token";

interface MockTutorial {
  slug: string;
  title: string;
  description: string;
  level: "beginner" | "intermediate" | "advanced";
  order: number;
  youtube_id: string | null;
  duration_seconds: number | null;
  completed: boolean;
  percent: number;
}

/** Mirrors app/services/tutorials.py — same slugs, same levels, same order. */
const CATALOGUE: MockTutorial[] = [
  { slug: "getting-started-with-leadpilot", title: "Getting Started with Vectorae",
    description: "A tour of the platform end to end.", level: "beginner", order: 1,
    youtube_id: null, duration_seconds: null, completed: false, percent: 0 },
  { slug: "setting-up-your-first-campaign", title: "Setting Up Your First Campaign",
    description: "Create a product and launch outreach.", level: "beginner", order: 2,
    youtube_id: null, duration_seconds: null, completed: false, percent: 0 },
  { slug: "understanding-your-icp", title: "Understanding Your ICP",
    description: "What an Ideal Customer Profile is.", level: "beginner", order: 3,
    youtube_id: null, duration_seconds: null, completed: false, percent: 0 },
  { slug: "advanced-lead-sourcing-with-apollo", title: "Advanced Lead Sourcing with Apollo",
    description: "Build precise Apollo searches.", level: "intermediate", order: 1,
    youtube_id: null, duration_seconds: null, completed: false, percent: 0 },
  { slug: "writing-high-converting-dms", title: "Writing High-Converting DMs",
    description: "The anatomy of a message that gets replies.", level: "intermediate", order: 2,
    youtube_id: null, duration_seconds: null, completed: false, percent: 0 },
  { slug: "using-the-leadpilot-dashboard", title: "Using the Vectorae Dashboard",
    description: "Pipeline, campaigns, strategies and settings.", level: "intermediate", order: 3,
    youtube_id: null, duration_seconds: null, completed: false, percent: 0 },
  { slug: "multi-channel-outreach-strategy", title: "Multi-Channel Outreach Strategy",
    description: "Sequencing email and WhatsApp together.", level: "advanced", order: 1,
    youtube_id: null, duration_seconds: null, completed: false, percent: 0 },
  { slug: "reading-your-analytics", title: "Reading Your Analytics",
    description: "Which numbers predict booked meetings.", level: "advanced", order: 2,
    youtube_id: null, duration_seconds: null, completed: false, percent: 0 },
  { slug: "scaling-your-pipeline", title: "Scaling Your Pipeline",
    description: "Growing send volume without wrecking deliverability.", level: "advanced", order: 3,
    youtube_id: null, duration_seconds: null, completed: false, percent: 0 },
];

const LEVELS = ["beginner", "intermediate", "advanced"] as const;
const LEVEL_LABELS = { beginner: "Beginner", intermediate: "Intermediate",
                       advanced: "Advanced" } as const;

function buildPayload(state: MockTutorial[], q: string | null, level: string | null) {
  const needle = (q ?? "").trim().toLowerCase();
  let filtered = state;
  if (level) filtered = filtered.filter((t) => t.level === level);
  if (needle) {
    filtered = filtered.filter(
      (t) =>
        t.title.toLowerCase().includes(needle) ||
        t.description.toLowerCase().includes(needle),
    );
  }

  const done = state.filter((t) => t.completed).map((t) => t.slug);
  const by_level = Object.fromEntries(
    LEVELS.map((lv) => {
      const inLevel = state.filter((t) => t.level === lv);
      return [lv, {
        label: LEVEL_LABELS[lv],
        total: inLevel.length,
        completed: inLevel.filter((t) => t.completed).length,
      }];
    }),
  );

  const badgeFor = (slug: string, label: string, lv: string | null) => {
    const required = lv ? state.filter((t) => t.level === lv) : state;
    const completed = required.filter((t) => t.completed).length;
    return {
      slug, label, description: `Finish every ${lv ?? "library"} tutorial.`,
      level: lv, earned: completed === required.length && required.length > 0,
      earned_at: null, required_total: required.length,
      required_completed: completed,
    };
  };

  return {
    // Sorted the way the API sorts: level order, then order within level.
    tutorials: [...filtered]
      .sort((a, b) =>
        LEVELS.indexOf(a.level) - LEVELS.indexOf(b.level) || a.order - b.order)
      .map((t) => ({
        slug: t.slug, title: t.title, description: t.description,
        level: t.level, order: t.order, youtube_id: t.youtube_id,
        duration_seconds: t.duration_seconds,
        is_placeholder: !t.youtube_id,
        progress: {
          position_seconds: 0, duration_seconds: t.duration_seconds,
          percent: t.percent, completed: t.completed,
          completed_at: t.completed ? "2026-08-29T12:00:00+00:00" : null,
          last_watched_at: t.percent > 0 ? "2026-08-29T12:00:00+00:00" : null,
          started: t.percent > 0 || t.completed,
        },
      })),
    levels: LEVELS.map((lv) => ({ level: lv, label: LEVEL_LABELS[lv] })),
    // Summary and badges describe the WHOLE catalogue, never the filtered set.
    summary: {
      total: state.length,
      completed: done.length,
      percent: Math.round((1000 * done.length) / state.length) / 10,
      by_level,
    },
    badges: [
      badgeFor("beginner-complete", "Beginner Complete", "beginner"),
      badgeFor("intermediate-complete", "Intermediate Complete", "intermediate"),
      badgeFor("advanced-complete", "Advanced Complete", "advanced"),
      badgeFor("leadpilot-certified", "Vectorae Certified", null),
    ],
    query: { q, level },
  };
}

/**
 * Mock the whole tutorial API over a mutable in-memory catalogue, so a
 * "Mark complete" click really does change what the next GET returns.
 */
async function mockTutorials(page: Page, overrides: Partial<MockTutorial>[] = []) {
  const state: MockTutorial[] = CATALOGUE.map((t) => {
    const o = overrides.find((x) => x.slug === t.slug);
    return { ...t, ...(o ?? {}) };
  });

  await page.route("**/tutorials**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();

    // /tutorials/<slug>/complete  |  /tutorials/<slug>/progress
    const match = url.pathname.match(/\/tutorials\/([^/]+)\/(complete|progress)$/);
    if (match) {
      const slug = decodeURIComponent(match[1]);
      const row = state.find((t) => t.slug === slug);
      if (!row) return route.fulfill({ status: 404, json: { detail: "unknown tutorial" } });
      if (method === "POST") { row.completed = true; row.percent = 100; }
      if (method === "DELETE") { row.completed = false; row.percent = 0; }
      if (method === "PUT") {
        const body = request.postDataJSON() as { position_seconds: number;
                                                 duration_seconds?: number };
        if (body.duration_seconds) {
          row.percent = Math.max(row.percent,
            Math.min(100, (body.position_seconds / body.duration_seconds) * 100));
          if (row.percent >= 90) { row.completed = true; row.percent = 100; }
        }
      }
      const payload = buildPayload(state, null, null);
      const one = payload.tutorials.find((t) => t.slug === slug);
      return route.fulfill({ json: one });
    }

    return route.fulfill({
      json: buildPayload(state, url.searchParams.get("q"),
                         url.searchParams.get("level")),
    });
  });
}

async function mockShell(page: Page) {
  await page.route("**/health", (r) => r.fulfill({ json: { status: "ok" } }));
  await page.route("**/auth/me", (r) =>
    r.fulfill({ json: { id: "u1", email: "learner@x.com", plan: "free",
                        is_admin: false, email_verified: true } }));
  await page.route("**/me/theme", (r) => r.fulfill({ json: { theme: {} } }));
}

async function seedSession(page: Page) {
  await page.addInitScript(([a, r]) => {
    window.sessionStorage.setItem("ch_access_token", a);
    window.sessionStorage.setItem("ch_refresh_token", r);
  }, [ACCESS, REFRESH]);
}

async function openLearn(page: Page) {
  await seedSession(page);
  await mockShell(page);
  await page.goto("/learn/");
  await expect(page.getByRole("heading", { name: "Learn Vectorae" }))
    .toBeVisible({ timeout: 20_000 });
}

// --------------------------------------------------------------------------
// Navigation
// --------------------------------------------------------------------------

test("Learn appears in the dashboard navigation and routes to the page",
  async ({ page }) => {
    await mockTutorials(page);
    await seedSession(page);
    await mockShell(page);
    await page.route("**/leads**", (r) =>
      r.fulfill({ json: { items: [], total: 0, limit: 50, offset: 0 } }));
    await page.route("**/strategies**", (r) => r.fulfill({ json: [] }));

    await page.goto("/pipeline/");
    // href renders as "/learn/", not "/learn": next.config.js sets
    // trailingSlash:true so the static export can serve /learn/index.html.
    // .first() because the shell renders the nav twice — sidebar on >=md and
    // a bottom tab bar on mobile — and both are in the DOM at every viewport.
    const learnLink = page.getByRole("link", { name: "Learn", exact: true }).first();
    await expect(learnLink).toBeVisible({ timeout: 20_000 });
    await learnLink.click();
    await page.waitForURL(/\/learn\/?$/);
    await expect(page.getByRole("heading", { name: "Learn Vectorae" })).toBeVisible();
  });

// --------------------------------------------------------------------------
// Catalogue
// --------------------------------------------------------------------------

test("all three level sections render with their tutorials", async ({ page }) => {
  await mockTutorials(page);
  await openLearn(page);

  for (const lv of LEVELS) {
    await expect(page.getByTestId(`section-${lv}`)).toBeVisible();
  }
  await expect(page.locator('[data-testid^="tutorial-"]')).toHaveCount(9);
  // exact:true is required — Playwright matches an accessible name by
  // SUBSTRING by default, so "Advanced" also matched the card heading
  // "Advanced Lead Sourcing with Apollo".
  await expect(page.getByRole("heading", { name: "Beginner", exact: true }))
    .toBeVisible();
  await expect(page.getByRole("heading", { name: "Intermediate", exact: true }))
    .toBeVisible();
  await expect(page.getByRole("heading", { name: "Advanced", exact: true }))
    .toBeVisible();
});

test("overall progress starts at 0 of 9", async ({ page }) => {
  await mockTutorials(page);
  await openLearn(page);
  await expect(page.getByTestId("overall-progress-text")).toHaveText("0 / 9");
  await expect(page.getByRole("progressbar", { name: "Overall course progress" }))
    .toHaveAttribute("aria-valuenow", "0");
});

test("existing progress is reflected on load", async ({ page }) => {
  await mockTutorials(page, [
    { slug: "understanding-your-icp", completed: true, percent: 100 },
    { slug: "reading-your-analytics", completed: false, percent: 40 },
  ]);
  await openLearn(page);

  await expect(page.getByTestId("overall-progress-text")).toHaveText("1 / 9");
  await expect(page.getByTestId("percent-understanding-your-icp")).toHaveText("100%");
  await expect(page.getByTestId("percent-reading-your-analytics")).toHaveText("40%");
  await expect(page.getByTestId("level-summary-beginner")).toContainText("1 / 3");
});

// --------------------------------------------------------------------------
// Search + filter
// --------------------------------------------------------------------------

test("search filters the catalogue", async ({ page }) => {
  await mockTutorials(page);
  await openLearn(page);

  await page.fill("#tutorial-search", "apollo");
  await expect(page.locator('[data-testid^="tutorial-"]')).toHaveCount(1, {
    timeout: 15_000,
  });
  await expect(page.getByTestId("tutorial-advanced-lead-sourcing-with-apollo"))
    .toBeVisible();
});

test("search also matches descriptions, not just titles", async ({ page }) => {
  await mockTutorials(page);
  await openLearn(page);

  // "deliverability" appears in no title.
  await page.fill("#tutorial-search", "deliverability");
  await expect(page.getByTestId("tutorial-scaling-your-pipeline"))
    .toBeVisible({ timeout: 15_000 });
  await expect(page.locator('[data-testid^="tutorial-"]')).toHaveCount(1);
});

test("a search with no matches shows an empty state, not a blank page",
  async ({ page }) => {
    await mockTutorials(page);
    await openLearn(page);
    await page.fill("#tutorial-search", "zzzznothing");
    await expect(page.getByTestId("no-results")).toBeVisible({ timeout: 15_000 });
  });

test("clearing the search restores the full catalogue", async ({ page }) => {
  await mockTutorials(page);
  await openLearn(page);
  await page.fill("#tutorial-search", "apollo");
  await expect(page.locator('[data-testid^="tutorial-"]')).toHaveCount(1, {
    timeout: 15_000,
  });
  await page.fill("#tutorial-search", "");
  await expect(page.locator('[data-testid^="tutorial-"]')).toHaveCount(9, {
    timeout: 15_000,
  });
});

test("level buttons filter to one section", async ({ page }) => {
  await mockTutorials(page);
  await openLearn(page);

  await page.getByRole("button", { name: "Intermediate", exact: true }).click();
  await expect(page.locator('[data-testid^="tutorial-"]')).toHaveCount(3, {
    timeout: 15_000,
  });
  await expect(page.getByTestId("section-intermediate")).toBeVisible();
  await expect(page.getByTestId("section-beginner")).toHaveCount(0);

  await page.getByRole("button", { name: "All", exact: true }).click();
  await expect(page.locator('[data-testid^="tutorial-"]')).toHaveCount(9, {
    timeout: 15_000,
  });
});

test("the overall summary does NOT move while searching", async ({ page }) => {
  // It answers "how far through the course am I" — filtering the list must not
  // rewrite the user's progress.
  await mockTutorials(page, [
    { slug: "understanding-your-icp", completed: true, percent: 100 },
  ]);
  await openLearn(page);
  await expect(page.getByTestId("overall-progress-text")).toHaveText("1 / 9");

  await page.fill("#tutorial-search", "apollo");
  await expect(page.locator('[data-testid^="tutorial-"]')).toHaveCount(1, {
    timeout: 15_000,
  });
  await expect(page.getByTestId("overall-progress-text")).toHaveText("1 / 9");
});

// --------------------------------------------------------------------------
// Badges
// --------------------------------------------------------------------------

test("unearned badges are shown greyed with their remaining count",
  async ({ page }) => {
    await mockTutorials(page);
    await openLearn(page);

    const badge = page.getByTestId("badge-beginner-complete");
    await expect(badge).toBeVisible();
    await expect(badge).toHaveAttribute("data-earned", "false");
    await expect(badge).toContainText("0 of 3 complete");
  });

test("finishing a level earns exactly that badge", async ({ page }) => {
  await mockTutorials(page, [
    { slug: "getting-started-with-leadpilot", completed: true, percent: 100 },
    { slug: "setting-up-your-first-campaign", completed: true, percent: 100 },
    { slug: "understanding-your-icp", completed: true, percent: 100 },
  ]);
  await openLearn(page);

  await expect(page.getByTestId("badge-beginner-complete"))
    .toHaveAttribute("data-earned", "true");
  await expect(page.getByTestId("badge-intermediate-complete"))
    .toHaveAttribute("data-earned", "false");
  await expect(page.getByTestId("badge-leadpilot-certified"))
    .toHaveAttribute("data-earned", "false");
});

// --------------------------------------------------------------------------
// Player
// --------------------------------------------------------------------------

test("a placeholder video shows a 'not published' panel, NOT a broken iframe",
  async ({ page }) => {
    // A fake YouTube id would render YouTube's own "Video unavailable" error,
    // which looks exactly like a bug in this app.
    await mockTutorials(page);
    await openLearn(page);

    await page.getByTestId("tutorial-understanding-your-icp")
      .getByRole("button", { name: "Watch" }).click();

    await expect(page.getByTestId("video-placeholder")).toBeVisible();
    await expect(page.getByTestId("video-placeholder"))
      .toContainText(/not published yet/i);
    await expect(page.locator("iframe")).toHaveCount(0);
  });

test("a real video id renders the player mount, not the placeholder",
  async ({ page }) => {
    await mockTutorials(page, [
      { slug: "understanding-your-icp", youtube_id: "abc12345678",
        duration_seconds: 424 },
    ]);
    await openLearn(page);

    const card = page.getByTestId("tutorial-understanding-your-icp");
    await expect(card).toContainText("7:04");            // duration formatting
    await card.getByRole("button", { name: "Watch" }).click();

    await expect(page.getByTestId("yt-mount")).toBeVisible();
    await expect(page.getByTestId("video-placeholder")).toHaveCount(0);
    await expect(card.getByText("Coming soon")).toHaveCount(0);
  });

// --------------------------------------------------------------------------
// Completion
// --------------------------------------------------------------------------

test("Mark complete updates progress, the summary and the badge",
  async ({ page }) => {
    await mockTutorials(page, [
      { slug: "getting-started-with-leadpilot", completed: true, percent: 100 },
      { slug: "setting-up-your-first-campaign", completed: true, percent: 100 },
    ]);
    await openLearn(page);
    await expect(page.getByTestId("overall-progress-text")).toHaveText("2 / 9");
    await expect(page.getByTestId("badge-beginner-complete"))
      .toHaveAttribute("data-earned", "false");

    const card = page.getByTestId("tutorial-understanding-your-icp");
    await card.getByRole("button", { name: "Watch" }).click();
    await card.getByRole("button", { name: "Mark complete" }).click();

    await expect(page.getByTestId("overall-progress-text"))
      .toHaveText("3 / 9", { timeout: 15_000 });
    await expect(page.getByTestId("percent-understanding-your-icp"))
      .toHaveText("100%");
    // The third beginner video completes the level.
    await expect(page.getByTestId("badge-beginner-complete"))
      .toHaveAttribute("data-earned", "true");
  });

test("Reset progress revokes the completion and its badge", async ({ page }) => {
  await mockTutorials(page, [
    { slug: "getting-started-with-leadpilot", completed: true, percent: 100 },
    { slug: "setting-up-your-first-campaign", completed: true, percent: 100 },
    { slug: "understanding-your-icp", completed: true, percent: 100 },
  ]);
  await openLearn(page);
  await expect(page.getByTestId("badge-beginner-complete"))
    .toHaveAttribute("data-earned", "true");

  const card = page.getByTestId("tutorial-understanding-your-icp");
  await card.getByRole("button", { name: "Watch" }).click();
  await card.getByRole("button", { name: "Reset progress" }).click();

  await expect(page.getByTestId("overall-progress-text"))
    .toHaveText("2 / 9", { timeout: 15_000 });
  await expect(page.getByTestId("badge-beginner-complete"))
    .toHaveAttribute("data-earned", "false");
});

test("a completed tutorial offers Reset but not Mark complete", async ({ page }) => {
  await mockTutorials(page, [
    { slug: "understanding-your-icp", completed: true, percent: 100 },
  ]);
  await openLearn(page);

  const card = page.getByTestId("tutorial-understanding-your-icp");
  await card.getByRole("button", { name: "Watch" }).click();
  await expect(card.getByRole("button", { name: "Reset progress" })).toBeVisible();
  await expect(card.getByRole("button", { name: "Mark complete" })).toHaveCount(0);
});

test("a partly-watched tutorial offers Resume rather than Watch", async ({ page }) => {
  await mockTutorials(page, [
    { slug: "reading-your-analytics", completed: false, percent: 40 },
  ]);
  await openLearn(page);
  await expect(
    page.getByTestId("tutorial-reading-your-analytics")
      .getByRole("button", { name: "Resume" }),
  ).toBeVisible();
});
