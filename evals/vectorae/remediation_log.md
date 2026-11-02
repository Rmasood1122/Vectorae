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
