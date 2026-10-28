/**
 * The one source of product facts for the public site (pages, JSON-LD and,
 * later, llms.txt): src/content/product-facts.json.
 *
 * The brand is defined once there (brand.product_name; brand.company_name is
 * the operator, Rehan Masood: no company is registered yet, VEC-NAME).
 * Titles are literal strings in each page.tsx (src/tests/websiteClaims.test.ts
 * reads them from source), so after a rename scripts/seo/check_static_seo.mjs
 * fails every title that lacks the new PRODUCT_FULL_NAME until it is updated.
 *
 * A capability is described as working only when its status is "live", which
 * is a founder answer (docs/program/ai-visibility/NOTES.md), not a flag.
 */

import facts from "@/content/product-facts.json";

export type CapabilityStatus = keyof typeof facts.status_labels;
export type CapabilityId = (typeof facts.capabilities)[number]["id"];

export const PRODUCT_FACTS = facts;
export const PRODUCT_NAME = facts.brand.product_name;
export const COMPANY_NAME = facts.brand.company_name;
/** "Vectorae": how the product is named in titles and first mentions. The
 *  "by Calendarharvest" suffix and the "| Calendarharvest" title site name
 *  were dropped (docs/program/DECISIONS.md VIC-NAME, VEC-NAME): Vectorae is
 *  operated by Rehan Masood, an individual; no company is registered yet. */
export const PRODUCT_FULL_NAME = PRODUCT_NAME;

export function capability(id: CapabilityId) {
  const found = facts.capabilities.find((c) => c.id === id);
  if (!found) throw new Error(`unknown capability ${id}`);
  return { ...found, status: found.status as CapabilityStatus };
}

export function statusLabel(status: CapabilityStatus): string {
  return facts.status_labels[status];
}

export function capabilitiesWithStatus(status: CapabilityStatus) {
  return facts.capabilities.filter((c) => c.status === status);
}

/** True once the founders mark Stripe checkout "live" in product-facts.json.
 *  Until then the site promises no free trial and no online cancellation,
 *  and says checkout opens soon (founders, 2026-10-14). */
export const CHECKOUT_LIVE = capability("stripe_checkout").status === "live";
export const CHECKOUT_SOON = "Online checkout opens soon. To start now, book a call.";

/** "LinkedIn steps and Stripe checkout (…): built and tested only against
 *  simulated services, not yet live." Shown wherever those are mentioned. */
export function notLiveSentence(): string {
  const names = capabilitiesWithStatus("simulated_only").map((c) => c.name);
  return `${names.join(" and ")}: built and tested only against simulated services, not yet live.`;
}
