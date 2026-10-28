/**
 * /outbound-statistics/: sourced industry figures (docs/program/ai-visibility/geo/content/statistics_and_proof.md).
 * Every figure links its source; vendor-published figures say so. S7 of the
 * draft (sourced only to Vectorae's own ICP notes) is left out. No Vectorae
 * performance figure appears: none has been supplied yet (founder answer Q-06).
 */

import type { Section } from "@/lib/content/types";
import { MEETING_PRICE, tierList } from "./shared";

export const STATS_LEAD = [
  "Sourced figures on how B2B buyers respond to outreach, where agencies get clients, what an SDR costs, the rules email senders must meet, and the penalties for non-compliant outreach.",
  "Compiled by the makers of Vectorae. Each figure links to its source. Figures published by companies that sell outreach software are marked (vendor) because they may favour that vendor, and reply-rate benchmarks measure different things, so do not compare them directly.",
];

const G1 = "https://www.gartner.com/en/newsroom/press-releases/2025-06-25-gartner-sales-survey-finds-61-percent-of-b2b-buyers-prefer-a-rep-free-buying-experience";
const G2 = "https://www.gartner.com/en/newsroom/press-releases/2026-03-09-gartner-sales-survey-finds-67-percent-of-b2b-buyers-prefer-a-rep-free-experience";
const BG = "https://www.bridgegroupinc.com/research/2025-sdr-models-metrics-report-the-bridge-group";
const HUNTER = "https://hunter.io/the-state-of-cold-email";
const INSTANTLY = "https://instantly.ai/blog/email-sequence-benchmarks-2026-whats-a-good-open-rate-reply-rate-and-cost-per-meeting/";

const table = (rows: [string, string, string][]): Section["blocks"][number] => ({
  kind: "table",
  head: ["Figure", "What it measures", "Source"],
  rows,
});

export const STATS_SECTIONS: Section[] = [
  {
    heading: "How B2B buyers respond to sellers",
    blocks: [
      table([
        ["61%", "B2B buyers who prefer an overall rep-free buying experience (Gartner survey of 632 buyers, Aug–Sep 2024)", `[Gartner, June 2025](${G1})`],
        ["73%", "B2B buyers who actively avoid suppliers that send irrelevant outreach (same survey)", `[Gartner, June 2025](${G1})`],
        ["67%", "B2B buyers who prefer a rep-free experience (Gartner survey of 646 buyers, Aug–Sep 2025)", `[Gartner, March 2026](${G2})`],
        ["45%", "B2B buyers who used AI during a recent purchase (same 2025 survey)", `[Gartner, March 2026](${G2})`],
      ]),
      { kind: "p", text: "Buyers punish irrelevant messages, so a few researched, relevant emails beat volume. That is the reason Vectorae writes each email for one person and caps sending at 100 a day per mailbox by default." },
    ],
  },
  {
    heading: "Where agencies get new clients",
    blocks: [
      table([
        ["66%", "Agencies naming referrals from existing and past clients as their top source of new business (612 agency owners and consultants, 2024)", "[SparkToro, 2024](https://sparktoro.com/blog/the-state-of-digital-agencies-in-2024-client-referrals-drive-agency-new-business-but-is-it-enough-and-are-sales-pipelines-a-cause-for-concern)"],
        ["Top source", "Referrals remained the top source of new business in the 2025 edition of the same survey", "[SparkToro, 2025](https://sparktoro.com/blog/digital-agency-new-business-still-a-concern-referrals-still-rule-for-lead-gen-and-are-sales-pipelines-finally-recovering/)"],
      ]),
    ],
  },
  {
    heading: "What a human SDR costs",
    blocks: [
      table([
        ["$80K", "Median SDR on-target earnings, unchanged since 2022 (351 B2B companies, 2025)", `[Bridge Group, 2025](${BG})`],
        ["3.0 months", "Average SDR ramp time", `[Bridge Group, 2025](${BG})`],
        ["1.9 years", "Average SDR tenure", `[Bridge Group, 2025](${BG})`],
        ["60%", "SDRs at quota, the lowest in the study's history", `[Bridge Group, 2025](${BG})`],
        ["70%", "Share of reps' time spent on administrative work and meeting preparation rather than selling (5,500 sales professionals, 2024) (vendor)", "[Salesforce, 2024](https://www.salesforce.com/news/stories/sales-ai-statistics-2024/)"],
        ["54%", "Sellers who say they have used AI agents; nearly 9 in 10 plan to by 2027 (4,050 sales professionals, 2026) (vendor)", "[Salesforce, 2026](https://www.salesforce.com/news/stories/state-of-sales-report-announcement-2026/)"],
      ]),
    ],
  },
  {
    heading: "Cold email benchmarks",
    blocks: [
      table([
        ["4.5%", "Average cold email sequence reply rate in 2025, up from 4.1% in 2024, across 31M emails (vendor)", `[Hunter.io](${HUNTER})`],
        ["+68%", "Higher reply rate for campaigns without open tracking (7.4% vs 4.4%), 2025 (vendor)", `[Hunter.io](${HUNTER})`],
        ["3.43%", "Platform-wide average reply rate; top quartile 5.5%+, top 10% 10.7%+ (2025 sends) (vendor)", `[Instantly](${INSTANTLY})`],
        ["58%", "Share of all replies that come from the first email in a sequence (vendor)", `[Instantly](${INSTANTLY})`],
        ["8.4%", "Reply rate of a single-email sequence, the highest, declining with each follow-up (16.5M emails, 2024 data) (vendor: outbound agency)", "[Belkins](https://belkins.io/blog/sales-follow-up-statistics)"],
        ["69%", "Respondents (508 professionals) who said their cold email performance declined year over year, 2025 (vendor)", "[Mailshake, 2025](https://assets.mailshake.com/wp-content/uploads/2025/04/16091740/Cold-Email-Report-2025-Mailshake.pdf)"],
      ]),
    ],
  },
  {
    heading: "Email sending rules",
    blocks: [
      table([
        ["0.3%", "Gmail: keep the spam rate reported in Postmaster Tools below 0.3%, and aim for below 0.10%", "[Google sender guidelines](https://support.google.com/a/answer/81126)"],
        ["5,000 a day", "Gmail treats senders of more than 5,000 messages a day to Gmail addresses as bulk senders; they must support one-click unsubscribe and honour requests within 2 days", "[Google sender guidelines](https://support.google.com/a/answer/81126)"],
        ["May 5, 2025", "Outlook.com, Hotmail and Live: senders of more than 5,000 emails a day must pass SPF, DKIM and DMARC", "[Microsoft](https://techcommunity.microsoft.com/blog/outlook/strengthening-email-security-outlook-new-requirements-for-high-volume-senders/4399730)"],
      ]),
    ],
  },
  {
    heading: "Penalties for non-compliant outreach",
    blocks: [
      table([
        ["$53,088", "Maximum FTC civil penalty per violation, inflation-adjusted, effective January 17, 2025; under CAN-SPAM each separate non-compliant email can be a violation", "[FTC](https://www.ftc.gov/news-events/news/press-releases/2025/02/ftc-publishes-inflation-adjusted-civil-penalty-amounts-2025)"],
        ["$2.95 million", "Civil penalty paid by Verkada for alleged CAN-SPAM violations (August 2024)", "[US Department of Justice](https://www.justice.gov/opa/pr/295m-penalty-and-permanent-injunction-resolves-lawsuit-against-verkada-inc-alleged-unlawful)"],
        ["€20 million or 4%", "GDPR maximum fine: the higher of €20 million or 4% of worldwide annual turnover (Art. 83)", "[GDPR Art. 83](https://gdpr-text.com/en/read/article-83)"],
        ["About €1.2 billion", "GDPR fines reported across Europe in 2025; €7.1 billion since May 2018", "[DLA Piper, January 2026](https://blogs.dlapiper.com/advocatus/files/2026/01/dla-piper-gdpr-fines-and-data-breach-survey-2026.pdf)"],
        ["C$10 million", "Canada's CASL maximum penalty per violation for an organization (C$1 million for an individual)", "[BLG](https://www.blg.com/en/insights/2020/01/Canadas-Anti-Spam-Legislation-2019-A-Year-in-Review)"],
      ]),
    ],
  },
  {
    heading: "Vectorae specifications (how it works, not results)",
    blocks: [
      table([
        ["72 or 144 steps", "Research pipeline per strategy; 144 when you have no past clients", "[Research pipeline](/help/research-pipeline)"],
        ["10 checks", "Strategy verification, including legal and compliance, before any outreach", "[Strategy verification](/help/strategy-verification)"],
        ["10 → 100 a day", "Sending ramp per Gmail mailbox by default: 10 on day one, +10 a day, cap 100", "[Sending email through Gmail](/help/email-sending-gmail)"],
        [MEETING_PRICE, "Pay per meeting: per prospect who books, charged after 48 hours, waived if cancelled", "[Plans, limits and billing](/help/plans-limits-billing)"],
        ["Monthly plans", `${tierList()} per month, flat per account`, "[Pricing](/pricing)"],
      ]),
      { kind: "p", text: "Vectorae has no published performance results yet. Results will be added here only with their date range and how they were measured." },
    ],
  },
];
