/**
 * app/faq/page.tsx — /faq/: 30 questions, with FAQPage JSON-LD built from the
 * same answers the page shows (src/content/geo/faq.ts).
 */

import type { Metadata } from "next";

import { ContentPage } from "@/components/content/ContentPage";
import { FAQ_ALL, FAQ_SECTIONS } from "@/content/geo/faq";
import { FACTS_CHECKED } from "@/content/geo/shared";
import { plainText } from "@/lib/content/richText";
import { PRODUCT_FULL_NAME } from "@/lib/productFacts";
import { canonicalMeta } from "@/lib/seo/pageMeta";
import { pageGraph } from "@/lib/seo/structuredData";

export const metadata: Metadata = {
  title: "Vectorae FAQ: 30 questions answered",
  description: `Straight answers about ${PRODUCT_FULL_NAME}: what it is, how it works, what is live, how it compares with Apollo, Instantly and Clay, and what it costs.`,
  ...canonicalMeta("/faq/"),
};

export default function FaqPage() {
  return (
    <ContentPage
      jsonLd={pageGraph({
        path: "/faq/",
        breadcrumb: "FAQ",
        faq: FAQ_ALL.map((item) => ({ q: item.q, a: plainText(item.a) })),
      })}
      eyebrow="FAQ"
      h1={`${PRODUCT_FULL_NAME}: frequently asked questions`}
      lead={["Each answer starts with the direct answer. Anything that is not live yet says so."]}
      lastChecked={FACTS_CHECKED}
      sections={FAQ_SECTIONS}
      related={[
        { href: "/what-is-leadpilot", label: "What is Vectorae?" },
        { href: "/help", label: "Help center" },
        { href: "/compare", label: "Compare Vectorae" },
      ]}
    />
  );
}
