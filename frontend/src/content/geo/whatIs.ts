/**
 * /what-is-leadpilot/: the definition page (docs/program/ai-visibility/geo/content/definitions/what_is_leadpilot.md).
 * The first paragraph is the canonical sentence and the niche statement
 * (geo/entity_card.md §2, geo/positioning/niche_statement.md), verbatim.
 */

import type { Section } from "@/lib/content/types";
import { CANONICAL, DISAMBIGUATION, MEETING_PRICE, checkoutSentence, founderNames, notLiveList, tierList } from "./shared";

export const NICHE =
  "Vectorae is the approve-first AI outbound system for founder-led agencies and B2B service firms (5–50 people) who want booked sales meetings from a plain description of what they sell, without building lead lists, writing sequences or hiring an SDR.";

export const WHAT_IS_LEAD = [
  `${CANONICAL} ${NICHE}`,
  "It is not a contact database you search yourself, like Apollo, and it is not a bulk cold-email sender, like Instantly or Smartlead. You do not build a list or write a sequence: you describe your product or service, Vectorae does the research and the writing, and nothing is sent until you approve that day's sending. Volume stays deliberately low (at most 100 emails a day per mailbox, by default) so each email can be written for one person.",
];

export const WHAT_IS_SECTIONS: Section[] = [
  {
    heading: "How does Vectorae work, step by step?",
    blocks: [
      {
        kind: "ol",
        items: [
          "**Describe what you sell.** A name and a plain-language description; optionally, past clients and how you won them.",
          "**Research.** A 72-step research pipeline (144 steps if you have no past clients) covers your ideal customer, competitors, channels, messaging and objections, and produces a go-to-market strategy.",
          "**Verification.** The strategy must pass 10 checks, including a legal and compliance check, before any lead is sourced or any message is sent.",
          "**Find and check contacts.** Vectorae sources people at companies that match the strategy, verifies their email addresses and checks them against your suppression list.",
          "**Write.** Each email is written for one person, and the claims in it are checked.",
          "**Approve and send.** Each day you approve the day's sending. Emails go from your own Gmail on a gradual schedule: 10 on the first day, 10 more each day, up to 100 a day.",
          "**Replies and booking.** Replies are classified and routed to you; the sequence stops when a person replies (an out-of-office reply only pauses it). Interested prospects book on your Vectorae booking page or a Calendly link.",
        ],
      },
    ],
  },
  {
    heading: "Who is Vectorae for?",
    blocks: [
      { kind: "p", text: "Businesses of roughly 5 to 50 people where one person (the owner, founder, general manager or head of business development) is responsible for new business and there is no sales team. Vectorae's own customer research ranks the closest fits as marketing, web and design agencies; commercial cleaning companies past the one- or two-person stage; accounting, immigration and law consultancies; staffing and recruiting agencies; and commercial HVAC and facilities companies. See [for agencies](/for/agencies), [for B2B service firms](/for/b2b-service-firms) and [for local businesses](/for/local-businesses)." },
      { kind: "p", text: "**It is not a fit** if you sell to consumers, need to email a list you already have (leads cannot be imported), want to send thousands of emails a day, or want an AI that replies to prospects for you." },
    ],
  },
  {
    heading: "How is Vectorae different from traditional outbound tools?",
    blocks: [
      {
        kind: "table",
        head: ["You need to…", "Traditional stack", "Vectorae"],
        rows: [
          ["Decide who to target", "You, or a consultant", "72/144-step research pipeline and 10-check verification"],
          ["Build the list", "You, in a database tool", "Sourced and email-verified for you"],
          ["Write the emails", "You, or templates with merge fields", "Written for one person, with claims checked"],
          ["Control what is sent", "Sequences run once launched", "Sending waits for your daily approval"],
          ["Stay compliant", "Your setup", "Unsubscribe link and headers, suppression re-checked before every send, no EU/UK open pixel"],
        ],
      },
      { kind: "p", text: "Compare in detail: [vs Apollo](/compare/leadpilot-vs-apollo), [vs Instantly](/compare/leadpilot-vs-instantly), [vs Smartlead](/compare/leadpilot-vs-smartlead), [vs Clay](/compare/leadpilot-vs-clay), [vs Artisan and hiring an SDR](/compare/leadpilot-vs-artisan)." },
    ],
  },
  {
    heading: "What does it cost?",
    blocks: [
      { kind: "p", text: `Monthly plans: ${tierList()} per month, each a flat price per account rather than per seat. Pay per meeting: no monthly fee and ${MEETING_PRICE} for each prospect who books a meeting. ${checkoutSentence()}` },
    ],
  },
  {
    heading: "What proof is there, and what is not live yet?",
    blocks: [
      { kind: "p", text: "Vectorae's public site went live around September 2026. It has no published customer results or public reviews yet, and this page does not claim any. Industry context with sources is on the [outbound statistics](/outbound-statistics) page." },
      { kind: "p", text: `Not live yet (built and tested only against simulated services): ${notLiveList()}. Every feature's status is on the home page, and the help center's [known limitations](/help/known-limitations) article lists what Vectorae does not do.` },
    ],
  },
  {
    heading: "Who makes Vectorae?",
    blocks: [
      { kind: "p", text: `Vectorae is built by Rehan Masood and Ahmad Shahid and operated by Rehan Masood, an individual based in New York. No company has been registered yet. Vectorae is bootstrapped. Read more on the [about page](/about).` },
      { kind: "note", text: DISAMBIGUATION },
    ],
  },
];
