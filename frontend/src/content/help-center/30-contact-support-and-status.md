---
id: contact-support-and-status
title: Contacting support and checking service status
audience: customer
last_verified: 2026-10-11
owner: help-center
sources:
  - render.yaml
  - frontend/src/components/support/ChatWidget.tsx
  - frontend/src/components/shell/Shell.tsx
  - frontend/src/lib/support/ticketSubject.ts
  - frontend/src/app/contact/page.tsx
  - frontend/src/app/privacy/page.tsx
  - frontend/src/app/status/page.tsx
  - frontend/src/components/status/StatusBoard.tsx
  - frontend/src/lib/statusBoard.ts
  - frontend/src/lib/statusIncidents.ts
  - frontend/src/lib/statusPage.ts
  - frontend/src/components/Footer.tsx
  - frontend/src/components/home/HomePage.tsx
  - frontend/src/app/pricing/page.tsx
  - app/api/status_public.py
  - app/api/support.py
  - app/services/support_chat.py
  - app/services/support_kb.py
  - app/workers/support_tasks.py
  - app/workers/celery_app.py
  - app/api/admin.py
  - app/config.py
  - app/services/rbac.py
  - docs/features/ai-support-chat.md
  - docs/program/STAR_PROMPT.md
  - docs/program/BASELINE.md
  - docs/program/STATUS_PAGE.md
---

# Contacting support and checking service status

You can get help inside Victorae in two ways: ask the support assistant, or send a ticket to the team. This article explains how both work, what limits apply, what happens to your conversations, and where to check whether Victorae is running normally.

## Where do I find support in the app?

On every page of the app after you sign in, there is a round button with a life-ring icon in the bottom-right corner ("Open support chat"). It opens a panel called **Victorae support** with two modes, switched by the icons at the top of the panel:

- **Chat** – ask the support assistant a question.
- **Submit a ticket** – write to the team directly.

The ticket form can be opened at any time. You do not have to chat with the assistant first.

You need a confirmed email address to use the chat.

## What can the support assistant answer?

The assistant answers questions about Victorae: how it works, setting up campaigns, outreach, the learning loop, tutorials, your data and getting support. It answers only from Victorae's written help content, not from general knowledge.

- If you ask about something unrelated to Victorae, it replies with a fixed message: "I can only help with questions about Victorae — …". It does not offer a ticket for off-topic questions.
- If it cannot find the answer, or is not confident enough, it does not guess. It says so and offers **Submit a ticket**. The ticket subject is then filled in for you, starting with "Question not in FAQ:" followed by your question.
- When you open the chat, a few suggested questions are shown to get you started.

A question can be up to 2,000 characters long.

## How many chat messages can I send?

You can send 30 chat messages per day on Victorae's hosted service. This is a deployment setting: the hosted service sets 30, and the built-in default is 20. When you reach the limit you see "You've reached today's message limit. You can still submit a ticket." and the panel switches to the ticket form.

If the chat has been switched off on your deployment, you see "Support chat is temporarily unavailable. Submit a ticket instead." Tickets still work.

## How do I submit a ticket?

1. Open the support panel and choose **Submit a ticket**.
2. Fill in **Subject** (3 to 200 characters).
3. Fill in **What do you need help with?** (at least 10 characters).
4. Press **Submit ticket**. You see "Ticket submitted."

Tickets are not limited by the daily chat limit, so you can always reach the team.

## How quickly will the team respond?

The ticket form says the team responds within 24 hours. That is a commitment written in the app, not something the product enforces. Here is what actually happens to a ticket:

- It is stored in Victorae's database and appears in the team's support queue, which shows your account email address.
- The team is not alerted automatically when a ticket arrives; someone checks the queue.
- The team marks a ticket resolved in its queue. Replies are not shown inside the app, and the app has no screen listing your own tickets.

If the matter is urgent, for example outreach that must be stopped, pause your campaigns yourself first (see the pausing article), then contact the team.

## What happens to my chat history?

Each chat conversation is private to you. You can start a fresh conversation with **Start a new chat**; earlier conversations are kept.

A conversation is deleted 30 days after its last message, by a job that runs every night (a deployment setting; 30 days is the default). A ticket you raised from a conversation is not deleted when the conversation is; the ticket keeps existing on its own.

## Are there other ways to contact Victorae?

Yes. The public **Contact** page on the Victorae website offers a link to book a call, an email address, the company's LinkedIn page and WhatsApp numbers. You can delete your account yourself on **Profile** (see [Privacy, data retention and deletion](21-privacy-data-retention-deletion.md)); for any other privacy request, use the contact email on the **Privacy** page.

## Is there a status page?

Yes. The Victorae status page is at **www.calendarharvest.com/status**. You can open it from the **Status** link in the footer of the website's home page and its About, How it works, Pricing, Contact, Privacy and Terms pages, or from the "Service status" icon in the header of the support panel in the app.

It shows:

- **An overall summary**, for example "All systems operational", "Degraded performance", "Partial outage" or "Victorae API unreachable — major outage".
- **Four components and their state:** API, Background processing (scheduled sends and other work that runs in the background), AI features, and Booking pages. Every state is written out in words, not shown by colour alone. "No data" means a component could not be checked; it does not mean it is working.
- **When it last checked**, in your local time. While the tab is open it checks again every 30 seconds; it pauses while the tab is in the background and checks again when you come back. There is also a **Check now** button.
- **Incidents** the team has posted, active ones first. When there are none, it says "No incidents reported."

The page is hosted separately from the Victorae API, so it still loads when Victorae itself is down. If your browser cannot get an answer from the API within 8 seconds, the page says "Victorae API unreachable — major outage". A problem with your own internet connection can cause the same message, so if colleagues on another network see Victorae working, check your connection first.

What the status page does not do:

- It does not send alerts. There is no email or text subscription.
- It does not show uptime history; it shows the state right now and the incidents the team has posted.
- If very many people check from the same network at once, the page may say it was asked to slow down. It then keeps showing the last result, with its time, and tries again after 2 minutes. That message is not an outage.

If the status page shows a problem, or Victorae misbehaves while the page says everything is fine:

- Remember that campaigns run on Victorae's servers, not in your browser or phone, so closing the app does not stop or pause them.
- If messages are not going out, see the "Nothing is sending" section of the troubleshooting article, then submit a ticket.

## Related
- [Troubleshooting](27-troubleshooting.md)
- [Pausing and stopping outreach](25-pause-stop-kill-switch.md)
- [Privacy, data retention and deletion](21-privacy-data-retention-deletion.md)
- [Known limitations](29-known-limitations.md)
- [Mobile app](26-mobile-app.md)
