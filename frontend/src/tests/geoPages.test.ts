import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { COMPARISONS, comparisonPath, comparisonSections } from "@/content/geo/compare";
import { FAQ_ALL } from "@/content/geo/faq";
import { FOR_PAGES } from "@/content/geo/forPages";
import { FOUNDER_DISCLOSURE, checkoutSentence, trialAnswer } from "@/content/geo/shared";
import { STATS_SECTIONS } from "@/content/geo/statistics";
import { WHAT_IS_LEAD } from "@/content/geo/whatIs";
import { plainText, safeHref } from "@/lib/content/richText";
import { CHECKOUT_LIVE, PRODUCT_FACTS, capability } from "@/lib/productFacts";
import { llmsTxt } from "@/lib/seo/llmsTxt";
import sitePages from "@/content/site-pages.json";

/** The GEO pages (docs/program/ai-visibility/geo/, founder answers 2026-10-16):
 *  comparisons disclose who wrote them and say when to choose the other tool;
 *  nothing claims a not-live capability is live; every page is linked from
 *  the shared footer; the facts come from product-facts.json. */

const allText = (o: unknown): string => JSON.stringify(o);

describe("comparison pages", () => {
  for (const c of COMPARISONS) {
    it(`${c.competitor}: founders' authorship, a "when to choose" section and sources`, () => {
      const headings = comparisonSections(c).map((s) => s.heading);
      expect(FOUNDER_DISCLOSURE).toMatch(/Written by Vectorae's founders/);
      expect(headings).toContain("When to choose Vectorae");
      expect(headings.some((h) => h.startsWith(`When to choose ${c.competitor}`))).toBe(true);
      expect(headings).toContain("Sources");
      expect(c.sources.length).toBeGreaterThan(0);
      for (const s of c.sources) expect(s.href).toMatch(/^https:\/\//);
      expect(c.title).toContain("Vectorae");
    });
  }
});

describe("no not-live capability is described as live", () => {
  const pages = [COMPARISONS, FAQ_ALL, FOR_PAGES, STATS_SECTIONS, WHAT_IS_LEAD].map(allText).join(" ");

  it("LinkedIn is only mentioned as not live while its status says so", () => {
    if (capability("linkedin_steps").status === "live") return;
    expect(pages).not.toMatch(/LinkedIn (steps|sending|messages) (are|is) live/i);
    expect(pages).toMatch(/LinkedIn steps (are )?(built and tested only against simulated services|not live)/i);
  });

  it("no free trial is promised until checkout is live", () => {
    if (CHECKOUT_LIVE) return;
    expect(trialAnswer()).toMatch(/^Not online yet/);
    expect(checkoutSentence()).toMatch(/not live yet/);
    expect(pages).not.toMatch(/\d+-day free trial/);
  });

  it("no customer results or ratings are claimed", () => {
    expect(pages).not.toMatch(/\d+ (customers|clients) (use|trust)/i);
    expect(pages).not.toMatch(/rated [\d.]+ ?(\/|out of) ?5/i);
  });
});

describe("every GEO and help page is reachable and in the sitemap", () => {
  const paths = sitePages.pages.map((p) => p.path);
  const footer = readFileSync("src/components/Footer.tsx", "utf8");

  it("the 12 GEO pages are in site-pages.json and linked from the footer", () => {
    const geo = [
      "/what-is-leadpilot/", "/faq/", "/outbound-statistics/", "/compare/",
      ...COMPARISONS.map((c) => comparisonPath(c.slug)),
      ...FOR_PAGES.map((p) => `/for/${p.slug}/`),
    ];
    expect(geo).toHaveLength(12);
    for (const p of geo) {
      expect(paths).toContain(p);
      expect(footer).toContain(`href: "${p.slice(0, -1)}"`);
    }
  });

  it("every help-center article has a sitemap entry", () => {
    const dir = "src/content/help-center";
    for (const f of readdirSync(dir)) {
      const id = /^id:\s*(\S+)/m.exec(readFileSync(path.join(dir, f), "utf8"))?.[1];
      expect(paths).toContain(`/help/${id}/`);
    }
    expect(paths).toContain("/help/");
  });
});

describe("facts come from product-facts.json", () => {
  it("the definition page opens with the canonical description", () => {
    expect(WHAT_IS_LEAD[0].startsWith(PRODUCT_FACTS.description)).toBe(true);
  });

  it("llms.txt carries the description, every founder and the disambiguation line", () => {
    const text = llmsTxt();
    expect(text).toContain(`> ${PRODUCT_FACTS.description}`);
    for (const f of PRODUCT_FACTS.founders) expect(text).toContain(f.name);
    expect(text).toContain("not affiliated with LeadPilot AB");
    expect(text).toMatch(/## For AI Models/);
  });
});

describe("inline links are sanitized", () => {
  it("keeps site paths and https URLs only", () => {
    expect(safeHref("/pricing")).toBe("/pricing");
    expect(safeHref("#section")).toBe("#section");
    expect(safeHref("https://example.com/a")).toBe("https://example.com/a");
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref("http://example.com")).toBeNull();
    expect(safeHref("//evil.example")).toBeNull();
    expect(safeHref("data:text/html,x")).toBeNull();
  });

  it("plainText strips the inline syntax", () => {
    expect(plainText("**Bold** and [a link](/x) and `code`")).toBe("Bold and a link and code");
  });
});
