# Tutorial Video URLs — Replace Before Launch

The nine tutorials from `scripts/seed_tutorials.py` are published with **no
video**. Opening one shows a "This video is not published yet" panel, and each
card carries a "Coming soon" badge. Both disappear automatically once a video
ID is set.

## What to paste

The database stores the **11-character YouTube video ID**, not a full URL.

```
https://www.youtube.com/watch?v=AbCdEfGhIjK      ->  AbCdEfGhIjK
https://youtu.be/AbCdEfGhIjK                     ->  AbCdEfGhIjK
https://www.youtube.com/embed/AbCdEfGhIjK        ->  AbCdEfGhIjK
```

The app builds the embed URL itself (`youtube-nocookie.com`). Videos can be
**Unlisted** but must not be **Private**, because Private videos cannot be embedded.

**Never change a `slug`.** User progress points at it, and renaming one wipes
that progress for every user.

## Option 1 — Admin API (recommended)

`PUT /admin/tutorials/{slug}` with an admin bearer token. You can also do this
from the dashboard at `/admin/tutorials`.

```bash
curl -X PUT "https://<api-host>/admin/tutorials/welcome-to-leadpilot" \
     -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"youtube_id": "YOUR_ID", "duration_seconds": 600}'
```

`duration_seconds` is optional. Send it if the real video length differs from
the planned duration.

## Option 2 — SQL

Table: `tutorial_catalogue`. Run in the Neon SQL editor.

| Placeholder  | Slug                                 | Title |
|--------------|--------------------------------------|-------|
| REPLACE_ME_1 | `welcome-to-leadpilot`               | Welcome to Vectorae — Your First 10 Minutes |
| REPLACE_ME_2 | `setting-up-your-icp`                | Setting Up Your ICP — Who You Are Targeting |
| REPLACE_ME_3 | `connecting-your-linkedin-account`   | Connecting Your LinkedIn Account |
| REPLACE_ME_4 | `reading-your-pipeline-health-score` | Reading Your Pipeline Health Score |
| REPLACE_ME_5 | `understanding-reply-intelligence`   | Understanding Reply Intelligence |
| REPLACE_ME_6 | `running-your-first-campaign`        | Running Your First Campaign |
| REPLACE_ME_7 | `founder-voice-cloning`              | Founder Voice Cloning — Personalizing at Scale |
| REPLACE_ME_8 | `competitor-displacement-alerts`     | Competitor Displacement Alerts |
| REPLACE_ME_9 | `reading-your-roi-dashboard`         | Reading Your ROI Dashboard and Sharing Proof Cards |

```sql
-- Beginner
UPDATE tutorial_catalogue SET youtube_id = 'REPLACE_ME_1', updated_at = now()
WHERE slug = 'welcome-to-leadpilot';

UPDATE tutorial_catalogue SET youtube_id = 'REPLACE_ME_2', updated_at = now()
WHERE slug = 'setting-up-your-icp';

UPDATE tutorial_catalogue SET youtube_id = 'REPLACE_ME_3', updated_at = now()
WHERE slug = 'connecting-your-linkedin-account';

-- Intermediate
UPDATE tutorial_catalogue SET youtube_id = 'REPLACE_ME_4', updated_at = now()
WHERE slug = 'reading-your-pipeline-health-score';

UPDATE tutorial_catalogue SET youtube_id = 'REPLACE_ME_5', updated_at = now()
WHERE slug = 'understanding-reply-intelligence';

UPDATE tutorial_catalogue SET youtube_id = 'REPLACE_ME_6', updated_at = now()
WHERE slug = 'running-your-first-campaign';

-- Advanced
UPDATE tutorial_catalogue SET youtube_id = 'REPLACE_ME_7', updated_at = now()
WHERE slug = 'founder-voice-cloning';

UPDATE tutorial_catalogue SET youtube_id = 'REPLACE_ME_8', updated_at = now()
WHERE slug = 'competitor-displacement-alerts';

UPDATE tutorial_catalogue SET youtube_id = 'REPLACE_ME_9', updated_at = now()
WHERE slug = 'reading-your-roi-dashboard';
```

Replace each `REPLACE_ME_n` with the real 11-character ID before running. Do
**not** run these with the placeholder text still in place: a fake ID shows
YouTube's "Video unavailable" error, which is worse than the placeholder panel.

## Check what is still missing

```sql
SELECT slug, title, youtube_id
FROM tutorial_catalogue
WHERE is_published AND youtube_id IS NULL
ORDER BY sort_order;
```

## The nine older drafts

Migration 0018 created nine earlier tutorials, such as "Getting Started with
Vectorae" and "Understanding Your ICP". They are still in the table,
**unpublished**, and invisible to users. The seed script does not touch them.
Delete them from `/admin/tutorials` if they will not be used.
