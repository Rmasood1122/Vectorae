/**
 * app/page.tsx — "/".
 *
 * On the web: the public home page (founder request 2026-10-11), with the
 * product video, the six-stage walkthrough and the shared Footer.
 *
 * In the Android app (Capacitor loads this same export) and an installed PWA:
 * no marketing at all. HomeGate routes exactly as the old redirect-only page
 * did (/login, the dashboard, /check-email or /onboarding/verify), and
 * APP_SHELL_SCRIPT hides the marketing markup before hydration so it never
 * flashes. See src/lib/homeEntry.ts.
 */

import type { Metadata } from "next";

import { HomeGate } from "@/components/home/HomeGate";
import { HomePage } from "@/components/home/HomePage";
import { FAQ, HOME_DESCRIPTION, HOME_TITLE } from "@/components/home/content";
import { APP_SHELL_SCRIPT } from "@/lib/homeEntry";
import { canonicalMeta } from "@/lib/seo/pageMeta";
import { JsonLd } from "@/components/seo/JsonLd";
import { pageGraph } from "@/lib/seo/structuredData";

export const metadata: Metadata = {
  title: "Vectorae: from what you sell to meetings on your calendar",
  description: HOME_DESCRIPTION,
  ...canonicalMeta("/", {
    title: HOME_TITLE,
    description: HOME_DESCRIPTION,
    images: ["/media/leadpilot-walkthrough-poster.jpg"],
  }),
};

export default function Home() {
  return (
    <>
      <JsonLd data={pageGraph({ path: "/", software: true, faq: FAQ })} />
      {/* Must run before the marketing markup is painted. */}
      <script dangerouslySetInnerHTML={{ __html: APP_SHELL_SCRIPT }} />
      <HomeGate>
        <HomePage />
      </HomeGate>
    </>
  );
}
