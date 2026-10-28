"use client";

/** Engagement Hub, Feature 2 — the PUBLIC booking page. No auth.
 *
 * Deliberately outside the (app) route group, so it gets the root layout's
 * providers and none of Shell's navigation, session guard or bottom tab bar.
 * The person on this page is a prospect, not a user.
 *
 * ─── WHY THIS IS /book/ AND NOT /book/[slug]/ ──────────────────────────────
 * The brief specifies `app/book/[slug]/page.tsx`. This frontend is built with
 * `output: 'export'` (next.config.js — it is what Capacitor bundles into the
 * APK), and a static export can only emit routes it can enumerate at build
 * time. A dynamic segment needs `generateStaticParams`, and the set of booking
 * slugs is created by users after the build. There are no `[param]` routes
 * anywhere in this app for exactly this reason — `/strategies/detail?id=` is
 * the established pattern.
 *
 * So the route is static and the slug is resolved from EITHER form:
 *   /book/?slug=intro-call     works with no hosting configuration at all
 *   /book/intro-call/          works when the host rewrites /book/* to this
 *                              page (frontend/vercel.json ships that rewrite)
 *
 * That keeps the shareable link pretty on the deployed site without making the
 * build depend on data that does not exist yet.
 */

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { CalendarCheck2, Clock } from "lucide-react";

import {
  bookPublicSlot,
  getPublicBookingPage,
  getPublicSlots,
  type BookingConfirmation,
  type Slot,
} from "@/lib/api/calendar";
import { MiniCalendar } from "@/components/calendar/MiniCalendar";
import { SlotPicker } from "@/components/calendar/SlotPicker";
import { Button } from "@/components/ui/button";
import { Input, Label, Textarea } from "@/components/ui/input";
import { browserTimezone, dateKey } from "@/lib/calendar/grid";

/** A short, curated list plus whatever the browser reports.
 *
 *  Not the full IANA set: it is ~600 entries, and a select of 600 options is a
 *  worse way to pick a timezone than a correct default and a handful of common
 *  alternatives. The browser's own zone is prepended and pre-selected, which is
 *  the right answer for almost everyone. */
const COMMON_ZONES = [
  "UTC",
  "America/Los_Angeles",
  "America/Chicago",
  "America/New_York",
  "Europe/London",
  "Europe/Berlin",
  "Asia/Karachi",
  "Asia/Dubai",
  "Asia/Singapore",
  "Australia/Sydney",
  "Pacific/Auckland",
];

/** The slug, from the query string or from the path. See the module docstring. */
function useSlug(): string | null {
  const params = useSearchParams();
  const [fromPath, setFromPath] = useState<string | null>(null);

  useEffect(() => {
    // Read in an effect, not during render: `window` does not exist while
    // Next pre-renders this page to HTML at build time.
    const segments = window.location.pathname.split("/").filter(Boolean);
    // ["book"] -> no slug; ["book", "intro-call"] -> "intro-call".
    setFromPath(segments[0] === "book" && segments[1] ? segments[1] : null);
  }, []);

  return params.get("slug") ?? fromPath;
}

/** The Suspense boundary `useSearchParams` requires during prerender.
 *
 *  Next 14 refuses to statically export a page that reads search params
 *  without one -- it cannot know them at build time, so the subtree has to be
 *  able to suspend into a client render. Every other page in this app that
 *  reads them (/strategies/detail, /meetings/detail) sits inside the (app)
 *  layout, whose Shell renders nothing until it has checked the session, and
 *  is therefore never prerendered far enough to hit the same rule. This page
 *  is deliberately outside that layout -- the visitor is a prospect, not a
 *  user -- so it declares the boundary itself.
 *
 *  The fallback is the same "Loading…" the page shows while fetching, so the
 *  visitor never sees two different loading states in a row. */
export default function PublicBookingPage() {
  return (
    <Suspense
      fallback={
        <Shell>
          <p className="text-sm text-muted-foreground" role="status">
            Loading…
          </p>
        </Shell>
      }
    >
      <BookingFlow />
    </Suspense>
  );
}

function BookingFlow() {
  const slug = useSlug();
  const [timezone, setTimezone] = useState(() => browserTimezone());
  const [month, setMonth] = useState(() => new Date());
  const [selectedDay, setSelectedDay] = useState(() => new Date());
  const [slot, setSlot] = useState<Slot | null>(null);
  const [confirmed, setConfirmed] = useState<BookingConfirmation | null>(null);

  const zones = useMemo(() => {
    const browser = browserTimezone();
    return [browser, ...COMMON_ZONES.filter((zone) => zone !== browser)];
  }, []);

  const pageQuery = useQuery({
    queryKey: ["public-booking-page", slug],
    queryFn: () => getPublicBookingPage(slug as string),
    enabled: !!slug,
    retry: false,
  });

  const slotsQuery = useQuery({
    queryKey: ["public-slots", slug, timezone],
    queryFn: () => getPublicSlots(slug as string, timezone),
    enabled: !!slug && !!pageQuery.data,
    // Slots go stale the moment somebody else books one. 60s is short enough
    // that a visitor rarely sees a taken slot, and the 409 from the booking
    // endpoint is the backstop for when they do.
    staleTime: 60_000,
  });

  const daysWithSlots = useMemo(
    () => new Set((slotsQuery.data ?? []).map((s) => s.date)),
    [slotsQuery.data],
  );

  // Land the visitor on the first day that actually has times, rather than on
  // today with an empty list and no hint that Thursday is wide open.
  useEffect(() => {
    if (!slotsQuery.data?.length) return;
    if (daysWithSlots.has(dateKey(selectedDay))) return;
    const first = new Date(slotsQuery.data[0].start_at);
    setSelectedDay(first);
    setMonth(first);
    // selectedDay is intentionally excluded: including it would re-run this
    // every time the visitor picks an empty day and yank them back.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slotsQuery.data, daysWithSlots]);

  if (!slug) {
    return (
      <Shell>
        <p className="text-sm text-muted-foreground">
          This booking link is missing its page name.
        </p>
      </Shell>
    );
  }

  if (pageQuery.isLoading) {
    return (
      <Shell>
        <p className="text-sm text-muted-foreground" role="status">
          Loading…
        </p>
      </Shell>
    );
  }

  if (pageQuery.error || !pageQuery.data) {
    return (
      <Shell>
        <h1 className="text-lg font-semibold">This link isn&apos;t available</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          The booking page may have been paused or removed. Ask whoever sent it
          for a new link.
        </p>
      </Shell>
    );
  }

  const page = pageQuery.data;

  if (confirmed) {
    return (
      <Shell>
        <div className="text-center">
          <CalendarCheck2
            size={40}
            aria-hidden="true"
            className="mx-auto text-[rgb(var(--success))]"
          />
          <h1 className="mt-3 text-lg font-semibold">You&apos;re booked</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {new Date(confirmed.start_at).toLocaleString([], {
              weekday: "long",
              day: "numeric",
              month: "long",
              hour: "2-digit",
              minute: "2-digit",
              timeZone: timezone,
            })}{" "}
            ({timezone})
          </p>
          <p className="mt-3 text-sm">
            A confirmation is on its way to your inbox.
          </p>
          {confirmed.meeting_link && (
            <a
              href={confirmed.meeting_link}
              className="mt-3 inline-block text-sm underline underline-offset-2"
            >
              Join link
            </a>
          )}
        </div>
      </Shell>
    );
  }

  return (
    <Shell>
      <header className="mb-4">
        <h1 className="text-lg font-semibold">{page.title}</h1>
        <p className="mt-1 flex items-center gap-1 text-sm text-muted-foreground">
          <Clock size={14} aria-hidden="true" />
          {page.duration_minutes} minutes
        </p>
        {page.description && (
          <p className="mt-2 whitespace-pre-wrap text-sm text-muted-foreground">
            {page.description}
          </p>
        )}
      </header>

      <div className="space-y-4">
        <div className="space-y-1">
          <Label htmlFor="tz">Time zone</Label>
          <select
            id="tz"
            value={timezone}
            onChange={(event) => {
              setTimezone(event.target.value);
              setSlot(null);
            }}
            className="h-10 w-full rounded border border-border bg-card px-3 text-sm"
          >
            {zones.map((zone) => (
              <option key={zone} value={zone}>
                {zone}
              </option>
            ))}
          </select>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="rounded border border-border p-3">
            <MiniCalendar
              month={month}
              selected={selectedDay}
              onMonthChange={setMonth}
              onSelect={(day) => {
                setSelectedDay(day);
                setSlot(null);
              }}
              markedDates={daysWithSlots}
              minDate={new Date()}
            />
          </div>

          <div>
            <SlotPicker
              slots={slotsQuery.data ?? []}
              selectedDate={dateKey(selectedDay)}
              selectedSlot={slot}
              onSelect={setSlot}
              timezone={timezone}
              isLoading={slotsQuery.isLoading}
            />
          </div>
        </div>

        {slot && (
          <BookingForm
            slug={slug}
            slot={slot}
            timezone={timezone}
            questions={page.custom_questions}
            onBooked={setConfirmed}
          />
        )}
      </div>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto min-h-screen w-full max-w-2xl bg-background px-4 py-10 text-foreground">
      <div className="rounded-lg border border-border bg-card p-5 text-card-foreground shadow-sm">
        {children}
      </div>
      <p className="mt-4 text-center text-xs text-muted-foreground">
        Scheduling by Vectorae
      </p>
    </main>
  );
}

function BookingForm({
  slug,
  slot,
  timezone,
  questions,
  onBooked,
}: {
  slug: string;
  slot: Slot;
  timezone: string;
  questions: { key: string; label: string; required?: boolean }[];
  onBooked: (confirmation: BookingConfirmation) => void;
}) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const book = useMutation({
    mutationFn: () =>
      bookPublicSlot(slug, {
        start_at: slot.start_at,
        invitee_name: name.trim(),
        invitee_email: email.trim(),
        invitee_phone: phone.trim() || null,
        invitee_timezone: timezone,
        answers,
      }),
    onSuccess: onBooked,
    onError: (err) => setError((err as Error).message),
  });

  const missingRequired = questions.some(
    (question) => question.required && !(answers[question.key] ?? "").trim(),
  );
  const canSubmit = name.trim() && email.trim() && !missingRequired;

  return (
    <form
      className="space-y-3 border-t border-border pt-4"
      onSubmit={(event) => {
        event.preventDefault();
        setError(null);
        book.mutate();
      }}
    >
      <p className="text-sm font-medium">
        {new Date(slot.start_at).toLocaleString([], {
          weekday: "long",
          day: "numeric",
          month: "long",
          hour: "2-digit",
          minute: "2-digit",
          timeZone: timezone,
        })}
      </p>

      <div className="space-y-1">
        <Label htmlFor="bk-name">Your name</Label>
        <Input
          id="bk-name"
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </div>

      <div className="space-y-1">
        <Label htmlFor="bk-email">Email</Label>
        <Input
          id="bk-email"
          type="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </div>

      <div className="space-y-1">
        <Label htmlFor="bk-phone">Phone (optional)</Label>
        <Input
          id="bk-phone"
          value={phone}
          onChange={(event) => setPhone(event.target.value)}
        />
      </div>

      {questions.map((question) => (
        <div key={question.key} className="space-y-1">
          <Label htmlFor={`q-${question.key}`}>
            {question.label}
            {question.required && (
              <span aria-hidden="true" className="ml-1 text-[rgb(var(--destructive))]">
                *
              </span>
            )}
          </Label>
          <Textarea
            id={`q-${question.key}`}
            rows={2}
            required={question.required}
            value={answers[question.key] ?? ""}
            onChange={(event) =>
              setAnswers({ ...answers, [question.key]: event.target.value })
            }
          />
        </div>
      ))}

      {error && (
        <p role="alert" className="text-sm text-[rgb(var(--destructive))]">
          {error}
        </p>
      )}

      <Button type="submit" disabled={!canSubmit || book.isPending} className="w-full">
        {book.isPending ? "Booking…" : "Confirm booking"}
      </Button>
    </form>
  );
}
