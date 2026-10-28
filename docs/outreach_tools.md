# Manual Outreach Toolkit — Finding Small Agency Owners (USA), Zero Budget

**Status:** research/strategy document. No application code, tests, or deploy
config touched. Builds directly on `docs/ICP_USA.md` — target buyer stays the
same (small marketing/web-dev/design agency owners, USA, ~8–25 employees) and
Section 6's messaging angles are reused below rather than reinvented.

**Why this document exists:** the founder cannot currently afford the
Anthropic API costs to run Vectorae on their own behalf, so this is a manual,
zero-paid-API prospecting toolkit — free or genuinely-free-tier tools only,
used by hand.

**The selection test applied to everything below:** a platform only qualifies
if it's high in target-buyer density **and** low in competing lead-gen/agency-
tool-seller noise. Both are assessed explicitly for every entry. A platform
full of the right buyers but already crawling with other people pitching
outreach tools is a worse pick than a quieter one with fewer but genuinely
reachable buyers — this is stated as the ranking logic up front because it's
the whole point of the document, not just a nice-to-have filter.

---

## 1. Free directories and marketplaces where agencies list themselves

These are lead-source databases to browse and qualify manually — not pitch
venues. You're reading listings, not posting in them.

### Clutch.co
- **Link:** https://clutch.co
- **Cost:** Free to browse, search, and filter. <cite index="61-1">Clutch does not publish a public API — the directory exists only as web pages</cite>, which is exactly what makes it usable for free manual browsing (the paid tools referenced across the research are scrapers built to avoid doing this by hand — you don't need one). <cite index="61-1">Narrowing the directory by service line, location, company size, and hourly rate is done on Clutch's own filters</cite>, all free.
- **Buyer concentration:** High. Clutch is built specifically as an agency-shortlisting directory; <cite index="64-1">filters include location, budget/minimum project size, and specialization, and every profile shows company size and founded date</cite> — this is precisely the "titles/company_size_ranges" data your `docs/ICP_USA.md` worked example already targets.
- **Competitor/noise concentration:** Not applicable in the usual sense — you're not posting here, you're reading. The relevant risk is different: multiple commercial scraper products exist specifically to bulk-extract Clutch listings for cold outreach lists, which tells you Clutch-sourced agencies are a known, already-mined pool for other outbound sellers (assumption, inferred from how many scraping tools exist for this exact directory — not a direct measure of how much outreach a given agency owner receives, which isn't publicly knowable).
- **First action this week:** Go to Clutch's Digital Marketing or Web Design category, filter to United States + your target employee-size band, and manually record the next 25 agencies' name, website, and (from each profile) the founder/principal's name if listed. This becomes your first prospect list — no scraper needed for a list this size.

### DesignRush
- **Link:** https://www.designrush.com
- **Cost:** Free to browse and filter by category, location, and budget.
- **Buyer concentration:** High — same category as Clutch (agency directory/marketplace), so agency owners self-list here for the same reason: inbound project leads.
- **Competitor/noise concentration:** Same caveat as Clutch — not a pitch venue, but a directory that other lead-gen tools are also known to scrape. No direct data found on relative saturation vs. Clutch — **(assumption, unverified)**: DesignRush is generally considered a secondary/smaller directory to Clutch, so plausibly less heavily mined, but this wasn't independently confirmed in this research pass.
- **First action this week:** Cross-check your Clutch list against DesignRush for the same agencies (dedupe) and add any new ones DesignRush surfaces that Clutch didn't — a second directory catches agencies that only list on one.

### GoodFirms
- **Link:** https://www.goodfirms.co
- **Cost:** Free to browse and filter.
- **Buyer concentration:** High, same category as above.
- **Competitor/noise concentration:** Same structural caveat as Clutch/DesignRush — not independently sourced beyond the general pattern that all three directories exist for the same purpose and are plausibly mined by the same class of tools.
- **First action this week:** Same dedupe-and-extend exercise as DesignRush — a third pass is enough; going past 3 directories has diminishing returns for a first list.

---

## 2. Communities where agency owners actually participate

Verified per-community for activity and self-promotion tolerance, since that
determines both opportunity and ban risk.

### Grow Your Agency (Slack)
- **Link:** https://growyouragency.group
- **Cost:** **Not free — $35 one-time, lifetime access** (flagged explicitly per the task's rule against recommending near-free-but-not-really tools as if free). <cite index="70-1">The $35 price is explicitly stated by the founder to be a filter against bots and freebie hunters, not a subscription — one payment, forever access</cite>.
- **Buyer concentration:** Very high and directly on-ICP. <cite index="70-1">The community is described as "mostly independent agencies and freelance operators in the 1–20 person range — creative, digital, marketing, design, dev," with 1,600+ members, mostly US/UK/Canada/Australia/Europe</cite>. This is close to a direct match for the `docs/ICP_USA.md` target band (8–25 employees), skewed slightly smaller but overlapping heavily.
- **Competitor/noise concentration:** No direct data on outbound-pitch density inside the Slack itself (private community, not indexed). <cite index="70-1">It's described as a peer community with no curriculum or upsells, curated around weekly discussion topics rather than open pitching</cite> — this suggests low tolerance for cold pitching by structure, but that's an inference from the community's stated design, not a verified count of how many people currently pitch there.
- **First action this week:** If the $35 is affordable, join, introduce yourself as a peer (not a vendor) per the community's stated norms, and spend the first 1–2 weeks answering questions before mentioning anything you've built — consistent with every community-marketing source found in this research, which converges on "contribute before you promote" as the way to avoid being flagged as noise yourself.

### Online Geniuses (Slack)
- **Link:** https://onlinegeniuses.com
- **Cost:** Free.
- **Buyer concentration:** High but broader than agency-owners specifically. <cite index="68-1">Over 35,000 accepted members and 3M+ messages sent, with VPs, CMOs, freelancers, and agency owners</cite> — agency owners are present but the community isn't agency-owner-exclusive the way Grow Your Agency is, so buyer density per message is lower even though raw size is much larger.
- **Competitor/noise concentration:** Not directly sourced. At 35,000+ members with broad marketing-professional membership, it plausibly has higher outbound-pitch traffic than a smaller, curated, paid-filter community like Grow Your Agency — **(assumption, unverified)**: large free communities generally attract more self-promotion than small paid ones, but no specific measurement of Online Geniuses' pitch volume was found.
- **First action this week:** Use Slack's search within the workspace for recent threads mentioning "new clients," "pipeline," or "lead gen" — these threads surface agency owners actively discussing the exact problem Vectorae solves, giving you a warm, on-topic way to reply helpfully before ever DMing anyone.

### r/marketing (Reddit)
- **Link:** https://reddit.com/r/marketing
- **Cost:** Free.
- **Buyer concentration:** Moderate-high — marketing professionals broadly, including agency owners, though not agency-owner-exclusive (mixed with in-house marketers).
- **Competitor/noise concentration:** Very low for direct pitching, specifically because it's disallowed. <cite index="77-1">Promoting your agency, tool, course, or service is not allowed in posts or comments, and this is enforced aggressively — even subtle mentions like "at my agency, we..." get flagged by mods who are experienced marketers</cite>, and <cite index="78-1">the subreddit has a stated zero-tolerance policy on advertising, self-promotion, and spam</cite>. This cuts both ways: it means almost no one else is successfully pitching here either (genuinely low noise), but it also means **you cannot DM-pitch or post-pitch here without risking a ban** — this is a listening/warm-up channel, not a direct-outreach channel.
- **First action this week:** Do not pitch. Instead, search the subreddit for agency owners asking about new-business/pipeline problems, note their usernames, and — only once you've built some visible non-promotional comment history — consider a respectful, disclosed DM referencing the specific thread, not a cold pitch. This is slower than other channels by design; treat it as a lead-identification source, not an outreach channel.

### r/agency (Reddit)
- **Link:** https://reddit.com/r/agency
- No independent activity/rules data was found for this specific subreddit in this research pass — **flagged as an open gap rather than guessed at.** Before relying on it, check its subscriber count and pinned rules directly; do not assume it mirrors r/marketing's rules.

---

## 3. LinkedIn tactics — free tier only

<cite index="87-1">LinkedIn's connection request limit in 2026 is 100 invitations per week across all account tiers — Free, Premium, and Sales Navigator all share the same cap</cite>, so free-tier LinkedIn is not meaningfully volume-limited versus paid tiers for this purpose — the real free-tier constraints are elsewhere.

- **Search operators that work on free LinkedIn:** <cite index="84-1">OR and quotation marks work in the free search bar; AND and NOT no longer function in standard search — use a minus sign to exclude terms instead (e.g. "product manager" -associate)</cite>, and <cite index="84-1">parentheses still work for grouping, e.g. (marketing OR growth) AND director</cite> for the terms that do support it — worth testing directly since sources vary slightly on current exact behavior; <cite index="86-1">LinkedIn's own Boolean search operators are quotation marks, parentheses, NOT, AND, and OR, in that evaluation order</cite>, so treat the minus-sign workaround as the practical fallback if a full AND/NOT string doesn't return expected results.
- **Concrete search string to start with:** `("agency owner" OR "founder" OR "managing director") AND ("marketing agency" OR "web design agency" OR "digital agency")` in the free People search bar, then use LinkedIn's free filters (not Sales Navigator) for Location = United States.
- **Free profile-view limits:** <cite index="82-1">a free LinkedIn account can view up to 1,000 profiles per month (100 pages of 10 results each)</cite> — plenty for weekly manual prospecting at this stage; this is not a real bottleneck yet.
- **Connection-request personalized notes:** <cite index="87-1">notes raise acceptance by 15–30% on high-value prospects but are limited to 20 per month on free LinkedIn</cite> — spend these 20 on your highest-conviction prospects each month, not the first 20 you find.
- **Warm-up tactic before DMing:** engage genuinely (a real comment, not "Great post!") on 3–5 posts from target-profile agency owners over a week or two before sending a connection request — this is standard advice across the community-marketing sources found throughout this research (Section 2 above draws on the same underlying principle) and costs nothing but time.
- **First action this week:** Run the search string above, filter to United States, and personalize-note your first 5–8 highest-fit results using ICP_USA.md's messaging angle #2 ("You already pay for a CRM you don't have time to fill") adapted to a one-line opener, since it doesn't require knowing anything specific about the prospect beyond "they run an agency" — the other two ICP messaging angles need more context (a known referral-dependency pain, or a known Apollo-adjacent tool) that a first-touch LinkedIn note usually can't establish yet.

---

## 4. Free/freemium lead-list-building tools

This category needed the most skepticism — most "free Google Maps scraper"
results are marketing content from the tools themselves, so free-tier claims
here are flagged clearly rather than taken at face value.

### Manual Google Maps + Google Search (no tool at all)
- **Cost:** Free, no tool required.
- **What it gets you:** searching e.g. `"marketing agency" [city]` on Google Maps directly, by hand, returns business name, website, phone, and often an owner name in reviews/about sections — this is the zero-risk, zero-signup baseline every other tool in this category is trying to automate.
- **Limit:** <cite index="89-1">Google Maps itself caps results at 120 per query</cite>, so for a first list this ceiling doesn't matter (you don't need more than ~25–50 to start), but it explains why every scraper tool markets around this number.
- **First action this week:** Search Google Maps directly for your target city + "marketing agency" / "web design agency" / "digital agency," and manually log the first 20 results with a website into a spreadsheet — zero cost, zero signup, and matches your actual current need (a first list, not a database).

### Hunter.io (free tier, email-finding only — not a list-builder)
- **Cost:** Genuinely free tier exists for a limited number of searches/month (specific current number not independently verified in this pass — check Hunter.io's pricing page directly before relying on an exact figure, since free-tier allowances change).
- **What it's for:** once you already have a company name/domain (from Clutch, DesignRush, or Google Maps above), Hunter finds a likely email format/address for that domain — it's an enrichment step, not a discovery tool.
- **Caveat:** several of the "free Google Maps scraper" tools surfaced in this research (LeadsAgent, Scrap.io, Outscraper, Chat4Data) make specific free-tier claims (e.g. <cite index="90-1">"1,000 verified leads per month completely free — no card, no trial timer"</cite>) that could not be independently verified beyond the vendors' own marketing pages in this research pass — **treat these as unverified vendor claims, not confirmed facts**, and note that several explicitly combine a small genuine free allotment with an aggressive paid upsell (the pattern the task rules specifically warned against). None of these are recommended as a primary tool here; manual Google Maps search plus directory browsing (Sections 1 and this section's first entry) is the verified-free path.
- **First action this week:** Once you have 15–20 company domains from Section 1/this section, run them through Hunter's free tier to get candidate email addresses — stop and switch to the directory's listed contact form or LinkedIn DM once the free monthly quota runs out rather than paying to continue immediately.

### LinkedIn Sales Navigator's free alternative: none needed here
Not a separate tool — flagged only to say explicitly that Section 3's free-tier LinkedIn tactics are the list-building tool for LinkedIn-sourced leads; no separate paid export tool is necessary or recommended at this stage.

---

## 5. Content/visibility plays (inbound alternative)

### LinkedIn build-in-public posts (not a platform, a tactic)
- **Cost:** Free.
- **Buyer concentration:** Potentially high — if posts specifically discuss agency new-business/pipeline problems (not generic "building my SaaS" content), they surface directly to the people searching or scrolling those topics, and this is the same platform where Section 3's search tactics already put you in front of agency owners.
- **Competitor/noise concentration:** Not measurable directly, but LinkedIn's build-in-public content volume is high across all SaaS categories generally — differentiation has to come from being specific to the agency-owner pain point (referrals hitting a ceiling, an idle CRM) rather than generic "day 47 of building my startup" posts, which are common and easy to scroll past.
- **First action this week:** Post one specific, concrete story — e.g., how you found and validated the ICP in `docs/ICP_USA.md` itself, framed as "here's what I learned researching who actually needs this" — which doubles as free proof-of-work content and as a natural, non-pitchy way to signal what you're building to exactly the audience you want reading it.

### Indie Hackers — downranked, flagged explicitly
- **Link:** https://indiehackers.com
- **Cost:** Free.
- **Buyer concentration: Low for this specific ICP**, despite being a default recommendation in most "build in public" advice. <cite index="96-1">Indie Hacker communities in 2026 skew toward developers, solo SaaS founders, and technical builders</cite> rather than non-technical service-business owners — small marketing/web-dev/design agency owners are a minority presence there relative to SaaS founders. This is exactly the mismatch the task asked to watch for: high founder-community relevance in general "startup" advice, but not necessarily high target-buyer density for *this specific* ICP.
- **Competitor/noise concentration:** High, for the same reason — a disproportionate share of Indie Hackers' active posters are people building and marketing SaaS tools (including, plausibly, other lead-gen/outreach tools), meaning any post here competes for attention with a lot of structurally similar founder content.
- **Verdict:** **Not recommended as a primary channel for this ICP.** Included here only to explain why it's excluded despite being a common default suggestion — worth reconsidering later if Vectorae ever targets developer-tool or SaaS-founder buyers directly, but not for the current marketing-agency-owner ICP.

### r/smallbusiness / r/Entrepreneur (Reddit)
- No independent verification of these subreddits' current self-promotion rules or agency-owner density was completed in this research pass — **flagged as an open gap.** Both are large, general-purpose business subreddits (not agency-specific), so buyer density is plausibly lower than r/marketing per the general-vs-niche pattern seen elsewhere in this document — check their current pinned rules directly before relying on them.

---

## Ranked top list

| Rank | Platform | One-sentence reason |
|---|---|---|
| 1 | **Clutch.co (directory browsing)** | Highest verified buyer density with zero pitching risk (you're reading, not posting), completely free, and directly produces a working prospect list this week with no tool needed. |
| 2 | **Manual Google Maps search** | Zero cost, zero signup, zero noise risk (not a community, no rules to break) — the fastest possible first list, and pairs directly with Clutch as a second sourcing pass for agencies that don't list on directories. |
| 3 | **LinkedIn free-tier search + warm-up DMs** | High buyer density (agency owners are genuinely on LinkedIn) and the platform's own structure (weekly caps, personalized-note limits) forces the kind of small, targeted, high-effort outreach that performs better than volume pitching anyway. |
| 4 | **r/marketing (as a listening channel, not a pitch channel)** | Very low noise because pitching is banned and enforced — genuinely useful for finding warm, currently-in-pain prospects, but ranked below the top 3 because it cannot be used for direct outreach without real ban risk. |
| 5 | **DesignRush / GoodFirms (secondary directories)** | Same model and buyer density as Clutch, ranked lower only because they're a second/third pass over largely the same category of listings, not a distinct new pool. |
| 6 | **Grow Your Agency (Slack, $35)** | Best-matched buyer density of anything in this list (1,600+ members explicitly in the 1–20 employee agency band) but ranked below the free options only because it costs money, however small — use once the free channels are exhausted or if $35 is genuinely affordable now. |
| 7 | **LinkedIn build-in-public posting** | Real inbound potential but slower to compound than direct outreach — a medium-term play to run in parallel with 1–4, not a first move. |
| 8 | **Online Geniuses (Slack)** | Free and large, but lower agency-owner density per member than Grow Your Agency and higher plausible outbound-pitch noise given its size — useful as a secondary listening channel, not a first stop. |
| — | **Indie Hackers — excluded** | Default "build in public" advice, but genuinely low buyer density for this specific ICP (skews SaaS/developer founders, not agency owners) — not recommended here. |

---

## Plain-language summary

Start on **Clutch.co today.** It's completely free, it's a directory of exactly the kind of agencies you're looking for, and you're just reading — no risk of getting banned or looking spammy. Here are your first three steps: **(1)** Go to Clutch's marketing/web-design category, filter to United States, and write down the name, website, and (if listed) the owner's name for the first 25 agencies you see. **(2)** Do the same 15-minute pass on Google Maps by searching "marketing agency [a US city you want to target]" and adding another 20 names to the same list. **(3)** Take that combined list to LinkedIn, search for the owners by name, and send your first 5 personalized connection requests this week using an opener built from the "you already pay for a CRM you don't have time to fill" angle from `docs/ICP_USA.md`. That's a working first list and first outreach batch, entirely free, before the end of the week.