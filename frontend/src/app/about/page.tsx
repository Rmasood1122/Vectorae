/**
 * app/about/page.tsx — public marketing page served at /about.
 *
 * Static server component: no API calls, no auth. It deliberately does NOT use
 * components/shell/Shell.tsx — that is the authenticated dashboard shell and
 * redirects anyone without a session to /login.
 */

import type { Metadata } from "next";
import Link from "next/link";
import { Footer } from "@/components/Footer";
import { PRODUCT_FACTS, PRODUCT_FULL_NAME } from "@/lib/productFacts";
import { canonicalMeta } from "@/lib/seo/pageMeta";
import { JsonLd } from "@/components/seo/JsonLd";
import { pageGraph } from "@/lib/seo/structuredData";

export const metadata: Metadata = {
  title: "About Vectorae",
  description:
    `${PRODUCT_FULL_NAME} was built by a founder who used it to find his own clients. Here is the story.`,
  ...canonicalMeta("/about/"),
};

const STEPS = [
  {
    title: "Research",
    body: "Research runs once per campaign strategy before a single message is written: 72 research steps, or 144 if you have no past clients yet, covering your ideal client profile, market, competitors, channels, and messaging. Sourced email addresses are checked for deliverability, and right before an email is written Vectorae can pull company news so the opening references something real. The AI is instructed never to invent a detail.",
  },
  {
    title: "Outreach",
    body: "Personalised email sequences send automatically from the Gmail account you connect once you approve that day's automatic sending, within permitted sending hours and daily per-account limits. LinkedIn steps are built and tested only against simulated services, and are not live yet. By default, high-risk messages, such as those to senior executives or to prospects who last replied with an objection, are held for your approval before they send.",
  },
  {
    title: "Replies and meetings",
    body: "Replies come to you, classified, and a reply stops the prospect's sequence. You write every answer. Prospects book on your own Vectorae booking page.",
  },
];

/** Who it is for: the same list as the home page (src/content/product-facts.json). */
const AUDIENCE = PRODUCT_FACTS.fit.map((a) => ({ title: a.name, body: a.body }));
const FOUNDER = PRODUCT_FACTS.founders[0];
/** Every founder in product-facts.json, each with the title recorded there. */
const FOUNDERS = PRODUCT_FACTS.founders;

export default function AboutPage() {
  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <JsonLd data={pageGraph({ path: "/about/", breadcrumb: "About", founders: true })} />
      <SiteNav />

      <main>
        {/* 1. Hero */}
        <section className="px-6 py-24">
          <div className="mx-auto max-w-4xl">
            <p className="text-sm font-medium uppercase tracking-wider text-blue-500">About {PRODUCT_FULL_NAME}</p>
            <h1 className="mt-4 text-4xl font-bold leading-tight tracking-tight sm:text-5xl">
              Built by a founder who used it to find his own clients.
            </h1>
            <p className="mt-6 max-w-3xl text-lg leading-relaxed text-gray-300">
              Rehan Masood is an AI engineer. He could build delivery systems for clients, but every
              time he went deep on delivery, his pipeline went quiet. So he built the system he wished
              existed: {PRODUCT_FULL_NAME}.
            </p>
            <p className="mt-4 max-w-3xl text-base leading-relaxed text-gray-400">
              {PRODUCT_FULL_NAME} was formerly called LeadPilot.
            </p>
          </div>
        </section>

        {/* 2. The problem */}
        <section className="border-t border-white/10 px-6 py-24">
          <div className="mx-auto max-w-4xl">
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
              The pipeline runs when you push it. It stops when you stop.
            </h2>
            <p className="mt-6 text-lg leading-relaxed text-gray-300">
              Most agency founders are excellent at delivery, and that is exactly what traps them in a
              feast-famine cycle. When a client project gets heavy, prospecting is the first thing to go,
              and two months later there is nothing in the pipeline. Nobody can prospect well and deliver
              well at the same time, however disciplined they are. This is not a discipline problem. It is
              a structural problem, and it needs a structural fix.
            </p>
          </div>
        </section>

        {/* 3. How it works */}
        <section className="border-t border-white/10 px-6 py-24">
          <div className="mx-auto max-w-6xl">
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">How Vectorae works</h2>
            <ol className="mt-12 grid gap-10 md:grid-cols-3">
              {STEPS.map((step, i) => (
                <li key={step.title} className="border-t border-white/10 pt-6">
                  <span className="text-4xl font-bold text-blue-500">{i + 1}</span>
                  <h3 className="mt-4 text-xl font-semibold">{step.title}</h3>
                  <p className="mt-3 leading-relaxed text-gray-300">{step.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* 4. Who we serve */}
        <section className="border-t border-white/10 px-6 py-24">
          <div className="mx-auto max-w-4xl">
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">Who we serve</h2>
            <ul className="mt-10 space-y-8">
              {AUDIENCE.map((a) => (
                <li key={a.title} className="border-l-2 border-blue-600 pl-5">
                  <h3 className="text-lg font-semibold">{a.title}</h3>
                  <p className="mt-2 leading-relaxed text-gray-300">{a.body}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        {/* 5. Founders */}
        <section className="border-t border-white/10 px-6 py-24">
          <div className="mx-auto max-w-4xl">
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">Founders</h2>
            <ul className="mt-8 space-y-2 text-lg text-gray-300" data-testid="founder-list">
              {FOUNDERS.map((f) => (
                <li key={f.name}>
                  <span className="font-semibold text-white">{f.name}</span>, {f.job_title}
                </li>
              ))}
            </ul>
            <h3 className="mt-12 text-xl font-semibold">Rehan Masood</h3>
            <p className="mt-4 text-lg leading-relaxed text-gray-300">
              Rehan is an AI engineer based in {FOUNDER.home_location}, working with clients around the
              world. He builds AI-powered automation and agent systems. His strongest conviction is
              simple: a founder who is excellent at delivery should never have to choose between doing the
              work and finding the next client. Vectorae is his answer to that problem.
            </p>
          </div>
        </section>

        {/* 6. CTA strip */}
        <section className="border-t border-white/10 px-6 py-24">
          <div className="mx-auto flex max-w-4xl flex-col items-start gap-8 md:flex-row md:items-center md:justify-between">
            <h2 className="text-2xl font-bold tracking-tight sm:text-3xl">
              Ready to remove yourself from your own pipeline?
            </h2>
            <div className="flex flex-wrap items-center gap-6">
              <Link
                href="/contact"
                className="rounded-md bg-blue-600 px-6 py-3 font-semibold text-white hover:bg-blue-700"
              >
                Book a call
              </Link>
              <Link href="/how-it-works" className="font-medium text-blue-500 hover:text-blue-400">
                See how it works
              </Link>
            </div>
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
          <li><Link href="/about" aria-current="page" className="text-white">About</Link></li>
          <li><Link href="/pricing" className="hover:text-white">Pricing</Link></li>
          <li><Link href="/contact" className="hover:text-white">Contact</Link></li>
        </ul>
      </nav>
    </header>
  );
}
