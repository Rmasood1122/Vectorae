/**
 * app/how-it-works/page.tsx — public marketing page served at /how-it-works.
 *
 * Static server component: no API calls, no auth. Does NOT use Shell.tsx
 * (authenticated dashboard shell).
 */

import type { Metadata } from "next";
import Link from "next/link";
import { Footer } from "@/components/Footer";
import { PRODUCT_FULL_NAME } from "@/lib/productFacts";
import { canonicalMeta } from "@/lib/seo/pageMeta";
import { JsonLd } from "@/components/seo/JsonLd";
import { pageGraph } from "@/lib/seo/structuredData";

export const metadata: Metadata = {
  title: "How Vectorae works",
  description:
    `How ${PRODUCT_FULL_NAME} researches your market, finds and verifies business contacts, writes each email for one person, sends after your daily approval, and routes replies to you.`,
  ...canonicalMeta("/how-it-works/"),
};

const STEPS = [
  {
    number: "01",
    title: "Research",
    body: "Research runs once per campaign strategy: 72 research steps, or 144 if you have no past clients yet, covering your ideal client profile, market, competitors, channels, and messaging. Sourced email addresses go through a verification check, and undeliverable ones are dropped. Right before an email is written, Vectorae can pull company news so the first line references something real, and the AI is instructed never to invent a detail. Using a prospect's recent LinkedIn posts goes through the LinkedIn connection, which is not live yet.",
  },
  {
    number: "02",
    title: "Outreach",
    body: "Personalised emails, written in your voice when you add writing samples. Sequences send automatically from the Gmail account you connect once you approve that day's automatic sending, only during permitted sending hours in the prospect's own time zone and within daily sending limits. WhatsApp steps are available on the plans that include WhatsApp. LinkedIn steps are built and tested only against simulated services, and are not live yet. Opt-outs are re-checked immediately before every send. By default, messages to senior decision-makers, to prospects whose last reply was an objection, or whose copy trips our tone checks are held for your approval before they go.",
  },
  {
    number: "03",
    title: "Replies and meetings",
    body: "Email and WhatsApp replies land in one inbox. Each reply is classified and the prospect's sequence stops. Vectorae suggests a next step and a draft, but you write every answer. Prospects book on your own Vectorae booking page, and a booking pauses their sequence.",
  },
];

export default function HowItWorksPage() {
  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <JsonLd data={pageGraph({ path: "/how-it-works/", breadcrumb: "How it Works" })} />
      <SiteNav />

      <main>
        {/* 1. Hero */}
        <section className="px-6 py-24">
          <div className="mx-auto max-w-4xl">
            <p className="text-sm font-medium uppercase tracking-wider text-blue-500">How it works</p>
            <h1 className="mt-4 text-4xl font-bold leading-tight tracking-tight sm:text-5xl">
              Your pipeline. Running without you.
            </h1>
            <p className="mt-6 max-w-3xl text-lg leading-relaxed text-gray-300">
              {PRODUCT_FULL_NAME} does the research, finds and checks the prospects, writes and
              sends the outreach after your daily approval, and follows up. You answer the replies and take
              the meetings.
            </p>
          </div>
        </section>

        {/* 2. Process */}
        <section className="border-t border-white/10 px-6 py-24">
          <ol className="mx-auto max-w-4xl space-y-16">
            {STEPS.map((step) => (
              <li key={step.number} className="grid gap-4 md:grid-cols-[6rem_1fr]">
                <span className="text-4xl font-bold text-blue-500">{step.number}</span>
                <div>
                  <h2 className="text-2xl font-bold tracking-tight sm:text-3xl">{step.title}</h2>
                  <p className="mt-4 text-lg leading-relaxed text-gray-300">{step.body}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        {/* 3. What you do */}
        <section className="border-t border-white/10 px-6 py-24">
          <div className="mx-auto max-w-4xl">
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">What you still do.</h2>
            <p className="mt-6 text-lg leading-relaxed text-gray-300">
              You review the strategy and the leads before outreach launches, approve sending once a day,
              approve any message held for review, write every answer to a prospect, and take the calls
              prospects book. Finding, checking, writing and sending in between, Vectorae handles.
            </p>
          </div>
        </section>

        {/* 4. CTA */}
        <section className="border-t border-white/10 px-6 py-24">
          <div className="mx-auto flex max-w-4xl flex-wrap items-center gap-6">
            <Link
              href="/contact"
              className="rounded-md bg-blue-600 px-6 py-3 font-semibold text-white hover:bg-blue-700"
            >
              Book a call
            </Link>
            <Link href="/about" className="font-medium text-blue-500 hover:text-blue-400">
              Read about us
            </Link>
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
          <li><Link href="/how-it-works" aria-current="page" className="text-white">How it Works</Link></li>
          <li><Link href="/about" className="hover:text-white">About</Link></li>
          <li><Link href="/pricing" className="hover:text-white">Pricing</Link></li>
          <li><Link href="/contact" className="hover:text-white">Contact</Link></li>
        </ul>
      </nav>
    </header>
  );
}
