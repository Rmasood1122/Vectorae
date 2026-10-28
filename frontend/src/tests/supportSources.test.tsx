/**
 * Phase 2 help center: the support widget shows which help-center articles
 * an answer came from.
 *
 * Three layers: the pure helper (sourceTitlesFor), the MessageSources
 * component, and the real ChatWidget with the API module mocked -- the last
 * one proves the field the backend added actually reaches the screen.
 */

import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/api/support", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/support")>();
  return {
    ...actual,
    getSupportFaq: vi.fn(),
    listChatSessions: vi.fn(),
    getChatSession: vi.fn(),
    sendChatMessage: vi.fn(),
    startChatSession: vi.fn(),
    createSupportTicket: vi.fn(),
  };
});

import type { ChatMessage, ChatReply } from "@/lib/api/types";
import {
  getSupportFaq, listChatSessions, sendChatMessage,
} from "@/lib/api/support";
import { sourceTitlesFor } from "@/lib/support/sources";
import { MessageSources } from "@/components/support/MessageSources";
import { ChatWidget } from "@/components/support/ChatWidget";

const PLANS = {
  id: "plans-limits-billing#how-much-do-the-plans-cost",
  article_id: "plans-limits-billing",
  title: "Plans, limits and billing",
};
const START = {
  id: "getting-started#do-i-need-a-plan",
  article_id: "getting-started",
  title: "Getting started with Vectorae",
};

function assistant(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: "m-2", role: "assistant", content: "Starter is $149 per month.",
    reason: "answered", confidence: 0.9, faq_ids: [PLANS.id],
    sources: [PLANS], suggest_ticket: false, created_at: null, ...overrides,
  };
}

describe("sourceTitlesFor", () => {
  it("returns article titles in citation order", () => {
    expect(sourceTitlesFor(assistant({ sources: [START, PLANS] }))).toEqual([
      "Getting started with Vectorae", "Plans, limits and billing",
    ]);
  });

  it("de-duplicates by title and drops blank titles", () => {
    const blank = { ...START, title: "   " };
    expect(sourceTitlesFor(assistant({ sources: [PLANS, PLANS, blank] }))).toEqual([
      "Plans, limits and billing",
    ]);
  });

  it("is empty for user turns and for messages without the field", () => {
    expect(sourceTitlesFor({ role: "user", sources: [PLANS] })).toEqual([]);
    // A transcript stored before Phase 2, or an optimistic local echo.
    expect(sourceTitlesFor({ role: "assistant" })).toEqual([]);
  });
});

describe("MessageSources", () => {
  it("lists each source title under a Sources label", () => {
    render(<MessageSources message={assistant({ sources: [PLANS, START] })} />);
    const list = screen.getByRole("list", { name: "Sources" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Plans, limits and billing", "Getting started with Vectorae",
    ]);
  });

  it("renders nothing when there is nothing to cite", () => {
    const { container } = render(
      <MessageSources message={assistant({ sources: [], faq_ids: [] })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("ChatWidget shows the sources of each answer", () => {
  beforeAll(() => {
    // jsdom has no layout; the widget scrolls its transcript on update.
    Element.prototype.scrollTo = function scrollTo() {};
  });

  beforeEach(() => {
    vi.mocked(getSupportFaq).mockResolvedValue({ faq: [], chat_enabled: true });
    vi.mocked(listChatSessions).mockResolvedValue({ sessions: [], retention_days: 30 });
    vi.mocked(sendChatMessage).mockReset();
  });

  function renderWidget() {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <ChatWidget />
      </QueryClientProvider>,
    );
  }

  async function ask(question: string) {
    await userEvent.click(screen.getByTestId("support-widget-open"));
    await userEvent.type(screen.getByTestId("support-input"), question);
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
  }

  it("lists the article titles under the assistant's answer", async () => {
    const reply: ChatReply = {
      session_id: "s-1",
      answer: {
        text: "Starter is $149 per month.", on_topic: true, confidence: 0.9,
        faq_ids: [PLANS.id], sources: [PLANS], suggest_ticket: false,
        reason: "answered",
      },
      message: assistant(),
    };
    vi.mocked(sendChatMessage).mockResolvedValue(reply);

    renderWidget();
    await ask("how much does it cost");

    const answer = await screen.findByTestId("support-msg-assistant");
    expect(within(answer).getByTestId("support-sources")).toHaveTextContent(
      "Plans, limits and billing",
    );
    // The user's own message never carries a sources block.
    expect(
      within(screen.getByTestId("support-msg-user")).queryByTestId("support-sources"),
    ).toBeNull();
  });

  it("shows no sources on a ticket offer", async () => {
    vi.mocked(sendChatMessage).mockResolvedValue({
      session_id: "s-1",
      answer: {
        text: "I don't have an answer for that.", on_topic: true, confidence: 0,
        faq_ids: [], sources: [], suggest_ticket: true, reason: "mock_no_match",
      },
      message: assistant({
        content: "I don't have an answer for that.", reason: "mock_no_match",
        confidence: 0, faq_ids: [], sources: [], suggest_ticket: true,
      }),
    });

    renderWidget();
    await ask("what is the weather");

    const answer = await screen.findByTestId("support-msg-assistant");
    expect(within(answer).queryByTestId("support-sources")).toBeNull();
    expect(within(answer).getByTestId("support-escalate")).toBeInTheDocument();
  });
});
