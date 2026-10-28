/**
 * app/help/page.tsx — /help/: index of the published help center
 * (src/lib/content/helpCenter.ts).
 */

import type { Metadata } from "next";

import { ContentPage } from "@/components/content/ContentPage";
import { plainText } from "@/lib/content/richText";
import { helpArticles, helpStatusNote } from "@/lib/content/helpCenter";
import { PRODUCT_FULL_NAME } from "@/lib/productFacts";
import { canonicalMeta } from "@/lib/seo/pageMeta";
import { pageGraph } from "@/lib/seo/structuredData";

export const metadata: Metadata = {
  title: "Vectorae help center",
  description: `How ${PRODUCT_FULL_NAME} works in detail: research, verification, contacts, sending, replies, booking, compliance, plans and known limitations.`,
  ...canonicalMeta("/help/"),
};

const firstSentence = (text: string) => {
  const plain = plainText(text);
  const m = /^(.+?[.!?])(\s|$)/.exec(plain);
  return m ? m[1] : plain;
};

export default function HelpIndexPage() {
  const articles = helpArticles();
  return (
    <ContentPage
      jsonLd={pageGraph({ path: "/help/", breadcrumb: "Help" })}
      current="/help"
      eyebrow="Help center"
      h1={`${PRODUCT_FULL_NAME} help center`}
      lead={["Every article describes what the product does, checked against its code, with the date it was last verified."]}
      disclosure={helpStatusNote()}
      sections={[
        {
          heading: "All articles",
          blocks: [
            {
              kind: "ul",
              items: articles.map((a) => `[${a.title}](/help/${a.id}): ${a.lead[0] ? firstSentence(a.lead[0]) : ""}`),
            },
          ],
        },
      ]}
    />
  );
}
