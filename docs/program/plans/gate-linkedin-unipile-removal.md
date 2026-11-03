# Gate change: remove Unipile and the RapidAPI LinkedIn scraper; seal the LinkedIn guards

Date: 2026-11-02 · Branch: `claude/gate-linkedin-unipile-removal` (stacked on PR A,
`claude/dreamy-pasteur-qflxk2`) · Program: `evals/vectorae/` (Phase 5B)

## Decision (founder, 2026-11-02)
**Option (a):** delete Unipile and the scraper, and build LinkedIn as L0 (assisted tasks: Vectorae drafts, the user sends) plus L3 (the user's own connections export, consent-first). A gate branch was approved for the sealed-test changes. A revoke step was required, because it was not confirmed that production has no connected accounts.

## Why the sealed tests change
These tests guarded behaviour that is now deliberately removed. Each one is rewritten to guard what replaces it. None is deleted or weakened to pass.

| Sealed file | Old guarantee | New guarantee |
|---|---|---|
| `test_account_erasure.py` | erasure disconnects LinkedIn accounts at Unipile; the Unipile callback refuses an erased account | erasure removes the legacy LinkedIn rows, LinkedIn tasks and consents, and records that legacy rows existed (the sessions are ended by the offboarding script); the Unipile callback no longer exists (404/405) |
| `test_daily_send_approval_e2e.py` | a LinkedIn step is held, then sent through Unipile after an acknowledged approval | the hold and the acknowledged approval are unchanged; the real channel queues exactly one L0 task only after approval, and that task is still `pending` (the user approves it) |
| `test_daily_send_approval_network.py` | a Unipile bulk send waits for today's LinkedIn approval | no send or bulk route or worker exists; approving LinkedIn releases nothing on the network and never approves a task; each task needs its own approval |
| `test_network_messages_guards.py` | (unchanged guard) | only the fixture for the removed Redis limiter is gone |
| `test_privacy_docs_truth.py` | every integration module is a named processor (Unipile, RapidAPI) | the deleted modules are gone from the map; `linkedin_channel` is listed as not a processor (it sends nothing out) |

## Newly sealed (same commit, per CLAUDE.md)
- `test_linkedin_guard.py`: no automation or scraping mechanism, no third-party LinkedIn provider outside a reviewed allowlist, LinkedIn API calls consent-gated, every LinkedIn OAuth scope has a consent text. It fires on PR A's code: 6 of 10 tests fail there.
- `test_linkedin_tasks.py`: L0 per-task approval and its 24-hour expiry, no bulk approve, suppression checked at create, approve and open, caps of 20 and 50 (lowerable only), business hours, audit without message text.
- `test_linkedin_l3_attestation.py`: no attestation means no import; a new consent-text version means re-consent; no email leads; suppressed people are hidden.
- `test_linkedin_unipile_offboard.py`: the one-time script ends every not-yet-revoked session, including paused accounts, keeps failed ones for a re-run, purges stored posts, and prints counts only.

## Thresholds and registry
Unchanged. `evals/registry.yaml` is not edited. The sealed list grows from 56 to 60 files, so `dataset_hash` changes and the baseline and block fixtures are refreshed with the documented procedure (`evals/tools/baseline.py --runs 2`, fixtures regenerated from real runs).

## Founder actions before or with merge (stop gates)
1. Run `scripts/linkedin_unipile_offboard.py` against production. Do a dry run first, then `--apply`. The script needs `UNIPILE_DSN` and `UNIPILE_API_KEY`.
2. In Unipile's dashboard, delete any remaining accounts. Accounts a user disconnected before this change were removed only from Vectorae's table (GAP-LI-03), so only the dashboard can reach them.
3. Cancel the Unipile subscription and revoke the RapidAPI key.
4. Privacy Policy (`docs/PRIVACY_POLICY.md`, `frontend/src/app/privacy/page.tsx`): remove Unipile and RapidAPI and describe L0/L3 data. This is legal text, so the founder edits it. Until then the policy over-discloses.
5. Drop the legacy `linkedin_accounts` table and the Unipile lead columns in a later, founder-run migration. They are kept in this change.
