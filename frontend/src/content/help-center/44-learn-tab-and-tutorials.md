---
id: learn-tab-and-tutorials
title: The Learn tab and video tutorials
audience: customer
last_verified: 2026-10-18
owner: help-center
sources:
  - frontend/src/app/(app)/learn/page.tsx
  - frontend/src/components/tutorials/VideoPlayer.tsx
  - frontend/src/components/tutorials/BadgeShelf.tsx
  - frontend/src/components/shell/nav.ts
  - frontend/src/app/(admin)/admin/layout.tsx
  - frontend/src/app/(admin)/admin/tutorials/page.tsx
  - app/services/tutorials.py
  - app/api/tutorials.py
  - app/api/admin.py
  - app/services/account_export.py
  - app/services/account_erasure.py
  - scripts/seed_tutorials.py
  - scripts/TUTORIAL_VIDEO_URLS.md
  - docs/features/tutorial-section.md
---

# The Learn tab and video tutorials

Vectorae has a built-in tutorial section with short video lessons. This article explains where to find it, what it contains, how your progress and badges work, where the videos are hosted, and what you see when a video is not available.

## Where do I find the training videos?

Click **Learn** in the main menu. The page is called **Learn Vectorae** and lists the tutorials in three levels: **Beginner**, **Intermediate** and **Advanced**. Each tutorial shows its title, a short description, its length, a progress bar and a **Watch** button (**Resume** once you have started, **Close** while it is open).

At the top, **Your progress** shows how many tutorials you have finished out of the total, as a number and a percentage, with a count per level. Under it are your badges. You can type in **Search tutorials** to search titles and descriptions, or use the level buttons (**All** or one level) to narrow the list. Searching and filtering do not change your overall progress figures.

The standard set that Vectorae's setup script adds has nine tutorials, three per level, on topics such as your first ten minutes, setting up your ICP, connecting LinkedIn, the pipeline health score, reply intelligence, running your first campaign, founder voice cloning, competitor displacement alerts and the ROI dashboard. Your Vectorae administrator can edit, publish, unpublish and remove tutorials on the **Tutorials** page of the admin area (adding a new one is possible only through the admin API), so the list you see may differ.

## How are progress and badges counted?

Your progress is saved per video as you watch: where you stopped, and how much you have seen. **Resume** starts from where you stopped; a tutorial you have finished plays from the beginning. Going back in a video moves your resume point but never reduces how much you have watched.

A tutorial counts as finished once you have watched 90% of it. You can also click **Mark complete** if you already know the material, and **Reset progress** to start a tutorial again.

There are four badges: **Beginner Complete**, **Intermediate Complete** and **Advanced Complete** for finishing every tutorial in a level, and **Vectorae Certified** for finishing every tutorial. Badges are worked out from your progress each time, so resetting a tutorial removes the badge it earned.

Your progress is private to you. Administrators can see only totals (how many people finished or started each video), never which videos a named person watched. Your tutorial progress is included in your account export and deleted when your account is erased.

## Where are the videos hosted?

The videos are on YouTube and are played inside Vectorae through YouTube's privacy-enhanced embed (youtube-nocookie.com). Vectorae stores only each video's YouTube ID, not the video itself. An administrator adds or changes a video by setting that ID on the **Tutorials** admin page; no new release of Vectorae is needed.

The videos can be Unlisted on YouTube, so they do not have to appear in YouTube search, but they cannot be Private, because YouTube does not allow Private videos to be embedded. Because every video streams from YouTube, it plays only where your browser and network can reach YouTube.

## What if a video is not available?

What you see depends on why:

- **"Coming soon" and "This video is not published yet".** The tutorial is listed but no video has been added yet. The tutorials in the standard set were published without videos, to be added later, so you may see this on some or all of them. You can still click **Mark complete**.
- **An empty player.** If your browser blocks YouTube's player script, for example through an ad blocker or a network filter, the player area can stay empty with no message. Allow YouTube for Vectorae, or click **Mark complete** once you have watched the lesson elsewhere.
- **YouTube's own error, such as "Video unavailable"**, inside the player. The video ID is wrong or the video is set to Private on YouTube (Unlisted videos work). Only your administrator can fix this; tell them which tutorial it is.

If the **Learn** item is missing from your menu, or a tutorial you had started disappears, contact your administrator or support. See [Contacting support and checking service status](30-contact-support-and-status.md).

## Related
- [Getting started with Vectorae](03-getting-started.md)
- [The Android app](26-mobile-app.md)
- [Privacy, data retention and deletion](21-privacy-data-retention-deletion.md)
- [Glossary of Vectorae terms](28-glossary.md)
- [Contacting support and checking service status](30-contact-support-and-status.md)
