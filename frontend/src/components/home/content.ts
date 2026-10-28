/**
 * Every word on the public home page (src/app/page.tsx), in one place so the
 * claims can be tested against the code (founder decision D1: the website
 * describes what the code does).
 *
 * Sources, so a reviewer can check each sentence:
 *   docs/help-center/01, 02, 06, 08, 12, 13, 14, 15, 20, 21, 22, 26, 29
 *   docs/features/daily-send-approval.md, docs/features/gdpr-erasure.md
 *   app/services/system_settings.py DEFAULTS (feature flags)
 *   src/content/product-facts.json (who it is for, capability status per the
 *     founders' Phase 0 answers, docs/program/ai-visibility/NOTES.md)
 *
 * src/tests/homeClaims.test.ts enforces:
 *   - a feature marked "available" whose flag defaults OFF fails the test
 *     (it must say "available on request" instead), and vice versa;
 *   - none of the test's banned promises (see BANNED there) appears here.
 */

import { CHECKOUT_LIVE, PRODUCT_FACTS, notLiveSentence, statusLabel } from "@/lib/productFacts";

export const HOME_TITLE = "Vectorae: from what you sell to meetings on your calendar";
export const HOME_DESCRIPTION =
  "Describe what you sell. Vectorae researches your market, finds and checks business contacts, " +
  "writes each email for one person, sends on a schedule you approve each day, and routes every reply to you.";

export const SIGNUP_PATH = "/signup";
export const LOGIN_PATH = "/login";
export const PRICING_PATH = "/pricing";
export const CONTACT_PATH = "/contact";
export const HOW_IT_WORKS_ID = "how-it-works";

export const NAV = [
  { href: `#${HOW_IT_WORKS_ID}`, label: "How it works" },
  { href: PRICING_PATH, label: "Pricing" },
  { href: "/about", label: "About" },
  { href: CONTACT_PATH, label: "Contact" },
] as const;

export const HERO = {
  headline: "From what you sell to meetings on your calendar.",
  body:
    "Describe your offer. Vectorae researches your market, finds business contacts and checks " +
    "their email addresses, writes each email for one person, and sends on a schedule you approve " +
    "each day. Replies come to you. Meetings land on your booking calendar.",
  primary: "Start free",
  secondary: "See how it works",
  // The trial starts through Stripe checkout, so it is promised only once checkout is live.
  note: CHECKOUT_LIVE
    ? "Creating an account is free. Monthly plans start with a free trial."
    : "Creating an account is free. Online checkout opens soon; to start a plan now, book a call.",
};

export const VIDEO = {
  mp4: "/media/leadpilot-walkthrough.mp4",
  webm: "/media/leadpilot-walkthrough.webm",
  poster: "/media/leadpilot-walkthrough-poster.jpg",
  captions: "/media/leadpilot-walkthrough.en.vtt",
  label: "Vectorae product walkthrough",
  description: "A 26-second product animation with sample data. It has no sound.",
  /** Must match the cues of public/media/leadpilot-walkthrough.en.vtt, in order. */
  transcript: [
    "Describe what you sell: a name and a plain-language description.",
    "Vectorae researches your market once per strategy, then checks the strategy: 10 of 10 checks passed.",
    "It finds business contacts and verifies each email address. Undeliverable ones are removed.",
    "Each email is written for one person when it is sent, with an unsubscribe link in the footer.",
    "You approve automatic sending once a day. Emails go out in business hours in each prospect's time zone.",
    "Replies come to you, sorted. A reply stops the sequence. An unsubscribe suppresses the person everywhere.",
    "Prospects book meetings on your Vectorae booking page. All names shown are sample data.",
  ],
};

export interface Stage {
  id: string;
  name: string;
  title: string;
  body: string;
  /** Short facts, each one traceable to the code. */
  facts: string[];
  /** What the person using Vectorae decides at this stage. */
  you: string;
}

/** research -> leads -> sequences -> best-time sending -> replies -> meetings */
export const STAGES: Stage[] = [
  {
    id: "research",
    name: "Research",
    title: "Research that runs once per strategy",
    body:
      "Give Vectorae a name and a plain-language description of what you sell. It researches your " +
      "ideal client, market, competitors, channels and messaging, then checks the finished strategy " +
      "against 10 criteria before a single lead is sourced. The legal and compliance check can only " +
      "pass on its own; nobody can wave it through.",
    facts: ["72 research steps, or 144 if you have no past clients yet", "10 verification checks"],
    you: "You describe the offer and, if you can, the clients you have won before.",
  },
  {
    id: "leads",
    name: "Leads",
    title: "Business contacts, checked before they count",
    body:
      "Vectorae searches for people at companies that match the verified strategy, adds what it can " +
      "find about them, and checks every email address. Undeliverable addresses are dropped, and anyone " +
      "on your suppression list is skipped.",
    facts: ["Every email address verified", "Suppressed people never imported"],
    you: "You review the strategy and the leads before outreach launches.",
  },
  {
    id: "sequences",
    name: "Sequences",
    title: "Each email written for one person",
    body:
      "Build a sequence with the steps and delays you want. Each email is written at the moment it is " +
      "sent, from the strategy and what Vectorae stored about that person. Any claim about the " +
      "prospect has to be backed by stored data, or it is removed. Every sequence is reviewed for " +
      "spam and compliance problems before it can launch.",
    facts: ["Claims about prospects checked before sending", "Pre-launch review of every sequence"],
    you: "You set the steps, the delays and, with writing samples, the voice.",
  },
  {
    id: "sending",
    name: "Sending",
    title: "Sent at a good time, after you say yes",
    body:
      "Emails go out from your connected Gmail inside business hours in each prospect's own time zone, " +
      "weekends skipped. Turn on smart send time for a campaign and a step can wait up to 72 hours for " +
      "that campaign's best engagement window. New mailboxes start slowly and stay under a daily cap.",
    facts: ["Automatic sending waits for your approval each day", "By default, a new mailbox sends 10 emails on day one and at most 100 a day"],
    you: "Each day you approve automatic sending, and you approve held high-risk messages one by one.",
  },
  {
    id: "replies",
    name: "Replies",
    title: "Replies come to you, sorted",
    body:
      "Email and WhatsApp replies land in one inbox. Each reply is classified, and the " +
      "prospect's sequence stops on every channel. Vectorae suggests a next step and a draft, but it " +
      "never answers for you.",
    facts: ["A reply stops the sequence everywhere", "An unsubscribe suppresses the person on every channel"],
    you: "You write every answer to a prospect.",
  },
  {
    id: "meetings",
    name: "Meetings",
    title: "Meetings on your calendar",
    body:
      "Prospects book on your own Vectorae booking page, and a booking pauses their sequence.",
    facts: ["Booking pages on your own availability", "A booking pauses the prospect's sequence"],
    you: "You take the call.",
  },
];

/** "not_live": built and tested only against simulated services; "unconfirmed":
 *  built and switched on, but not yet confirmed working end to end in production
 *  (founders, 2026-10-13). Both from product-facts.json. */
export type Availability = "available" | "on_request" | "not_live" | "unconfirmed";

export interface Feature {
  name: string;
  body: string;
  status: Availability;
  /** Key in app/services/system_settings.py DEFAULTS, when a flag controls it. */
  flag?: string;
  /** Plan note when a plan, not a flag, controls it (app/core/plans.py). */
  plan?: string;
}

export const FEATURES: Feature[] = [
  {
    name: "Strategy research and verification",
    body: "A full go-to-market strategy for each offer, checked against 10 criteria before outreach.",
    status: "available",
  },
  {
    name: "Lead scoring",
    body: "Every sourced lead gets an AI estimate of how likely they are to book.",
    status: "unconfirmed",
    flag: "lead_scoring_enabled",
  },
  {
    name: "Personal first lines",
    body:
      "Company news can shape the opening, never invented details. Using a prospect's recent LinkedIn " +
      "posts goes through the LinkedIn connection, which is not live yet.",
    status: "available",
    flag: "linkedin_personalization_enabled",
  },
  {
    name: "Claim checks on every email",
    body: "Statements about the prospect that stored data cannot back are removed before sending.",
    status: "available",
    flag: "claim_verification_enabled",
  },
  {
    name: "Send-review queue",
    body: "Messages to senior people, after an objection, or with risky wording wait for your approval.",
    status: "available",
    flag: "send_review_enabled",
  },
  {
    name: "Daily send approval",
    body: "Automatic sending starts each day only after the account owner approves it.",
    status: "available",
    flag: "daily_send_approval_enabled",
  },
  {
    name: "Unified inbox",
    body: "Every reply in one conversation per prospect, with a suggested next step.",
    status: "available",
  },
  {
    name: "\"Not now\" memory",
    body: "A timing objection becomes a dated return visit with the reason the prospect gave.",
    status: "unconfirmed",
    flag: "reengagement_memory_enabled",
  },
  {
    name: "Booking pages",
    body: "Your own booking pages; a booking pauses the prospect's sequence.",
    status: "available",
  },
  {
    name: "Meeting prep",
    body: "A prep brief for each booked meeting, reminders before the call, and an AI summary afterwards.",
    status: "unconfirmed",
    flag: "meeting_prep_enabled",
  },
  {
    name: "WhatsApp steps",
    body: "Add WhatsApp steps to a sequence on the plans that include WhatsApp.",
    status: "available",
    plan: "WhatsApp from Starter",
  },
  {
    name: "Workspaces for agencies",
    body: "Separate client workspaces, roles for your team, and per-client sending domains for email.",
    status: "unconfirmed",
  },
  {
    name: "Android app",
    body: "The same screens as the web app on an Android phone, with the same sign-in.",
    status: "available",
  },
  // Built, but switched off by default (system_settings.py). Listed honestly.
  {
    name: "Reply Copilot",
    body: "Grounded, claim-checked answer drafts for replies.",
    status: "on_request",
    flag: "reply_copilot_enabled",
  },
  {
    name: "Deliverability Autopilot",
    body: "Launch preflight for SPF, DKIM, DMARC and one-click unsubscribe, plus a mailbox ramp.",
    status: "on_request",
    flag: "deliverability_autopilot_enabled",
  },
  {
    name: "Waterfall enrichment",
    body: "Find a lead's email through several providers in turn.",
    status: "on_request",
    flag: "waterfall_enrichment_enabled",
  },
  {
    name: "Signal Radar",
    body: "Turn news and posts about your prospects into timely reasons to reach out.",
    status: "on_request",
    flag: "signal_radar_enabled",
  },
  {
    name: "Spend Guard",
    body: "A cost forecast for every strategy before it runs.",
    status: "on_request",
    flag: "spend_guard_enabled",
  },
  {
    name: "Start from your website",
    body: "Begin a strategy from your website address instead of a written description.",
    status: "on_request",
    flag: "quickstart_enabled",
  },
  // Built, not live yet: tested only against simulated services (founders,
  // 2026-10-13; product-facts.json status "simulated_only").
  {
    name: "LinkedIn steps",
    body: "Add LinkedIn steps to a sequence, on the plans that include LinkedIn (Growth and up).",
    status: "not_live",
  },
  {
    name: "Stripe checkout",
    body: "Choosing and paying for a plan online.",
    status: "not_live",
  },
];

export const NOT_LIVE_TITLE = statusLabel("simulated_only");
export const UNCONFIRMED_TITLE = statusLabel("unconfirmed");

/** Who it is for, and not for: product-facts.json, shared with /about and the JSON-LD. */
export const AUDIENCES = PRODUCT_FACTS.fit;
export const NOT_A_FIT = PRODUCT_FACTS.not_fit;
/** The one-sentence description, shown on the page and used in the JSON-LD. */
export const PRODUCT_DESCRIPTION = PRODUCT_FACTS.description;

export const TRUST = [
  {
    name: "Unsubscribe in every email",
    body:
      "Every email has a footer with your business identity and an unsubscribe link, plus one-click " +
      "unsubscribe headers. It takes effect immediately, with no login.",
  },
  {
    name: "Suppression on every channel",
    body:
      "An unsubscribe or a \"stop\" reply suppresses the person on email, LinkedIn, WhatsApp and phone. " +
      "The list is checked when leads are sourced and again right before every send.",
  },
  {
    name: "Daily send approval",
    body:
      "On by default. Automatic sending waits each day until the account owner approves it. LinkedIn " +
      "steps, once live, need their own approval.",
  },
  {
    name: "GDPR deletion and export",
    body:
      "Erase a prospect from their lead page, download a copy of your data, and delete your own " +
      "account from your profile.",
  },
];

export const TRUST_NOTE =
  "These are safeguards in the software, not legal advice. You remain responsible for having a lawful basis to contact each person.";

export const PRICING_TEASER = {
  title: "Pay monthly, or pay per booked meeting",
  body:
    "Plans differ by how many strategies and leads you can run, how long a sequence can be, and which " +
    "channels you can use. " +
    (CHECKOUT_LIVE
      ? "Monthly plans start with a free trial and can be cancelled online."
      : "Online checkout opens soon; to start a plan now, book a call."),
  cta: "See pricing",
};

export const FAQ = [
  {
    q: "Do I have to send the messages myself?",
    a:
      "No. Once you launch a sequence and approve the day's sending, Vectorae sends automatically. " +
      "Before every send it re-checks the suppression list, the campaign and contact state, the send " +
      "window and the daily allowance.",
  },
  {
    q: "Will Vectorae reply to prospects for me?",
    a:
      "No. Replies are classified and routed to you, with a suggested next step and a draft you can " +
      "edit. You write and send every answer.",
  },
  {
    q: "Can I upload my own contact list?",
    a:
      "Not as leads. Leads come from Vectorae's own sourcing, which checks every email address. On " +
      "Growth plans and up you can upload your LinkedIn connections export; messaging those " +
      "connections uses LinkedIn steps, which are not live yet.",
  },
  {
    q: "How many emails does it send a day?",
    a:
      "By default, a newly connected Gmail account starts at 10 emails on its first day, adds 10 a day, " +
      "and stops at 100 a day. Messages over the day's allowance wait for the next day; they are not dropped.",
  },
  {
    q: "Does it work outside the United States?",
    a:
      "It works out each lead's region and applies that region's rules: for example, no open-tracking " +
      "pixel for leads in the EU, EEA or UK, and a regional notice in the footer where needed. You are " +
      "still responsible for the outreach rules that apply to you.",
  },
  {
    q: "Is there a mobile app?",
    a:
      "There is an Android app built from the same code as the web app, with the same sign-in. There " +
      "is no iPhone app. Contact us for the current way to install it.",
  },
  {
    q: "What is not live yet?",
    a:
      `${notLiveSentence()} Several other features are built but switched off by default; they ` +
      "are listed above as available on request.",
  },
];

export const CLOSING = {
  title: "Start with what you sell.",
  body: "Create an account, describe your offer, and read the strategy before anything is sent.",
  primary: "Start free",
  secondary: "Book a call",
  points: ["Strategy first, outreach second", "You approve sending each day", "Unsubscribes honoured on every channel"],
};
