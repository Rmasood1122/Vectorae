/**
 * E2E — Feature 3, the AI support chat widget.
 *
 * The backend is mocked with page.route(), so what is under test is the
 * WIDGET: where the button lives, what it does with each kind of answer, how
 * it handles a spent daily budget and the kill switch, and whether the ticket
 * form is always reachable.
 *
 * Whether the real model refuses off-topic questions is a different question
 * that no browser test can answer — see scripts/support_chat_adversarial.py
 * and the status recorded in docs/features/ai-support-chat.md.
 */

import { test, expect, type Page } from "@playwright/test";

test.beforeEach(({}, testInfo) => {
  // WebKit hydrating a static export at workers:2 needs more than the 30s
  // default — same reasoning as the other two specs in this directory.
  testInfo.setTimeout(90_000);
});

const ACCESS = "test-access-token";
const REFRESH = "test-refresh-token";

const REFUSAL =
  "I can only help with questions about Vectorae — how it works, setting up " +
  "campaigns, outreach, the learning loop, tutorials, your data, and getting " +
  "support. Ask me anything about the product and I'll do my best.";

const TICKET_SUGGESTION =
  "I'm not confident enough to answer that accurately, and I'd rather not " +
  "guess. Submit a ticket and the team will respond within 24 hours.";

const FAQ = [
  { id: "what-is-leadpilot", question: "What is Vectorae?", answer: "…" },
  { id: "what-does-it-replace", question: "What does Vectorae replace?", answer: "…" },
  { id: "who-is-it-for", question: "Who is Vectorae for?", answer: "…" },
  { id: "how-outreach-works", question: "How does outreach work?", answer: "…" },
];

async function seedSession(page: Page) {
  await page.addInitScript(([a, r]) => {
    window.sessionStorage.setItem("ch_access_token", a);
    window.sessionStorage.setItem("ch_refresh_token", r);
  }, [ACCESS, REFRESH]);
}

async function mockShell(page: Page) {
  await page.route("**/health", (r) => r.fulfill({ json: { status: "ok" } }));
  await page.route("**/auth/me", (r) =>
    r.fulfill({ json: { id: "u1", email: "user@x.com", plan: "free",
                        is_admin: false, email_verified: true } }));
  await page.route("**/me/theme", (r) => r.fulfill({ json: { theme: {} } }));
  await page.route("**/leads**", (r) =>
    r.fulfill({ json: { items: [], total: 0, limit: 50, offset: 0 } }));
  await page.route("**/strategies**", (r) => r.fulfill({ json: [] }));
}

interface SupportMockOptions {
  chatEnabled?: boolean;
  /** What POST /support/chat returns. */
  answer?: {
    text: string; on_topic: boolean; confidence: number;
    suggest_ticket: boolean; reason: string;
  };
  chatStatus?: number;
}

async function mockSupport(page: Page, opts: SupportMockOptions = {}) {
  const {
    chatEnabled = true,
    answer = {
      text: "Vectorae runs a 72-step research pipeline and books meetings.",
      on_topic: true, confidence: 0.92, suggest_ticket: false,
      reason: "answered",
    },
    chatStatus = 200,
  } = opts;

  const tickets: unknown[] = [];

  await page.route("**/support/faq", (r) =>
    r.fulfill({ json: { faq: FAQ, chat_enabled: chatEnabled } }));

  // No prior conversations, so the widget starts clean.
  await page.route("**/support/chat/sessions", async (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({ status: 201, json: {
        id: "session-new", title: null, created_at: null,
        last_message_at: null, messages: [] } });
    }
    return route.fulfill({ json: { sessions: [], retention_days: 30 } });
  });

  await page.route("**/support/chat", async (route) => {
    if (chatStatus !== 200) {
      return route.fulfill({
        status: chatStatus,
        json: { detail: chatStatus === 429
          ? { error: "rate_limit_exceeded" }
          : "Support chat is temporarily unavailable. You can still submit a ticket." },
      });
    }
    return route.fulfill({ json: {
      session_id: "session-1",
      answer,
      message: {
        id: "m2", role: "assistant", content: answer.text,
        reason: answer.reason, confidence: answer.confidence,
        faq_ids: [], suggest_ticket: answer.suggest_ticket,
        created_at: "2026-08-30T10:00:00+00:00",
      },
    } });
  });

  await page.route("**/support/tickets", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      tickets.push(body);
      return route.fulfill({ status: 201, json: {
        id: "t1", subject: body.subject, body: body.body, status: "open",
        chat_session_id: body.chat_session_id ?? null,
        created_at: "2026-08-30T10:00:00+00:00",
        resolved_at: null, resolution_note: null } });
    }
    return route.fulfill({ json: { tickets } });
  });

  return { tickets };
}

async function openWidget(page: Page) {
  await seedSession(page);
  await mockShell(page);
  await page.goto("/pipeline/");
  const launcher = page.getByTestId("support-widget-open");
  await expect(launcher).toBeVisible({ timeout: 20_000 });
  await launcher.click();
  await expect(page.getByTestId("support-widget")).toBeVisible();
}

// --------------------------------------------------------------------------
// Presence
// --------------------------------------------------------------------------

test("the widget launcher is present on every dashboard page", async ({ page }) => {
  await mockSupport(page);
  await seedSession(page);
  await mockShell(page);

  for (const path of ["/pipeline/", "/campaigns/", "/settings/"]) {
    await page.goto(path);
    await expect(page.getByTestId("support-widget-open"))
      .toBeVisible({ timeout: 20_000 });
  }
});

test("the widget starts CLOSED and opens on click", async ({ page }) => {
  // A support widget that opens itself covers the product the user came for.
  await mockSupport(page);
  await seedSession(page);
  await mockShell(page);
  await page.goto("/pipeline/");

  await expect(page.getByTestId("support-widget")).toHaveCount(0);
  await page.getByTestId("support-widget-open").click();
  await expect(page.getByTestId("support-widget")).toBeVisible();
});

test("it can be closed again", async ({ page }) => {
  await mockSupport(page);
  await openWidget(page);
  await page.getByTestId("support-widget-close").click();
  await expect(page.getByTestId("support-widget")).toHaveCount(0);
  await expect(page.getByTestId("support-widget-open")).toBeVisible();
});

test("suggested questions come from the real FAQ", async ({ page }) => {
  await mockSupport(page);
  await openWidget(page);
  const suggestions = page.getByTestId("support-suggestions");
  await expect(suggestions).toBeVisible();
  await expect(suggestions).toContainText("What is Vectorae?");
  await expect(suggestions).toContainText("How does outreach work?");
});

test("clicking a suggestion fills the input", async ({ page }) => {
  await mockSupport(page);
  await openWidget(page);
  await page.getByRole("button", { name: "What is Vectorae?" }).click();
  await expect(page.getByTestId("support-input")).toHaveValue("What is Vectorae?");
});

// --------------------------------------------------------------------------
// Answers
// --------------------------------------------------------------------------

test("an on-topic question shows the grounded answer", async ({ page }) => {
  await mockSupport(page);
  await openWidget(page);

  await page.getByTestId("support-input").fill("What is Vectorae?");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByTestId("support-msg-user")).toContainText("What is Vectorae?");
  await expect(page.getByTestId("support-msg-assistant"))
    .toContainText("72-step research pipeline", { timeout: 15_000 });
  // A confident answer does not push the ticket button at the user.
  await expect(page.getByTestId("support-escalate")).toHaveCount(0);
});

test("an OFF-TOPIC question shows the refusal and offers no ticket",
  async ({ page }) => {
    // Off-topic must not invite a ticket — filing one about the weather makes
    // work for a human and teaches the user the bot is a routing layer.
    await mockSupport(page, {
      answer: { text: REFUSAL, on_topic: false, confidence: 1,
                suggest_ticket: false, reason: "off_topic" },
    });
    await openWidget(page);

    await page.getByTestId("support-input").fill("What is the capital of France?");
    await page.getByRole("button", { name: "Send" }).click();

    const reply = page.getByTestId("support-msg-assistant");
    await expect(reply).toContainText("I can only help with questions about Vectorae",
                                      { timeout: 15_000 });
    await expect(reply).not.toContainText("Paris");
    await expect(page.getByTestId("support-escalate")).toHaveCount(0);
  });

test("a low-confidence answer offers the ticket escalation", async ({ page }) => {
  await mockSupport(page, {
    answer: { text: TICKET_SUGGESTION, on_topic: true, confidence: 0,
              suggest_ticket: true, reason: "low_confidence" },
  });
  await openWidget(page);

  await page.getByTestId("support-input").fill("How much does it cost?");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByTestId("support-msg-assistant"))
    .toContainText("not confident enough", { timeout: 15_000 });
  const escalate = page.getByTestId("support-escalate");
  await expect(escalate).toBeVisible();
  await escalate.click();
  await expect(page.getByLabel("Subject")).toBeVisible();
});

test("a model outage still offers a route forward", async ({ page }) => {
  await mockSupport(page, {
    answer: { text: TICKET_SUGGESTION, on_topic: true, confidence: 0,
              suggest_ticket: true, reason: "model_error" },
  });
  await openWidget(page);
  await page.getByTestId("support-input").fill("anything");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByTestId("support-escalate")).toBeVisible({ timeout: 15_000 });
});

// --------------------------------------------------------------------------
// Limits and the kill switch
// --------------------------------------------------------------------------

test("hitting the daily limit explains it and switches to the ticket form",
  async ({ page }) => {
    await mockSupport(page, { chatStatus: 429 });
    await openWidget(page);

    await page.getByTestId("support-input").fill("question eleven");
    await page.getByRole("button", { name: "Send" }).click();

    await expect(page.getByTestId("support-error"))
      .toContainText(/message limit/i, { timeout: 15_000 });
    await expect(page.getByLabel("Subject")).toBeVisible();
    // The optimistic echo is rolled back — the transcript must not show a
    // question that was never actually asked.
    await expect(page.getByTestId("support-msg-user")).toHaveCount(0);
  });

test("the kill switch disables the input but not the ticket form",
  async ({ page }) => {
    await mockSupport(page, { chatEnabled: false });
    await openWidget(page);

    await expect(page.getByTestId("support-input")).toBeDisabled({ timeout: 15_000 });
    await page.getByTestId("support-mode-ticket").click();
    await expect(page.getByLabel("Subject")).toBeEnabled();
  });

// --------------------------------------------------------------------------
// Tickets
// --------------------------------------------------------------------------

test("the ticket form is reachable WITHOUT chatting first", async ({ page }) => {
  // A user who already knows the bot cannot help should not have to perform a
  // conversation before reaching a human.
  await mockSupport(page);
  await openWidget(page);

  await page.getByTestId("support-mode-ticket").click();
  await expect(page.getByLabel("Subject")).toBeVisible();
});

test("submitting a ticket sends it and confirms", async ({ page }) => {
  const { tickets } = await mockSupport(page);
  await openWidget(page);

  await page.getByTestId("support-mode-ticket").click();
  await page.getByLabel("Subject").fill("Billing question");
  await page.getByLabel("What do you need help with?")
    .fill("The AI could not answer my question about invoices.");
  await page.getByRole("button", { name: "Submit ticket" }).click();

  await expect(page.getByTestId("support-ticket-sent"))
    .toContainText(/ticket submitted/i, { timeout: 15_000 });
  await expect(page.getByTestId("support-ticket-sent"))
    .toContainText(/within 24 hours/i);
  expect(tickets).toHaveLength(1);
});

test("the submit button stays disabled until both fields are long enough",
  async ({ page }) => {
    await mockSupport(page);
    await openWidget(page);
    await page.getByTestId("support-mode-ticket").click();

    const submit = page.getByRole("button", { name: "Submit ticket" });
    await expect(submit).toBeDisabled();
    await page.getByLabel("Subject").fill("Hi");            // too short
    await expect(submit).toBeDisabled();
    await page.getByLabel("Subject").fill("A real subject");
    await page.getByLabel("What do you need help with?").fill("short");
    await expect(submit).toBeDisabled();
    await page.getByLabel("What do you need help with?")
      .fill("A long enough description of the problem.");
    await expect(submit).toBeEnabled();
  });
