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
  title: "Vectorae Terms of Service",
  description: `The terms that govern your use of ${PRODUCT_FULL_NAME}.`,
  robots: "index, follow",
  ...canonicalMeta("/terms/"),
};

const EMAIL = "rehanmasood@calendarharvest.com";

// Body text: TERMS.md as approved by the founder (2026-10-29), word for word.
export default function TermsPage() {
  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <JsonLd data={pageGraph({ path: "/terms/", breadcrumb: "Terms of Service" })} />
      <SiteNav />

      <main className="px-6 py-24">
        <article className="mx-auto max-w-3xl">
          <h1 className="text-4xl font-bold tracking-tight">Vectorae Terms of Service</h1>
          <p className="mt-4 text-sm text-gray-400">Effective date: October 29, 2026</p>

          <Section title="1. Who we are">
            <p>
              Vectorae (calendarharvest.com) is a software product operated by Rehan Masood, an individual
              based in New York, USA. Vectorae was built by Rehan Masood and Ahmad Shahid. No company has
              been registered for Vectorae yet. In these Terms, &quot;we&quot;, &quot;us&quot; and
              &quot;Vectorae&quot; mean Rehan Masood. Vectorae is not affiliated with LeadPilot AB
              (leadpilot.com) or any other product named LeadPilot. Contact: <EmailLink />.
            </p>
          </Section>

          <Section title="2. Accepting these Terms">
            <p>
              These Terms are a binding agreement between you and Rehan Masood. If you accept them for a
              business, you confirm you have authority to bind that business. By using Vectorae you agree to
              these Terms. If a signed agreement with you conflicts with these Terms, the signed agreement
              controls on that point.
            </p>
          </Section>

          <Section title="3. The service">
            <p>
              Vectorae is an outbound sales tool. It researches your market, finds and verifies business
              contacts at matching companies, and writes each email for one person. It sends from your own
              Gmail account, connected through Google sign-in (OAuth), only after you approve each day&apos;s
              sending. Some features are switched off by default or are not available yet; the FAQ lists
              what is live. LinkedIn steps are not available.
            </p>
          </Section>

          <Section title="4. Who can use Vectorae">
            <p>
              You must be at least 18 and able to enter a contract. Vectorae is for businesses and sole
              traders only, not consumers. It is offered to clients in the United States, United Kingdom,
              Canada, Australia, New Zealand, Ireland and Singapore. We may decline or end an engagement that
              does not meet these conditions.
            </p>
          </Section>

          <Section title="5. Your responsibilities">
            <ul className="list-disc space-y-2 pl-6">
              <li>You are the sender of every email sent from your mailbox, and you are responsible for it.</li>
              <li>
                You must comply with the laws that apply to your outreach, including the US CAN-SPAM Act,
                Canada&apos;s CASL, the GDPR, the UK GDPR and PECR.
              </li>
              <li>
                Before launch you confirm that you have a lawful basis to contact each person. Vectorae does
                not give legal advice.
              </li>
              <li>
                You must give accurate information, keep it up to date, and keep your sending mailbox in good
                standing with Google.
              </li>
              <li>You must not use Vectorae to send anything you could not lawfully send yourself.</li>
            </ul>
          </Section>

          <Section title="6. Our responsibilities">
            <p>
              We will provide the service with reasonable skill and care. We will honour every opt-out and
              keep suppressed people suppressed across all channels. We keep your data confidential and use it
              only to provide the service, as described in our Privacy Policy.
            </p>
          </Section>

          <Section title="7. Prohibited uses">
            <p>You may not use Vectorae to:</p>
            <ul className="list-disc space-y-2 pl-6">
              <li>
                contact people in healthcare, legal, government, non-profit, real estate or other industries
                excluded by our ideal-client policy;
              </li>
              <li>send deceptive content, impersonate anyone, or make claims you cannot support;</li>
              <li>contact anyone who has opted out;</li>
              <li>send to consumers or to purchased or uploaded lists.</li>
            </ul>
          </Section>

          <Section title="8. Fees and payment">
            <ul className="list-disc space-y-2 pl-6">
              <li>Plans and prices are shown on the pricing page or in your signed agreement.</li>
              <li>
                <strong className="text-white">Pay per meeting:</strong> $0 a month and $179 for each meeting
                a prospect books through Vectorae. We charge no earlier than 48 hours after the booking. If
                the meeting is cancelled within those 48 hours, there is no charge. Each prospect is charged
                at most once.
              </li>
              <li><strong className="text-white">Monthly plans</strong> are billed in advance.</li>
              <li>
                Payments are made to Rehan Masood by the method on your invoice. Online checkout is not
                available yet.
              </li>
              <li>Fees are non-refundable once a campaign launches, except where the law requires a refund.</li>
              <li>You may cancel with 30 days&apos; written notice. Fees for the notice period remain payable.</li>
              <li>Prices exclude any taxes that apply.</li>
            </ul>
          </Section>

          <Section title="9. Intellectual property">
            <p>
              You own your business data, prospect lists and materials. You give us a limited licence to use
              them only to provide the service. We keep all rights in the Vectorae software, methods and
              technology, including improvements made during your engagement.
            </p>
          </Section>

          <Section title="10. No guarantee">
            <p>
              Vectorae does not guarantee meetings, replies, revenue or deliverability. To the extent the law
              allows, the service is provided &quot;as is&quot; without any implied warranties.
            </p>
          </Section>

          <Section title="11. Limitation of liability">
            <p>
              To the extent the law allows, our total liability for any claim is limited to the fees you paid
              us in the three months before the event that caused the claim. We are not liable for indirect or
              consequential losses, lost profits, or mailbox or account restrictions caused by sending you
              chose to do outside our recommended limits. Nothing in these Terms limits liability that cannot
              legally be limited.
            </p>
          </Section>

          <Section title="12. Indemnity">
            <p>
              You will defend and indemnify us against third-party claims arising from the content of your
              outreach, your breach of these Terms, or your breach of the law.
            </p>
          </Section>

          <Section title="13. Ending the service">
            <p>
              Either party may end the service with 30 days&apos; written notice. We may end it immediately
              for a material breach, including a prohibited use, or for non-payment. When the service ends,
              sending stops and we return or delete your data on request, as our Privacy Policy describes.
              Sections 8 to 12 and 14 survive termination.
            </p>
          </Section>

          <Section title="14. Governing law and disputes">
            <p>
              These Terms are governed by the laws of the State of New York, USA, without regard to its
              conflict-of-law rules. Before starting a court case, each party will try in good faith for 30
              days to settle the dispute after written notice. Disputes that are not settled go exclusively to
              the state courts in Queens County, New York, or the US District Court for the Eastern District
              of New York.
            </p>
          </Section>

          <Section title="15. Changes to these Terms">
            <p>
              We may update these Terms. Active clients get at least 30 days&apos; email notice before a
              material change takes effect. The effective date at the top shows the current version.
            </p>
          </Section>

          <Section title="16. Contact">
            <p>
              Rehan Masood, operator of Vectorae · <EmailLink />
            </p>
          </Section>
        </article>
      </main>

      <Footer />
    </div>
  );
}

function EmailLink() {
  return (
    <a href={`mailto:${EMAIL}`} className="text-blue-500 hover:text-blue-400">
      {EMAIL}
    </a>
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
          Vectorae
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
