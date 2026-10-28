---
id: guide-sdr-team-member
title: Working as an SDR in someone else's workspace
audience: customer
last_verified: 2026-10-18
owner: help-center
sources:
  - app/services/rbac.py
  - app/services/auth.py
  - app/services/workspaces.py
  - app/api/workspaces.py
  - app/api/strategies.py
  - app/api/sequences.py
  - app/api/sequence_reviews.py
  - app/api/send_approval.py
  - app/api/send_reviews.py
  - app/api/inbox.py
  - app/api/autopilot.py
  - app/api/crm.py
  - app/api/client_workspaces.py
  - app/api/spend_guard.py
  - app/api/reengagement.py
  - app/api/email_network.py
  - app/api/leads.py
  - frontend/src/app/(app)/team/page.tsx
  - frontend/src/app/(app)/invite/page.tsx
  - frontend/src/app/(app)/send-approval/page.tsx
  - frontend/src/app/(app)/pipeline/page.tsx
  - frontend/src/components/shell/WorkspaceSwitcher.tsx
  - frontend/src/components/shell/nav.ts
  - frontend/src/components/strategy/OutreachPanel.tsx
  - frontend/src/components/crm/grid/GridToolbar.tsx
  - frontend/src/lib/workspace.ts
  - app/db/models.py
  - docs/help-center/24-workspaces-and-agency-mode.md
---

# Working as an SDR in someone else's workspace

This guide is for someone invited into another person's Vectorae workspace with the SDR role (and, briefly, the viewer role). It covers getting into the workspace, which screens you can use, what will be refused and the exact message you will see, and how your campaign launches are approved. For the overview of all four roles, see [Workspaces, team roles and agency mode](24-workspaces-and-agency-mode.md).

## How do I get into the workspace I was invited to?

Open the link from the invitation email while signed in with the address it was sent to. The **Workspace invitation** page then says "You joined [workspace] as sdr." and switches you into that workspace. If it fails, the page shows why, for example "This invitation was sent to a different email address.", "invitation expired -- ask for a new one" or "invitation already used". Ask the owner or a manager for a new link in those cases.

After that, the **Workspace** switcher in the top bar lists every workspace you belong to, each with your role (for example "Acme's workspace · SDR"), including your own. Choosing one reloads the page so that nothing from the previous workspace stays on screen. The choice is remembered in that browser only, so on another browser or device you start in your own workspace until you switch. The **Team** page always tells you where you are and in what role ("you are SDR").

## What can I see and change with the SDR role?

All of them. The menu is the same for every role: nothing is hidden because you are an SDR, and you can open and read every screen in the workspace. The difference is in what you can save. Each change is checked by the server against your role when you make it.

As an SDR you can:

- work leads in **Pipeline** and **CRM**, and work sequences, deals and meetings;
- launch campaigns (the first launch of each sequence may wait for approval, see below);
- approve, edit or reject held messages under **Campaigns**, on the **Review** tab;
- answer replies and use **Mark handled** in the **Inbox**.

On **Team** you see the member list and the **Approvals** tab, but not the **Invite** card or the **Approve** and **Decline** buttons. The **Settings** and **White label** tabs are shown to the owner only.

## What will be refused, and what message will I see?

These actions need an owner or manager. The server refuses them with these messages:

| What you tried | Message |
|---|---|
| Deleting anything: a lead (**Delete (GDPR erasure)**), a strategy, a note, a tag, a saved view | "Deleting needs a manager in this workspace." |
| Changing integrations (for example connecting or pausing a LinkedIn account; removing one gets the deleting message above), outgoing webhooks, costs or pilots | "Integrations, webhooks, costs and pilots need a manager." |
| Approving today's automatic sending | "Approving automatic sending needs an owner or manager in this workspace." |
| Turning Autopilot on or off | "Turning on Autopilot needs the account owner or a workspace manager: it launches outreach without a separate approval." |
| Overriding a blocked pre-send review | "Overriding a review needs a manager." |
| Approving another SDR's launch | "Approving a launch needs a manager." |
| **Distribute to SDRs** in the CRM grid | "Distributing leads needs a manager in this workspace." |
| Adding or changing clients and their domains (removing a domain gets the deleting message above) | "Managing clients needs a manager in this workspace." |
| Changing the spend cap | "Changing the spend cap needs an owner or manager." |
| Inviting someone | "Inviting people needs a manager or above." |

So the answer to "why can't I delete a lead?" is that deleting is a manager's decision in every workspace, and the integrations, webhooks and costs are the owner's account plumbing and money. One screen does not pass the reason on: on **Pipeline**, a refused **Delete (GDPR erasure)** only says "Could not delete lead". The reason is the first row of the table. The **Today's automatic sending** page tells you in advance: "Only a workspace owner or manager can approve or decline automatic sending."

## Will my campaign launch wait for a manager?

Usually, for the first launch of each sequence. When the workspace requires approval (it does unless the owner switched it off), pressing launch on **2. Write the email and launch** shows "Sent to a manager for approval. Nothing is sent until they approve." Nothing is enrolled or scheduled yet. The owner and managers get a "Campaign awaiting approval" notification.

The usual launch checks run before your request is stored. In particular, if the pre-send review of your sequence has blocking findings, the request itself is refused; you cannot send blocked content up for approval, and only an owner or manager can override the review. The phone-verification check at launch is made on the workspace owner's account, not yours.

You are notified of the decision ("Campaign approved" or "Campaign declined", with the manager's note). A declined sequence goes back to draft and stays listed as "Declined" with the note under **Team**, **Approvals**. Once a sequence has been approved, your later launches of that same sequence go out without asking again. If the owner turned approval off, your launches go out straight away. The manager's side is described in [Workspaces, team roles and agency mode](24-workspaces-and-agency-mode.md).

## What can I do with the viewer role?

Read. A viewer can open every screen in the workspace, but every change to the workspace's data is refused with "Viewers have read-only access to this workspace." That includes things an SDR may do, such as approving a held message, marking an inbox thread handled or editing a lead. A viewer can still leave the workspace, and an attempt to invite someone gets "Inviting people needs a manager or above."

Whatever your role, your personal pages still work, because they always act on your own account rather than the workspace: your profile and password, your devices, support chats, tutorials and onboarding, and your own billing. If you need more than your role allows, ask the owner (who can change any member's role) or a manager (who can change SDRs and viewers).

## Related
- [Workspaces, team roles and agency mode](24-workspaces-and-agency-mode.md)
- [The send-review queue and approvals](12-send-review-queue.md)
- [Replies and the unified inbox](13-replies-and-unified-inbox.md)
- [Sequences and follow-ups](08-sequences-and-follow-ups.md)
- [Daily send approval and notifications](39-daily-send-approval-and-notifications.md)
- [Account security](23-account-security.md)
