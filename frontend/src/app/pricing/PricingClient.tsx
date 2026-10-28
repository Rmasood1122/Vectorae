"use client";

/** Section E — the public pricing page.
 *
 *  Two ways to pay, side by side: the SUBSCRIPTION (visually marked
 *  Recommended, four tiers, billed monthly — or annually when the backend
 *  offers it: a Monthly/Annual toggle) and PAY PER MEETING BOOKED. Every
 *  price and limit comes from GET /billing/catalog, which reads the same
 *  module checkout charges from, so this page cannot advertise a number the
 *  product does not bill or a limit it does not enforce.
 *
 *  AUTO-RENEWAL DISCLOSURE (docs/features/annual-billing.md): next to the
 *  checkout button the page states the amount, the renewal term and how to
 *  cancel, before the customer is sent to payment; annual also needs the
 *  "I agree to these renewal terms" tick (the backend refuses without it).
 *
 *  Outside the (app) route group: prospects see it without an account. */

import { useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarCheck2, Check, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { ApiError, restoreSession } from "@/lib/api/client";
import { me } from "@/lib/api/auth";
import { billingApi } from "@/lib/api/billing";
import { PHONE_NOT_VERIFIED } from "@/lib/identity";
import {
  breakEvenMeetings,
  checkoutNextStep,
  compareOptions,
  featureList,
  formatPrice,
  checkoutButtonLabel,
  newAccountTerms,
  planSwitchNotice,
  renewalDisclosure,
  type BillingInterval,
  type CheckoutTerms,
  type CheckoutModel,
  type MonthlyTier,
} from "@/lib/pricing";
import { Button } from "@/components/ui/button";
import { LogoMark } from "@/components/ui/Logo";
import { cn } from "@/lib/utils";

/** The interactive pricing page. Rendered by ./page.tsx (a server component,
 *  so the page has its own metadata), which passes in the shared footer and
 *  the static plan list shown until the live catalog loads (so the exported
 *  HTML names every plan for crawlers that do not run JavaScript). */
export function PricingClient({ footer, staticPlans, intro, checkoutLive }: {
  footer: ReactNode;
  staticPlans: ReactNode;
  /** False until Stripe checkout is live (product-facts.json): no trial or
   *  "cancel online" promise, and a "checkout opens soon" note instead. */
  checkoutLive: boolean;
  /** Product description and capability status from product-facts.json. */
  intro: ReactNode;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  // Signed-in state (and whether the account already has a plan). Restores a
  // persistent session first, so a returning visitor is recognised here too.
  const { data: viewer } = useQuery({
    queryKey: ["auth", "me"],
    queryFn: async () => ((await restoreSession()) ? me() : null),
    retry: false,
    staleTime: 60 * 1000,
  });
  const signedIn = !!viewer;
  const needsPlan = viewer?.has_active_plan === false;
  const { data: catalog, isLoading, isError } = useQuery({
    queryKey: ["billing", "catalog"],
    queryFn: billingApi.catalog,
    staleTime: 10 * 60 * 1000,
  });
  const [tierId, setTierId] = useState("growth");
  const [meetings, setMeetings] = useState(4);
  const [interval, setBillingInterval] = useState<BillingInterval>("monthly");
  const [agreed, setAgreed] = useState(false);
  // The trial a checkout would get NOW (first trial / used trial / pilot) is
  // the backend's decision; the disclosure and the button are driven by it.
  // A signed-out visitor sees a new account's terms (they sign up first).
  // The current subscription, so the page can say what a switch does to it
  // BEFORE checkout (planSwitchNotice; gaps-sweep-2 #15).
  const { data: overview } = useQuery({
    queryKey: ["billing", "overview"],
    queryFn: billingApi.overview,
    enabled: signedIn,
    retry: false,
    staleTime: 60 * 1000,
  });
  const switchNotice = planSwitchNotice(overview?.subscription);
  const { data: accountTerms, isLoading: termsLoading } = useQuery({
    queryKey: ["billing", "checkout-terms"],
    queryFn: billingApi.checkoutTerms,
    enabled: signedIn,
    retry: false,
    staleTime: 60 * 1000,
  });

  const checkout = useMutation({
    mutationFn: (args: { model: CheckoutModel; tier?: string; agreed?: boolean }) =>
      billingApi.checkout(args.model, args.tier, args.agreed ?? false),
    onSuccess: (res) => {
      const next = checkoutNextStep(res);
      if (next.kind === "redirect") {
        window.location.assign(next.url);
        return;
      }
      toast.success("Your plan is active.");
      void queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
      router.push("/settings?billing=success");
    },
    onError: (err) => {
      if (err instanceof ApiError && err.detail === PHONE_NOT_VERIFIED) {
        toast.message("Verify your phone to choose a plan.");
        router.push("/onboarding/verify");
        return;
      }
      toast.error(err instanceof ApiError ? err.detail : "Could not start checkout");
    },
  });

  function choose(model: CheckoutModel, tier?: string) {
    if (!signedIn) {
      router.push("/signup");
      return;
    }
    checkout.mutate({ model, tier, agreed });
  }

  const tier: MonthlyTier | undefined = useMemo(
    () => catalog?.monthly.tiers.find((t) => t.id === tierId) ?? catalog?.monthly.tiers[0],
    [catalog, tierId],
  );
  // Annual is shown only while the backend offers it (catalog.annual).
  const annualOffered = !!catalog?.annual && catalog.annual.tiers.length > 0;
  const showAnnual = annualOffered && interval === "annual";
  const annualTier = catalog?.annual?.tiers.find((t) => t.id === tier?.id);
  const terms: CheckoutTerms | undefined = signedIn
    ? accountTerms
    : (catalog ? newAccountTerms(catalog.trial_days, new Date()) : undefined);
  const chosenInterval: BillingInterval = showAnnual && annualTier ? "annual" : "monthly";
  const disclosure = catalog && tier && terms
    ? renewalDisclosure({
      interval: chosenInterval,
      tierName: tier.name,
      amountCents: chosenInterval === "annual" && annualTier
        ? annualTier.annual_price_cents : tier.price_cents,
      currency: catalog.currency,
      terms,
    })
    : null;

  return (
    <main className="min-h-dvh bg-background">
      <header className="mx-auto flex max-w-6xl items-center justify-between p-4">
        <Link href="/" className="flex items-center gap-2 font-semibold">
          <LogoMark size={22} /> Vectorae
        </Link>
        <Link href={signedIn ? "/pipeline" : "/login"} className="text-sm underline">
          {signedIn ? "Back to app" : "Sign in"}
        </Link>
      </header>

      <section className="mx-auto max-w-6xl px-4 pb-16">
        <div className="mx-auto max-w-2xl py-8 text-center">
          <h1 className="text-3xl font-bold tracking-tight md:text-4xl">Pricing that fits how you sell</h1>
          <p className="mt-3 text-muted-foreground">
            A predictable monthly plan, or pay only when a prospect books a meeting.
          </p>
          {intro}
          {needsPlan && (
            <p role="status" className="mt-4 rounded-lg border border-border bg-card p-3 text-sm">
              Welcome, {viewer?.email}. Choose a plan to start using Vectorae.
            </p>
          )}
        </div>

        {switchNotice && (
          <p role="note" data-testid="plan-switch-notice"
             className="mx-auto mb-6 max-w-2xl rounded-lg border border-border bg-card p-3 text-sm">
            {switchNotice}
          </p>
        )}

        {!catalog && staticPlans}
        {isLoading && <p className="text-center text-muted-foreground">Loading plans…</p>}
        {isError && <p className="text-center text-destructive">Plans could not be loaded. Refresh to try again.</p>}

        {catalog && tier && (
          <>
            <div className="grid gap-6 lg:grid-cols-[1.6fr_1fr]">
              {/* ── Option 1: Monthly (Recommended) ───────────────────────── */}
              <article
                aria-labelledby="monthly-title"
                className="relative rounded-xl border-2 border-[rgb(var(--primary))] bg-card p-6 shadow-lg"
              >
                <span className="absolute -top-3 left-6 inline-flex items-center gap-1 rounded-full bg-[rgb(var(--primary))] px-3 py-1 text-xs font-semibold text-primary-foreground">
                  <Sparkles size={12} aria-hidden="true" /> Recommended
                </span>
                <h2 id="monthly-title" className="text-xl font-semibold">
                  {showAnnual ? catalog.annual?.name : catalog.monthly.name}
                </h2>
                <p className="text-sm text-muted-foreground">
                  {checkoutLive
                    ? `A ${catalog.trial_days}-day free trial for a new account. Cancel anytime online.`
                    : "Billed monthly. Online checkout opens soon."}
                </p>
                {annualOffered && (
                  <div role="radiogroup" aria-label="Billing period"
                       className="mt-4 inline-flex rounded-lg border border-border p-1 text-sm">
                    {(["monthly", "annual"] as const).map((value) => (
                      <button key={value} type="button" role="radio"
                              aria-checked={interval === value}
                              onClick={() => { setBillingInterval(value); setAgreed(false); }}
                              className={cn("rounded-md px-3 py-1",
                                interval === value ? "bg-muted font-semibold" : "text-muted-foreground")}>
                        {value === "monthly" ? "Monthly" : `Annual · ${12 - (catalog.annual?.months_charged ?? 12)} months free`}
                      </button>
                    ))}
                  </div>
                )}

                <div role="radiogroup" aria-label="Monthly tier" className="mt-5 grid gap-3 sm:grid-cols-2">
                  {catalog.monthly.tiers.map((t) => {
                    const yearly = showAnnual ? catalog.annual?.tiers.find((a) => a.id === t.id) : undefined;
                    return (
                      <button
                        key={t.id}
                        type="button"
                        role="radio"
                        aria-checked={t.id === tier.id}
                        onClick={() => { setTierId(t.id); setAgreed(false); }}
                        className={cn(
                          "rounded-lg border p-4 text-left transition-colors",
                          t.id === tier.id
                            ? "border-[rgb(var(--primary))] bg-muted"
                            : "border-border hover:bg-muted/50",
                        )}
                      >
                        <div className="flex items-baseline justify-between">
                          <span className="font-semibold">{t.name}</span>
                          {yearly ? (
                            <span className="text-lg font-bold">
                              {formatPrice(yearly.annual_price_cents, catalog.currency)}
                              <span className="text-xs font-normal text-muted-foreground">/yr</span>
                            </span>
                          ) : (
                            <span className="text-lg font-bold">
                              {formatPrice(t.price_cents, catalog.currency)}
                              <span className="text-xs font-normal text-muted-foreground">/mo</span>
                            </span>
                          )}
                        </div>
                        {yearly && (
                          <p className="mt-1 text-xs">
                            {formatPrice(yearly.monthly_equivalent_cents, catalog.currency)}/mo equivalent ·
                            save {formatPrice(yearly.savings_cents, catalog.currency)} a year
                          </p>
                        )}
                        <p className="mt-1 text-xs text-muted-foreground">{t.tagline}</p>
                      </button>
                    );
                  })}
                </div>

                <ul className="mt-5 grid gap-2 text-sm sm:grid-cols-2">
                  {featureList(tier.limits).map((f) => (
                    <li key={f} className="flex items-start gap-2">
                      <Check size={16} className="mt-0.5 shrink-0 text-[rgb(var(--primary))]" aria-hidden="true" />
                      {f}
                    </li>
                  ))}
                </ul>

                {checkoutLive ? (
                  <>
                    <p data-testid="renewal-disclosure"
                       className="mt-6 rounded-lg border border-border bg-muted/40 p-3 text-xs leading-relaxed">
                      {disclosure ?? (termsLoading ? "Loading your terms…" : "Your terms could not be loaded. Refresh to try again.")}
                    </p>
                    {/* Every auto-renewing plan, monthly and annual, needs the tick. */}
                    <label className="mt-3 flex items-start gap-2 text-sm">
                      <input type="checkbox" className="mt-1" checked={agreed} disabled={!disclosure}
                             onChange={(e) => setAgreed(e.target.checked)} />
                      <span>I agree to these renewal terms.</span>
                    </label>
                    <Button
                      className="mt-4 w-full"
                      disabled={checkout.isPending || !terms || !disclosure || (signedIn && !agreed)}
                      onClick={() => choose(chosenInterval, tier.id)}
                    >
                      {terms ? checkoutButtonLabel(terms, tier.name, chosenInterval) : "Loading…"}
                    </Button>
                  </>
                ) : (
                  <CheckoutSoon />
                )}
              </article>

              {/* ── Option 2: Pay per meeting ─────────────────────────────── */}
              <article aria-labelledby="ppm-title" className="rounded-xl border border-border bg-card p-6">
                <h2 id="ppm-title" className="flex items-center gap-2 text-xl font-semibold">
                  <CalendarCheck2 size={20} aria-hidden="true" /> {catalog.pay_per_meeting.name}
                </h2>
                <p className="text-sm text-muted-foreground">{catalog.pay_per_meeting.tagline}</p>
                <p className="mt-5 text-3xl font-bold">
                  {formatPrice(catalog.pay_per_meeting.price_per_meeting_cents, catalog.currency)}
                  <span className="text-sm font-normal text-muted-foreground"> per booked meeting</span>
                </p>
                <p className="text-sm text-muted-foreground">
                  {formatPrice(catalog.pay_per_meeting.monthly_fee_cents, catalog.currency)} monthly fee
                </p>

                <ul className="mt-5 space-y-2 text-sm">
                  {[
                    `Charged ${catalog.pay_per_meeting.grace_hours}h after booking — cancellations are free`,
                    "Billed once per prospect, even if they rebook",
                    ...featureList(catalog.pay_per_meeting.limits),
                  ].map((f) => (
                    <li key={f} className="flex items-start gap-2">
                      <Check size={16} className="mt-0.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                      {f}
                    </li>
                  ))}
                </ul>

                {checkoutLive ? (
                  <Button
                    variant="outline"
                    className="mt-6 w-full"
                    disabled={checkout.isPending}
                    onClick={() => choose("pay_per_meeting")}
                  >
                    Pay per meeting
                  </Button>
                ) : (
                  <CheckoutSoon />
                )}
              </article>
            </div>

            <ComparisonStrip
              meetings={meetings}
              setMeetings={setMeetings}
              tier={tier}
              perMeetingCents={catalog.pay_per_meeting.price_per_meeting_cents}
              monthlyFeeCents={catalog.pay_per_meeting.monthly_fee_cents}
              currency={catalog.currency}
            />
          </>
        )}
      </section>
      {footer}
    </main>
  );
}

/** Until Stripe checkout is live (product-facts.json), no trial, renewal or
 *  checkout promise: say checkout opens soon and offer a call instead. */
function CheckoutSoon() {
  return (
    <div className="mt-6 rounded-lg border border-border bg-muted/40 p-3 text-sm">
      <p>Online checkout opens soon. To start now, book a call.</p>
      <Link href="/contact" className="mt-3 inline-block w-full rounded-md bg-[rgb(var(--primary))] px-4 py-2 text-center font-medium text-primary-foreground">
        Book a call
      </Link>
    </div>
  );
}

function ComparisonStrip({ meetings, setMeetings, tier, perMeetingCents, monthlyFeeCents, currency }: {
  meetings: number;
  setMeetings: (n: number) => void;
  tier: MonthlyTier;
  perMeetingCents: number;
  monthlyFeeCents: number;
  currency: string;
}) {
  const result = compareOptions(meetings, tier,
    { price_per_meeting_cents: perMeetingCents, monthly_fee_cents: monthlyFeeCents });
  const breakEven = breakEvenMeetings(tier.price_cents, perMeetingCents);
  return (
    <section aria-label="Compare the two options" className="mt-8 rounded-xl border border-border bg-card p-6">
      <label htmlFor="meetings" className="text-sm font-medium">
        Meetings you expect to book per month: <span className="font-bold">{meetings}</span>
      </label>
      <input id="meetings" type="range" min={0} max={30} value={meetings}
             onChange={(e) => setMeetings(Number(e.target.value))} className="mt-2 w-full" />
      <div className="mt-4 grid gap-3 text-sm sm:grid-cols-3">
        <p>{tier.name} monthly: <strong>{formatPrice(result.monthly_cents, currency)}</strong></p>
        <p>Pay per meeting: <strong>{formatPrice(result.pay_per_meeting_cents, currency)}</strong></p>
        <p className="text-muted-foreground">
          {result.cheaper === "same"
            ? "Both cost the same."
            : `${result.cheaper === "monthly" ? tier.name + " monthly" : "Pay per meeting"} saves ${formatPrice(result.savings_cents, currency)}.`}
          {" "}Monthly wins from {breakEven} meetings.
        </p>
      </div>
    </section>
  );
}
