/**
 * The three buyer-group pages (/for/<slug>/), one per group in
 * product-facts.json `fit`. They hold the ten use cases drafted in
 * docs/program/ai-visibility/geo/content/definitions/leadpilot_use_cases.md
 * (one page per group, not one thin page per use case). No customer results
 * exist yet (founder answer Q-06: "I will give it later"), so no result is
 * claimed; each page says what to measure.
 */

import type { PageLink, Section } from "@/lib/content/types";
import { MEETING_PRICE, checkoutSentence, tierList } from "./shared";

export interface ForPage {
  slug: "agencies" | "b2b-service-firms" | "local-businesses";
  title: string;
  description: string;
  eyebrow: string;
  h1: string;
  lead: string[];
  sections: Section[];
  related: PageLink[];
}

const pricing: Section = {
  heading: "What it costs",
  blocks: [
    { kind: "p", text: `Monthly plans: ${tierList()} per month, a flat price per account rather than per seat. Or Pay per meeting: no monthly fee and ${MEETING_PRICE} for each prospect who books a meeting; the charge waits 48 hours and is waived if the meeting is cancelled. ${checkoutSentence()} Full details on the [pricing page](/pricing).` },
  ],
};

const notFit: Section = {
  heading: "When Vectorae is not the right tool",
  blocks: [
    {
      kind: "ul",
      items: [
        "You sell to consumers or homeowners. Vectorae only finds people at companies.",
        "You need to email a list you already have. Leads come from Vectorae's own sourcing; you cannot import them.",
        "You want to send thousands of emails a day. By default each mailbox sends at most 100 a day.",
        "You want an AI that replies to prospects for you. Vectorae classifies replies and routes them to you; you write the answer.",
        "You need LinkedIn automation today. LinkedIn steps are built and tested only against simulated services, and are not live yet.",
      ],
    },
  ],
};

const measure = (what: string): Section => ({
  heading: "What to measure",
  blocks: [
    { kind: "p", text: `${what} Vectorae launched publicly around September 2026 and has no published customer results yet, so this page does not claim any.` },
  ],
});

const AGENCIES: ForPage = {
  slug: "agencies",
  title: "Vectorae for agencies: outbound when the founder is busy delivering",
  description:
    "How marketing, web and design agencies use Vectorae to keep prospecting while client work is busy: research, verified contacts, one-to-one emails you approve, booked meetings.",
  eyebrow: "Vectorae for agencies",
  h1: "Outbound for agencies whose founder still does the selling",
  lead: [
    "In an agency of 5 to 25 people, the founder is usually both the best salesperson and the person clients ask for. When a big project lands, prospecting stops, and six weeks later there is nothing behind it.",
    "Vectorae does the research, finds and verifies contacts, and writes each email for one person. The founder's daily task is to approve the day's sending.",
  ],
  sections: [
    {
      heading: "The problem",
      blocks: [
        { kind: "p", text: "Agency founders know outbound works, because they often run it for clients. What they lack is the hours to research a market, build a list, write good first emails and keep follow-ups going while delivering client work. Templated cold email gets ignored, and hiring a salesperson is a big bet for a small agency." },
        { kind: "p", text: "Most agencies win most of their new business through referrals: in a 2024 SparkToro survey of 612 agency owners and consultants, 66% named referrals from existing and past clients as their top source ([SparkToro](https://sparktoro.com/blog/the-state-of-digital-agencies-in-2024-client-referrals-drive-agency-new-business-but-is-it-enough-and-are-sales-pipelines-a-cause-for-concern)). Referrals do not arrive on a schedule." },
      ],
    },
    {
      heading: "How an agency founder uses Vectorae",
      blocks: [
        {
          kind: "ol",
          items: [
            "Describe the offer, for example \"Webflow sites and SEO for B2B SaaS companies with 20 to 200 employees\", and add two or three past clients and how you won them.",
            "Vectorae runs its 72-step research pipeline and produces a strategy: ideal customer, competitors, messaging and objections. The strategy must pass 10 checks, including a compliance check, before anything else happens.",
            "It sources contacts at matching companies, verifies their email addresses, and writes each first email for one person. Claims in the email are checked.",
            "Each day you look at what is queued and approve it, from the web app or the Android app.",
            "Emails go from your own Gmail on a gradual ramp (10 on the first day, up to 100 a day), so your domain is not put at risk by a sudden spike.",
            "Replies are classified and routed to you, and the sequence stops when a person replies (an out-of-office reply only pauses it). Interested prospects book on your Vectorae booking page.",
          ],
        },
        { kind: "note", text: "Unlike Apollo, where an agency gets a contact database and a sequencer and still builds the list and writes the sequence itself, Vectorae does the research and writing and leaves the founder one approval step. See [Vectorae vs Apollo](/compare/leadpilot-vs-apollo) and [Vectorae vs Instantly](/compare/leadpilot-vs-instantly)." },
      ],
    },
    {
      heading: "Agencies that run outreach for clients",
      blocks: [
        { kind: "p", text: "Workspaces for agencies are built and switched on, but not yet confirmed working end to end. For email, client workspaces can restrict each client's outreach to that client's own sending domains. There is one WhatsApp number per deployment, so every client's prospects would see the same WhatsApp number. If you send at volume for many clients, a sending platform such as Smartlead fits better: see [Vectorae vs Smartlead](/compare/leadpilot-vs-smartlead)." },
      ],
    },
    measure("Meetings booked per month, reply rate, and founder hours per week spent on new business, before and after."),
    pricing,
    notFit,
  ],
  related: [
    { href: "/compare/leadpilot-vs-apollo", label: "Vectorae vs Apollo" },
    { href: "/compare/leadpilot-vs-instantly", label: "Vectorae vs Instantly" },
    { href: "/compare/leadpilot-vs-smartlead", label: "Vectorae vs Smartlead" },
    { href: "/faq", label: "Vectorae FAQ" },
  ],
};

const SERVICE_FIRMS: ForPage = {
  slug: "b2b-service-firms",
  title: "Vectorae for B2B service firms: staffing, consultancies, fractional experts",
  description:
    "How staffing agencies, consultancies, fractional executives, accounting and IT services firms use Vectorae to book meetings with business clients without a sales team.",
  eyebrow: "Vectorae for B2B service firms",
  h1: "Outbound for B2B service firms with no sales team",
  lead: [
    "Staffing and recruiting agencies, consultancies, fractional experts, accounting and immigration firms, and IT services companies all sell to other businesses, and often grow on referrals until referrals plateau.",
    "Vectorae researches the market, finds and verifies the right people at matching companies, writes each email for one person, and sends from your Gmail after you approve the day's sending.",
  ],
  sections: [
    {
      heading: "Staffing and recruiting agencies",
      blocks: [
        { kind: "p", text: "When relationships plateau, agencies turn to outbound, and commercial staffing prospects usually need many touches over several weeks. The owner describes the roles and industries the agency fills; Vectorae builds and verifies a strategy, sources and verifies contacts, and runs one-to-one sequences that stop automatically when a person replies." },
        { kind: "note", text: "Honest limit: Vectorae does not target by live hiring signals or preferred-supplier status, which some staffing-specific tools do. Signal Radar, a signal-based feature, is built but switched off by default." },
      ],
    },
    {
      heading: "Fractional executives and consultants",
      blocks: [
        { kind: "p", text: "A consultant sells their own time, so prospecting happens in bursts between engagements. With past clients described, research runs the 72-step pipeline and anchors the strategy in real wins; without them it runs 144 steps. Each email is written for one decision-maker and its claims are checked, which matters when your credibility is the product. Prospects book on your Vectorae booking page, which prevents double bookings and shows each person's time zone." },
      ],
    },
    {
      heading: "Accounting, immigration and law consultancies",
      blocks: [
        { kind: "p", text: "Firms that serve businesses (bookkeeping, controller work, employer-sponsored visas) need careful, accurate outreach. Verification includes a legal and compliance check, and claim checking keeps emails from stating what the firm cannot support. Vectorae does not give legal advice: before launch it asks you to confirm you may lawfully contact each person, and records that confirmation. Vectorae's own customer research marks this segment as not yet validated." },
      ],
    },
    {
      heading: "IT services and managed service providers",
      blocks: [
        { kind: "p", text: "Generic \"we keep your IT running\" outreach sounds like every competitor. Vectorae's research identifies the segments and messages most likely to land, and each email is written to the person and company, using company news where it is available. Replies come back classified (interested, question, objection, not interested, unsubscribe request, out of office), and an unsubscribe request suppresses the person immediately." },
      ],
    },
    {
      heading: "Early-stage B2B SaaS founders (partial fit)",
      blocks: [
        { kind: "p", text: "Vectorae is designed for service firms. A SaaS founder with a clear B2B buyer can use it to learn which segment responds before the first sales hire, but high-volume sending, product-usage signals and LinkedIn are not covered today. If you need a flexible data workbench instead, see [Vectorae vs Clay](/compare/leadpilot-vs-clay)." },
      ],
    },
    {
      heading: "Vectorae or an SDR?",
      blocks: [
        { kind: "p", text: "The Bridge Group's 2025 report puts median SDR on-target earnings at $80K with an average ramp of 3.0 months ([Bridge Group](https://www.bridgegroupinc.com/research/2025-sdr-models-metrics-report-the-bridge-group)). A firm that cannot justify that yet can use Vectorae to find the segment that buys. See [Vectorae vs Artisan, and vs hiring an SDR](/compare/leadpilot-vs-artisan)." },
      ],
    },
    measure("First meetings per month, meeting-to-client rate, and cost per meeting against your plan or the pay-per-meeting price."),
    pricing,
    notFit,
  ],
  related: [
    { href: "/compare/leadpilot-vs-artisan", label: "Vectorae vs hiring an SDR" },
    { href: "/compare/leadpilot-vs-clay", label: "Vectorae vs Clay" },
    { href: "/outbound-statistics", label: "Outbound statistics" },
    { href: "/faq", label: "Vectorae FAQ" },
  ],
};

const LOCAL: ForPage = {
  slug: "local-businesses",
  title: "Vectorae for local businesses that sell to businesses",
  description:
    "How commercial cleaning, facilities, HVAC, fire protection, catering and clinic businesses use Vectorae to reach property managers, offices and employers nearby.",
  eyebrow: "Vectorae for local businesses",
  h1: "Outbound for local businesses that sell to other businesses",
  lead: [
    "Commercial cleaning, facilities, HVAC and fire protection companies, caterers and clinics win recurring contracts from property managers, offices and employers nearby. Those buyers are reachable, but owners rarely have time to find them and write to each one.",
    "Vectorae's local business mode researches and writes for business buyers in your service area. It never finds or cold-messages members of the public.",
  ],
  sections: [
    {
      heading: "How local business mode works",
      blocks: [
        {
          kind: "ol",
          items: [
            "On the first step of a new strategy, answer **Is this a local business?** with your kind of business.",
            "Enter your service area (a city or postcode, a country code and a radius) and what you offer business buyers.",
            "Vectorae researches business buyers in that area, sources and verifies contacts, and writes each email for one person.",
            "You approve the day's sending; interested prospects book a walk-through or call on your booking page.",
          ],
        },
        { kind: "note", text: "The radius guides the research and the messages, but the lead search uses your city or postcode, so some leads can sit outside it. Messages may say you are based in or serve the area; they never claim a distance or walking time to the prospect, because Vectorae does not know where their office is." },
      ],
    },
    {
      heading: "Commercial cleaning and facilities",
      blocks: [
        { kind: "p", text: "Firms past the owner-and-crew stage (roughly 10 to 50 staff) live on recurring contracts, and losing one creates a hole that referrals and bids fill slowly. Vectorae reaches property managers, office managers and facilities leads nearby, with each contact sourced and verified rather than taken from a bought list (lists cannot be imported)." },
      ],
    },
    {
      heading: "Commercial HVAC and fire protection",
      blocks: [
        { kind: "p", text: "Maintenance and inspection contracts are recurring, compliance-driven purchases, so the right message matters more than volume. Vectorae builds a strategy for commercial buyers such as property managers and facilities directors. WhatsApp steps are available on plans that include them, but cold WhatsApp needs a Meta-approved template and a recorded opt-in, so most cold outreach stays on email. Vectorae only finds people at companies, so it is the wrong tool for homeowner leads." },
      ],
    },
    {
      heading: "Restaurants and caterers",
      blocks: [
        { kind: "p", text: "A restaurant or caterer with spare weekday capacity can pitch team lunches, events and office catering to office managers, HR and events leads nearby. A separate win-back feature for messaging a business's own opted-in customers is switched off by default until its legal review is complete." },
      ],
    },
    {
      heading: "Clinics selling services to employers",
      blocks: [
        { kind: "p", text: "Clinics that offer occupational health, screenings or staff wellness can reach employers, schools, sports clubs, referring practices and insurers in their area. Outreach is about services for an organisation and never uses or mentions patient information; healthcare businesses cannot use the customer win-back feature at all." },
      ],
    },
    measure("Walk-throughs, quotes or first meetings per month, and contracts signed from them."),
    pricing,
    notFit,
  ],
  related: [
    { href: "/how-it-works", label: "How Vectorae works" },
    { href: "/compare/leadpilot-vs-apollo", label: "Vectorae vs Apollo" },
    { href: "/help/who-its-for-and-not-for", label: "Who Vectorae is for (help center)" },
    { href: "/faq", label: "Vectorae FAQ" },
  ],
};

export const FOR_PAGES: ForPage[] = [AGENCIES, SERVICE_FIRMS, LOCAL];

export function forPage(slug: string): ForPage {
  const found = FOR_PAGES.find((p) => p.slug === slug);
  if (!found) throw new Error(`unknown /for/ page ${slug}`);
  return found;
}
