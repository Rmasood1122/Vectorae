/**
 * app/what-is-leadpilot/page.tsx — the definition page (/what-is-leadpilot/).
 * Static server component; copy in src/content/geo/whatIs.ts.
 */

import type { Metadata } from "next";

import { ContentPage } from "@/components/content/ContentPage";
import { FACTS_CHECKED } from "@/content/geo/shared";
import { WHAT_IS_LEAD, WHAT_IS_SECTIONS } from "@/content/geo/whatIs";
import { PRODUCT_FULL_NAME } from "@/lib/productFacts";
import { canonicalMeta } from "@/lib/seo/pageMeta";
import { pageGraph } from "@/lib/seo/structuredData";

export const metadata: Metadata = {
  title: "What is Vectorae? AI outbound for agencies and B2B service firms",
  description: `What ${PRODUCT_FULL_NAME} is, how it works step by step, who it is for and not for, what it costs, and what is not live yet.`,
  ...canonicalMeta("/what-is-leadpilot/"),
};

export default function WhatIsVectoraePage() {
  return (
    <ContentPage
      jsonLd={pageGraph({ path: "/what-is-leadpilot/", breadcrumb: "What is Vectorae", software: true })}
      eyebrow="About the product"
      h1={`What is ${PRODUCT_FULL_NAME}?`}
      lead={WHAT_IS_LEAD}
      lastChecked={FACTS_CHECKED}
      sections={WHAT_IS_SECTIONS}
      related={[
        { href: "/faq", label: "Vectorae FAQ" },
        { href: "/compare", label: "Compare Vectorae" },
        { href: "/pricing", label: "Pricing" },
      ]}
    />
  );
}
