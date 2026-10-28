# Learning Loop — Vectorae

## Overview

Every campaign outcome feeds back into a playbook that improves future strategies. The system learns which subject lines, offers, and sequences work for which ICPs — without any manual curation.

---

## Data Flow

```
1. Campaign runs (sends, opens, replies, bookings, wins)
          ↓
2. Every event written to outcomes table in real time
          ↓
3. Nightly aggregation (Celery Beat, 00:05 UTC)
   - Groups outcomes by (strategy_id, variant, pattern_key)
   - Computes reply_rate = replies / sends, booking_rate = bookings / sends
   - Upserts PlaybookScore rows
          ↓
4. auto_promote_winners
   - For each strategy with >= 2 variants and >= PLAYBOOK_MIN_SAMPLE (30) sends per variant:
   - Runs two-proportion z-test
   - If winner has AB_MIN_LIFT (10%) relative improvement AND p < AB_SIGNIFICANCE_THRESHOLD (0.05):
     - Sets strategy.default_variant = winner
     - Writes ab_promoted outcome event (idempotent — won't promote twice)
          ↓
5. New strategy creation
   - Pipeline phases 6 and 8 query PlaybookService.get_insights(pattern_key)
   - If insights exist (sample_size >= PLAYBOOK_MIN_SAMPLE):
     - Winning subject lines, offers, sequences injected into the Anthropic prompt
   - TF-IDF similarity index also surfaces relevant strategies with similar ICPs
```

---

## Pattern Keys

Pattern keys map an ICP to a bucketed identifier for cross-strategy learning:

```python
pattern_key = f"{industry}_{company_size_bucket}_{primary_channel}"
# Example: "saas_smb_email", "professional_services_mid_whatsapp"
```

Company size buckets: `micro` (1–10), `smb` (11–200), `mid` (201–1000), `enterprise` (1000+).

When a new strategy targets `saas_smb_email`, it receives the aggregated learnings from all strategies across all users that target the same pattern key — if the sample size threshold is met.

---

## A/B Testing

Every outreach sequence can have multiple variants:
- `control` — the original strategy-generated sequence
- `variant_a`, `variant_b` — tested alternatives (different subject line, offer, etc.)

Leads are assigned variants at sequence creation time (random assignment within the campaign).

Winners are promoted automatically — no manual action required. Once promoted, new campaigns using the same strategy automatically use the winning variant as default.

**Guard against double-promotion**: The `ab_promoted` outcome event is idempotent. Running `auto_promote_winners` twice on an already-promoted strategy writes zero new events.

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `PLAYBOOK_MIN_SAMPLE` | 30 | Minimum sends per variant before a playbook score is considered reliable |
| `AB_MIN_LIFT` | 0.10 | Minimum relative lift (10%) for the winner to be promoted |
| `AB_SIGNIFICANCE_THRESHOLD` | 0.05 | p-value threshold for the z-test |

Lower these in development/test environments to observe promotion behavior with smaller datasets.

---

## Similarity Index

The `StrategySimilarityIndex` uses TF-IDF cosine similarity on product description + ICP text to find strategies that are semantically similar, even when the exact pattern key hasn't been seen.

Used in two places:
1. `GET /playbook/similar-strategies?query=...` — surfaces playbook context at strategy creation
2. Phase 6 + 8 prompt enrichment — injects insights from similar past strategies

---

## Interpreting Playbook Scores

```
GET /api/v1/playbook/scores?strategy_id=...

[
  {
    "pattern_key": "saas_smb_email",
    "variant": "variant_b",
    "reply_rate": 0.36,         ← 36% reply rate for this variant
    "booking_rate": 0.12,       ← 12% of sends resulted in a booked meeting
    "sample_size": 50,          ← based on 50 sends
    "last_updated": "2026-08-17T00:05:00"
  }
]
```

A `reply_rate` above 10% is considered high-performing. The playbook injects these strategies' subject lines and offers into new campaigns targeting the same ICP.
