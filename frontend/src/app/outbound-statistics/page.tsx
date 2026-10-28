/**
 * app/outbound-statistics/page.tsx — /outbound-statistics/: sourced figures
 * (src/content/geo/statistics.ts). Every figure links its source.
 */

import type { Metadata } from "next";

import { ContentPage } from "@/components/content/ContentPage";
import { FACTS_CHECKED } from "@/content/geo/shared";
import { STATS_LEAD, STATS_SECTIONS } from "@/content/geo/statistics";
import { canonicalMeta } from "@/lib/seo/pageMeta";
import { pageGraph } from "@/lib/seo/structuredData";

export const metadata: Metadata = {
  title: "Outbound sales statistics for agencies and B2B service firms (2026) | Vectorae",
  description:
    "Sourced 2025–2026 figures on B2B buyers, agency referrals, SDR cost, cold email reply rates, Gmail and Outlook sender rules, and CAN-SPAM, GDPR and CASL penalties.",
  ...canonicalMeta("/outbound-statistics/"),
};

export default function OutboundStatisticsPage() {
  return (
    <ContentPage
      jsonLd={pageGraph({ path: "/outbound-statistics/", breadcrumb: "Outbound statistics" })}
      eyebrow="Resources"
      h1="Outbound sales statistics for small agencies and B2B service firms (2026)"
      lead={STATS_LEAD}
      lastChecked={FACTS_CHECKED}
      sections={STATS_SECTIONS}
      related={[
        { href: "/for/agencies", label: "Vectorae for agencies" },
        { href: "/compare/leadpilot-vs-artisan", label: "Vectorae vs hiring an SDR" },
        { href: "/help/compliance", label: "Compliance (help center)" },
      ]}
    />
  );
}
