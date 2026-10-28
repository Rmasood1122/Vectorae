/**
 * Founder decision D1 for the home page: it says only what the code does.
 *
 *  - A feature advertised as available must not be behind a flag that
 *    defaults OFF in app/services/system_settings.py; a feature listed as
 *    "available on request" must really be off by default. Read from the
 *    backend source, so flipping a default without updating the page fails.
 *  - Phrases that would promise what Vectorae does not do are banned from
 *    every home page source file.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { AUDIENCES, FAQ, FEATURES, HERO, HOME_TITLE, NOT_A_FIT, PRICING_TEASER, STAGES } from "@/components/home/content";
import { CHECKOUT_LIVE } from "@/lib/productFacts";

const SETTINGS = readFileSync("../app/services/system_settings.py", "utf8");

function flagDefault(flag: string): boolean | undefined {
  const m = new RegExp(`"${flag}":\\s*\\(\\s*(True|False)`).exec(SETTINGS);
  return m ? m[1] === "True" : undefined;
}

const HOME_FILES = [
  "src/app/page.tsx",
  "src/components/home/content.ts",
  "src/components/home/HomePage.tsx",
  "src/components/home/HomeHeader.tsx",
  "src/components/home/HomeVideo.tsx",
  "src/components/home/StageStepper.tsx",
  "src/app/(auth)/signup/page.tsx",
  "public/media/leadpilot-walkthrough.en.vtt",
  "video-src/walkthrough.html",
];

const BANNED: { phrase: RegExp; why: string }[] = [
  { phrase: /guarantee/i, why: "no outcome is guaranteed" },
  { phrase: /no credit card/i, why: "Stripe Checkout collects a payment method by default; app/services/billing.py does not turn that off" },
  { phrase: /unlimited/i, why: "every plan has finite limits (app/core/plans.py)" },
  { phrase: /repl(y|ies) (to prospects )?for you/i, why: "Vectorae never answers a prospect for you" },
  { phrase: /auto-?repl(y|ies)/i, why: "no automatic replies to prospects" },
  { phrase: /upload (your|a) (contact )?list/i, why: "leads cannot be imported" },
  { phrase: /\b\d+x\b|\b\d+%\s+(more|higher|better)/i, why: "no measured uplift exists to quote" },
  { phrase: /app store|available (for|on) (the )?(iphone|ios)|(?<!no )(iphone|ios) app/i, why: "there is no iOS app" },
];

describe("home page claims match the code (D1)", () => {
  it("reads the backend flag defaults", () => {
    expect(flagDefault("daily_send_approval_enabled")).toBe(true);
    expect(flagDefault("reply_copilot_enabled")).toBe(false);
  });

  for (const f of FEATURES.filter((x) => x.flag)) {
    it(`${f.name}: "${f.status}" matches ${f.flag}'s default`, () => {
      const def = flagDefault(f.flag!);
      expect(def, `${f.flag} not found in system_settings.py DEFAULTS`).not.toBeUndefined();
      // "unconfirmed": switched on by default, but the founders have not confirmed it
      // works end to end in production (2026-10-13, product-facts.json); it is listed
      // under that heading instead of as available. Only an ON flag may be unconfirmed.
      if (f.status === "unconfirmed") expect(def).toBe(true);
      else expect(f.status).toBe(def ? "available" : "on_request");
    });
  }

  it("every flag that defaults OFF and is mentioned is listed as on request, never as available", () => {
    const offFlags = [...SETTINGS.matchAll(/"(\w+_enabled)":\s*\(\s*False/g)].map((m) => m[1]);
    for (const f of FEATURES.filter((x) => x.status === "available" && x.flag)) {
      expect(offFlags).not.toContain(f.flag);
    }
  });

  it("the AI phone channel (off by default, regulated) is not advertised", () => {
    expect(flagDefault("phone_calling_enabled")).toBe(false);
    const all = HOME_FILES.map((f) => readFileSync(f, "utf8")).join("\n");
    expect(all).not.toMatch(/AI (phone )?call(s|ing)\b/i);
  });

  it("the stepper covers research -> leads -> sequences -> sending -> replies -> meetings", () => {
    expect(STAGES.map((s) => s.id)).toEqual(["research", "leads", "sequences", "sending", "replies", "meetings"]);
  });

  it("the numbers quoted match the code's defaults", () => {
    const text = JSON.stringify({ STAGES, FAQ });
    // research step counts (app/pipeline/registry.py) and 10 verification passes
    expect(text).toContain("72 research steps, or 144");
    expect(text).toContain("10 verification checks");
    // smart send time max delay and the Gmail ramp (system_settings.py / app/config.py)
    expect(text).toContain("up to 72 hours");
    expect(SETTINGS).toMatch(/"send_time_max_delay_hours":\s*\(72,/);
    const config = readFileSync("../app/config.py", "utf8");
    expect(config).toMatch(/gmail_daily_cap: int = 100\b/);
    expect(config).toMatch(/gmail_warmup_start_sends: int = 10\b/);
    expect(config).toMatch(/gmail_warmup_daily_increment: int = 10\b/);
    expect(text).toContain("starts at 10 emails on its first day, adds 10 a day");
  });

  it("the hero promises an account, not free use: a trial only once checkout is live", () => {
    // The trial starts through Stripe checkout; until the founders mark checkout
    // live in product-facts.json, no trial is promised (founders, 2026-10-14).
    if (CHECKOUT_LIVE) {
      expect(HERO.note).toBe("Creating an account is free. Monthly plans start with a free trial.");
      const catalog = readFileSync("../app/core/billing_catalog.py", "utf8");
      expect(catalog).toMatch(/^TRIAL_DAYS = [1-9]\d*/m);
    } else {
      expect(HERO.note).toBe("Creating an account is free. Online checkout opens soon; to start a plan now, book a call.");
      expect(JSON.stringify({ HERO, PRICING_TEASER, FAQ })).not.toMatch(/free trial|cancel(led)? online/i);
    }
  });

  it("no consumer outreach: consumers appear only in the not-a-fit list", () => {
    expect(JSON.stringify(AUDIENCES)).not.toMatch(/consumer|homeowner|household/i);
    expect(NOT_A_FIT.join(" ")).toMatch(/consumers or homeowners/);
  });

  it("the page title carries the brand", () => {
    expect(HOME_TITLE).toMatch(/^Vectorae/);
    expect(readFileSync("src/app/page.tsx", "utf8")).toContain(`title: "${HOME_TITLE}"`);
  });

  for (const { phrase, why } of BANNED) {
    it(`no home page file says ${phrase} (${why})`, () => {
      const hits = HOME_FILES.filter((f) => phrase.test(readFileSync(f, "utf8")));
      expect(hits).toEqual([]);
    });
  }
});
