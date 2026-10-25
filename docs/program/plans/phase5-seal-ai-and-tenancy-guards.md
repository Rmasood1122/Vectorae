# Gate change: seal the Phase 3 AI guards and the F-SEC-05 tenancy guards, refresh the baseline

Date: 2026-10-25. Branch `claude/gate-seal-ai-and-tenancy-guards`. The founder approved the
scope in the session ("Seal as proposed"). It covers `phase3-seal-ai-guards.md` plus the two
tenancy test classes from #137 and #139.

## Why this is its own PR

CLAUDE.md: a seal or baseline change is its own reviewed PR with a written justification. The
`protect_sealed` hook allows sealed-file edits only on `gate/` or `claude/gate-` branches. This
follows the same pattern as #86, #92 and #94.

The guards below decide what a model's text may say, link to, promise or claim, and who may
reach another account's pages or the install's templates. Until now CI went red on them only
through unsealed tests, and an unsealed test can be weakened or deleted in the same PR as the
code it guards. Once sealed, the gate fails closed: any of these tests failing, or any of these
files going missing or collecting zero tests, BLOCKs.

## 1. Moved, unchanged (commit 1)

Sealing whole feature files would freeze unrelated feature tests. So the guard classes and
tests moved into one `*_guards.py` file per source module:

- 17 whole classes and 12 single tests;
- a single test sits inside a class of the same name, which carries that class's own fixtures;
- the module helpers, fixtures and autouse fixtures they use are copied, so each file stands
  alone.

How the move was checked:

| Check | Result |
|---|---|
| Every moved class or test is byte-identical to master | 29 of 29 |
| Moved code is gone from its source | 29 of 29 |
| Test IDs collected from the source files plus the new files, against the source files on master | the same 594 |
| Run | 594 passed |
| `ruff --select F` | only the 32 F811 already on master in `test_reply_copilot_strictness.py` |

Copying instead of importing keeps a sealed test from depending on an unsealed file: a later
edit to `tests/test_website_builder.py` cannot change what `TestTenancy` runs against. The cost
is duplicated helpers, for example `GOOD_HTML` and `_make_page` in two files.

## 2. `evals/compliance_testset.yaml`: 18 files added (31 → 49), 154 tests

| File | Tests | Guard |
|---|---|---|
| tests/test_complete_json_contract.py | 10 | `complete_json` returns an object or raises; never a list, a string or null |
| tests/test_prompt_injection_containment.py | 20 | untrusted text is delimited; a reply cannot steer the next message; analysed model text is delimited |
| tests/test_prompt_caching.py | 14 | cache writes and reads are metered at their real price, so spend caps hold |
| tests/test_reply_intelligence_guards.py | 11 | an opt-out reply gets no model call and no draft (I1); reply drafts carry no foreign link or unsourced figure (I2, I3) |
| tests/test_verification_oscillation_guards.py | 6 | a later rewrite cannot drop what the legal pass approved, or add unsourced facts (I1, I3) |
| tests/test_personalization_guards.py | 25 | foreign links stripped, body length capped, WhatsApp slots checked, grounding helper, unsupported Loom claims removed |
| tests/test_linkedin_channel_guards.py | 7 | LinkedIn copy carries no foreign link |
| tests/test_voice_profile_guards.py | 6 | style and voice profiles carry no instruction or link |
| tests/test_voice_twin_claim_guards.py | 3 | "I'm Priya, a real person" counts as a human claim |
| tests/test_reply_copilot_strictness_guards.py | 3 | Copilot makes no unchecked claim about us |
| tests/test_meeting_recording_guards.py | 3 | action items and due dates come from what was said |
| tests/test_phone_calls_guards.py | 16 | a spoken stop on an outbound call is recorded without the model, once (I1) |
| tests/test_followup_guards.py | 3 | an ungrounded follow-up brief falls back |
| tests/test_reengagement_memory_guards.py | 1 | a reason the reply never gave is not kept |
| tests/test_meeting_prep_guards.py | 2 | invented prices, quotes, dates or promises are removed |
| tests/test_network_messages_guards.py | 1 | an invented meeting or funding round is removed |
| tests/test_website_builder_guards.py | 12 | F-SEC-05: our pages are admin-only; a customer reaches only their workspace; export, sitemap and link menu never mix sites |
| tests/test_whatsapp_template_guards.py | 11 | F-SEC-05: only admins manage templates, others see approved only; drafted templates carry no link, guarantee or invented figure |

Not sealed, as the proposal said: `tests/test_output_schemas.py` and
`tests/test_gateway_temperature.py` check request shape, not a legal or safety duty. The golden
eval sets are already gated through `tests/test_ai_evals.py`.

## 3. `evals/baseline.json` and block fixtures: refreshed

The sealed set changed, so `dataset_hash` changed, and the baseline must be rebuilt. It is
rebuilt from 2 runs with `baseline.py --runs 2`, and the three block fixtures are regenerated
from the honest junit. Thresholds and the registry are unchanged.

The evidence is in the PR description.
