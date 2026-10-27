# Gate change: seal the learning-aggregate tenancy guards, refresh the baseline

Date: 2026-10-27. Branch `claude/gate-seal-subject-sendtime`. The founder chose this item in
the session ("Seal subject-line guard", together with "Send-time per account").

## Why this is its own PR

CLAUDE.md: a seal or baseline change is its own reviewed PR with a written justification, and
new legal or safety guard files join the sealed list. The two files below were added with
their fixes in #144 and #145 and are not sealed yet, so they could still be weakened in the
same PR as the code they guard.

## 1. `evals/compliance_testset.yaml`: 2 files added (49 → 51), 9 tests

| File | Tests | Guard |
|---|---|---|
| tests/test_subject_patterns_no_example.py | 6 | `subject_line_patterns` is one table for the install: no sent subject line (a prospect's name or company) is extracted, stored or returned to another account; the nightly write clears what the old code stored; a failed query rolls the session back |
| tests/test_send_time_slots_per_account.py | 3 | send-time slots are built from one account's sends and served only to that account; an account with no data gets the default hours, not another's |

Both files are sealed whole: every test in them is a guard, so nothing unrelated is frozen,
and no test is moved. The two rollback tests in the first file guard the same module's
session handling; they are kept with it rather than split out.

Not sealed: `tests/test_whatsapp_cloud_contract.py` (#146, not on master yet). Its
international-number refusal is a misdelivery guard and can be sealed once #146 is merged.

## 2. `evals/baseline.json` and block fixtures: refreshed

The sealed set changed, so `dataset_hash` changed and the baseline must be rebuilt. It is
rebuilt from 2 runs with `baseline.py --runs 2`, the compliance and count fixtures are
regenerated from the honest junit, the suite fixture from a real run with an injected failing
test, and `healthy.json` from the honest candidate. Thresholds and the registry are unchanged.

The evidence is in the PR description.
