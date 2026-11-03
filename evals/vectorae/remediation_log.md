# Remediation log

## 2026-11-02: GAP-LI-01 contained (kill switch), not closed
- Change: `settings.linkedin_unofficial_providers_enabled` (default False) gates
  `linkedin_outreach.client_for` (every acting Unipile path) and
  `linkedin_posts.fetch_recent_posts` (Unipile + RapidAPI readers). Erasure's
  disconnect (`unipile_linkedin.get_client`) stays ungated.
- New test: `tests/test_linkedin_unofficial_providers_off.py` (8 tests).
  - Before the fix: 6 failed, 2 passed. The structure and revoke tests pass either way.
  - After the fix: 8 passed.
- Opt-ins added: `tests/test_linkedin_channel.py`, `tests/test_network_messages.py` and
  `tests/test_personalization.py` set the switch on, because they exercise the provider path behind it.
  - None of these three files is sealed.
  - Sealed guard tests are unchanged and green.
- Full suite:
  - Before: 8,371 tests, 1.0 / 1.0, PROMOTE.
  - After: 8,379 tests, 1.0 / 1.0, PROMOTE.
  - Both runs carry the soft runtime warning (this container).
- Scores: unchanged. D11 stays 0 under the program rule (the code still exists).

## 2026-11-02: option (a) — LinkedIn rebuilt user-permission-based
- **PR A** (`claude/dreamy-pasteur-qflxk2`, commits c027595 and 81e9a7d):
  - L0 assisted tasks and L3 consent-first import, plus migration 0097.
  - Offline suite: 8,422 tests, 1.0/1.0, gate PROMOTE.
- **PR B** (`claude/gate-linkedin-unipile-removal`, stacked on PR A):
  - Unipile, the RapidAPI scraper, `linkedin_limits`, and the network send and bulk-send paths are removed.
  - Sequence LinkedIn steps become L0 tasks through `AssistedLinkedInChannel`.
  - One-time offboarding script added.
  - 5 sealed tests rewritten (justification: `docs/program/plans/gate-linkedin-unipile-removal.md`).
  - 4 guard files sealed (56 → 60).
  - Baseline refreshed from 2 green runs: 8,370 tests, 1.0/1.0, runtime mean 1,131 s.
  - Block fixtures regenerated from real sabotage runs; `verify_block_fixtures`: all 3 hard gates fire, healthy promotes.
  - `promote`: PROMOTE (soft runtime warning only).
- **Pre-fix evidence:**
  - `test_linkedin_guard.py` on PR A's code: 6 of 10 fail.
  - The new L0/L3 test files cannot be collected before PR A.
- **A failure this pass caught:** the first refresh attempt found 2 red tests that referenced deleted code (autopilot mocks, the webhook-dispatch source check). The refresh was stopped, the baseline files were reverted, the tests were fixed and the refresh was redone.
- **Scores:**
  - **D11:** the hard cap condition is gone once PR B merges, because no automation or scraping code ships (guard-tested). Not re-scored here: D11 also needs L1/L2, which are not built.
  - **D7:** unchanged at 2 (the class-A slugs are still open).
