/**
 * app/terms/page.tsx — Terms of Service, served at /terms.
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
  title: "Terms of Service for Victorae | Calendarharvest",
  description: `The terms that govern your use of ${PRODUCT_FULL_NAME}.`,
  robots: "index, follow",
  ...canonicalMeta("/terms/"),
};

const EMAIL = "ahmadshahid@calendarharvest.com";

// DRAFT for legal review (founders, 2026-10-14). Open questions for counsel, not rendered:
//  - No legal entity yet: "Calendarharvest" is a trading name. Who is the contracting party?
//  - Governing law England and Wales, arbitration in London, while the founder is in
//    Astoria, New York: confirm the choice of law and forum.
//  - No postal address for notices.
export default function TermsPage() {
  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <JsonLd data={pageGraph({ path: "/terms/", breadcrumb: "Terms of Service" })} />
      <SiteNav />

      <main className="px-6 py-24">
        <article className="mx-auto max-w-3xl">
          <h1 className="text-4xl font-bold tracking-tight">Terms of Service</h1>
          <p className="mt-4 text-sm text-gray-400">Effective date: September 13, 2026</p>

          <Section title="1. Acceptance of terms">
            <p>
              These Terms of Service (&quot;Terms&quot;) form a binding agreement between you and
              Calendarharvest, which operates Vectorae Enterprise (&quot;Vectorae&quot;, &quot;we&quot;,
              &quot;us&quot;, or &quot;our&quot;). By signing an engagement with us, creating an account, or
              otherwise using Vectorae, you agree to these Terms. If you are accepting on behalf of a
              business, you confirm that you have authority to bind that business, and &quot;you&quot;
              refers to it. If you do not agree, do not use the service.
            </p>
          </Section>

          <Section title="2. Description of service">
            <p>
              {/* DRAFT for legal review (founders, 2026-10-14): LinkedIn sending is not live yet;
                  email (Gmail) is the main channel, with WhatsApp on plans that include it. */}
              Vectorae Enterprise is a done-with-you outbound pipeline system. We research prospects
              who match your ideal client profile, write personalised outreach, and manage email sequences
              (and WhatsApp messages, on plans that include WhatsApp) on your behalf. These sequences are
              sent automatically by our software, after you approve each day&apos;s sending, through the
              accounts you connect. LinkedIn steps are not yet available. The specific scope, volumes,
              and deliverables for your account are set out in your client engagement, which forms part of
              these Terms. Where the engagement and these Terms conflict, the engagement prevails for that
              point only.
            </p>
          </Section>

          <Section title="3. Eligibility">
            <p>
              To use Vectorae you must be at least 18 years old and legally able to enter into contracts.
              The service is offered only to business entities and sole traders; it is not a consumer
              service. Vectorae is currently available only to clients based in the United States, the
              United Kingdom, Canada, Australia, New Zealand, Ireland, and Singapore. We may decline or end
              an engagement where these conditions are not met.
            </p>
          </Section>

          <Section title="4. Client responsibilities">
            <p>
              You agree to give us accurate, complete information about your business, your offer, and your
              ideal client profile, and to tell us promptly when any of it changes. You must keep the email
              account you connect for sending active and in good standing for the duration of your
              engagement.
            </p>
            <p>
              You must not use Vectorae for illegal outreach, spam, or any communication you would not be
              entitled to send yourself. You remain responsible for complying with the laws that apply to
              your outreach, including the CAN-SPAM Act, the GDPR and UK GDPR, Canada&apos;s Anti-Spam
              Legislation (CASL), and any other relevant local laws in the places where your prospects are
              located.
            </p>
          </Section>

          <Section title="5. Our responsibilities">
            <p>
              We will deliver the research and outreach sequences agreed in your engagement with reasonable
              skill and care. LinkedIn connection requests and messages are sent automatically through the
              LinkedIn accounts you connect, and we operate within per-account daily activity limits designed
              to reduce the risk of restrictions on your account. We will keep
              your business information and client data confidential and use it only to deliver the service.
              We will provide regular campaign performance reporting so you can see what is being sent and
              how prospects are responding.
            </p>
          </Section>

          <Section title="6. Prohibited uses">
            <p>
              Vectorae may not be used to run outreach in or targeting the healthcare, legal, government,
              non-profit, or real estate sectors, or in any other industry excluded under our ideal client
              profile policy as communicated to you. You may not ask us to send outreach that is deceptive
              or misleading, that impersonates another person or business, or that makes claims you cannot
              substantiate. You may not ask us to contact any individual who has opted out of communication
              from you or from us, and we will honour every opt-out we receive.
            </p>
          </Section>

          <Section title="7. Payment terms">
            <p>
              Fees are as agreed in your client engagement and are processed through Stripe. Unless your
              engagement states otherwise, subscription fees are billed in advance for each billing period.
              Because research and outreach work begins as soon as a campaign launches, fees are
              non-refundable once your campaign has launched. You may cancel a subscription by giving us 30
              days&apos; written notice; you will remain responsible for fees covering that notice period.
            </p>
          </Section>

          <Section title="8. Intellectual property">
            <p>
              You own your prospect lists, your business data, and the materials you provide to us. You
              grant us a limited licence to use them solely to deliver the service to you. Calendarharvest
              retains all rights in the Vectorae system, its research methodology, its software, and all
              underlying technology, including improvements made while delivering your engagement. Nothing
              in these Terms transfers ownership of that technology to you.
            </p>
          </Section>

          <Section title="9. Limitation of liability">
            <p>
              Outbound results depend on many factors outside our control, including your offer, your market,
              and how prospects choose to respond. We therefore do not guarantee any specific result, such as
              a number of meetings booked or an amount of revenue generated.
            </p>
            <p>
              To the fullest extent permitted by law, our total liability arising out of or relating to these
              Terms or the service is limited to the fees you paid us in the three months before the event
              giving rise to the claim. We are not liable for indirect or consequential losses, including lost
              profits or lost opportunities. We are not liable for restrictions placed on your LinkedIn
              account that arise from actions you requested outside our recommended limits. Nothing in these
              Terms limits liability that cannot be limited by law.
            </p>
          </Section>

          <Section title="10. Termination">
            <p>
              Either party may terminate the engagement by giving 30 days&apos; written notice to the other.
              We may terminate immediately by written notice if you materially breach these Terms, including
              the prohibited uses above, or fail to pay fees when due. On termination, outreach on your behalf
              will stop, and on request we will return or delete your prospect lists and business data in line
              with our Privacy Policy. Sections that by their nature should survive termination, including
              payment obligations, intellectual property, and limitation of liability, will survive.
            </p>
          </Section>

          <Section title="11. Governing law">
            <p>
              These Terms are governed by the laws of England and Wales. If a dispute arises, both parties
              agree first to try in good faith to resolve it through negotiation. If the dispute is not
              resolved within 30 days of written notice, it will be referred to and finally resolved by
              arbitration, seated in London and conducted in English.
            </p>
          </Section>

          <Section title="12. Changes to terms">
            <p>
              We may update these Terms from time to time. We will notify active clients by email at least 30
              days before any material change takes effect. Continuing to use Vectorae after that date means
              you accept the updated Terms. The effective date at the top of this page shows the current
              version.
            </p>
          </Section>

          <Section title="13. Contact">
            <p>
              Questions about these Terms can be sent to{" "}
              <a href={`mailto:${EMAIL}`} className="text-blue-500 hover:text-blue-400">
                {EMAIL}
              </a>
              .
            </p>
          </Section>
        </article>
      </main>

      <Footer />
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-12 border-t border-white/10 pt-10">
      <h2 className="text-2xl font-semibold text-white">{title}</h2>
      <div className="mt-4 space-y-4 leading-relaxed text-gray-300">{children}</div>
    </section>
  );
}

function SiteNav() {
  return (
    <header className="border-b border-white/10 px-6 py-5">
      <nav className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-8 gap-y-3">
        <Link href="/" className="text-lg font-bold text-white">
          Victorae
        </Link>
        <ul className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-gray-300">
          <li><Link href="/how-it-works" className="hover:text-white">How it Works</Link></li>
          <li><Link href="/about" className="hover:text-white">About</Link></li>
          <li><Link href="/pricing" className="hover:text-white">Pricing</Link></li>
          <li><Link href="/contact" className="hover:text-white">Contact</Link></li>
        </ul>
      </nav>
    </header>
  );
}
