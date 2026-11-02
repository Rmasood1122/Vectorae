# Vectorae rubric: baseline (2026-11-02, PROVISIONAL)

Scope of this baseline: only the dimensions with deterministic evidence
gathered in Phases 0–1. A dimension with no score is **not scored yet**.
That means it was not measured, not that it scored zero. No overall score
is computed until every dimension has been measured, but the hard caps below
already bind it.

| # | Dimension | Weight | Score | Evidence | Confidence |
|---|-----------|--------|-------|----------|------------|
| D7 | Brand consistency | 8 | **2** (capped) | `brand_leaks.csv`: 28 class-A rows (old-name URL slugs, media file names, llms.txt link), all live on calendarharvest.com (`baseline/live_crawl/pages.jsonl`). Strong controls exist (`scripts/gate/brand_lint.py` exits 0 over 1,916 files, with a reasoned 154-line allowlist), but any class-A leak caps D7 at 2. | High (deterministic) |
| D11 | LinkedIn consent and platform compliance | 10 | **0** (hard cap) | Unipile session-based actions plus a "RapidAPI LinkedIn scraper" (`app/integrations/unipile_linkedin.py`, `app/integrations/linkedin_posts.py`, `app/services/credentials.py:93`). The live help page states the scraper fallback. The user's disconnect does not revoke the Unipile session (`app/api/linkedin.py:208-212`). | High |
| D8 | Website quality and SEO | 8 | not scored | Partial evidence. Viewport zoom is disabled on 70/70 pages (WCAG 1.4.4 fail). The offline banner is in the static HTML of 70/70 pages. Pricing does **not** render "Loading plans…" (hypothesis refuted, FACT). Lighthouse, axe and lychee have not been run. | — |
| D1 | Claim truthfulness | 15 | not scored | `claim_truth_matrix.csv` not yet built. Two pricing claims are open (GAP-CL-01, GAP-CL-02). The "AI calls" plan feature was **not found** on the live pricing page (the crawl found "AI call" only as an LLM cost note). | — |
| D2–D6, D9, D10 | — | — | not scored | Phase 2 work. Note for D2: `app/core/production_guard.py:224-243` refuses to start with a placeholder CAN-SPAM postal address. Whether production has a real one is UNKNOWN. | — |

## Hard caps already triggered
- **LinkedIn automation/scraping code present → D11 = 0, overall ≤ 50.**
  The kill switch (GAP-LI-01) stops it from running, but the code still
  exists. Under the program rule the cap lifts only when the code is
  removed or Rehan records a decision.
- **Class-A brand leak → D7 ≤ 2.**

## Method
Deterministic only so far (rg, the brand_leaks.py scanner, a Firecrawl crawl
at maxAge 0, pytest). No LLM-judged items yet, so the two-pass rule has not
applied.
