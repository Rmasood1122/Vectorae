# Gate change (proposed, not yet made): seal the Phase 3 AI guard tests

Date: 2026-10-22. **Status: superseded 2026-10-25 by `phase5-seal-ai-and-tenancy-guards.md` (sealed).** Original proposal: Nothing in `evals/compliance_testset.yaml`, `evals/baseline.json` or the block fixtures has changed. Sealing is its own reviewed PR, made on a `claude/gate-` branch after the Phase 3 PRs merge (CLAUDE.md; the `protect_sealed` hook). The pattern is the same as #86, #92 and #94.

## Why

Phase 3 added deterministic guards around the AI calls. Each one decides what a model's text may do: say, link to, promise, or claim. If a later change breaks one of them, CI goes red only through unsealed tests, and an unsealed test can be weakened or deleted in the same PR. Sealing makes the gate fail closed on them.

## 1. Whole files to seal (they test guards only)

| File | Arrives in | Guard |
|---|---|---|
| `tests/test_complete_json_contract.py` | item 1 (#122) | `complete_json` returns an object or raises; it never returns a list, a string or `null` |
| `tests/test_prompt_injection_containment.py` | on master since #62; item 1 (#122) adds the second-order and analysed-text tests | untrusted text is delimited; a reply cannot steer the next message (second-order injection); analysed model text is delimited |
| `tests/test_prompt_caching.py` | item 3 | cache writes and reads are metered at their real price, so the spend caps (F-AI-01, P0-5, F07) cannot be exceeded through caching |

## 2. Guard classes inside feature files: move them, then seal

These guards sit in files that mostly test feature behaviour. Sealing those whole files would freeze unrelated feature tests. Instead, the gate PR moves the classes below, unchanged, into one new file, `tests/test_ai_output_guards.py`, and seals that file. A move with no edits keeps every assertion, and the PR diff shows it.

| Class (current file) | Guard | Round |
|---|---|---|
| `TestConsentGuard` (`test_reply_intelligence.py`) | an opt-out reply gets no model call and no draft | I1 |
| `TestShipBlockingPassRechecked`, `TestFactualPassRechecked` (`test_verification_oscillation.py`) | a later rewrite cannot drop what the legal pass approved, or add unsourced facts | I1, I3 |
| `TestForeignLinksStripped`, `TestBodyLengthCeiling`, `TestWhatsAppSlotChecks`, `TestGrounding` (`test_personalization.py`) | email links, body length, WhatsApp slots, the grounding helper | I1, I2, I3 |
| `TestRenderLinks` (`test_linkedin_channel.py`) | LinkedIn copy links | I2 |
| `TestDraftLinks`, `TestDraftGrounding` (`test_reply_intelligence.py`) | reply-draft links and figures | I2, I3 |
| `TestGeneratedBodyRules` (`test_whatsapp_template_lifecycle.py`) | drafted templates: links, guarantees, invented figures | I2 |
| `TestModelDerivedGuidanceIsFiltered` (`test_voice_profile.py`) | style and voice profiles carry no instruction or link | I2 |
| `TestHumanClaimWithAName` (`test_voice_twin.py`) | "I'm Priya, a real person" is a human claim | I2 |
| `TestClaimsAboutUs` (`test_reply_copilot_strictness.py`) | Copilot makes no unchecked claim about us | I3 |
| `TestActionItemsGrounded` (`test_meeting_recording.py`) | action items and due dates come from what was said | I3 |
| the new spoken-stop tests (`test_phone_calls.py`) | a spoken stop on any call is recorded without the model | I1 |
| `test_an_ungrounded_brief_falls_back` (`test_followup.py`), `test_a_reason_the_reply_never_gave_is_not_kept` (`test_reengagement_memory.py`), `test_an_invented_price_and_quote_are_removed`, `test_an_invented_price_date_or_promise_is_removed_from_the_draft` (`test_meeting_prep.py`), `test_an_invented_meeting_or_round_is_removed_from_the_draft` (`test_network_messages.py`), `test_an_unsupported_claim_is_stripped_and_the_title_replaced` (`test_personalization.py`) | grounding on each touchpoint | I3 |

Some of these depend on fixtures local to their files. The move brings those fixtures along or reuses `conftest.py`; this is checked in the gate PR, not assumed here.

## 3. Then

- Rebuild `evals/baseline.json` and the block fixtures, because the dataset hash changes: `baseline.py --runs 2`, then the three sabotage fixtures.
- Run `promote.py` and `verify_block_fixtures.py`, and record the evidence in the gate PR.

## Not proposed

- `tests/test_output_schemas.py` and `tests/test_gateway_temperature.py` test request shape, not a legal or safety duty.
- The golden eval sets (`evals/ai/golden/`) are already enforced by `tests/test_ai_evals.py`, which the full suite gates.

## Founder decision needed

Approve this scope, or narrow it, before the gate PR is made.
