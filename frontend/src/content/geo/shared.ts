/**
 * Facts the GEO pages repeat, built from the same sources the rest of the
 * site uses, so a change in one place updates every page:
 *   - prices: src/content/plans-snapshot.json (from app/core/billing_catalog.py)
 *   - capability status, founders, checkout: src/content/product-facts.json
 * Docs: docs/program/ai-visibility/geo/ (drafts, entity card, decisions).
 */

import snapshot from "@/content/plans-snapshot.json";
import { CHECKOUT_LIVE, PRODUCT_FACTS, PRODUCT_FULL_NAME, capabilitiesWithStatus } from "@/lib/productFacts";

/** Date the competitor facts on the comparison pages were last checked. */
export const COMPETITORS_CHECKED = "2026-10-16";
/** Date the product facts on the GEO pages were last checked against the code. */
export const FACTS_CHECKED = "2026-10-16";

const usd = (cents: number) => `$${(cents / 100).toLocaleString("en-US")}`;

const catalog = snapshot.catalog;
export const TIERS = catalog.monthly.tiers.map((t) => ({ name: t.name, price: usd(t.price_cents) }));
export const MEETING_PRICE = usd(catalog.pay_per_meeting.price_per_meeting_cents);
export const MEETING_GRACE_HOURS = catalog.pay_per_meeting.grace_hours;

/** "Starter $149, Growth $349, Scale $699 and Enterprise $1,499" */
export function tierList(): string {
  const parts = TIERS.map((t) => `${t.name} ${t.price}`);
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** "$149 to $1,499" */
export function tierRange(): string {
  return `${TIERS[0].price} to ${TIERS[TIERS.length - 1].price}`;
}

/** Online checkout status, said the same way everywhere. */
export function checkoutSentence(): string {
  return CHECKOUT_LIVE
    ? "You can choose a plan and pay online on the Pricing page."
    : "Online checkout is not live yet; to start, [book a call](/contact).";
}

/** The free-trial answer, true before and after checkout goes live. */
export function trialAnswer(): string {
  return CHECKOUT_LIVE
    ? `Yes. Each monthly plan starts with a ${catalog.trial_days}-day free trial, once per account. A free account also exists with tight limits (3 strategies, 100 leads per strategy, email only).`
    : "Not online yet: free trials start through online checkout, which is not live yet, so the website does not offer one today. A free account exists with tight limits (3 strategies, 100 leads per strategy, email only). To try Vectorae on your real market, [book a call](/contact) with the founders.";
}

/** The canonical one-sentence description (entity card §2). */
export const CANONICAL = PRODUCT_FACTS.description;

/** "Vectorae (calendarharvest.com) is not affiliated with LeadPilot AB (leadpilot.com)…" */
export const DISAMBIGUATION = `${PRODUCT_FULL_NAME} (calendarharvest.com) is not affiliated with LeadPilot AB (leadpilot.com), a Swedish company that also sells B2B prospecting software, or with other products named LeadPilot or Lead Pilot.`;

/** "Rehan Masood and Ahmad Shahid" */
export function founderNames(): string {
  const names = PRODUCT_FACTS.founders.map((f) => f.name);
  return names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : names[0];
}

const names = (status: Parameters<typeof capabilitiesWithStatus>[0]) =>
  capabilitiesWithStatus(status).map((c) => c.name);

/** Capability lists by status, for "what is live" answers. */
export function liveList(): string {
  return names("live").join("; ");
}
export function unconfirmedList(): string {
  return names("unconfirmed").join("; ");
}
export function notLiveList(): string {
  return names("simulated_only").join("; ");
}
export function offByDefaultList(): string {
  return names("off_by_default").join("; ");
}

/** Comparison pages: who wrote them. */
export const FOUNDER_DISCLOSURE =
  "Written by Vectorae's founders. We make Vectorae, so weigh this accordingly; each section says where the other tool is the better choice.";
