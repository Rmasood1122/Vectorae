# Vectorae strict eval: status report (2026-11-02)

**Phases done:** 0 (recon + baseline) and 1 (brand-leak eval). A P0 LinkedIn
gap was found, contained behind a kill switch, and is **stopped for Rehan**.
Phases 2–6 are not started. No overall score yet. See `rubric_scores.md` for
the two dimensions scored and the hard caps already in force.

## Headline numbers (FACT, reproducible)
| Measure | Before | After GAP-LI-01 | Command |
|---|---|---|---|
| Offline suite | 8,371 tests, 100% pass | see remediation_log.md | `python3 evals/run_evals.py` |
| Compliance suite | 100% | see remediation_log.md | same |
| Gate | PROMOTE (soft warning: runtime 1,122s vs 900s) | see remediation_log.md | `python3 evals/tools/promote.py` |
| Repo brand lint (old names only) | exit 0, 1,916 files | — | `python3 scripts/gate/brand_lint.py` |
| Program brand regex (also calendarharvest) | 658 rows: A 28 / B 303 / C 208 / D 119 | — | `python3 evals/vectorae/tools/brand_leaks.py` |
| Live crawl | 72 URLs, 0 failures | — | `baseline/live_crawl/crawl_meta.json` |
| D7 brand | 2 (capped by class-A leaks) | — | rubric_scores.md |
| D11 LinkedIn | 0 (hard cap; overall ≤ 50) | 0 until Rehan decides | rubric_scores.md |

## P0/P1 gaps
- **Closed:** none.
- **Contained, awaiting Rehan:** GAP-LI-01.
- **Open:** GAP-LI-02, LI-03, SEAL-01, CL-01, CL-02 (P0); BR-01, BR-02, BR-03, EXT-01, OPS-01 (P1).

See `gap_register.csv`.

## Hypotheses from the prompt, checked
- Pricing renders "Loading plans…" for crawlers: **FALSE.** No page's HTML contains it.
- Viewport blocks zoom: **TRUE**, on 70/70 HTML pages (GAP-A11Y-01).
- "AI calls" plan feature: **NOT FOUND** on live pricing. "AI call" appears only as an LLM cost note in help.
- "No caps on API throughput" and "Self-learning playbook": **present**, and still unverified (GAP-CL-01, GAP-CL-02).
- LinkedIn is "not live yet": the label is present on 45 pages. However, the Unipile and scraper code exists in the repo, and a help page describes it as the mechanism (GAP-LI-02).

## Decisions for Rehan (stop gates)
1. **Unipile + RapidAPI scraper (GAP-LI-01).**
   - **(a)** Delete both, and rebuild LinkedIn as L0 assisted tasks plus L3 CSV import (recommended: the only ToS-clean path).
   - **(b)** Keep the code dormant behind the switch. D11 stays 0 and overall stays capped at 50.
   - **(c)** Re-enable it. This breaches LinkedIn's terms and puts users' accounts at risk. Not recommended.
2. **Help copy (GAP-LI-02).** Approve the rewrite that removes "RapidAPI scraper" and prospect-post reading.
3. **Seal the new guard test (GAP-SEAL-01).** This needs a `gate/` PR plus a baseline refresh.
4. **Social profiles in JSON-LD (GAP-BR-03).** Either rename the profiles yourself, or let me drop the `sameAs` entries until they are renamed.
5. **Domain, mailbox, OAuth, Gmail restricted scopes, entity and postal address** (`external_migration_register.csv`). `domain_migration_plan.md` is not written yet.

## Limits of this report
- The live site was fetched through Firecrawl, because this container's egress blocks calendarharvest.com. Status codes and redirect hops therefore come from Firecrawl, not curl. The 301 checks in the prompt need curl from an unblocked host.
- Frontend, status-page and native checks, coverage, security scanners, Lighthouse, axe and lychee have **not** been run.
