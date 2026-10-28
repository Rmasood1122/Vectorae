# Feature Group 6 — AI phone calling

## Read this before enabling
Since the FCC's **February 2024** declaratory ruling, AI-generated voices are an
"artificial or prerecorded voice" under the US **TCPA**. Calling a US mobile
number with one — or leaving a prerecorded voicemail — requires the called
party's **prior express consent** (written consent for telemarketing). Other
countries have their own rules. Vectorae therefore:

- ships with calling **off** (`phone_calling_enabled`, Admin › System Settings);
- **requires recorded consent per lead** (`phone_require_consent`, default on).
  A phone step for a lead without consent is *skipped*; the sequence continues;
- makes every call **open with an AI disclosure** ("this is an AI assistant
  calling on behalf of …") — enforced in code even if the script omits it;
- calls only inside the lead's local send window, under a per-user daily
  limit (`phone_daily_call_limit`);
- honours a stop request in the call by suppressing the number.

It does **not** check national Do-Not-Call registries. That is the operator's
responsibility before enabling the channel. None of this is legal advice.

## How a call works
1. **Script** — Claude writes a per-lead script from the step brief, the
   strategy's messaging research, the ICP and the lead's own posts/news, in the
   sender's voice: first line (with disclosure), objective, talking points,
   objection handling, questions, close — plus a short personalised voicemail.
2. **Voicemail** — rendered to audio with **ElevenLabs** TTS and hosted (so it
   can be played back on the lead page); the provider detects a machine and
   speaks the voicemail in the configured ElevenLabs voice. Outcome:
   `voicemail_dropped`.
3. **Dial** — `PhoneChannel` (an `OutreachChannel`) places the call through
   **Vapi.ai**, falling back to ElevenLabs Conversational AI.
4. **Result** — the provider's signed end-of-call webhook stores the recording
   URL, duration, transcript and ended reason. A conversation is analysed by
   Claude on the outreach queue: outcome (`interested` / `not_interested` /
   `answered`), summary, objections, interest signals, next step.
5. **Routing** — interested or not interested stops the sequence like a reply
   (interested also sends a push/Slack `reply_interested`); voicemail and no
   answer let it continue.

## UI
- Campaigns › **Calls** tab: every call in the campaign.
- Lead › **Calls** tab: record/revoke phone consent, "Call now", and each call's
  recording, voicemail, transcript and analysis.

## Setup
Admin › Integrations: **Vapi** `api_key`, `phone_number_id`, `webhook_secret`;
**ElevenLabs** `api_key`, `voice_id` (and `agent_id`, `agent_phone_number_id`,
`webhook_secret` for the calling fallback). Then Admin › System Settings ›
`phone_calling_enabled`.
