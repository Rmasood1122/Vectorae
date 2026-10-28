/**
 * The five comparison pages (/compare/leadpilot-vs-<slug>/) and the /compare/ hub.
 *
 * Rules (docs/program/ai-visibility/geo/content/comparisons/, founder answer Q-09,
 * 2026-10-16): the founders' authorship is stated at the top; every page says
 * plainly when to choose the other tool; a competitor price is shown only when
 * two independent sources agreed on it when checked (COMPETITORS_CHECKED),
 * otherwise the page points to the competitor's own pricing page; competitor
 * names identify them, no logos. Research log: geo/research/competitor_analysis.md.
 */

import type { PageLink, Section } from "@/lib/content/types";
import { CANONICAL, DISAMBIGUATION, MEETING_PRICE, checkoutSentence, tierList, tierRange } from "./shared";

export interface Comparison {
  slug: "apollo" | "instantly" | "smartlead" | "clay" | "artisan";
  competitor: string;
  title: string;
  description: string;
  h1: string;
  shortAnswer: string;
  bestFor: string;
  sections: Section[];
  sources: PageLink[];
}

const LP_PRICE = `${tierList()} per month (flat per account, not per seat), or Pay per meeting: $0 a month and ${MEETING_PRICE} per prospect who books`;

const whenVectorae = (extra: string[]): Section => ({
  heading: "When to choose Vectorae",
  blocks: [
    {
      kind: "ul",
      items: [
        "You run an agency or B2B service firm of roughly 5 to 50 people, and the founder or owner does the selling.",
        "You want the market research, contact finding and email writing done for you, not a tool you operate.",
        "You want to approve each day's sending before anything goes out, and you answer replies yourself.",
        ...extra,
      ],
    },
  ],
});

const sourcesSection = (sources: PageLink[]): Section => ({
  heading: "Sources",
  blocks: [
    {
      kind: "ul",
      items: [
        ...sources.map((s) => `[${s.label}](${s.href})`),
        "Vectorae facts: the [pricing page](/pricing), [how it works](/how-it-works) and the [help center](/help).",
      ],
    },
    { kind: "note", text: DISAMBIGUATION },
  ],
});

const APOLLO: Comparison = {
  slug: "apollo",
  competitor: "Apollo.io",
  title: "Vectorae vs Apollo.io: which fits a small agency?",
  description:
    "Vectorae vs Apollo.io for agencies and B2B service firms: a contact database you operate, or research, contacts and one-to-one emails done for you. Features, pricing and when to choose each.",
  h1: "Vectorae vs Apollo.io",
  shortAnswer:
    `Apollo.io is a sales platform built around a large B2B contact database with sequencing: you search its contacts, build a list, write a sequence and run it. ${CANONICAL} Choose Apollo if you want data and control at a low per-seat price. Choose Vectorae if you run a founder-led firm with no time to run outbound yourself.`,
  bestFor: "Teams who want a large searchable database plus sequences, dialer and CRM sync, and will operate them",
  sections: [
    whenVectorae([`You would rather pay per result: ${MEETING_PRICE} per booked meeting, no monthly fee.`]),
    {
      heading: "When to choose Apollo.io",
      blocks: [
        {
          kind: "ul",
          items: [
            "You want to search and export from a large B2B contact database yourself.",
            "You (or an SDR) will build lists and run sequences.",
            "You need LinkedIn task steps, a built-in dialer or deep CRM sync today. Vectorae's LinkedIn steps are not live yet, and its phone channel is switched off by default.",
            "You want a free plan or the lowest per-seat entry price.",
            "You want a mature product with many public reviews. Vectorae is new and has none yet.",
          ],
        },
      ],
    },
    {
      heading: "Feature comparison",
      blocks: [
        {
          kind: "table",
          head: ["Feature", "Vectorae", "Apollo.io"],
          rows: [
            ["Starting point", "Describe what you sell", "Search the contact database"],
            ["Market research and strategy", "72-step research pipeline (144 with no past clients), then 10 verification checks", "Not a research product; you choose the market"],
            ["Contact data", "Sourced and email-verified for you; not browsable", "Large searchable database"],
            ["Writing", "Each email written for one person; claims checked", "AI drafting and templates you manage"],
            ["Sending control", "Daily approval required; high-risk messages held for review", "Sequences run once launched"],
            ["Volume", "One Gmail mailbox per account; 10 a day rising to 100 a day", "Multiple mailboxes with deliverability tools"],
            ["Channels", "Email (live), WhatsApp (live, opt-in required), LinkedIn (not live)", "Email, phone dialer, LinkedIn tasks"],
            ["Replies", "Classified and routed to you; no auto-reply", "Inbox and tasks"],
            ["Booking", "Own booking pages with double-booking protection; Calendly links", "Booking links and routing"],
            ["Compliance defaults", "Unsubscribe link and one-click headers on every email; suppression re-checked before every send; no open pixel for EU/EEA/UK leads", "Configured by the user"],
          ],
        },
      ],
    },
    {
      heading: "Pricing comparison",
      blocks: [
        {
          kind: "table",
          head: ["", "Vectorae", "Apollo.io"],
          rows: [
            ["Plans", LP_PRICE, "Free plan; Basic $49, Professional $79, Organization $119 per user per month billed annually (Organization needs at least 3 users); monthly billing costs more"],
            ["Pay per result", `${MEETING_PRICE} per booked meeting, charged after 48 hours and waived if the meeting is cancelled`, "None"],
          ],
        },
        { kind: "p", text: `For one person who already knows their market and will write their own sequences, Apollo costs far less. Vectorae costs more per month because it does the research and writing Apollo leaves to you. ${checkoutSentence()}` },
      ],
    },
    {
      heading: "Verdict: which is better for which use case",
      blocks: [
        {
          kind: "table",
          head: ["Use case", "Better choice"],
          rows: [
            ["Agency founder with no sales team and no time to prospect", "Vectorae"],
            ["SDR team that needs data, a dialer and LinkedIn", "Apollo.io"],
            ["Solo founder on a tight budget who enjoys writing outreach", "Apollo.io (free or Basic)"],
            ["Firm that wants to pay only for booked meetings", "Vectorae"],
            ["Anyone who needs to email or upload their own list", "Apollo.io (Vectorae cannot import leads)"],
            ["Firm worried about compliance mistakes in cold email", "Vectorae (compliance enforced by default)"],
          ],
        },
      ],
    },
  ],
  sources: [
    { label: "Apollo.io pricing", href: "https://www.apollo.io/pricing" },
    { label: "Apollo.io", href: "https://www.apollo.io/" },
  ],
};

const INSTANTLY: Comparison = {
  slug: "instantly",
  competitor: "Instantly",
  title: "Vectorae vs Instantly: one-to-one outreach or cold email at scale?",
  description:
    "Vectorae vs Instantly: researched, one-to-one emails sent from your Gmail after daily approval, or high-volume cold email across many inboxes. Features, pricing and when to choose each.",
  h1: "Vectorae vs Instantly",
  shortAnswer:
    `Instantly is cold-email software for scaling outreach across many email accounts, with built-in warmup; you bring the strategy, the list (or buy lead credits) and the copy. ${CANONICAL} Instantly is built for volume. Vectorae is built for a founder who wants fewer, researched emails written for them.`,
  bestFor: "Agencies and senders running high-volume cold email across many inboxes",
  sections: [
    whenVectorae(["You want to send from your own Gmail at low volume: at most 100 emails a day per mailbox by default.", `You would like to pay per booked meeting (${MEETING_PRICE}) instead of per month.`]),
    {
      heading: "When to choose Instantly",
      blocks: [
        {
          kind: "ul",
          items: [
            "You or your agency send thousands of cold emails a month across many inboxes.",
            "You need a warmup network, inbox rotation and many sending accounts.",
            "You already have a strategy, a list source and copy that works.",
            "You want an AI agent that answers replies and shares a booking link.",
            "You are price-sensitive: Instantly's entry plan costs much less per month.",
          ],
        },
      ],
    },
    {
      heading: "Feature comparison",
      blocks: [
        {
          kind: "table",
          head: ["Feature", "Vectorae", "Instantly"],
          rows: [
            ["Starting point", "Describe what you sell", "Connect mailboxes, upload or find leads"],
            ["Research and strategy", "72/144-step pipeline and 10 verification checks", "Not included"],
            ["Lead sourcing", "Included and email-verified", "Lead database through separate credits"],
            ["Writing", "One-to-one, claims checked", "AI sequence writer and templates"],
            ["Mailboxes", "One Gmail mailbox per account", "Unlimited email accounts"],
            ["Volume", "10 a day rising to 100 a day", "Up to 500,000 emails a month on its top listed plan"],
            ["Warmup", "Gradual ramp only", "Built-in warmup network"],
            ["Replies", "Classified and routed to you; no auto-reply", "Unified inbox; AI reply agent"],
            ["Daily approval before sending", "Required", "Not a default step"],
          ],
        },
      ],
    },
    {
      heading: "Pricing comparison",
      blocks: [
        {
          kind: "table",
          head: ["", "Vectorae", "Instantly"],
          rows: [
            ["Plans", LP_PRICE, "Outreach plans: Growth $47, Hypergrowth $97, Light Speed $358 per month, flat (about 20% less billed annually)"],
            ["Leads and CRM", "Included", "Lead credits and the CRM are separate subscriptions"],
            ["Pay per result", `${MEETING_PRICE} per booked meeting`, "None"],
          ],
        },
        { kind: "p", text: checkoutSentence() },
      ],
    },
    {
      heading: "Verdict: which is better for which use case",
      blocks: [
        {
          kind: "table",
          head: ["Use case", "Better choice"],
          rows: [
            ["Agency running high-volume campaigns for clients", "Instantly"],
            ["Founder with no list, no copy and no time", "Vectorae"],
            ["Team with proven copy that wants cheap sending at scale", "Instantly"],
            ["Firm that wants every day's sending approved and no AI replying", "Vectorae"],
            ["Firm that wants to pay only when a meeting is booked", "Vectorae"],
          ],
        },
      ],
    },
  ],
  sources: [
    { label: "Instantly pricing", href: "https://instantly.ai/pricing" },
    { label: "Instantly", href: "https://instantly.ai/" },
  ],
};

const SMARTLEAD: Comparison = {
  slug: "smartlead",
  competitor: "Smartlead",
  title: "Vectorae vs Smartlead: for the founder who sells or the agency that sends",
  description:
    "Vectorae vs Smartlead: an AI outbound tool for a founder selling their own services, or cold-email infrastructure for agencies sending for many clients. Features, pricing and when to choose each.",
  h1: "Vectorae vs Smartlead",
  shortAnswer:
    `Smartlead is cold-email sending infrastructure popular with lead-generation agencies: unlimited mailboxes, warmup, client workspaces and an API. ${CANONICAL} If you run outbound as a service for many clients, Smartlead fits better. If you sell your own services and the founder does the selling, Vectorae fits better.`,
  bestFor: "Lead-generation agencies sending at volume for many clients",
  sections: [
    whenVectorae(["You want compliance defaults enforced in code: unsubscribe headers and a suppression re-check before every send.", "You want booking pages included, and the option to pay per booked meeting."]),
    {
      heading: "When to choose Smartlead",
      blocks: [
        {
          kind: "ul",
          items: [
            "You run a lead-generation agency sending campaigns for many clients and need client workspaces.",
            "You send at volume across many mailboxes and need warmup and inbox rotation.",
            "You have your own list sources and copywriting process.",
            "You want a low monthly price for sending infrastructure and a mature API.",
          ],
        },
      ],
    },
    {
      heading: "Feature comparison",
      blocks: [
        {
          kind: "table",
          head: ["Feature", "Vectorae", "Smartlead"],
          rows: [
            ["Built for", "Founder-led firms selling their own services", "Agencies and teams sending at volume"],
            ["Research and strategy", "Included", "Not included"],
            ["Leads", "Sourced and verified for you", "Bring your own, or its lead finder"],
            ["Writing", "One-to-one, claims checked", "Your copy, with AI tools"],
            ["Mailboxes and volume", "One Gmail mailbox, at most 100 a day", "Unlimited mailboxes; monthly email allowance by plan"],
            ["Client workspaces", "Agency workspaces built, not yet confirmed working end to end; no white-label", "Client workspaces (add-on)"],
            ["Booking", "Own booking pages and Calendly links", "Not a core feature"],
            ["Daily approval before sending", "Required", "Not a default step"],
          ],
        },
      ],
    },
    {
      heading: "Pricing comparison",
      blocks: [
        {
          kind: "table",
          head: ["", "Vectorae", "Smartlead"],
          rows: [
            ["Plans", LP_PRICE, "Base $39 and Pro $94 per month, flat (about 17% less billed annually); higher-volume plans and client workspaces cost more"],
            ["Pay per result", `${MEETING_PRICE} per booked meeting`, "None"],
          ],
        },
        { kind: "p", text: checkoutSentence() },
      ],
    },
    {
      heading: "Verdict: which is better for which use case",
      blocks: [
        {
          kind: "table",
          head: ["Use case", "Better choice"],
          rows: [
            ["Lead-generation agency sending for 10 or more clients", "Smartlead"],
            ["Web or marketing agency that needs its own new clients", "Vectorae"],
            ["Team with proven copy that wants cheap sending", "Smartlead"],
            ["Founder who wants research and writing done, and approves sending", "Vectorae"],
          ],
        },
      ],
    },
  ],
  sources: [
    { label: "Smartlead pricing", href: "https://www.smartlead.ai/pricing" },
    { label: "Smartlead", href: "https://www.smartlead.ai/" },
  ],
};

const CLAY: Comparison = {
  slug: "clay",
  competitor: "Clay",
  title: "Vectorae vs Clay: an outbound outcome or a data workbench?",
  description:
    "Vectorae vs Clay: a fixed flow from what you sell to booked meetings, or a flexible enrichment and workflow platform you build yourself. Features, pricing and when to choose each.",
  h1: "Vectorae vs Clay",
  shortAnswer:
    `Clay is a data enrichment and workflow platform: it pulls from many data providers, runs AI research per row, and you design and maintain the workflow. ${CANONICAL} Clay gives skilled operators a powerful workbench; Vectorae gives a busy owner a fixed flow that ends in booked meetings.`,
  bestFor: "GTM engineers and ops teams who want full control over data and workflows",
  sections: [
    whenVectorae(["Nobody on your team wants to build or maintain enrichment tables and workflows.", "You want flat monthly pricing or pay per booked meeting rather than credits."]),
    {
      heading: "When to choose Clay",
      blocks: [
        {
          kind: "ul",
          items: [
            "You have a GTM engineer or ops person who wants full control over data and logic.",
            "You need data from many providers (Clay cites more than 150), custom signals, or enrichment at scale.",
            "You want to feed enriched data into tools you already use, such as your CRM or sequencer.",
            "You need flexibility more than simplicity.",
          ],
        },
      ],
    },
    {
      heading: "Feature comparison",
      blocks: [
        {
          kind: "table",
          head: ["Feature", "Vectorae", "Clay"],
          rows: [
            ["Model", "Fixed end-to-end flow", "Build-your-own tables and workflows"],
            ["Setup", "Describe what you sell", "Design tables, prompts and integrations"],
            ["Research", "Strategy-level pipeline (72/144 steps) and 10 checks", "Per-row AI web research"],
            ["Data", "Sourcing and email verification (waterfall enrichment built, off by default)", "Many data providers with waterfall enrichment"],
            ["Sending", "From your Gmail after daily approval", "Its own sequencer, or the tools you connect"],
            ["Booking", "Own booking pages and Calendly links", "Not a core feature"],
            ["Learning curve", "Low", "Higher"],
          ],
        },
      ],
    },
    {
      heading: "Pricing comparison",
      blocks: [
        {
          kind: "table",
          head: ["", "Vectorae", "Clay"],
          rows: [
            ["Plans", LP_PRICE, "Free plan; paid plans are priced by credits and actions. Published figures differ by billing period, so check [Clay's pricing page](https://www.clay.com/pricing)"],
            ["Pay per result", `${MEETING_PRICE} per booked meeting`, "None"],
          ],
        },
        { kind: "p", text: checkoutSentence() },
      ],
    },
    {
      heading: "Verdict: which is better for which use case",
      blocks: [
        {
          kind: "table",
          head: ["Use case", "Better choice"],
          rows: [
            ["Growth team with a GTM engineer", "Clay"],
            ["Agency owner with no ops person", "Vectorae"],
            ["Complex, signal-driven targeting across many data sources", "Clay"],
            ["A simple path from what you sell to booked meetings", "Vectorae"],
          ],
        },
      ],
    },
  ],
  sources: [
    { label: "Clay pricing", href: "https://www.clay.com/pricing" },
    { label: "Clay", href: "https://www.clay.com/" },
  ],
};

const ARTISAN: Comparison = {
  slug: "artisan",
  competitor: "Artisan",
  title: "Vectorae vs Artisan (Ava) and hiring an SDR",
  description:
    "Vectorae vs Artisan's AI BDR Ava, and vs hiring an SDR: approve-first AI outbound with human replies, an autonomous AI BDR, or a person. When to choose each, with SDR cost data.",
  h1: "Vectorae vs Artisan, and vs hiring an SDR",
  shortAnswer:
    `Artisan sells Ava, an autonomous AI BDR that sources, writes, follows up and handles replies on its own. ${CANONICAL} The real difference is control: Vectorae waits for your approval every day and never replies to prospects for you, while Ava is designed to run without you.`,
  bestFor: "Teams that want an autonomous AI BDR, including AI-handled replies",
  sections: [
    whenVectorae([
      "Your firm's reputation depends on a human voice in conversations: agencies, consultancies, professional services.",
      `You want to pay only for booked meetings (${MEETING_PRICE} each) or a flat monthly price per account.`,
    ]),
    {
      heading: "When to choose Artisan",
      blocks: [
        {
          kind: "ul",
          items: [
            "You want maximum autonomy, including AI follow-ups and objection handling in replies.",
            "You want a large built-in contact database, a dialer and multichannel reach today.",
            "You value a larger, funded vendor with an established brand.",
          ],
        },
      ],
    },
    {
      heading: "When to hire an SDR instead",
      blocks: [
        {
          kind: "p",
          text: "A human SDR can call, handle live objections and build relationships, which no tool replaces. The Bridge Group's 2025 SDR report (351 B2B companies) gives a median SDR on-target earnings of $80K, an average ramp time of 3.0 months, an average tenure of 1.9 years, and 60% of SDRs at quota ([Bridge Group](https://www.bridgegroupinc.com/research/2025-sdr-models-metrics-report-the-bridge-group)). Hiring makes sense once you know which segment buys and have someone to manage the hire.",
        },
      ],
    },
    {
      heading: "Feature comparison",
      blocks: [
        {
          kind: "table",
          head: ["Feature", "Vectorae", "Artisan (Ava)"],
          rows: [
            ["Positioning", "Approve-first AI outbound", "Autonomous AI BDR"],
            ["Research", "72/144-step strategy pipeline and 10 verification checks", "Prospect research and personalization"],
            ["Data", "Sourced per strategy, email-verified", "Built-in contact database"],
            ["Sending control", "Daily approval required; send-review queue for high-risk messages", "Runs on autopilot"],
            ["Replies", "Classified and routed to you; never auto-replies", "AI handles follow-ups and objections"],
            ["Channels", "Email live; WhatsApp live with opt-in; LinkedIn not live", "Email and more channels"],
            ["Track record", "New (public site live since around September 2026); no public reviews yet", "Established vendor"],
          ],
        },
      ],
    },
    {
      heading: "Pricing comparison",
      blocks: [
        {
          kind: "table",
          head: ["", "Vectorae", "Artisan", "Human SDR"],
          rows: [
            ["Model", LP_PRICE, "Plans through its sales team; see [Artisan's pricing page](https://www.artisan.co/pricing)", "Salary plus variable pay"],
            ["Typical cost", `${tierRange()} a month, or ${MEETING_PRICE} per booked meeting`, "Quoted", "Median OTE $80K a year (Bridge Group, 2025)"],
          ],
        },
        { kind: "p", text: checkoutSentence() },
      ],
    },
    {
      heading: "Verdict: which is better for which use case",
      blocks: [
        {
          kind: "table",
          head: ["Use case", "Better choice"],
          rows: [
            ["AI that runs outbound and replies with minimal oversight", "Artisan"],
            ["AI research and writing, with a human gate and a human voice in replies", "Vectorae"],
            ["Proven segment, budget for a hire, and someone to manage it", "Human SDR"],
            ["Firm that wants to pay only for booked meetings", "Vectorae"],
          ],
        },
      ],
    },
  ],
  sources: [
    { label: "Artisan pricing", href: "https://www.artisan.co/pricing" },
    { label: "Artisan", href: "https://www.artisan.co/" },
    { label: "Bridge Group 2025 SDR Metrics Report", href: "https://www.bridgegroupinc.com/research/2025-sdr-models-metrics-report-the-bridge-group" },
  ],
};

export const COMPARISONS: Comparison[] = [APOLLO, INSTANTLY, SMARTLEAD, CLAY, ARTISAN];

export function comparison(slug: string): Comparison {
  const found = COMPARISONS.find((c) => c.slug === slug);
  if (!found) throw new Error(`unknown comparison ${slug}`);
  return found;
}

export function comparisonSections(c: Comparison): Section[] {
  return [...c.sections, sourcesSection(c.sources)];
}

export const comparisonPath = (slug: Comparison["slug"]) => `/compare/leadpilot-vs-${slug}/` as const;
