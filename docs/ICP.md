# Vectorae — USA Ideal Customer Profile (ICP)

**Status:** research/strategy deliverable. No application code, tests, or deploy
config touched by this document.
**Scope:** Vectorae's own go-to-market (not a customer's ICP run through the
product). Target market: USA. Founder based in Pakistan.

---

## A note on methodology and its limits

This document is grounded in web research pulled in August 2026. Every
firmographic, budget, and channel claim below is either cited to a source or
explicitly marked **(assumption, unverified)**. A few things worth flagging up
front, because they change the shape of the recommendation from what the
original "commercial B2B service contractors" framing assumed:

1. **The category is not monolithic on budget or sophistication**, and the gap
   between the best-fit and worst-fit sub-niche inside "commercial B2B service
   contractors" is large enough that treating it as one segment would produce
   messaging that fits none of them well.
2. **"Underserved by enterprise tools" needs a correction.** ZoomInfo and
   Cognism are genuinely out of reach for this buyer (floor around
   $15,000/year, annual contracts).<cite index="52-1,52-2">ZoomInfo's Professional tier starts around $14,995/year, paid as an annual contract with no monthly billing option at any tier.</cite> But Apollo — the tool most likely to actually
   be this buyer's alternative — is not out of reach: <cite index="50-1,50-2">Apollo is a self-serve, all-in-one prospecting and engagement platform starting at $49 per seat per month with no contract.</cite> So the honest differentiation
   claim isn't "we're affordable and ZoomInfo isn't" (true but not the live
   competitive question for this buyer) — it's "Apollo gives you a database
   and a sequencer, you still have to build the list, write the sequence, and
   run it yourself; Vectorae does the research, targeting, and outreach
   generation for you." That's a done-for-you vs. self-serve distinction, not
   a price distinction. This matters for messaging (Section 6).
3. **Staffing/recruiting is more competitively crowded than the earlier
   framing assumed.** Multiple point solutions already sell specifically into
   staffing agency BD (Pin, Overloop, agency-leads.com, Instalent, and others
   surfaced repeatedly in research), several with staffing-specific features
   (hiring-signal targeting, VMS/preferred-supplier awareness) Vectorae
   doesn't have. This doesn't disqualify the niche, but it changes its rank
   (see Section 1).
4. **Commercial HVAC is a minority slice of "HVAC," and most HVAC-industry
   data is residential/B2C, not B2B.** <cite index="31-1">The majority of HVAC firms are small, family-owned businesses or sole proprietorships, typically owner-operated with fewer than five employees and annual revenues under $1 million.</cite> Most of the size/revenue data available online is not segmented by
   commercial vs. residential, which means firmographic confidence for this
   sub-niche is lower than for cleaning or staffing — flagged explicitly
   below rather than papered over.

---

## 1. Firmographics

### Company size and why

**Target range: 5–50 employees, with a sweet spot around 8–25.**

Reasoning (assumption, unverified, built from the budget/behavior data in
Sections 2–4, not from a single cited source): below ~5 employees, the
business is usually the owner plus crew/field staff with no discretionary
software budget and no real "pipeline problem" separate from "I need more
work this week" — solved by referrals, not a GTM tool. Above ~50 employees,
firms increasingly have a dedicated BD/sales hire or a marketing agency
retainer already in place, and the buying decision requires more than one
signer. The 5–50 band is where there's revenue to protect, a named person
responsible for keeping the pipeline full, and no formal sales infrastructure
yet.

### Annual revenue range

**Target range: roughly $500K–$8M**, again assumption-derived from the
sub-niche revenue data in this section rather than a single source. This
range is wide because it has to span sub-niches with very different revenue
per employee (a 10-person staffing agency can bill far more than a 10-person
cleaning company).

### Sub-niches ranked by fit

| Rank | Sub-niche | Why |
|---|---|---|
| 1 | **Small marketing/web-dev/design agencies** | Best combination of: buyer already thinks in "channels and messaging" terms (fastest to understand what Vectorae does), documented existing spend on adjacent tools (CRM, lead-gen software — Section 4), and a single owner-operator decision maker. Weakness: this buyer is the most likely to want to DIY it themselves rather than pay for a tool, since GTM is literally their trade. |
| 2 | **Commercial cleaning/janitorial — but only the "at scale" tier, not the median business** | The category is enormous (<cite index="13-1">the U.S. janitorial services market is worth $112 billion, with over 1 million cleaning businesses as of 2026</cite>) but the *median* firm is far too small to be a buyer: <cite index="15-1">the average janitorial business employs just 1.9 people</cite>. The real target inside this sub-niche is the minority of firms that have scaled past solo/family operation into the 10–50 employee band with recurring commercial contracts — a smaller pool than the headline market size implies, but with clear budget (contracts run $500–$2,000/year just for basic maintenance work, so B2B sales relationships have real recurring value) (assumption on budget-to-value math, unverified). |
| 3 | **Small accounting/immigration/law consultancies** | Not independently researched in this pass beyond the general assumption from the original framing — flagged here as **(assumption, unverified)** rather than ranked with confidence. Plausibly a good fit (single decision-maker, high-value clients, referral-dependent) but needs its own research pass before being used in outreach; do not treat this rank as validated the way ranks 1, 2, and 4 are. |
| 4 | **B2B staffing/recruiting agencies** | Downgraded from the original framing. The buyer profile is genuinely strong — <cite index="26-1">agency owners run structured discovery calls and actively want to filter on hiring signals, existing vendor mix, and trigger events</cite> — but the competitive water is already crowded with staffing-specific point solutions (Pin, Overloop, Instalent, agency-leads.com among others found in this research), several offering hiring-signal and VMS-aware features Vectorae does not have. Fit is real, differentiation is harder, and expect a more skeptical, more-shopped-around buyer. |
| 5 | **Commercial HVAC / facilities maintenance** | Lowest confidence rank. <cite index="31-1">The majority of HVAC firms are small, family-owned businesses or sole proprietorships, typically owner-operated with fewer than five employees and annual revenues under $1 million</cite> — meaning most of the industry is below the target size band before even separating commercial from residential work. Commercial-only HVAC/facilities firms in the right size band likely exist and likely fit, but the available market data doesn't segment cleanly enough to size or characterize them with confidence here — this needs its own targeted research pass, not inference from general HVAC statistics. |

### Geographic concentration

No sub-niche-specific geographic research was done in this pass beyond one
data point: <cite index="15-1">janitorial business formation is heavily state-concentrated — New York alone is a $9.7 billion janitorial market with about 71,014 businesses, and Illinois supports about 46,042 janitorial businesses</cite>. Facilities/commercial-services firms in general are plausibly concentrated in major metros (assumption, unverified — not independently confirmed for this document). Given Vectorae has no existing US footprint, geographic concentration is not yet a targeting lever worth building into outreach — it's more useful once outreach starts producing results and a pattern emerges.

---

## 2. Decision-maker profile

Titles are consistent across sub-niches at this company size:
**owner/founder**, **general manager**, or (in staffing and marketing
agencies specifically) **head of sales/BD**. At 5–50 employees this is almost
always a single named person, not a committee — matching the original
framing's "single decision-maker" assumption. This part of the original
thesis holds up.

**Current methods, researched rather than assumed:**

- Staffing agencies: <cite index="30-1">41% of new agency clients come from referrals and 44% from word of mouth — 85% of new business comes from relationships, not ads</cite>. Where they do go outbound, <cite index="24-1">LinkedIn remains the most effective channel for reaching staffing agency decision-makers, though most agencies use it incorrectly</cite>, and <cite index="29-1">planning for eight to ten touchpoints across four to six weeks across email, LinkedIn, and phone is a realistic baseline for commercial staffing prospects</cite> — i.e., manual, effortful, multi-touch, and something the buyer already knows they're bad at executing consistently.
- Cleaning/janitorial: no sub-niche-specific sourcing on acquisition method found in this pass — treat as **(assumption, unverified)**: most likely referral- and bid-driven (property manager relationships, RFPs), similar in spirit to staffing's referral dependency.
- Marketing agencies: this buyer already runs outbound for clients, so their own new-business process is plausibly the most self-aware and the most likely to already be a semi-structured (if under-resourced) manual process — **(assumption, unverified)**, not separately sourced.

**Technical sophistication — flag for product, not just marketing:** this
buyer runs field operations, contracts, and (for agencies) client campaigns —
they are not naive users, but they are also not going to configure API
integrations or tune a sequencer's send logic themselves. <cite index="43-1">Expandi, a comparable outbound automation tool, is priced at $99/month for the software alone, with the explicit caveat that this doesn't include "the person operating it."</cite> That gap — tool cost vs. operating cost — is exactly where Vectorae's
done-for-you positioning has to land for this buyer, and it means onboarding
needs to minimize configuration decisions rather than offer them. This is a
product/onboarding note, not just a marketing one.

---

## 3. Pain points and buying triggers

**Trigger moments** (assumption, unverified — no sub-niche-specific survey
data found; inferred from the referral-dependency and slow-season framing
that recurs across the staffing and cleaning-industry sources above): loss
of a major recurring contract, a slow season with no pipeline behind it, or a
competitor visibly winning a bid/RFP the buyer expected to win. This matches
the original framing's hypothesis; nothing in this research pass contradicts
it, but nothing directly confirms it either — it should be tested against
real conversations with early customers rather than assumed as fact in
messaging copy.

**Cost of the problem, where quantifiable:**

- Staffing: <cite index="22-1">referral-based leads cost around $25 to acquire versus a $497 industry average for other channels</cite> — meaning the buyer's default acquisition method (referral) is cheap but has a hard ceiling on volume; growth beyond what referrals produce requires paying roughly 20x more per lead through other channels, which is the affordability wedge for a tool that makes outbound cheap-per-lead.
- Cleaning: <cite index="15-1">industry revenue growth of about 1.8% in 2026 is too slow to carry an individual business — margin has to come from pricing and efficiency rather than volume</cite>, and the business count is growing faster than revenue, meaning more competitors are chasing a slower-growing pool of contracts. This is a market-level pressure argument, not a single-business cost figure — flagged as market context, not a "you lose $X/year" claim, since no such figure was found.

---

## 4. Budget reality

**What this buyer pays today for adjacent things**, sourced:

- General-purpose CRM: <cite index="41-1">$7 to $60/month for CRM software, $10 to $209/month for marketing automation, $56–$99/month for lead scraping tools</cite>.
- Self-serve prospecting/sequencing (the closest existing substitute for
  Vectorae): <cite index="50-1">Apollo starts at $49 per seat per month, self-serve, with no contract</cite>; <cite index="43-1">Expandi starts at $99/month for the software layer alone</cite>.
- Agency/DFY lead generation (the other closest substitute — paying someone
  else to do the work, which is the model closer to Vectorae's positioning):
  <cite index="44-1">small businesses and startups typically spend $500 to $2,500 per month</cite> on budget-conscious lead-gen approaches, while <cite index="43-1">full agency retainers run $3,000 to $25,000 per month</cite> — the retainer end is well above this buyer's realistic ceiling, but the $500–$2,500/month band is squarely relevant.
- Enterprise data tools this buyer will **not** buy: <cite index="52-1">ZoomInfo's Professional tier runs approximately $14,995/year on a mandatory annual contract</cite>, confirming they're out of reach and out of relevance for this ICP.

**Sanity-check against Vectorae's stated pricing tiers ($49–99 / $199–299 /
$499+):**

- The **$49–99 tier lines up well** against this buyer's existing tool
  spend — it sits right next to what they already pay for Apollo or a CRM,
  so it's an easy "swap this in" decision rather than a new budget line.
- The **$199–299 tier is defensible but is the tier that needs the clearest
  differentiation story**, because it sits above self-serve tool pricing and
  into the low end of "budget-conscious DFY lead gen" territory
  ($500–$2,500/month). At this price the buyer will compare Vectorae against
  a cheap freelancer or a small local lead-gen shop, not against Apollo — the
  pitch has to be "cheaper and faster than a freelancer, more consistent than
  referrals," not "cheaper than ZoomInfo."
- The **$499+ tier is the one to flag as a genuine mismatch risk** for this
  specific ICP. At $499+/month ($5,988+/year), this buyer is now paying more
  than a $500K–$8M-revenue owner-operator business is likely to allocate to a
  single GTM software line, based on the CRM/prospecting-tool spend figures
  above, none of which cluster anywhere near that level for a business this
  size. $499+ is priced closer to where a small DFY retainer starts
  ($3,000–$25,000/month range), which suggests that tier is aimed at a buyer
  who wants meetings delivered, not a subscription — if Vectorae sells that
  tier into this ICP, it likely needs to be sold and scoped as a
  quasi-managed service (with a human-reviewed component, consistent with
  the codebase's existing verification-loop pattern) rather than as "the
  premium software plan," or it will be perceived as overpriced against what
  this buyer has ever paid for anything similar. **This is a real, not a
  default-validated, conclusion** — the earlier pricing wasn't wrong, but it
  wasn't built with this specific ICP's budget ceiling in mind, and the top
  tier needs either a different pitch or a different buyer.

---

## 5. Objections and disqualifiers

**Objections** (assumption, unverified — inferred from the buyer profile
above rather than sourced from a survey of this specific buyer segment):

- "AI-written outreach will sound generic / get flagged as spam" — plausible
  given this buyer's personal, relationship-driven sales history (85% of
  staffing new business, for example, already comes from referrals/word of
  mouth per Section 2) — automation is a bigger leap for a relationship
  seller than for a demand-gen marketer.
- Past bad experience with a lead-gen agency or a lead list purchase —
  plausible given how commonly "cold data" and "bought lists" are called out
  as a bad idea across the lead-gen cost sources found in this research (e.g.
  <cite index="41-1">buying lead lists and cold data is not advised due to its high failure rate from dated data and non-compliant risk</cite>).
- Doesn't believe a tool can understand their specific niche (a commercial
  cleaning company's buyer language and a staffing agency's buyer language
  are genuinely different) — this is where Vectorae's per-ICP research and
  pattern-based tactic classification (channels/motion/cadence, see Section
  7) is a real answer, not just marketing spin, since the product is
  architecturally built to differentiate by niche rather than templating one
  script for everyone.

**Clear disqualifiers:**

- A company already running Salesforce/HubSpot **plus** a dedicated
  sales/BD hire — not a fit regardless of size, since the pipeline problem
  this ICP has is specifically the absence of that infrastructure.
- Sub-5-employee owner-operators with no discretionary software budget
  (Section 1) — right psychographic (single decision maker, real pain) but
  wrong economics.
- Residential-only businesses of any kind — out of scope entirely, since the
  buyer is a homeowner, not a business (carried over from the original
  framing; nothing in this research contradicts it).

---

## 6. Channels and messaging

**Where this buyer spends time**, sourced per sub-niche where research
supports it:

- Staffing/recruiting: <cite index="24-1">LinkedIn is the most effective channel for reaching staffing agency decision-makers</cite>. Industry-specific communities and BD-focused blogs (e.g. the sources cited throughout Section 2/3 of this document) suggest this buyer also actively consumes staffing-specific trade content, meaning niche communities/newsletters are plausible secondary channels (assumption, unverified beyond the LinkedIn finding).
- Marketing/design agencies: least separately researched in this pass;
  **(assumption, unverified)** — likely LinkedIn plus agency-specific
  communities (e.g. agency Slack/Discord groups, Twitter/X marketing
  circles), consistent with this buyer's own professional domain, but not
  independently confirmed here.
- Cleaning/HVAC/facilities: no sub-niche-specific channel research performed
  in this pass — flagged as an open research gap rather than guessed at.

**Messaging angles**, grounded in the pain points above rather than generic
SaaS copy:

1. **"Referrals got you here, they won't get you to the next level."** Speaks
   directly to the 85%-referral reality in staffing (and plausibly similar in
   cleaning/facilities) — not anti-referral, but names the ceiling on
   referral-only growth explicitly, since referrals are cheap
   (<cite index="22-1">around $25/lead</cite>) but capped in volume.
2. **"You already pay for a CRM you don't have time to fill."** Targets the
   buyer who already owns a $7–60/month CRM (Section 4) that sits mostly
   empty because nobody's feeding it new prospects — reframes Vectorae as
   the missing input to a tool they already bought, not a new category of
   spend.
3. **"Not another database you have to work — a service that works it for
   you."** Directly answers the Apollo-adjacent objection from Section 4/2:
   this buyer doesn't lack access to contact data at $49/month, they lack the
   time and skill to turn that data into a working sequence — this is the
   done-for-you vs. self-serve distinction that's the actual competitive
   question, not a price argument against ZoomInfo.

---

## 7. Worked example — highest-ranked sub-niche

Highest-ranked sub-niche from Section 1: **small marketing/web-dev/design
agencies.**

Vocabulary below is copied exactly from the codebase, verified directly
against source rather than assumed:

- `CRITERIA_KEYS` (ICP criteria schema) — `app/services/icp_extraction.py`:
  `titles`, `industries`, `locations`, `company_size_ranges`, `keywords`
- `TACTIC_CHANNELS` — `["email", "whatsapp", "linkedin", "phone", "referral", "inbound"]`
- `TACTIC_MOTIONS` — `["cold_outbound", "warm_intro", "inbound_led", "product_led", "partner_led"]`
- `TACTIC_CADENCES` — `["light", "standard", "aggressive"]`
- `FlowType` (from `app/db/models.py`) — `with_clients` (Flow 1) or
  `no_clients` (Flow 2)

**Note on scope:** `pattern_recognition.py`'s `PATTERN_KEYS` (`industry`,
`company_size`, `buyer_role`, `deal_size`, `acquisition_channel`,
`trigger_event`, `sales_cycle_length`) is a *different*, free-text extraction
schema used for past-client pattern extraction, not the closed
channel/motion/cadence vocabulary the task description referenced. The
closed vocabulary actually lives in `icp_extraction.py`'s `TACTIC_CHANNELS` /
`TACTIC_MOTIONS` / `TACTIC_CADENCES`. The worked example below uses the
verified closed vocabulary; the free-text pattern fields are populated too,
since a real Flow 1 seed would carry both.

```json
{
  "icp_criteria": {
    "titles": ["Owner", "Founder", "Managing Director", "Head of New Business"],
    "industries": ["Marketing Agency", "Web Design Agency", "Digital Design Agency"],
    "locations": ["United States"],
    "company_size_ranges": ["8,25"],
    "keywords": ["boutique agency", "creative agency", "digital marketing agency", "web design studio"]
  },
  "tactic_profile": {
    "channels": ["email", "linkedin"],
    "sales_motion": "cold_outbound",
    "cadence": "standard",
    "flow_type": "no_clients"
  },
  "past_client_pattern": {
    "industry": "Marketing agency",
    "company_size": "8-25",
    "buyer_role": "Agency owner / founder",
    "deal_size": "$199-299/mo (assumption — no closed deal to source this from yet)",
    "acquisition_channel": "cold email + LinkedIn",
    "trigger_event": "pipeline gap after a large retainer client churned",
    "sales_cycle_length": "unknown — no closed deal yet (assumption, unverified)"
  }
}
```

`flow_type` is set to `no_clients` (Flow 2) deliberately: this is a
prospective seed for Vectorae's own outbound into a new ICP, not a strategy
anchored on a proven past client — Vectorae doesn't have a signed marketing-
agency client yet to anchor a Flow 1 (`with_clients`) strategy on. Once a
first agency client closes, the `past_client_pattern` block above should be
replaced with real, stated values (not the placeholder/assumption values
shown), and a Flow 1 strategy becomes possible for this niche.

---

## Plain-language summary

Target small marketing, web-design, or digital agencies in the US with
roughly 8–25 employees — not commercial cleaning or staffing first, even
though those were the original front-runners. Agencies already think in
"channels and messaging" terms so they'll get what Vectorae does fastest,
they already spend money on adjacent tools (a CRM, maybe Apollo) so a new
subscription isn't a new budget category, and staffing is more crowded with
competing point-solutions than assumed going in. Lead with LinkedIn and cold
email at the $49–99 or $199–299 price point — that's what actually matches
what a business this size spends today — and don't push the $499+ tier into
this ICP without turning it into something closer to a done-for-you service,
because at that price this buyer is comparing you to a human retainer, not a
software subscription.