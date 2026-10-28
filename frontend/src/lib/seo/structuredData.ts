/**
 * schema.org JSON-LD for the public marketing pages.
 *
 * Rules (docs/program/ai-visibility/SPEC.md 2.4):
 *  - Organization and WebSite on every page, with fixed @ids other nodes point at.
 *  - SoftwareApplication only on / and /pricing; no offers until real prices
 *    may be shown (Stripe checkout is not live: see src/lib/seo/staticPlans.ts).
 *  - FAQPage only where the page shows that FAQ; BreadcrumbList only below /.
 *  - The founder Person only on /about, with founder-confirmed facts only.
 *  - Never aggregateRating or review: there are no reviews.
 *  - Values come from src/content/product-facts.json or the page's own visible
 *    text, so the markup says nothing the page does not.
 * scripts/seo/check_static_seo.mjs enforces these on the exported HTML.
 */

import { PRODUCT_FACTS, PRODUCT_FULL_NAME, PRODUCT_NAME } from "@/lib/productFacts";
import { SITE_ORIGIN, absoluteUrl, type SitePath } from "@/lib/seo/pageMeta";

type Node = Record<string, unknown>;

export const ORGANIZATION_ID = `${SITE_ORIGIN}/#organization`;
export const WEBSITE_ID = `${SITE_ORIGIN}/#website`;
const SOFTWARE_ID = `${SITE_ORIGIN}/#software`;

function organization(): Node {
  const org = PRODUCT_FACTS.organization;
  return {
    "@type": "Organization",
    "@id": ORGANIZATION_ID,
    name: org.name,
    url: org.url,
    logo: org.logo,
    // Both founders (founder answer 2026-10-16: "Display Ahmad Shahid and Rehan Masood both as Founder").
    founder: PRODUCT_FACTS.founders.map((f: FounderFact) => ({ "@type": "Person", name: f.name, jobTitle: f.job_title })),
    // Only profiles the founders confirmed (none yet, Phase 0 Q4).
    ...(PRODUCT_FACTS.same_as.length ? { sameAs: PRODUCT_FACTS.same_as } : {}),
  };
}

function website(): Node {
  return {
    "@type": "WebSite",
    "@id": WEBSITE_ID,
    url: `${SITE_ORIGIN}/`,
    name: PRODUCT_NAME,
    publisher: { "@id": ORGANIZATION_ID },
  };
}

function softwareApplication(): Node {
  return {
    "@type": "SoftwareApplication",
    "@id": SOFTWARE_ID,
    name: PRODUCT_FULL_NAME,
    description: PRODUCT_FACTS.description,
    url: `${SITE_ORIGIN}/`,
    applicationCategory: "BusinessApplication",
    operatingSystem: "Web, Android",
    publisher: { "@id": ORGANIZATION_ID },
  };
}

function faqPage(path: SitePath, faq: readonly { q: string; a: string }[]): Node {
  return {
    "@type": "FAQPage",
    "@id": `${absoluteUrl(path)}#faq`,
    mainEntity: faq.map((item) => ({
      "@type": "Question",
      name: item.q,
      acceptedAnswer: { "@type": "Answer", text: item.a },
    })),
  };
}

/** Home (the header's "Vectorae" link) → this page (its nav or footer label). */
function breadcrumbs(path: SitePath, name: string): Node {
  return {
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: PRODUCT_NAME, item: `${SITE_ORIGIN}/` },
      { "@type": "ListItem", position: 2, name, item: absoluteUrl(path) },
    ],
  };
}

/** One Person per founder. Only the fields the facts file holds for that
 *  founder are emitted: nothing is filled in for a founder who gave less. */
function founders(): Node[] {
  return PRODUCT_FACTS.founders.map((f: FounderFact) => ({
    "@type": "Person",
    name: f.name,
    jobTitle: f.job_title,
    ...(f.description ? { description: f.description } : {}),
    ...(f.home_location ? { homeLocation: { "@type": "Place", name: f.home_location } } : {}),
    ...(f.same_as?.length ? { sameAs: f.same_as } : {}),
    worksFor: { "@id": ORGANIZATION_ID },
  }));
}

interface FounderFact {
  name: string;
  job_title: string;
  description?: string;
  home_location?: string;
  same_as?: string[];
}

export interface PageGraphOptions {
  path: SitePath;
  /** Visible nav/footer label for the breadcrumb (pages below / only). */
  breadcrumb?: string;
  software?: boolean;
  faq?: readonly { q: string; a: string }[];
  founders?: boolean;
}

export function pageGraph(opts: PageGraphOptions): Node {
  const graph: Node[] = [organization(), website()];
  if (opts.software) graph.push(softwareApplication());
  if (opts.faq?.length) graph.push(faqPage(opts.path, opts.faq));
  if (opts.breadcrumb && opts.path !== "/") graph.push(breadcrumbs(opts.path, opts.breadcrumb));
  if (opts.founders) graph.push(...founders());
  return { "@context": "https://schema.org", "@graph": graph };
}
