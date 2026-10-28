/**
 * app/contact/page.tsx — public marketing page served at /contact.
 *
 * Static server component. There is intentionally no form: no backend endpoint
 * exists to receive one, so contact is a Calendly link, a mailto link, and
 * LinkedIn. Does NOT use Shell.tsx (authenticated dashboard shell).
 */

import type { Metadata } from "next";
import Link from "next/link";
import { Footer } from "@/components/Footer";
import { CHECKOUT_LIVE, PRODUCT_FACTS, PRODUCT_FULL_NAME } from "@/lib/productFacts";
import { canonicalMeta } from "@/lib/seo/pageMeta";
import { JsonLd } from "@/components/seo/JsonLd";
import { pageGraph } from "@/lib/seo/structuredData";

export const metadata: Metadata = {
  title: "Book a call about Vectorae",
  description:
    `Talk to Rehan directly about ${PRODUCT_FULL_NAME}. If it is a fit, we will tell you honestly. If it is not, we will tell you that too.`,
  ...canonicalMeta("/contact/"),
};

const CALENDLY_URL = "https://calendly.com/rmasood112224/30min";
const EMAIL = "ahmadshahid@calendarharvest.com";
const LINKEDIN_URL = "https://www.linkedin.com/company/leadpilot-app";

const WHATSAPP = [
  { label: "+92 342 696 7730 (Pakistan)", href: "https://wa.me/923426967730" },
  { label: "+1 646 756 0056 (US)", href: "https://wa.me/16467560056" },
];

const FOR_YOU = [
  // Who it is for: product-facts.json, the same list as the home page and /about.
  `You are in one of the groups Vectorae is built for: ${PRODUCT_FACTS.fit.map((a) => a.name).join(", ")}.`,
  "You are doing all sales personally and your pipeline stops every time a client project gets heavy.",
  "You have tried cold email or LinkedIn outreach and it has not produced consistent results.",
  "You are open to a done-with-you system rather than a tool you configure yourself.",
  "You want a pipeline that runs without you in it.",
];

const NOT_FOR_YOU = [
  "You have a dedicated SDR team and a working outbound system.",
  // Where it is offered: Terms of Service ("currently available only to clients based in ...").
  "You are based outside the countries Vectorae is currently offered in: the United States, the United Kingdom, Canada, Australia, New Zealand, Ireland and Singapore (see the Terms of Service).",
  // Not a fit: product-facts.json, the same list as the home page.
  ...PRODUCT_FACTS.not_fit,
  "You need clients in the next 7 days. Research comes first and new mailboxes warm up gradually, so this is not a quick fix.",
];

const FAQ = [
  {
    q: "How long does it take to see results?",
    // No timeline is promised: none is measured. What is code: research runs
    // before outreach, and a new mailbox ramps 10 sends on day one, +10 a day
    // (app/config.py gmail_warmup_start_sends / gmail_warmup_daily_increment).
    a: "We do not promise a timeline; it depends on your market and offer. Research runs before any outreach, and a newly connected mailbox starts at 10 emails a day and rises by 10 a day by default, so volume builds over the first weeks.",
  },
  {
    // Code truth (platform-gaps item 8): prices are public on /pricing, read
    // from GET /billing/catalog (app/core/billing_catalog.py).
    q: "What does Vectorae cost?",
    a: CHECKOUT_LIVE
      ? "Every plan and price is published on the Pricing page: monthly plans with a free trial, and a pay-per-booked-meeting plan. On the call we can help you choose between them."
      : "Every plan and price is published on the Pricing page: monthly plans and a pay-per-booked-meeting plan. Online checkout opens soon; until then, book a call to start, and we can help you choose a plan.",
  },
  {
    // Code truth: email is the default channel (app/api/sequences.py
    // SequenceCreate.channel = EMAIL); LinkedIn is a plan channel on Growth,
    // Scale, Enterprise and Pay per meeting (app/core/plans.py
    // PLANS[...]["channels"]; legacy Pro has it too), not Free or Starter.
    q: "Do I need to be on LinkedIn?",
    a: "No. Email is the default channel, sent from the Gmail account you connect. LinkedIn steps are included in the Growth, Scale, Enterprise and Pay per meeting plans, but they are built and tested only against simulated services, and are not live yet.",
  },
];

export default function ContactPage() {
  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <JsonLd data={pageGraph({ path: "/contact/", breadcrumb: "Contact", faq: FAQ })} />
      <SiteNav />

      <main>
        {/* 1. Hero */}
        <section className="px-6 py-24">
          <div className="mx-auto max-w-4xl">
            <h1 className="text-4xl font-bold leading-tight tracking-tight sm:text-5xl">
              Talk to Rehan directly.
            </h1>
            <p className="mt-6 max-w-3xl text-lg leading-relaxed text-gray-300">
              No sales team. No SDR handoff. If {PRODUCT_FULL_NAME} is a fit for your business,
              we will tell you honestly. If it is not, we will tell you that too.
            </p>
          </div>
        </section>

        {/* 2. Contact options */}
        <section className="border-t border-white/10 px-6 py-24">
          <div className="mx-auto grid max-w-6xl gap-12 md:grid-cols-2">
            <div className="rounded-md border border-white/10 p-8">
              <h2 className="text-2xl font-bold">Book a call</h2>
              <p className="mt-4 leading-relaxed text-gray-300">
                30 minutes. We will look at your current pipeline, understand what you have tried, and tell
                you whether Vectorae makes sense for your situation.
              </p>
              <a
                href={CALENDLY_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-8 inline-block rounded-md bg-blue-600 px-6 py-3 font-semibold text-white hover:bg-blue-700"
              >
                Book on Calendly
              </a>
            </div>

            <div className="rounded-md border border-white/10 p-8">
              <h2 className="text-2xl font-bold">Get in touch</h2>
              <dl className="mt-6 space-y-6">
                <div>
                  <dt className="text-sm font-medium uppercase tracking-wider text-gray-400">Email</dt>
                  <dd className="mt-1">
                    <a href={`mailto:${EMAIL}`} className="break-all text-lg font-medium text-blue-500 hover:text-blue-400">
                      {EMAIL}
                    </a>
                  </dd>
                </div>
                <div>
                  <dt className="text-sm font-medium uppercase tracking-wider text-gray-400">LinkedIn</dt>
                  <dd className="mt-1">
                    <a
                      href={LINKEDIN_URL}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-lg font-medium text-blue-500 hover:text-blue-400"
                    >
                      Vectorae on LinkedIn
                    </a>
                  </dd>
                </div>
                <div>
                  <dt className="text-sm font-medium uppercase tracking-wider text-gray-400">WhatsApp</dt>
                  {WHATSAPP.map((w) => (
                    <dd key={w.href} className="mt-1">
                      <a
                        href={w.href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-lg font-medium text-blue-500 hover:text-blue-400"
                      >
                        {w.label}
                      </a>
                    </dd>
                  ))}
                </div>
              </dl>
            </div>
          </div>
        </section>

        {/* 3 + 4. Fit */}
        <section className="border-t border-white/10 px-6 py-24">
          <div className="mx-auto grid max-w-6xl gap-16 md:grid-cols-2">
            <div>
              <h2 className="text-2xl font-bold sm:text-3xl">This call is for you if:</h2>
              <ul className="mt-8 space-y-5">
                {FOR_YOU.map((item) => (
                  <li key={item} className="border-l-2 border-blue-600 pl-5 leading-relaxed text-gray-300">
                    {item}
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h2 className="text-2xl font-bold sm:text-3xl">This call is not for you if:</h2>
              <ul className="mt-8 space-y-5">
                {NOT_FOR_YOU.map((item) => (
                  <li key={item} className="border-l-2 border-gray-600 pl-5 leading-relaxed text-gray-300">
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        {/* 5. FAQ (static) */}
        <section className="border-t border-white/10 px-6 py-24">
          <div className="mx-auto max-w-4xl">
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">Common questions</h2>
            <dl className="mt-10 divide-y divide-white/10">
              {FAQ.map((item) => (
                <div key={item.q} className="py-8 first:pt-0">
                  <dt className="text-lg font-semibold">{item.q}</dt>
                  <dd className="mt-3 leading-relaxed text-gray-300">{item.a}</dd>
                </div>
              ))}
            </dl>
          </div>
        </section>
      </main>

      <Footer />
    </div>
  );
}

function SiteNav() {
  return (
    <header className="border-b border-white/10 px-6 py-5">
      <nav className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-8 gap-y-3">
        <Link href="/" className="text-lg font-bold text-white">
          Vectorae
        </Link>
        <ul className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-gray-300">
          <li><Link href="/how-it-works" className="hover:text-white">How it Works</Link></li>
          <li><Link href="/about" className="hover:text-white">About</Link></li>
          <li><Link href="/pricing" className="hover:text-white">Pricing</Link></li>
          <li><Link href="/contact" aria-current="page" className="text-white">Contact</Link></li>
        </ul>
      </nav>
    </header>
  );
}
