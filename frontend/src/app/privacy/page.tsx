/**
 * app/privacy/page.tsx — Privacy Policy, served at /privacy.
 *
 * Must stay publicly accessible over HTTPS (Play Store requirement for the
 * Android build). Static server component: no API calls, no auth. Does NOT use
 * Shell.tsx (authenticated dashboard shell).
 *
 * NEEDS FOUNDER/LEGAL REVIEW BEFORE MERGE (2026-10-11): §5 (no analytics
 * provider -- none exists in the code) and §8 (self-serve account deletion and
 * what it keeps: docs/features/gdpr-erasure.md, decisions E1-E3) were
 * rewritten to match the code (founder decision D1). The retention periods in
 * §8 are defaults awaiting confirmation. Keep docs/PRIVACY_POLICY.md in step.
 *
 * NEEDS FOUNDER/LEGAL REVIEW (gaps-sweep-2 #13, 2026-10-11): §2 claimed usage
 * data on which pages and features are used. No page or feature tracking
 * exists in the code; §2 now lists what IS kept (auth_sessions ip +
 * user_agent, security_audit_events, the network_guard IP country/VPN check,
 * usage_meter AI spend, tutorial progress, device_tokens, host request logs).
 * Relied on: the GDPR Art. 13(1) information duty (say what is collected) --
 * a reading, not settled legal advice.
 */

import type { Metadata } from "next";
import Link from "next/link";
import { Footer } from "@/components/Footer";
import { PRODUCT_FULL_NAME } from "@/lib/productFacts";
import { canonicalMeta } from "@/lib/seo/pageMeta";
import { JsonLd } from "@/components/seo/JsonLd";
import { pageGraph } from "@/lib/seo/structuredData";

export const metadata: Metadata = {
  title: "Privacy Policy for Victorae | Calendarharvest",
  description: `How ${PRODUCT_FULL_NAME} collects, uses, and protects your data.`,
  robots: "index, follow",
  ...canonicalMeta("/privacy/"),
};

const EMAIL = "ahmadshahid@calendarharvest.com";

// DRAFT for legal review (founders, 2026-10-14). Open questions for counsel, not rendered:
//  - No legal entity, postal address or named data controller / EU-UK representative.
//  - Unipile is listed for LinkedIn sending and receiving although LinkedIn steps are not live
//    yet: keep (the integration exists) or narrow until launch?
export default function PrivacyPage() {
  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <JsonLd data={pageGraph({ path: "/privacy/", breadcrumb: "Privacy Policy" })} />
      <SiteNav />

      <main className="px-6 py-24">
        <article className="mx-auto max-w-3xl">
          <h1 className="text-4xl font-bold tracking-tight">Privacy Policy</h1>
          <p className="mt-4 text-sm text-gray-400">Effective date: October 11, 2026</p>

          <Section title="1. Who we are and what this policy covers">
            <p>
              Vectorae Enterprise is operated by Calendarharvest (&quot;we&quot;, &quot;us&quot;, or
              &quot;our&quot;). We run done-with-you outbound pipeline campaigns for businesses. This policy
              explains how we collect, use, store, and protect personal data when you visit
              calendarharvest.com, use the Vectorae dashboard, or engage us to run outreach on your behalf.
              It also covers the data we process about the prospects we contact for our clients.
            </p>
          </Section>

          <Section title="2. What data we collect">
            <p>
              When you become a client we collect <strong className="text-white">account data</strong>: your
              name, email address, company name, and the details you share about your business and ideal
              client profile. As you use the dashboard we keep <strong className="text-white">technical and
              security data</strong>: for each signed-in session, the IP address and the browser and device
              description your browser sends; a log of sign-ins, refused attempts, data exports, deletions and
              other security-relevant account changes; the country and VPN check made on the IP address you
              sign up or sign in from; how much AI processing your account uses, so plan limits can be
              applied; your progress through the in-app tutorials; and, in the mobile app, a push-notification
              token for your device. Our servers&apos; request logs record the time, IP address and address
              requested for each request, for operation and security. We do not use analytics or tracking
              tools to record which pages you view or how you move through the dashboard.
            </p>
            <p>
              To run campaigns we collect <strong className="text-white">prospect data</strong> on behalf of
              our clients. This includes publicly available LinkedIn profile information, professional email
              addresses, job titles, company details, and public posts and activity used to personalise
              outreach. For this data, our client decides who is targeted and why, and we process it on
              their instructions.
            </p>
            <p>
              <strong className="text-white">Payment data</strong> is processed by Stripe. We receive
              confirmation of payment and limited billing details, but we never see or store your full card
              number. Finally, we keep <strong className="text-white">communication data</strong>: the emails
              and LinkedIn messages sent and received through the system, and any correspondence you have
              with us directly.
            </p>
          </Section>

          <Section title="3. How we use data">
            <p>
              We use personal data to deliver the Vectorae service: researching prospects, writing and
              sending outreach, managing replies, and reporting results to you. We use campaign results to
              improve performance, for example by learning which messages and audiences produce replies for
              your account. We also use your contact details to communicate with you about your account,
              billing, and material changes to the service.
            </p>
            <p>
              We do not sell your data, or your prospects&apos; data, to third parties. We do not use your
              data to train AI models without your consent. Where the UK or EU GDPR applies, we rely on
              performance of our contract with you, our legitimate interests in running and improving the
              service, and, where required, your consent.
            </p>
          </Section>

          <Section title="4. Data storage and security">
            <p>
              Data is stored on encrypted servers and transmitted over encrypted connections. Our primary
              database is a managed Neon PostgreSQL database hosted on servers in the European Union and the
              United States. Access to personal data is limited to the personnel who need it to deliver the
              service, and passwords are stored only as one-way hashes. No system is perfectly secure, but we
              review our safeguards regularly and will notify affected users and regulators of a data breach
              where the law requires it.
            </p>
          </Section>

          <Section title="5. Third-party services we use">
            <p>
              We share data with service providers only as far as each needs it to do its job. Always in use:
              Neon (database), Upstash (Redis cache and job queue), Render (application servers) and Vercel
              (this website); Anthropic (Claude models write research, strategies and message drafts from
              prospect data); Resend (account emails such as sign-up verification); Stripe (payments).
            </p>
            <p>
              Used when the feature is switched on or the connection is made: Google (Gmail API to send from
              the mailboxes you connect, Google Calendar and Meet for meeting links, Postmaster Tools for
              sending-domain reputation, Firebase Cloud Messaging for push notifications); Unipile (LinkedIn
              sending and receiving), with RapidAPI as a fallback for reading public LinkedIn posts; Meta
              (WhatsApp Business messages); Apollo and Hunter (finding and verifying prospects&apos; business
              contact details); NewsAPI (news about a prospect&apos;s company); OpenAI (a second AI model, when an
              administrator enables it); Vapi and ElevenLabs (AI phone calls and voicemail audio); Twilio (SMS
              verification codes); Recall.ai and Zoom (meeting recording and meeting links); Calendly
              (bookings); HubSpot, Salesforce and Slack (the CRM and chat tools you connect); Mailreach
              (mailbox health checks); ipapi.co, ipinfo.io, IPQualityScore and proxycheck.io (the country and
              VPN check on the IP address you sign up or sign in from); and the AegisAudit, PostIQ and
              SIGNALFORGE tools when you connect them. We do not use Google Analytics or any other analytics or
              advertising tracker on the website or in the dashboard.
            </p>
            <p>
              Some of these providers are based outside your country, including in the United States. Where
              we transfer data from the UK or EU, we rely on appropriate safeguards such as Standard
              Contractual Clauses.
            </p>
          </Section>

          <Section title="6. Your rights">
            <p>
              You can ask us to access, correct, or delete the personal data we hold about you, and you can
              withdraw any consent you have given at any time. You can also ask for a copy of your data in a
              portable, machine-readable format.
            </p>
            <p>
              If you are in the UK or the European Economic Area, the GDPR gives you additional rights,
              including the right to object to or restrict processing and the right to complain to your data
              protection authority, such as the Information Commissioner&apos;s Office in the UK. If you are a
              California resident, the CCPA gives you the right to know what personal information we collect
              and how we use it, to request its deletion, and not to be discriminated against for exercising
              these rights. As stated above, we do not sell personal information.
            </p>
            <p>
              If you are a prospect we contacted on behalf of a client, you can ask us at any time to stop
              contacting you and to delete your data, and we will honour that request.
            </p>
          </Section>

          <Section title="7. Cookies">
            <p>
              We use essential cookies only: the cookies needed to keep you signed in, keep the site secure,
              and make the dashboard work. We do not use advertising cookies, and we do not allow third
              parties to track you across other websites for advertising purposes.
            </p>
          </Section>

          <Section title="8. Data retention">
            <p>
              We keep account data for as long as your account exists. We do not run an automatic deletion
              schedule for account or prospect data: it stays in your account until it is deleted. The one
              exception is support chat: a conversation is deleted 30 days after its last message.
            </p>
            <p>
              You can erase an individual prospect&apos;s personal data from the dashboard at any time. Erased:
              their name, contact details, company, LinkedIn profile and research notes; the subject and text
              of every message written to them (any message still waiting to be sent is cancelled) and of every
              reply they sent; call transcripts, recording links and voicemail audio; booking details; meeting
              notes, transcripts and summaries; meeting briefs and follow-up drafts; notes, custom fields and
              deal notes about them; and the review, attribution and fact-check copies of their messages.
              Kept, without those details: the dates, statuses and counts that campaign reports are built from.
              Their email address, phone number and LinkedIn profile are added to a do-not-contact list that is
              checked before every send, and a record that the erasure took place is kept. Data already copied
              into a CRM you connected stays there. Deleting a campaign strategy deletes its research, prospects,
              sequences, and messages with it. You can download a copy of the data in your account from your
              profile page.
            </p>
            <p>
              You can delete your whole account yourself from your profile page, after confirming your password.
              Any paid plan is cancelled at once and nothing more is charged (if the cancellation cannot be
              confirmed with the payment provider, nothing is deleted and you are asked to try again), and your
              account and its data are erased immediately. Connected LinkedIn accounts are also disconnected at
              Unipile. Kept after that: billing records (amounts, dates, and payment-provider reference numbers,
              with no name or email address in our copy) for 7 years, for tax and accounting; the consent ledger
              for your prospects, meaning every recorded opt-in, opt-out, suppression and erasure with the email
              address or phone number it concerns, its source, its time and a short note on how it was given or
              withdrawn, and the do-not-contact list, both without a time limit, so that anyone who asked not
              to be contacted stays protected; and a security record that the account was deleted, without your
              IP address, device or email address. Stripe, our payment provider, keeps its own customer record
              of you (name, email and payment details) under its own privacy policy; we do not ask Stripe to
              delete it.
            </p>
            <p>
              Outside the database: provider responses used to find and verify contact details are cached for
              up to 30 days (search results for 1 day) and expire on their own; they are not removed when an
              account is deleted. Our hosting provider&apos;s request logs record the IP address of each request
              and are kept for the period the hosting provider sets. Access Vectorae was given inside Google,
              HubSpot, Salesforce or Slack is removed from Vectorae but not revoked on the provider&apos;s side;
              you can remove it there. You can also ask us to delete your account or any other data at the
              address below.
            </p>
          </Section>

          <Section title="9. Contact for privacy requests">
            <p>
              To exercise any of your rights or ask a question about this policy, email{" "}
              <a href={`mailto:${EMAIL}`} className="text-blue-500 hover:text-blue-400">
                {EMAIL}
              </a>
              . We may need to verify your identity before acting on a request, and we will respond within 30
              days.
            </p>
          </Section>

          <Section title="10. Changes to this policy">
            <p>
              We may update this policy as our service or the law changes. If we make a material change, we
              will notify users by email before it takes effect. The effective date at the top of this page
              shows the current version.
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
