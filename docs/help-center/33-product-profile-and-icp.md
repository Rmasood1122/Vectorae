---
id: product-profile-and-icp
title: Your product profile and ideal customer profile
audience: customer
last_verified: 2026-10-18
owner: help-center
sources:
  - frontend/src/app/(app)/strategies/new/page.tsx
  - frontend/src/components/intake/wizard.ts
  - frontend/src/lib/localBusiness.ts
  - app/api/products.py
  - app/api/schemas.py
  - app/api/strategies.py
  - app/db/models.py
  - app/services/pattern_recognition.py
  - app/pipeline/engine.py
  - app/pipeline/registry.py
  - app/services/icp_extraction.py
  - app/api/leads.py
  - app/workers/tasks.py
  - app/services/lead_scoring.py
  - app/services/fpta_scoring.py
  - frontend/src/lib/lead-score.ts
  - frontend/src/app/(app)/leads/detail/page.tsx
  - frontend/src/app/(app)/strategies/detail/page.tsx
  - frontend/src/app/(app)/strategies/page.tsx
  - frontend/src/app/(app)/quickstart/page.tsx
  - frontend/src/lib/quickstart.ts
  - app/api/quickstart.py
  - app/services/quickstart.py
  - app/services/system_settings.py
---

# Your product profile and ideal customer profile

Everything Vectorae researches, every lead it finds and every score it gives starts from what you type into the **New strategy** form. This article explains each field, which answers matter most, how Vectorae turns them into an ideal customer profile (ICP), and how that profile decides which leads you get and how they are scored.

## Which fields make up my product profile?

The first step of **New strategy**, **What do you want clients for?**, asks for:

- **Name**: required, up to 200 characters.
- **Describe the product or skill**: required, with no length limit. The hint reads "What it does, who it's for, what it costs…".
- **A product** or **A skill / service**.
- **Is this a local business?**: **No** by default. Choosing a business type adds **City or neighbourhood**, **Postcode (optional)**, **Country (2 letters)**, **Radius** and **What you offer business buyers (optional)**.

If you answer **Yes — build on proven patterns** to **Do you have any past clients for this?**, each **Past client** card asks for **Client details** ("Industry, size, who the buyer was, deal size…") and **How were they acquired?** ("Referral? Cold email? What triggered the deal?"). Both are required for every client you add. Through the API, up to 50 past clients can be sent in one request.

There is no form for typing an ICP directly; Vectorae builds it from your answers and its research (see below). Once a strategy is created, its name and description cannot be edited, in the app or through the API, and the app has no screen for changing past clients (the API can still add more, and research steps that run later read them). To try different wording, start a **New strategy**.

## Which answer matters most?

It depends on your answer to the past-clients question.

**No past clients.** Your description is the only thing Vectorae knows about your offer. Every one of the research steps receives the same three lines: the name, the type (product or skill) and the description. If the description is vague, all of the research is built on that vagueness.

**With past clients.** Vectorae uses AI to turn each client's two text boxes into seven short fields: industry, company size, buyer role, deal size, acquisition channel, trigger event and sales cycle length. Anything you did not state, and that cannot safely be inferred, is left empty. The research steps see only these seven fields for each client, not your original text. So the most useful thing you can write is the facts behind those seven fields. If the extraction fails, your clients are still saved, the fields are treated as unknown, and the strategy still launches.

## How do I write a description that gives good research?

The first two research phases work through your offer and your buyer: core offering, problem and cost of inaction, pricing logic, firmographics (industry, size, geography, revenue), buyer personas, decision-maker roles, pain points, buying triggers and disqualifiers. Answering those in plain words gives the research facts to work from instead of guesses. A good description says:

- **What it does**, in one or two sentences, and the result the buyer gets.
- **Who buys it**: the job title of the person who signs, the industry and the company size.
- **Where** your buyers are, if you only sell in some countries or regions.
- **What it costs**, or a price range.
- **Why they buy now**: the event that usually starts a purchase.
- **Who it is not for**, so the research can rule them out.

Write facts about your own offer, not marketing claims. What you say about yourself is not checked by Vectorae's claim check, so it must be true.

## Where does my ideal customer profile come from?

Research phase 2 is the ICP definition, and phase 3 is market sizing and segmentation. From their output, Vectorae uses AI to extract five lists of search criteria:

| Criterion | What it holds |
|---|---|
| Titles | Decision-maker job titles to target |
| Industries | Industry or vertical names |
| Locations | Regions or countries |
| Company size ranges | Employee ranges, such as 11 to 50 |
| Keywords | 3 to 8 short qualifier keywords |

For a local business, the locations are replaced by your service area (the radius is not sent to the lead provider). The app does not show these lists for a normal strategy. To read the reasoning behind them, open the strategy and press **View documents**; the ICP definition is one of the phase summaries. Through the API, a lead-sourcing request can pass its own `icp_criteria` with these five keys instead of having them extracted.

## How does the ICP decide which leads I get?

Each time you press **Find leads**, Vectorae extracts the criteria again from the strategy's phase 2 and 3 research. This is an AI call that counts toward your daily AI limit. The criteria become the people search sent to the lead provider, as described in the lead sourcing article.

The first time the criteria exist (when research finishes, or at your first **Find leads** if that step failed), they are also fixed as the strategy's pattern key, which links the strategy to its learning-loop results. That key does not change afterwards. See the patterns and playbooks article.

## How does my profile affect lead scores?

Two scores use it:

- **AI booking likelihood** on a lead's page combines **Role seniority** (25%), **Industry fit** (25%), **Email verification** (20%), **Company signals** (15%) and **Past campaign results** (15%). AI then reviews each lead against your product name, description and ICP and may move the score by at most 20 points.
- **F-P-T-A** (Fit, Problem, Timing, Access) is explained in the personalization article. Its AI step sees the same product and ICP and may move each dimension by at most 15 points.

**Known limitation:** in the current release, the data-based starting point of both scores does not read the ICP's industries, keywords or company size ranges, so it treats them as unknown. **Industry fit** therefore shows 50% for every lead, and F-P-T-A Fit never reports a company-size match. Your ICP reaches these scores only through the AI review step.

## Is there a faster way to start?

If your administrator has turned on Quickstart (it is off by default), the **Strategies** page shows a **Quickstart** button. You give your **Website**, **Your offer, in one line** (10 to 300 characters) and an optional **Product name**. Vectorae reads your website, works out who buys, finds up to 25 verified leads by default and writes a first draft email to each, while the full research runs. The run shows the ICP it used as "Looking for:". Drafts are for review only: you can copy or discard them, and nothing is sent. Outreach still goes through the verified strategy.

## Related
- [Getting started](03-getting-started.md)
- [The research pipeline](04-research-pipeline.md)
- [Lead sourcing and email verification](06-lead-sourcing-and-email-verification.md)
- [Personalization and claim verification](07-personalization-and-claim-verification.md)
- [Strategy patterns and playbooks](34-strategy-patterns-and-playbooks.md)
- [Who Vectorae is for and not for](02-who-its-for-and-not-for.md)
