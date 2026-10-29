# Gate change: seal the vendor send and consent guards, refresh the baseline

Date: 2026-10-29. Branch `claude/gate-seal-vendor-guards`. The founder chose this item in the
session ("Seal vendor guards", together with the Calendly, vendor and Gmail-threading items).

## Why this is its own PR

CLAUDE.md: a seal or baseline change is its own reviewed PR with a written justification, and
new legal or safety guard files join the sealed list. The guards below were added with their
fixes in #146, #148, #149, #151 and #157, inside contract test files that also hold ordinary
behaviour tests. Unsealed, they could be weakened in the same PR as the code they guard.

## 1. Guard tests moved, byte-identically, into five new files

Each test (or class) was cut from its contract file and pasted unchanged into a new
`*_guards.py` file; helpers only the moved tests used moved with them, helpers both sides need
were copied. Checked mechanically: every moved definition is byte-identical to its text on
master (`ast` block comparison against `git show origin/master:<file>`), and the 92 test ids
collected from the source files before the move are the same 92 after it. Only now-unused
imports were removed from the source files; no test, assertion or fixture changed.

| New sealed file | Moved from | Tests | Guard |
|---|---|---|---|
| tests/test_whatsapp_number_guards.py | test_whatsapp_cloud_contract.py (#146) | 18 | WhatsApp only to numbers in international format (Meta prepends the business's own country code otherwise: misdelivery to a stranger); a template Meta paused / disabled / archived / deleted is never sent, a reinstated one is |
| tests/test_gmail_send_once_guards.py | test_gmail_api_contract.py (#148) | 15 | a send that may have reached Gmail (timeout, 5xx) is never repeated; quota 403/429 retry later; the provider's reason is kept |
| tests/test_data_subject_objection_guards.py | test_hunter_api_contract.py (#149) | 5 | a Hunter 451 (the person objected to processing) drops the lead and suppresses the person in every find/verify path, legacy and waterfall |
| tests/test_voice_call_guards.py | test_hubspot_recall_voice_contract.py (#151) | 9 | an ElevenLabs call that may be ringing is never redialled; one that did not start is not "queued"; post-call webhooks older than 30 minutes are refused; the Vapi control URL needs our key; the recommended voicemail detector |
| tests/test_lead_erasure_threading_guards.py | test_gmail_threading.py (#157) | 1 | erasing a lead erases the Message-ID / References stored on its sent emails |

48 tests; the sealed list grows from 51 to 56 files. Not sealed (contract behaviour, not a legal
or safety guard): Apollo search, Unipile limits, Calendly paging and reschedules, Zoom host,
Recall transcription mode, HubSpot OAuth endpoints and scopes.

The voice file includes the Vapi voicemail-detector test because it sits in the same class as
the control-authentication test and was moved whole rather than split.

## 2. `evals/baseline.json` and block fixtures: refreshed

The sealed set changed, so `dataset_hash` changed and the baseline must be rebuilt. It is
rebuilt from 2 runs with `baseline.py --runs 2` on a quiet machine; the compliance and count
fixtures are regenerated from the honest junit, the suite fixture from a real run with an
injected failing test, and `healthy.json` from the honest candidate. Thresholds and the
registry are unchanged. This also re-measures `suite_runtime_s` on the current container,
which has run 760-900 s against the old 688.6 s baseline since 2026-10-27.

The evidence is in the PR description.
