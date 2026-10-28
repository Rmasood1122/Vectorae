"use client";

/**
 * The AI support chat widget (Feature 3).
 *
 * Mounted once in Shell.tsx, so it is present on every dashboard page without
 * each page having to remember it. Closed by default — a support widget that
 * opens itself covers the product the user came to use.
 *
 * TWO MODES, and the second one is the important one:
 *
 *   chat    ask questions, get grounded answers
 *   ticket  escalate to a human
 *
 * The ticket form is reachable at ALL times, not only after the AI gives up.
 * A user who already knows the bot cannot help them should not have to
 * perform a conversation first. It is also the only path left when the daily
 * message budget is spent or the kill switch is off — both of which are
 * states the widget handles explicitly rather than showing a dead input.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ticketSubjectFor } from "@/lib/support/ticketSubject";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity, AlertCircle, LifeBuoy, MessageSquare, Send, Ticket, X,
} from "lucide-react";
import { statusPageLink } from "@/lib/statusPage";
import {
  createSupportTicket, getSupportFaq, listChatSessions, getChatSession,
  sendChatMessage, startChatSession,
} from "@/lib/api/support";
import { ApiError } from "@/lib/api/client";
import type { ChatMessage } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { Input, Label, Textarea } from "@/components/ui/input";
import { MessageSources } from "@/components/support/MessageSources";
import { cn } from "@/lib/utils";

type Mode = "chat" | "ticket";

export function ChatWidget() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<Mode>("chat");
  const [draft, setDraft] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [ticketSubject, setTicketSubject] = useState("");
  const [ticketBody, setTicketBody] = useState("");
  const [ticketSent, setTicketSent] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Only fetched once the panel is opened. A widget that queries the backend
  // on every dashboard page load costs a request per navigation for a feature
  // most sessions never touch.
  const faq = useQuery({
    queryKey: ["support", "faq"],
    queryFn: getSupportFaq,
    enabled: open,
    staleTime: 5 * 60_000,
  });

  // Resume the most recent conversation so closing and reopening the panel
  // does not silently lose the thread.
  useEffect(() => {
    if (!open || sessionId) return;
    let cancelled = false;
    listChatSessions()
      .then(async ({ sessions }) => {
        if (cancelled || !sessions.length) return;
        const latest = await getChatSession(sessions[0].id);
        if (cancelled) return;
        setSessionId(latest.id);
        setMessages(latest.messages ?? []);
      })
      .catch(() => {
        /* No history is not an error — start fresh. */
      });
    return () => {
      cancelled = true;
    };
  }, [open, sessionId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, mode]);

  const chatEnabled = faq.data?.chat_enabled !== false;

  const ask = useMutation({
    mutationFn: (message: string) =>
      sendChatMessage(message, sessionId ?? undefined),
    onMutate: (message) => {
      setError(null);
      // Optimistic echo so the question appears immediately. The id is a
      // placeholder; the server's real row replaces nothing because only the
      // assistant turn is appended from the response.
      setMessages((current) => [
        ...current,
        {
          id: `local-${Date.now()}`, role: "user", content: message,
          reason: null, confidence: null, faq_ids: [],
          suggest_ticket: false, created_at: null,
        },
      ]);
    },
    onSuccess: (reply) => {
      setSessionId(reply.session_id);
      setMessages((current) => [...current, reply.message]);
    },
    onError: (err) => {
      // Roll the optimistic echo back so the transcript never shows a question
      // that was never actually asked.
      setMessages((current) => current.slice(0, -1));
      if (err instanceof ApiError && err.status === 429) {
        setError(
          "You've reached today's message limit. You can still submit a ticket.",
        );
        setMode("ticket");
      } else if (err instanceof ApiError && err.status === 503) {
        setError("Support chat is temporarily unavailable. Submit a ticket instead.");
        setMode("ticket");
      } else {
        setError(err instanceof ApiError ? err.detail : "Could not send that message.");
      }
    },
  });

  const submitTicket = useMutation({
    mutationFn: () =>
      createSupportTicket({
        subject: ticketSubject,
        body: ticketBody,
        chatSessionId: sessionId ?? undefined,
      }),
    onSuccess: () => {
      setTicketSent(true);
      setTicketSubject("");
      setTicketBody("");
      queryClient.invalidateQueries({ queryKey: ["support", "tickets"] });
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.detail : "Could not submit the ticket."),
  });

  const send = useCallback(
    (event: React.FormEvent) => {
      event.preventDefault();
      const text = draft.trim();
      if (!text || ask.isPending) return;
      setDraft("");
      ask.mutate(text);
    },
    [draft, ask],
  );

  /**
   * Open the ticket form from an assistant message, pre-filling the subject
   * with the question that message failed to answer (Task 4).
   *
   * The question is the nearest USER message above `index`, not
   * `messages[index - 1]`: an assistant turn is normally preceded by the user
   * turn, but scanning backwards keeps this correct if a system or retry
   * message is ever inserted between them.
   *
   * An already-typed subject is never overwritten -- the user editing the
   * field and then clicking a second escalate button must not lose their
   * wording.
   */
  const escalate = useCallback(
    (index: number) => {
      setMode("ticket");
      setTicketSent(false);
      setTicketSubject((current) => {
        if (current.trim()) return current;
        for (let i = index - 1; i >= 0; i -= 1) {
          if (messages[i]?.role === "user") {
            return ticketSubjectFor(messages[i].content);
          }
        }
        return current;
      });
    },
    [messages],
  );

  const startNew = useCallback(async () => {
    const session = await startChatSession();
    setSessionId(session.id);
    setMessages([]);
    setError(null);
  }, []);

  if (!open) {
    return (
      <button
        type="button"
        data-testid="support-widget-open"
        aria-label="Open support chat"
        onClick={() => setOpen(true)}
        className="no-print fixed bottom-20 right-4 z-20 flex h-12 w-12 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg md:bottom-6"
      >
        <LifeBuoy size={22} aria-hidden="true" />
      </button>
    );
  }

  return (
    <section
      data-testid="support-widget"
      aria-label="Vectorae support"
      className="no-print fixed bottom-20 right-4 z-30 flex max-h-[70vh] w-[min(24rem,calc(100vw-2rem))] flex-col rounded-lg border border-border bg-card shadow-xl md:bottom-6"
    >
      <header className="flex items-center justify-between border-b border-border p-3">
        <span className="flex items-center gap-2 text-sm font-semibold">
          <LifeBuoy size={16} aria-hidden="true" />
          Vectorae support
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            data-testid="support-mode-chat"
            aria-pressed={mode === "chat"}
            onClick={() => { setMode("chat"); setTicketSent(false); }}
            className={cn("rounded p-1.5", mode === "chat" ? "bg-muted" : "")}
            aria-label="Chat"
          >
            <MessageSquare size={15} aria-hidden="true" />
          </button>
          <button
            type="button"
            data-testid="support-mode-ticket"
            aria-pressed={mode === "ticket"}
            onClick={() => setMode("ticket")}
            className={cn("rounded p-1.5", mode === "ticket" ? "bg-muted" : "")}
            aria-label="Submit a ticket"
          >
            <Ticket size={15} aria-hidden="true" />
          </button>
          {statusPageLink() && (
            <a
              href={statusPageLink()!.href}
              // Same tab: /status/ is a page of this app (static export), and
              // target="_blank" on a relative URL is unreliable inside the
              // Capacitor WebView.
              data-testid="support-status-link"
              className="rounded p-1.5"
              aria-label="Service status"
              title="Service status"
            >
              <Activity size={15} aria-hidden="true" />
            </a>
          )}
          <button
            type="button"
            data-testid="support-widget-close"
            onClick={() => setOpen(false)}
            className="rounded p-1.5"
            aria-label="Close support"
          >
            <X size={15} aria-hidden="true" />
          </button>
        </div>
      </header>

      {error && (
        <p role="alert" data-testid="support-error"
           className="flex items-start gap-2 border-b border-border bg-destructive/5 p-3 text-xs text-destructive">
          <AlertCircle size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          {error}
        </p>
      )}

      {mode === "chat" ? (
        <>
          <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto p-3">
            {messages.length === 0 && (
              <div className="space-y-2" data-testid="support-suggestions">
                <p className="text-xs text-muted-foreground">
                  Ask about Vectorae — setup, outreach, tutorials, your data.
                </p>
                {(faq.data?.faq ?? []).slice(0, 4).map((entry) => (
                  <button
                    key={entry.id}
                    type="button"
                    onClick={() => { setDraft(entry.question); }}
                    className="block w-full rounded border border-border px-2 py-1.5 text-left text-xs hover:bg-muted"
                  >
                    {entry.question}
                  </button>
                ))}
              </div>
            )}
            {messages.map((message, index) => (
              <div
                key={message.id}
                data-testid={`support-msg-${message.role}`}
                className={cn(
                  "max-w-[85%] rounded-lg px-3 py-2 text-sm",
                  message.role === "user"
                    ? "ml-auto bg-primary text-primary-foreground"
                    : "bg-muted",
                )}
              >
                {message.content}
                <MessageSources message={message} />
                {message.role === "assistant" && message.suggest_ticket && (
                  <button
                    type="button"
                    data-testid="support-escalate"
                    onClick={() => escalate(index)}
                    className="mt-2 block w-full rounded bg-card px-2 py-1.5 text-xs font-medium underline"
                  >
                    Submit a ticket
                  </button>
                )}
              </div>
            ))}
            {ask.isPending && (
              <p data-testid="support-thinking"
                 className="text-xs text-muted-foreground">Thinking…</p>
            )}
          </div>

          <form onSubmit={send} className="border-t border-border p-3">
            <div className="flex items-end gap-2">
              <Input
                aria-label="Your question"
                data-testid="support-input"
                value={draft}
                disabled={!chatEnabled}
                placeholder={chatEnabled ? "Ask a question…" : "Chat unavailable"}
                onChange={(e) => setDraft(e.target.value)}
              />
              <Button type="submit" size="sm"
                      disabled={!chatEnabled || !draft.trim() || ask.isPending}
                      aria-label="Send">
                <Send size={15} aria-hidden="true" />
              </Button>
            </div>
            {messages.length > 0 && (
              <button type="button" onClick={startNew}
                      data-testid="support-new-chat"
                      className="mt-2 text-xs text-muted-foreground underline">
                Start a new chat
              </button>
            )}
          </form>
        </>
      ) : (
        <form
          className="flex-1 space-y-3 overflow-y-auto p-3"
          onSubmit={(e) => { e.preventDefault(); submitTicket.mutate(); }}
        >
          {ticketSent ? (
            <div data-testid="support-ticket-sent" className="space-y-2">
              <p role="status" className="text-sm font-medium text-[rgb(var(--primary))]">
                Ticket submitted.
              </p>
              <p className="text-xs text-muted-foreground">
                The team will respond within 24 hours.
              </p>
              <Button type="button" size="sm" variant="outline"
                      onClick={() => { setTicketSent(false); setMode("chat"); }}>
                Back to chat
              </Button>
            </div>
          ) : (
            <>
              <p className="text-xs text-muted-foreground">
                Can&rsquo;t find an answer? Send it to the team — they respond
                within 24 hours.
              </p>
              <div className="space-y-1">
                <Label htmlFor="ticket-subject">Subject</Label>
                <Input id="ticket-subject" required minLength={3} maxLength={200}
                       value={ticketSubject}
                       onChange={(e) => setTicketSubject(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="ticket-body">What do you need help with?</Label>
                <Textarea id="ticket-body" required minLength={10} rows={5}
                          value={ticketBody}
                          onChange={(e) => setTicketBody(e.target.value)} />
              </div>
              <Button type="submit" className="w-full" size="sm"
                      disabled={submitTicket.isPending ||
                                ticketSubject.trim().length < 3 ||
                                ticketBody.trim().length < 10}>
                {submitTicket.isPending ? "Sending…" : "Submit ticket"}
              </Button>
            </>
          )}
        </form>
      )}
    </section>
  );
}
