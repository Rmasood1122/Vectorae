/**
 * The public home page body (rendered by src/app/page.tsx inside HomeGate).
 * Server component; the interactive islands are HomeHeader, HomeVideo,
 * StageStepper and Reveal. Every sentence comes from ./content.ts, where the
 * claims are tested against the code (src/tests/homeClaims.test.ts).
 *
 * Palette (fixed, like the other public pages; not the in-app theme):
 *   ink #0f172a, paper #f8fafc, signal blue #1d4ed8, teal #0e7490,
 *   harvest #f2b544 (booked meetings, the active stage, the brand mark).
 */

import Link from "next/link";
import { CalendarCheck, Check, MailX, ShieldBan, Trash2, X } from "lucide-react";

import { Footer } from "@/components/Footer";
import { PRODUCT_FULL_NAME } from "@/lib/productFacts";
import {
  AUDIENCES,
  CLOSING,
  CONTACT_PATH,
  FAQ,
  FEATURES,
  HERO,
  HOW_IT_WORKS_ID,
  NOT_A_FIT,
  NOT_LIVE_TITLE,
  UNCONFIRMED_TITLE,
  PRICING_PATH,
  PRODUCT_DESCRIPTION,
  PRICING_TEASER,
  SIGNUP_PATH,
  TRUST,
  TRUST_NOTE,
} from "./content";
import { HomeHeader } from "./HomeHeader";
import { HomeVideo } from "./HomeVideo";
import { Reveal } from "./Reveal";
import { StageStepper } from "./StageStepper";

const TRUST_ICONS = [MailX, ShieldBan, CalendarCheck, Trash2];

export function HomePage() {
  const available = FEATURES.filter((f) => f.status === "available");
  const onRequest = FEATURES.filter((f) => f.status === "on_request");
  const notLive = FEATURES.filter((f) => f.status === "not_live");
  const unconfirmed = FEATURES.filter((f) => f.status === "unconfirmed");

  return (
    <div className="lp-home min-h-screen bg-[#f8fafc] text-[#0f172a]">
      <a href="#main" className="lp-skip">
        Skip to content
      </a>
      <HomeHeader />

      <main id="main">
        {/* Hero */}
        <section aria-labelledby="hero-title" className="relative overflow-hidden bg-[#0f172a] text-white">
          <div aria-hidden className="lp-grid absolute inset-0" />
          <div className="relative mx-auto grid max-w-6xl gap-12 px-4 pb-20 pt-12 sm:px-6 sm:pt-16 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:items-center lg:gap-14 lg:pb-24 lg:pt-20">
            <div className="lp-intro">
              <p className="mb-4 text-sm font-semibold uppercase tracking-wider text-[#94a3b8]">{PRODUCT_FULL_NAME}</p>
              <h1
                id="hero-title"
                className="max-w-[17ch] text-[2.6rem] font-extrabold leading-[1.02] tracking-[-0.035em] sm:text-6xl lg:text-[3.5rem] xl:text-[3.85rem]"
              >
                {HERO.headline}
              </h1>
              <p className="mt-6 max-w-[54ch] text-[1.0625rem] leading-relaxed text-[#cbd5e1] sm:text-lg">{HERO.body}</p>
              <div className="mt-8 flex flex-wrap items-center gap-3">
                <Link href={SIGNUP_PATH} className="lp-btn lp-btn-primary lp-btn-lg" data-testid="hero-start">
                  {HERO.primary}
                </Link>
                <a href={`#${HOW_IT_WORKS_ID}`} className="lp-btn lp-btn-ghost lp-btn-lg">
                  {HERO.secondary}
                </a>
              </div>
              <p className="mt-4 text-sm text-[#94a3b8]">{HERO.note}</p>
            </div>
            <div className="lp-intro lp-intro-late">
              <HomeVideo />
            </div>
          </div>
        </section>

        {/* How it works */}
        <section
          id={HOW_IT_WORKS_ID}
          aria-labelledby="how-title"
          className="scroll-mt-16 px-4 py-20 sm:px-6 lg:py-28"
        >
          <div className="mx-auto max-w-6xl">
            <div className="max-w-2xl">
              <h2 id="how-title" className="lp-h2">
                Six stages, one pipeline
              </h2>
              <p className="mt-4 text-[1.0625rem] leading-relaxed text-[#475569]">
                Vectorae runs on a server, so sequences keep sending and follow-ups keep firing when your computer
                is off. Pick a stage to see what happens there and what stays your call.
              </p>
            </div>
            <Reveal className="mt-12">
              <StageStepper />
            </Reveal>
          </div>
        </section>

        {/* Features */}
        <section aria-labelledby="features-title" className="border-y border-[#e2e8f0] bg-white px-4 py-20 sm:px-6 lg:py-24">
          <div className="mx-auto max-w-6xl">
            <h2 id="features-title" className="lp-h2 max-w-2xl">
              What is in the box today
            </h2>
            <Reveal>
              <ul className="mt-12 grid gap-x-10 gap-y-9 sm:grid-cols-2 lg:grid-cols-3" data-testid="features-available">
                {available.map((f) => (
                  <li key={f.name} className="border-t-2 border-[#0f172a] pt-4">
                    <h3 className="text-[1.0625rem] font-semibold tracking-tight">{f.name}</h3>
                    <p className="mt-1.5 text-[0.95rem] leading-relaxed text-[#475569]">{f.body}</p>
                    {f.plan && <p className="mt-2 text-sm font-medium text-[#0e7490]">{f.plan}</p>}
                  </li>
                ))}
              </ul>
            </Reveal>
            <div className="mt-16 rounded-2xl bg-[#f1f5f9] p-6 sm:p-8">
              <h3 className="text-lg font-semibold tracking-tight">Built, switched off by default, available on request</h3>
              <p className="mt-2 max-w-[70ch] text-[0.95rem] text-[#475569]">
                These are in the product but turned off until an administrator switches them on. Ask us if you need
                one.
              </p>
              <ul className="mt-6 grid gap-x-10 gap-y-4 sm:grid-cols-2 lg:grid-cols-3" data-testid="features-on-request">
                {onRequest.map((f) => (
                  <li key={f.name} className="text-[0.95rem]">
                    <span className="font-semibold">{f.name}.</span>{" "}
                    <span className="text-[#475569]">{f.body}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="mt-6 rounded-2xl border border-[#cbd5e1] p-6 sm:p-8">
              <h3 className="text-lg font-semibold tracking-tight">{UNCONFIRMED_TITLE}</h3>
              <ul className="mt-6 grid gap-x-10 gap-y-4 sm:grid-cols-2 lg:grid-cols-3" data-testid="features-unconfirmed">
                {unconfirmed.map((f) => (
                  <li key={f.name} className="text-[0.95rem]">
                    <span className="font-semibold">{f.name}.</span>{" "}
                    <span className="text-[#475569]">{f.body}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="mt-6 rounded-2xl border border-[#cbd5e1] p-6 sm:p-8">
              <h3 className="text-lg font-semibold tracking-tight">{NOT_LIVE_TITLE}</h3>
              <ul className="mt-6 grid gap-x-10 gap-y-4 sm:grid-cols-2 lg:grid-cols-3" data-testid="features-not-live">
                {notLive.map((f) => (
                  <li key={f.name} className="text-[0.95rem]">
                    <span className="font-semibold">{f.name}.</span>{" "}
                    <span className="text-[#475569]">{f.body}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        {/* Who it's for */}
        <section aria-labelledby="who-title" className="px-4 py-20 sm:px-6 lg:py-28">
          <div className="mx-auto max-w-6xl">
            <h2 id="who-title" className="lp-h2 max-w-3xl">
              For businesses that sell to other businesses
            </h2>
            <p className="mt-4 max-w-3xl text-[1.0625rem] leading-relaxed text-[#475569]">{PRODUCT_DESCRIPTION}</p>
            <Reveal className="mt-12 grid gap-10 lg:grid-cols-[2fr_1fr] lg:gap-14">
              <ul className="grid gap-8 sm:grid-cols-3 sm:gap-6">
                {AUDIENCES.map((a) => (
                  <li key={a.name}>
                    <span aria-hidden className="block h-1.5 w-10 rounded-full bg-[#f2b544]" />
                    <h3 className="mt-4 text-xl font-bold tracking-tight">{a.name}</h3>
                    <p className="mt-2 text-[0.95rem] leading-relaxed text-[#475569]">{a.body}</p>
                  </li>
                ))}
              </ul>
              <div className="rounded-2xl border border-[#cbd5e1] p-6">
                <h3 className="text-lg font-semibold tracking-tight">Not a fit if you are</h3>
                <ul className="mt-4 space-y-3">
                  {NOT_A_FIT.map((n) => (
                    <li key={n} className="flex gap-2.5 text-[0.95rem] leading-relaxed text-[#334155]">
                      <X aria-hidden className="mt-1 h-4 w-4 shrink-0 text-[#b91c1c]" strokeWidth={3} />
                      {n}
                    </li>
                  ))}
                </ul>
              </div>
            </Reveal>
          </div>
        </section>

        {/* Trust and compliance */}
        <section aria-labelledby="trust-title" className="bg-[#0f172a] px-4 py-20 text-white sm:px-6 lg:py-24">
          <div className="mx-auto max-w-6xl">
            <h2 id="trust-title" className="lp-h2 max-w-3xl text-white">
              Compliance is part of the send path
            </h2>
            <ul className="mt-12 grid gap-10 sm:grid-cols-2 lg:grid-cols-4 lg:gap-8">
              {TRUST.map((t, i) => {
                const Icon = TRUST_ICONS[i];
                return (
                  <li key={t.name}>
                    <Icon aria-hidden className="h-6 w-6 text-[#f2b544]" strokeWidth={2} />
                    <h3 className="mt-4 text-lg font-semibold tracking-tight">{t.name}</h3>
                    <p className="mt-2 text-[0.95rem] leading-relaxed text-[#cbd5e1]">{t.body}</p>
                  </li>
                );
              })}
            </ul>
            <p className="mt-12 max-w-[75ch] border-t border-white/10 pt-6 text-sm text-[#94a3b8]">
              {TRUST_NOTE}{" "}
              <Link href="/privacy" className="text-[#e2e8f0] underline underline-offset-4 hover:text-white">
                Privacy Policy
              </Link>
            </p>
          </div>
        </section>

        {/* Pricing teaser */}
        <section aria-labelledby="pricing-title" className="px-4 pt-20 sm:px-6 lg:pt-28">
          <div className="mx-auto flex max-w-6xl flex-col gap-6 rounded-2xl bg-white p-8 ring-1 ring-[#e2e8f0] sm:p-10 lg:flex-row lg:items-center lg:justify-between">
            <div className="max-w-2xl">
              <h2 id="pricing-title" className="text-2xl font-bold tracking-tight sm:text-3xl">
                {PRICING_TEASER.title}
              </h2>
              <p className="mt-3 text-[1.0625rem] leading-relaxed text-[#475569]">{PRICING_TEASER.body}</p>
            </div>
            <Link href={PRICING_PATH} className="lp-btn lp-btn-dark lp-btn-lg shrink-0 self-start lg:self-center">
              {PRICING_TEASER.cta}
            </Link>
          </div>
        </section>

        {/* FAQ */}
        <section aria-labelledby="faq-title" className="px-4 py-20 sm:px-6 lg:py-28">
          <div className="mx-auto grid max-w-6xl gap-10 lg:grid-cols-[1fr_2fr]">
            <div>
              <h2 id="faq-title" className="lp-h2">
                Questions
              </h2>
              <p className="mt-4 text-[0.95rem] text-[#475569]">
                More in the{" "}
                <Link href="/how-it-works" className="font-medium text-[#1d4ed8] underline underline-offset-4">
                  full walkthrough
                </Link>{" "}
                or{" "}
                <Link href={CONTACT_PATH} className="font-medium text-[#1d4ed8] underline underline-offset-4">
                  ask us
                </Link>
                .
              </p>
            </div>
            <div className="divide-y divide-[#e2e8f0] border-y border-[#e2e8f0]" data-testid="faq">
              {FAQ.map((f) => (
                <details key={f.q} className="lp-faq group">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-6 py-5 text-[1.0625rem] font-semibold tracking-tight">
                    {f.q}
                    <span aria-hidden className="lp-faq-mark" />
                  </summary>
                  <p className="max-w-[68ch] pb-6 text-[0.975rem] leading-relaxed text-[#475569]">{f.a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        {/* Closing call to action */}
        <section aria-labelledby="closing-title" className="relative overflow-hidden bg-[#0f172a] px-4 py-20 text-white sm:px-6 lg:py-24">
          <div aria-hidden className="lp-grid absolute inset-0" />
          <div className="relative mx-auto max-w-6xl">
            <h2 id="closing-title" className="text-4xl font-extrabold tracking-[-0.03em] sm:text-5xl">
              {CLOSING.title}
            </h2>
            <p className="mt-4 max-w-[52ch] text-lg text-[#cbd5e1]">{CLOSING.body}</p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href={SIGNUP_PATH} className="lp-btn lp-btn-primary lp-btn-lg">
                {CLOSING.primary}
              </Link>
              <Link href={CONTACT_PATH} className="lp-btn lp-btn-ghost lp-btn-lg">
                {CLOSING.secondary}
              </Link>
            </div>
            <ul className="mt-10 flex flex-wrap gap-x-6 gap-y-2 text-sm text-[#94a3b8]">
              {CLOSING.points.map((x) => (
                <li key={x} className="flex items-center gap-2">
                  <Check aria-hidden className="h-4 w-4 text-[#f2b544]" strokeWidth={3} />
                  {x}
                </li>
              ))}
            </ul>
          </div>
        </section>
      </main>

      <div className="bg-[#0f172a] text-white">
        <Footer />
      </div>
    </div>
  );
}
