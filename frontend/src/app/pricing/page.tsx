/**
 * app/pricing/page.tsx: the public pricing page at /pricing.
 *
 * A server component so the page carries its own title, description and
 * canonical URL, and so the plan names are in the exported HTML (StaticPlanList)
 * before the browser loads the live catalog. The interactive page (prices and
 * limits from GET /billing/catalog, checkout) is PricingClient.
 */

import type { Metadata } from "next";
import Link from "next/link";

import planNames from "@/content/plan-names.json";
import { Footer } from "@/components/Footer";
import { JsonLd } from "@/components/seo/JsonLd";
import { CHECKOUT_LIVE, CHECKOUT_SOON, PRODUCT_FACTS, PRODUCT_FULL_NAME, notLiveSentence } from "@/lib/productFacts";
import { canonicalMeta } from "@/lib/seo/pageMeta";
import { staticPlans, type StaticPlan } from "@/lib/seo/staticPlans";
import { pageGraph } from "@/lib/seo/structuredData";

import { PricingClient } from "./PricingClient";

export const metadata: Metadata = {
  title: "Vectorae pricing",
  description:
    `Plans for ${PRODUCT_FULL_NAME}: monthly plans (${planNames.monthly_tiers.join(", ")}), ` +
    `or ${planNames.pay_per_meeting.toLowerCase()} booked.`,
  ...canonicalMeta("/pricing/"),
};

export default function PricingPage() {
  return (
    <>
      <JsonLd data={pageGraph({ path: "/pricing/", breadcrumb: "Pricing", software: true })} />
      <PricingClient footer={<Footer tone="theme" />} staticPlans={<StaticPlanList />} intro={<PricingIntro />} checkoutLive={CHECKOUT_LIVE} />
    </>
  );
}

function PricingIntro() {
  return (
    <div className="mt-4 space-y-2 text-sm text-muted-foreground">
      <p>{PRODUCT_FACTS.description}</p>
      <p>{notLiveSentence()}</p>
      {!CHECKOUT_LIVE && (
        <p className="font-medium text-foreground">
          {CHECKOUT_SOON}{" "}
          <Link href="/contact" className="underline">Book a call</Link>
        </p>
      )}
    </div>
  );
}

function StaticPlanList() {
  const plans = staticPlans();
  const namesOnly = [...plans.monthly, plans.payPerMeeting].every((p) => p.features.length === 0);
  if (namesOnly) {
    // No snapshot yet (see src/lib/seo/staticPlans.ts): one compact card of names.
    return (
      <section aria-labelledby="static-plans" className="mx-auto mb-8 max-w-2xl rounded-xl border border-border bg-card p-6">
        <h2 id="static-plans" className="text-xl font-semibold">Plans</h2>
        <p className="mt-3 text-sm">
          <span className="font-semibold">Monthly subscription:</span> {plans.monthly.map((p) => p.name).join(", ")}
        </p>
        <p className="mt-1 text-sm">
          <span className="font-semibold">Or:</span> {plans.payPerMeeting.name}
        </p>
      </section>
    );
  }
  return (
    <div className="mx-auto mb-8 grid max-w-4xl gap-6 md:grid-cols-[1.6fr_1fr]">
      <section aria-labelledby="static-monthly" className="rounded-xl border border-border bg-card p-6">
        <h2 id="static-monthly" className="text-xl font-semibold">Monthly subscription</h2>
        {CHECKOUT_LIVE && plans.trialDays ? (
          <p className="text-sm text-muted-foreground">A {plans.trialDays}-day free trial for a new account.</p>
        ) : null}
        <ul className="mt-4 space-y-4">
          {plans.monthly.map((plan) => <PlanItem key={plan.name} plan={plan} />)}
        </ul>
      </section>
      <section aria-labelledby="static-ppm" className="rounded-xl border border-border bg-card p-6">
        <h2 id="static-ppm" className="text-xl font-semibold">{plans.payPerMeeting.name}</h2>
        <ul className="mt-4">
          <PlanItem plan={plans.payPerMeeting} hideName />
        </ul>
      </section>
    </div>
  );
}

function PlanItem({ plan, hideName = false }: { plan: StaticPlan; hideName?: boolean }) {
  return (
    <li>
      {!hideName && (
        <h3 className="font-semibold">
          {plan.name}
          {plan.price && <span className="font-normal text-muted-foreground"> {plan.price}</span>}
        </h3>
      )}
      {hideName && plan.price && <p className="font-semibold">{plan.price}</p>}
      {plan.tagline && <p className="text-sm text-muted-foreground">{plan.tagline}</p>}
      {plan.features.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-sm">
          {plan.features.map((f) => <li key={f}>{f}</li>)}
        </ul>
      )}
    </li>
  );
}
