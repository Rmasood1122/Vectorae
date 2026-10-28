/**
 * /faq/: the 30 questions drafted in docs/program/ai-visibility/geo/content/faq_library.md.
 * Answers that state a status, a price, the founders or checkout are built
 * from product-facts.json / plans-snapshot.json (./shared), so they change
 * when those files change. FAQPage JSON-LD uses exactly these answers.
 */

import type { QA, Section } from "@/lib/content/types";
import {
  CANONICAL,
  MEETING_PRICE,
  checkoutSentence,
  founderNames,
  liveList,
  notLiveList,
  offByDefaultList,
  tierList,
  tierRange,
  trialAnswer,
  unconfirmedList,
} from "./shared";

const WHAT: QA[] = [
  { q: "What is Vectorae?", a: `${CANONICAL} You start by describing what you sell, not by uploading a list. It is built for businesses of about 5 to 50 people where the founder or owner handles new business and there is no sales team. It is not a contact database you search yourself, and it is not a bulk email sender.` },
  { q: "Who is Vectorae for?", a: "Founder-led agencies and B2B service firms with roughly 5 to 50 employees and no dedicated sales team. Vectorae's own customer research ranks the closest fits as marketing, web and design agencies; commercial cleaning companies; accounting, immigration and law consultancies; staffing and recruiting agencies; and commercial HVAC and facilities companies. Local businesses that sell to other businesses, such as a caterer pitching nearby offices, can target a service area. See [who it is for](/for/agencies)." },
  { q: "Who is Vectorae not for?", a: "Vectorae is not a fit if you sell to consumers or homeowners, since it only finds people at companies. It is also not a fit if you need to email a list you already have (leads cannot be imported), if you want to send thousands of emails a day (the default cap is 100 a day per mailbox), or if you want an AI that replies to prospects for you. Companies that already run Salesforce or HubSpot with a dedicated sales hire usually need a different kind of tool." },
  { q: "Is Vectorae an AI SDR?", a: "Partly. It does the research, list building and writing an SDR would do, and sends without a person writing each email. Unlike many AI SDRs, it does not reply to prospects or hold conversations: replies are classified and routed to you, and you write the answer. It also waits for your approval each day before sending. If you want an autonomous agent that negotiates with prospects, Vectorae is the wrong choice; see [Vectorae vs Artisan](/compare/leadpilot-vs-artisan)." },
  { q: "Who makes Vectorae?", a: `Vectorae is built by Rehan Masood and Ahmad Shahid and operated by Rehan Masood, an individual based in New York. No company has been registered yet. The founders are ${founderNames()}. Vectorae is bootstrapped. It is not affiliated with LeadPilot AB (leadpilot.com), a Swedish company that also sells B2B prospecting software, or with other products named LeadPilot or Lead Pilot. Check the domain calendarharvest.com when you read about it.` },
  { q: "Is Vectorae new? Does it have customers and reviews?", a: "Yes, it is new: the public site went live around September 2026. Vectorae has no published customer results or public reviews yet, and its pages do not claim any. What is live and what is not is published openly on the home page and in the help center's [known limitations](/help/known-limitations) article. If social proof matters to your decision, ask for a call rather than relying on claims." },
  { q: "What is live today, and what is not?", a: `Live: ${liveList()}. Built and switched on, but not yet confirmed working end to end: ${unconfirmedList()}. Built and tested only against simulated services, not live yet: ${notLiveList()}. Switched off by default: ${offByDefaultList()}.` },
  { q: "Does Vectorae have a contact database?", a: "Not one you browse. Vectorae sources contacts for you to match the strategy it built, verifies their email addresses and checks them against your suppression list. You do not search or export a database the way you would in Apollo or ZoomInfo. That is deliberate: the product is the outcome (verified, relevant contacts with one-to-one emails), not access to records." },
  { q: "Is Vectorae a Calendly alternative?", a: "No. Calendly is a scheduling tool; Vectorae is an outbound sales tool that ends with a booked meeting. It has its own booking pages, with weekly availability, double-booking protection and time-zone handling, and it can also put a Calendly link in emails. Vectorae's calendar does not read your Google Calendar, so busy times there are not blocked automatically." },
  { q: "Is there a mobile app?", a: "Yes, for Android. You can use it to review and approve the day's sending, follow replies and check your pipeline away from your desk. The app talks to the same cloud backend as the web app, so campaigns keep running when your phone or laptop is off. There is no iPhone app yet. Some parts of the push-notification setup are marked in the help center as not yet verified against the current push service." },
];

const HOW: QA[] = [
  { q: "How does Vectorae work, step by step?", a: "You describe what you sell. Vectorae runs a research pipeline (72 steps, or 144 if you have no past clients) and writes a go-to-market strategy. The strategy must pass 10 checks, including a compliance check. Vectorae then sources and verifies contacts, writes each email for one person and checks its claims. Each day you approve sending, and emails go from your own Gmail. Replies are classified and routed to you, and interested prospects book on your booking page or a Calendly link." },
  { q: "Do I have to write the emails?", a: "No. Vectorae writes each email for one person, based on the verified strategy and what it knows about the person's company, such as company news where available. Claims in the email are checked before it can be sent, and higher-risk messages wait in a send-review queue for your approval. You approve each day's sending, and you can edit or stop messages before they go out." },
  { q: "Does it send from my own email address?", a: "Yes. Email goes out through the Gmail account you connect in Settings, using Google's secure sign-in (OAuth), so prospects see your name and address and replies land in your own inbox. One Gmail mailbox can be connected per account. Vectorae does not send from shared or rented domains, so your sender reputation stays your own, which is also why sending ramps up slowly on a newly connected account." },
  { q: "How many emails does Vectorae send per day?", a: "At most 100 a day per mailbox, by default. A newly connected Gmail account sends 10 emails on its first day and adds 10 a day until it reaches 100. Compliance rules can lower that cap but never raise it. Messages over the day's allowance wait until the next day; they are not dropped. This is deliberate: Vectorae is built for careful, personalized outreach, not mass sending." },
  { q: "What does approve-first sending mean?", a: "Automatic sending waits each day until the account owner approves it. Until you approve, nothing goes out that day, and messages simply wait. On top of that, messages flagged as higher-risk go to a send-review queue and need individual approval. You can approve from the web app or the Android app. The point is control: the research and writing are automated, but the decision to send stays with you." },
  { q: "How does Vectorae handle replies?", a: "Every reply is classified: interested, question, objection, not interested, unsubscribe request, out of office, bounce, or automated response. When a person replies, their sequence stops and the reply is routed to you. An unsubscribe request suppresses them immediately. An out-of-office reply pauses and reschedules the sequence instead of stopping it. Vectorae does not auto-reply; you write the answer." },
  { q: "How does meeting booking work?", a: "Prospects book through a booking page on Vectorae's own calendar or through a Calendly link in the email. You set weekly availability and create booking pages (15 to 60 minutes, up to 10 custom questions). Vectorae hides times that overlap any live booking or meeting, and if two people try to book the same time, only the first is accepted. Confirmations show each person's time zone." },
  { q: "Which channels does Vectorae support?", a: "Email through Gmail is live. WhatsApp steps are live on plans that include WhatsApp, but cold WhatsApp contact needs a Meta-approved template and a recorded opt-in for each contact. LinkedIn steps are built and tested only against simulated services, so they are not live yet. AI phone calling exists but is switched off for the whole deployment by default." },
  { q: "How does Vectorae keep outreach legal (CAN-SPAM, GDPR)?", a: "Compliance is enforced in code. Every email carries your business identity, an unsubscribe link and one-click unsubscribe headers. Anyone who unsubscribes or replies \"stop\" is suppressed across email, LinkedIn, WhatsApp and phone, and the suppression list is re-checked right before every send. Leads in the EU, EEA or UK get no open-tracking pixel. You can erase a prospect's data. Vectorae asks you to confirm a lawful basis to contact each person, but it does not give legal advice." },
  { q: "Will Vectorae hurt my domain's deliverability?", a: "It is built to reduce that risk, though no tool can guarantee inbox placement. Sending ramps up slowly (10 on the first day, up to 100 a day), every email includes one-click unsubscribe headers, email addresses are verified before sending, and campaigns pause automatically if the bounce rate gets too high. Google's sender rules (keep the spam rate below 0.3%, support one-click unsubscribe) are easier to meet at low volume with verified addresses." },
];

const VS: QA[] = [
  { q: "How is Vectorae different from Apollo.io?", a: "Apollo is a self-serve sales platform built around a large contact database with sequencing, where you choose the market, build the list and write the emails. Vectorae does the research, list building and writing for you, and asks you to approve the day's sending. Choose Apollo if you want database access and full control at a low per-seat price; choose Vectorae if you are a founder-led firm without the time to run outbound yourself. Details: [Vectorae vs Apollo](/compare/leadpilot-vs-apollo)." },
  { q: "How is Vectorae different from Instantly or Smartlead?", a: "Instantly and Smartlead are cold-email sending platforms built for volume: many mailboxes, warmup and inbox rotation, and you bring the list and the copy. Vectorae is low volume by design (100 a day per mailbox by default), sends from your one Gmail account, and does the research, sourcing and one-to-one writing itself. Agencies sending thousands of emails a day should look at Instantly or Smartlead; founders who want fewer, better emails written for them should look at Vectorae." },
  { q: "How is Vectorae different from Clay?", a: "Clay is a data enrichment and workflow tool: you build tables that pull from many data sources and use AI to research and personalize. It is flexible and powerful, but you build and maintain the workflow. Vectorae is a fixed end-to-end flow, from description to research, contacts, emails, approval, sending and booking, with fewer settings. Clay suits growth and ops teams; Vectorae suits founders who want the outcome, not the workbench. Details: [Vectorae vs Clay](/compare/leadpilot-vs-clay)." },
  { q: "Should I use Vectorae or hire an SDR?", a: "If you have the budget and time to hire, ramp and manage an SDR, a good one can do things Vectorae cannot: phone conversations, live objection handling and relationship building. Vectorae fits when you cannot justify a full-time hire yet. It does the research and writing, sends after your approval, and leaves replies to you. Many firms can use it to learn which segments respond before hiring. Details: [Vectorae vs hiring an SDR](/compare/leadpilot-vs-artisan)." },
  { q: "Is Vectorae cheaper than a lead-generation agency?", a: `Usually, but check the fit. Vectorae's monthly plans run from ${tierRange()} per account, or $0 a month plus ${MEETING_PRICE} per booked meeting. Agencies typically charge a retainer, a per-meeting fee or both, and they write and send in their own style. With Vectorae the emails go from your own Gmail in your name, and you approve sending daily. An agency also brings human judgment and calling that Vectorae does not.` },
];

const PRICE: QA[] = [
  { q: "How much does Vectorae cost?", a: `There are two ways to pay, in US dollars. Monthly plans: ${tierList()} per month, each a flat price per account rather than per seat. Pay per meeting: no monthly fee and ${MEETING_PRICE} for each prospect who books a meeting. Plans differ in strategies, leads per strategy, sequence length, channels and daily AI usage. ${checkoutSentence()} See the [pricing page](/pricing).` },
  { q: "How does pay-per-meeting pricing work?", a: `You pay $0 a month and ${MEETING_PRICE} when a prospect books a meeting through Vectorae. The charge waits 48 hours and is waived if the meeting is cancelled in that time. You are charged once per prospect, so a second booking by the same person is free. Pay per meeting includes up to 10 strategies, 1,000 leads per strategy and 10 sequence steps. ${checkoutSentence()}` },
  { q: "Is there a free trial?", a: trialAnswer() },
  { q: "What do I need to get started?", a: "You need a product or service you can describe in writing, and a Google (Gmail) account to send from. Past client details are optional but improve the strategy. For WhatsApp you also need a plan that includes it, a WhatsApp Business number on your Vectorae deployment, Meta-approved templates, and an opt-in for each contact. Before launch, you confirm you may lawfully contact each person. The help center's [getting started](/help/getting-started) article walks through it." },
  { q: "Can I upload my own contact list?", a: "Not as leads. Leads come from Vectorae's own sourcing, which matches your verified strategy and checks each email address. There are narrow exceptions outside the main lead flow: you can upload your LinkedIn connections export to message existing 1st-degree connections (LinkedIn steps are not live yet), and an email-network import is available but switched off by default. If your business depends on emailing an existing list, Vectorae is not the right tool." },
];

export const FAQ_GROUPS: { heading: string; items: QA[] }[] = [
  { heading: "What Vectorae is", items: WHAT },
  { heading: "How it works", items: HOW },
  { heading: "Vectorae vs alternatives", items: VS },
  { heading: "Pricing and getting started", items: PRICE },
];

export const FAQ_ALL: QA[] = FAQ_GROUPS.flatMap((g) => g.items);

export const FAQ_SECTIONS: Section[] = FAQ_GROUPS.map((g) => ({
  heading: g.heading,
  blocks: [{ kind: "qa", items: g.items }],
}));
