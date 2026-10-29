/**
 * app/privacy/page.tsx — Privacy Policy, served at /privacy.
 *
 * Must stay publicly accessible over HTTPS (Play Store requirement for the
 * Android build). Static server component: no API calls, no auth. Does NOT use
 * Shell.tsx (authenticated dashboard shell).
 *
 * Body text: PRIVACY.md as approved by the founder (2026-10-29). Section 3's
 * scope list is app/integrations/gmail.py GMAIL_SCOPES; its claims are pinned
 * by tests/test_gmail_inbox_scope.py. Keep docs/PRIVACY_POLICY.md in step
 * (tests/test_privacy_docs_truth.py checks both).
 */

import type { Metadata } from "next";
import Link from "next/link";
import { Footer } from "@/components/Footer";
import { PRODUCT_FULL_NAME } from "@/lib/productFacts";
import { canonicalMeta } from "@/lib/seo/pageMeta";
import { JsonLd } from "@/components/seo/JsonLd";
import { pageGraph } from "@/lib/seo/structuredData";

export const metadata: Metadata = {
  title: "Vectorae Privacy Policy",
  description: `How ${PRODUCT_FULL_NAME} collects, uses, and protects your data.`,
  robots: "index, follow",
  ...canonicalMeta("/privacy/"),
};

const EMAIL = "rehanmasood@calendarharvest.com";

const GMAIL_SCOPES: { scope: string; use: string }[] = [
  { scope: "https://www.googleapis.com/auth/gmail.send",
    use: "sends the emails you approved, from your Gmail address." },
  { scope: "https://www.googleapis.com/auth/gmail.readonly",
    use: "reads new messages in the email threads Vectorae started for you, to find replies, bounces and out-of-office messages." },
  { scope: "https://www.googleapis.com/auth/gmail.compose",
    use: "saves the follow-up email after a meeting as a draft in your Gmail, for you to review and send." },
  { scope: "https://www.googleapis.com/auth/calendar.events",
    use: "creates the calendar event and Google Meet link for a meeting booked with a prospect." },
];

export default function PrivacyPage() {
  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <JsonLd data={pageGraph({ path: "/privacy/", breadcrumb: "Privacy Policy" })} />
      <SiteNav />

      <main className="px-6 py-24">
        <article className="mx-auto max-w-3xl">
          <h1 className="text-4xl font-bold tracking-tight">Vectorae Privacy Policy</h1>
          <p className="mt-4 text-sm text-gray-400">Effective date: October 29, 2026</p>

          <Section title="1. Who is responsible">
            <p>
              Vectorae (calendarharvest.com) is operated by Rehan Masood, an individual based in New York,
              USA. Co-founder Ahmad Shahid helps run the service. No company has been registered for Vectorae
              yet. This policy covers visits to calendarharvest.com, use of the Vectorae dashboard and mobile
              app, and outreach run for our clients, including data about the prospects they contact. For
              prospect data, our client decides who is contacted and why, and we process that data on the
              client&apos;s instructions. Contact: <EmailLink />.
            </p>
          </Section>

          <Section title="2. Data we collect">
            <ul className="list-disc space-y-2 pl-6">
              <li>
                <strong className="text-white">Account data:</strong> name, email, company, and details about
                your business and ideal clients.
              </li>
              <li>
                <strong className="text-white">Technical and security data:</strong> IP address, browser and
                device type, sign-in and security logs, country and VPN checks, AI usage, tutorial progress,
                and a push-notification token in the mobile app.
              </li>
              <li>
                <strong className="text-white">Prospect data:</strong> public LinkedIn profile information and
                public posts, professional emails, job titles and company details, collected on our
                clients&apos; instructions.
              </li>
              <li>
                <strong className="text-white">Payment data:</strong> invoices and payment records. We never
                see or store full card numbers.
              </li>
              <li>
                <strong className="text-white">Communication data:</strong> emails sent and received through
                the service, and your correspondence with us.
              </li>
            </ul>
            <p>
              We do not use Google Analytics or any other analytics or advertising trackers to record which
              pages you view.
            </p>
          </Section>

          <Section title="3. Google user data (Gmail)">
            <p>When you connect Gmail, Vectorae asks Google for these permissions:</p>
            <ul className="list-disc space-y-2 pl-6">
              {GMAIL_SCOPES.map(({ scope, use }) => (
                <li key={scope}>
                  <code className="break-all text-gray-200">{scope}</code>: {use}
                </li>
              ))}
            </ul>
            <p>We use them only to:</p>
            <ul className="list-disc space-y-2 pl-6">
              <li>send the emails you approved, from your own Gmail address;</li>
              <li>
                detect replies, bounces and out-of-office messages to your Vectorae emails, so a reply stops
                the sequence;
              </li>
              <li>
                classify those replies (interested, question, objection, not interested, unsubscribe, out of
                office, bounce, automated) and show them to you.
              </li>
            </ul>
            <p>
              We do not read, store or use other email in your mailbox for any other purpose. Reply text from
              Gmail is sent to one AI provider, Anthropic, and only to classify the reply, suggest a reply
              draft for you to review, prepare your brief before a booked meeting, note when a prospect asks
              to be contacted again later, and let you practise a sales call against the objections the
              prospect raised.
            </p>
            <ul className="list-disc space-y-2 pl-6">
              <li>We do not sell Google user data or use it for advertising.</li>
              <li>
                We do not use Google user data to develop, improve or train generalized AI or machine-learning
                models.
              </li>
              <li>
                No person reads your Gmail data unless you ask us to for support, it is needed for security or
                to comply with the law, or the data has been made anonymous.
              </li>
              <li>
                You can disconnect Vectorae at any time at myaccount.google.com/permissions. You can also ask
                us to delete the Gmail data we hold (see section 8).
              </li>
            </ul>
            <p>
              Vectorae&apos;s use and transfer to any other app of information received from Google APIs will
              adhere to the{" "}
              <a href="https://developers.google.com/terms/api-services-user-data-policy"
                 className="text-blue-500 hover:text-blue-400">
                Google API Services User Data Policy
              </a>
              , including the Limited Use requirements.
            </p>
          </Section>

          <Section title="4. How we use data">
            <p>
              We use data to provide the service, report results to you, keep the service secure, and improve
              how it performs. We do not sell your data or your prospects&apos; data. We do not use your data
              to train AI models without your consent.
            </p>
          </Section>

          <Section title="5. Service providers">
            <p>We share data only with providers that help run Vectorae, and only for that purpose.</p>
            <ul className="list-disc space-y-2 pl-6">
              <li>
                <strong className="text-white">Always used:</strong> Neon (database), Upstash, Render, Vercel
                (hosting), Anthropic (AI), Resend (transactional email).
              </li>
              <li>
                <strong className="text-white">Used when a feature needs it:</strong> Google (Gmail API,
                Calendar, Meet, Postmaster Tools, Firebase Cloud Messaging), Unipile, Meta, Apollo, Hunter,
                NewsAPI, RapidAPI, Mailreach, OpenAI, Vapi, ElevenLabs, Twilio, Recall.ai, Zoom, Calendly,
                HubSpot, Salesforce, Slack, Stripe, and IP-check services.
              </li>
            </ul>
            <p>
              Our primary database is managed by Neon in the EU and US. Transfers of UK or EU data rely on
              safeguards such as Standard Contractual Clauses.
            </p>
          </Section>

          <Section title="6. Security">
            <p>
              Data is stored on encrypted servers and sent over encrypted connections. Passwords are stored
              only as one-way hashes (PBKDF2-HMAC-SHA256).
            </p>
          </Section>

          <Section title="7. How long we keep data">
            <ul className="list-disc space-y-2 pl-6">
              <li>Account and prospect data stays until you delete it.</li>
              <li>Support chat is deleted 30 days after its last message.</li>
              <li>Billing records are kept for 7 years.</li>
              <li>
                The do-not-contact list and consent records are kept without a time limit, so opted-out people
                stay opted out.
              </li>
              <li>Cached provider responses are kept for up to 30 days (search results for 1 day).</li>
            </ul>
          </Section>

          <Section title="8. Your rights">
            <p>
              You can access, correct, export or delete your data, and withdraw consent. You can erase a single
              prospect or delete your whole account yourself from your profile page. People in the UK or EEA
              have GDPR rights; California residents have CCPA rights. A prospect who asks to stop hearing from
              our clients is suppressed across all channels, and can ask us to delete their data. Email{" "}
              <EmailLink />. We may verify your identity and will answer within 30 days.
            </p>
          </Section>

          <Section title="9. Cookies">
            <p>
              We use only essential cookies, for sign-in, security and the dashboard. We use no advertising
              cookies.
            </p>
          </Section>

          <Section title="10. Children">
            <p>
              Vectorae is for businesses. It is not meant for anyone under 18, and we do not knowingly collect
              their data.
            </p>
          </Section>

          <Section title="11. Changes">
            <p>
              We will email active clients before a material change takes effect. The effective date at the
              top shows the current version.
            </p>
          </Section>

          <Section title="12. Contact">
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
