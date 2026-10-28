"""Admin-controlled, deployment-wide feature settings (the `system_settings` table).

WHY A TABLE AND NOT MORE ENVIRONMENT VARIABLES
Every knob added by the feature expansion -- "is the consensus engine on",
"how many LinkedIn connection requests per account per day", "how many idle
days before a strategy mutates" -- is something an operator needs to change
while the product is running, usually because it is costing money or annoying
someone. An env var needs a redeploy (and on Render's free plan, a cold
start). A row needs a click in /admin/settings.

THE DEFAULTS BELOW ARE THE CONTRACT
A key with no row returns its default, so a fresh database behaves correctly
with an empty table, and `set()` refuses a value whose type does not match
the default's -- the admin UI sends JSON, and "20" stored where 20 was
expected is how a daily cap comparison silently becomes a string comparison.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

# key -> (default, one-line description shown in the admin panel)
DEFAULTS: dict[str, tuple[Any, str]] = {
    # Feature Group 7 -- meeting preparation
    "meeting_prep_enabled": (True, "Generate a meeting prep brief whenever a "
                             "meeting is booked."),
    "meeting_reminder_24h_enabled": (True, "Send the day-before meeting "
                                     "reminder (push + Slack)."),
    "meeting_reminder_1h_enabled": (True, "Send the one-hour reminder carrying "
                                    "the opening-60-seconds script."),
    # Feature Group 1 -- AI intelligence
    "consensus_enabled": (False, "Run strategy generation through Claude AND "
                          "GPT-4o and flag disagreements. Needs an OpenAI "
                          "key under Integrations."),
    "consensus_openai_model": ("gpt-4o", "OpenAI model used by the consensus "
                               "engine."),
    "competitor_intel_enabled": (True, "Pull live news + Apollo signals for "
                                 "the ICP before pipeline Phase 1."),
    "lead_scoring_enabled": (True, "Score every sourced lead's "
                             "ai_booking_likelihood with Claude."),
    "strategy_mutation_enabled": (True, "Mutate a campaign's strategy after "
                                  "N idle days with zero replies."),
    "strategy_mutation_idle_days": (7, "Days a campaign must run with zero "
                                    "replies before it mutates."),
    # Feature Group 2 -- personalization
    "linkedin_personalization_enabled": (True, "Fetch each lead's recent "
                                         "LinkedIn posts before writing "
                                         "their outreach."),
    "company_news_enabled": (True, "Inject last-30-day company news into "
                             "outreach hooks."),
    "loom_score_threshold": (75, "ai_booking_likelihood above which step 2 "
                             "gets a personalised Loom CTA."),
    # Feature Group 3 -- analytics
    "send_time_min_opens": (50, "Opens a campaign needs before smart "
                            "send-time windows are computed."),
    "objection_spike_threshold": (0.20, "Week-over-week objection-rate "
                                  "increase (0.20 = 20%) that alerts the "
                                  "user."),
    "objection_spike_min_replies": (10, "Human replies BOTH weeks need before "
                                    "an objection spike can alert (below "
                                    "this, one reply swings the rate)."),
    "open_tracking_enabled": (True, "Add a 1x1 open-tracking pixel to outreach "
                              "email. Never added for EU/EEA/UK leads "
                              "(ePrivacy: tracking pixels need consent)."),
    "open_prefetch_seconds": (60, "Pixel loads this soon after sending are "
                              "ignored -- mail security scanners fetch images "
                              "on delivery, before any human reads."),
    "send_time_max_delay_hours": (72, "Longest smart send time may hold a "
                                  "step to reach a top engagement window."),
    "claude_input_cost_per_mtok": (3.0, "USD per million Claude input tokens "
                                   "(estimate used for cost per meeting)."),
    "claude_output_cost_per_mtok": (15.0, "USD per million Claude output "
                                    "tokens."),
    "openai_input_cost_per_mtok": (2.5, "USD per million OpenAI input tokens "
                                   "(consensus engine)."),
    "openai_output_cost_per_mtok": (10.0, "USD per million OpenAI output "
                                    "tokens."),
    "voyage_embed_cost_per_mtok": (0.12, "USD per million Voyage embedding "
                                   "tokens (voyage-context-4; help center "
                                   "index and support questions)."),
    "voyage_rerank_cost_per_mtok": (0.02, "USD per million Voyage rerank "
                                    "tokens (rerank-3-lite; query tokens x "
                                    "documents + document tokens)."),
    "voice_cost_per_minute": (0.15, "USD per AI-call minute, all-in "
                              "(Vapi/ElevenLabs + telephony)."),
    # Feature Group 5 -- LinkedIn
    "linkedin_daily_connection_limit": (20, "Connection requests per LinkedIn "
                                        "account per day."),
    "linkedin_daily_message_limit": (50, "LinkedIn messages (incl. InMail) "
                                     "per account per day."),
    "linkedin_weekly_connection_limit": (180, "Connection requests per LinkedIn "
                                         "account per week (accounts without "
                                         "Premium: at most 150)."),
    # Feature Group 6 -- calling
    "phone_calling_enabled": (False, "Allow the AI phone channel. Calls cost "
                              "real money per minute, and AI-voice calls are "
                              "regulated (US TCPA). Vectorae does NOT check "
                              "national Do-Not-Call registries -- do that "
                              "before enabling."),
    "phone_require_consent": (True, "Only AI-call leads with recorded phone "
                              "consent. Turning this off is a legal decision "
                              "for your jurisdiction, not a product one."),
    "phone_daily_call_limit": (40, "AI calls per account per day."),
    # Voice twin (docs/features/voice-twin.md). OFF by default, and it needs
    # phone_calling_enabled too: an account owner may clone their OWN voice
    # (written consent first) for AI calls, and turn on AI answering of
    # inbound calls. Every call still says it is an AI and that it is
    # recorded -- enforced in code, not configurable.
    "voice_clone_calls_enabled": (False, "Let account owners clone their own "
                                  "voice (ElevenLabs Professional Voice "
                                  "Clone, written consent first) for AI "
                                  "calls, and let the AI answer inbound "
                                  "calls. Needs AI calling on. Every call "
                                  "still opens by saying it is an AI "
                                  "assistant and that the call is recorded. "
                                  "Biometric-data and AI-disclosure laws "
                                  "apply -- read the voice twin spec "
                                  "before enabling."),
    # Feature Group 9 -- deliverability & safety
    "deliverability_health_threshold": (70, "Email health score below which "
                                        "a campaign launch is warned."),
    "blacklist_monitoring_enabled": (True, "Daily sending-domain blacklist "
                                     "check; auto-pauses campaigns on a "
                                     "hit."),
    "reply_fraud_detection_enabled": (True, "Second-pass check that drops "
                                      "automated replies (OOO, bots, spam "
                                      "traps) from engagement metrics."),
    # F03 -- Deliverability Autopilot (docs/features/f03-deliverability-autopilot.md)
    "deliverability_autopilot_enabled": (False, "Block a campaign launch when "
                                         "SPF/DKIM/DMARC or one-click "
                                         "unsubscribe fails the preflight, "
                                         "apply the stepped new-mailbox ramp, "
                                         "and brake sending at 0.10% / 0.30% "
                                         "spam rate."),
    "deliverability_dkim_selectors": ("google", "Comma-separated DKIM selectors "
                                      "the launch preflight looks for (Google "
                                      "Workspace's default is 'google')."),
    "postmaster_import_enabled": (False, "Import the real Gmail spam rate from "
                                  "Google Postmaster Tools daily (needs the "
                                  "Autopilot on and a Postmaster grant). "
                                  "UNVERIFIED against a live account -- see "
                                  "the F03 spec before enabling."),
    # Part 1 Feature 5 -- the human review queue for high-risk sends.
    # Each trigger is separately switchable because which ones are useful
    # depends entirely on the ICP. In particular: if the people you sell to
    # ARE founders and owners (Vectorae's own ICP is boutique agency
    # owners), leaving the VIP trigger on puts every message in the queue and
    # a queue nobody can finish is a queue nobody reads. Turn it off there.
    "send_review_enabled": (True, "Hold high-risk messages for explicit human "
                            "approval before sending."),
    "send_review_prior_objection": (True, "Hold a send to a prospect whose "
                                    "last reply was an objection."),
    "send_review_stalled_deal": (True, "Hold a send to a prospect with an "
                                 "open deal past its close date."),
    "send_review_vip_titles": (True, "Hold a send to a founder, C-level, VP "
                               "or board member. Turn OFF if your ICP IS "
                               "executives -- otherwise every message queues."),
    "send_review_tone": (True, "Hold a send whose rendered copy trips the "
                         "tone/spam rules."),
    # Part 1 Feature 7 -- re-engagement memory ("not now" is not "never").
    "reengagement_memory_enabled": (True, "Turn every 'not now' reply into a "
                                    "dated return visit, with the reason the "
                                    "prospect gave."),
    "reengagement_memory_days": (90, "Days to wait when the prospect named no "
                                 "date and gave no reason. A named date always "
                                 "wins; a known reason uses its own interval."),
    "reengagement_memory_auto_send": (False, "When a plan comes due, also "
                                      "SCHEDULE the return message (it still "
                                      "goes through suppression, compliance and "
                                      "the review queue). Off by default: most "
                                      "people want to read a nine-month-old "
                                      "promise before acting on it."),
    # Feature Group 8 -- white label
    "white_label_allowed": (True, "Allow workspace owners to enable "
                            "white-label mode."),
    # Feature A1 -- fabrication-proof claim engine
    "claim_verification_enabled": (True, "Check every factual claim an AI-written "
                                   "message makes about a prospect against stored "
                                   "data, and strip or rewrite what cannot be "
                                   "verified before it sends."),
    "claim_model_extraction_enabled": (True, "Also ask the model to find claims "
                                       "the rules miss (one extra Claude call per "
                                       "outbound message)."),
    # Feature A7 -- pre-send adversarial review
    "sequence_review_enabled": (True, "Red-team every sequence before it launches "
                                "(spam/tone, compliance, unverifiable claims) and "
                                "block activation on blocking findings until they "
                                "are fixed or explicitly overridden."),
    "sequence_review_model_enabled": (True, "Add an AI red-team pass to the rule "
                                      "checks (its findings are warnings only; "
                                      "one Claude call per review)."),
    # Feature A5 -- conversion probability + kill signals
    "conversion_gate_enabled": (True, "Estimate each lead's live conversion "
                                "probability before every send and pause or stop "
                                "the sequence when it has gone cold."),
    "conversion_default_prior": (0.10, "Starting probability for a lead with no "
                                 "AI booking score."),
    "conversion_cooling_threshold": (0.05, "Below this probability a lead is "
                                     "tagged cooling and its sequence paused."),
    "conversion_archive_threshold": (0.015, "Below this probability a lead is "
                                     "archived and its sequence stopped."),
    "conversion_min_unanswered_sends": (4, "Unanswered sends a lead must have "
                                        "before probability alone can cool or "
                                        "archive it (kill signals apply anytime)."),
    "conversion_cooling_pause_days": (21, "How long a cooling lead's sequence "
                                      "stays paused before it resumes on its own."),
    "conversion_inactivity_half_life_days": (30, "Half-life of the inactivity "
                                             "decay applied to a lead that has "
                                             "stopped engaging."),
    # Feature A4 -- cross-channel stagnation
    "stagnation_detection_enabled": (True, "Look hourly for leads with several "
                                     "unanswered sends on one channel and "
                                     "suggest the next best channel."),
    "stagnation_email_sends": (3, "Unanswered emails in a row before a lead "
                               "counts as stagnant on email."),
    "stagnation_other_sends": (2, "Unanswered LinkedIn / WhatsApp / call touches "
                               "in a row before a lead counts as stagnant there."),
    "stagnation_min_hours": (48, "Hours since the last send before stagnation is "
                             "judged -- a lead gets time to reply first."),
    "stagnation_auto_switch_enabled": (False, "Switch the lead's next scheduled "
                                       "message to the suggested channel "
                                       "automatically instead of waiting for a "
                                       "person (never for WhatsApp, which needs "
                                       "an approved template)."),
    # Product defects #2 / review M5 -- A/B auto-promotion. Off by default:
    # a promotion writes a permanent marker (once per strategy, never
    # revisited) and step variants are labels on separate sequences, not
    # randomised arms, so a "winner" can reflect who was enrolled where.
    "ab_auto_promotion_enabled": (False, "Nightly A/B auto-promotion: mark a "
                                  "winning variant on the strategy (permanent, "
                                  "once per strategy). Variants are not "
                                  "randomised arms; read results before "
                                  "turning this on."),
    # Feature A2 -- audit trail
    "click_tracking_enabled": (False, "Route links in outreach email through a "
                               "click redirect so clicks reach the audit trail. "
                               "Off by default: rewritten links are a spam-filter "
                               "signal. Never applied where open tracking is off "
                               "(EU/EEA/UK leads)."),
    # Feature 5 -- opt-in post-sequence re-engagement. Each campaign is still
    # OFF until its owner or a manager turns it on; these bound what they may
    # choose. A missing or mistyped row returns the default below.
    "reengagement_allowed": (True, "Allow campaigns to turn on post-sequence "
                             "re-engagement. Off stops every pending "
                             "re-engagement send at its next send-time check."),
    "reengagement_daily_cap_ceiling": (25, "Highest re-engagement sends per "
                                       "campaign per day. Campaign caps above "
                                       "it are clamped."),
    "reengagement_weekly_cap_ceiling": (100, "Highest re-engagement sends per "
                                        "campaign per rolling 7 days."),
    "reengagement_min_delay_days": (14, "Shortest wait after a lead's last "
                                    "sequence send before re-engagement may "
                                    "contact them."),
    # Feature 3 -- recording-provider transcripts
    "meeting_transcript_wait_minutes": (60, "After a recorded meeting ends, how "
                                        "long the panel waits for the "
                                        "provider's transcript before marking "
                                        "it timed out. The notes-only summary "
                                        "is generated immediately either way, "
                                        "and a late transcript still upgrades "
                                        "it."),
    # Feature 6 -- anonymised benchmarks. benchmarks.HARD_MIN_ACCOUNTS is a
    # floor no value here can lower.
    "benchmark_min_accounts": (10, "Distinct accounts a benchmark bucket needs "
                               "before it is published. Never below 5."),
    "benchmark_min_account_sends": (30, "Sends an account needs in a bucket "
                                    "before its rates count toward it."),
    "benchmark_window_days": (90, "Trailing window, in days, benchmarks are "
                              "computed over (7-365)."),
    # F05 -- Guardrails Studio. Off by default: turning it on enforces each
    # account's exclusion lists (domain / company / contact) at sourcing,
    # enrollment and the send chokepoint, its forbidden phrases in the
    # pre-launch review and at send, and its approved claims in claim
    # verification. Opt-outs (the suppression list) are enforced either way.
    "guardrails_enabled": (False, "Enforce each account's exclusion lists "
                           "(customers, open deals, churned, do-not-contact) "
                           "at sourcing and at send time, and its approved-"
                           "claims / forbidden-phrases library."),
    # F01 -- waterfall enrichment + bounce shield. Both OFF by default; see
    # docs/features/f01-waterfall-enrichment.md. Turn the waterfall on FIRST:
    # the shield blocks every lead that has no waterfall verdict on record.
    "waterfall_enrichment_enabled": (False, "Find each lead's email through the "
                                     "provider chain (Apollo, then Hunter), "
                                     "stop at the first address the verifier "
                                     "confirms, and log every paid attempt."),
    "waterfall_provider_order": ("apollo,hunter", "Order of the email-finder "
                                 "chain. Providers without credentials are "
                                 "skipped."),
    "bounce_shield_enabled": (False, "Block an email send unless the address "
                              "was verified valid (or a manager overrode it "
                              "for that lead). Leads with no waterfall "
                              "verdict are blocked until re-verified."),
    "bounce_shield_catch_all_policy": ("block", "Catch-all (accept-all) "
                                       "domains: 'block' or 'allow'. Any other "
                                       "value counts as 'block'."),
    "bounce_shield_max_age_days": (90, "A verification older than this many "
                                   "days no longer counts as verified."),
    # F02 -- Signal Radar ("why now"). Off by default: it adds a block to the
    # outreach prompt. It never fetches anything new and never offers a
    # signal while claim verification is off.
    "signal_radar_enabled": (False, "Turn stored news, LinkedIn posts, Apollo "
                             "funding/hiring and market intel into dated 'why "
                             "now' signals with source links on each lead, and "
                             "let email cite the freshest one -- only when the "
                             "claim engine ties the sentence to that stored "
                             "source."),
    # F06 -- Quickstart (docs/features/f06-quickstart.md). Off by default:
    # it fetches a user-supplied URL and spends provider credits before the
    # strategy is verified. Drafts are review-only; nothing is ever sent
    # from a Quickstart run.
    "quickstart_enabled": (False, "Let users start from a website URL and a "
                           "one-line offer: derive an ICP, source verified "
                           "leads and write review-only first-touch drafts "
                           "while the full research pipeline runs."),
    "quickstart_lead_target": (25, "Leads a Quickstart run sources (also "
                               "capped by the plan's leads per strategy)."),
    "quickstart_budget_usd": (1.0, "Most a single Quickstart run may spend on "
                              "model calls (ICP extraction + drafts). The "
                              "plan's daily AI cap applies on top."),
    # F04 -- Reply Copilot (docs/features/f04-reply-copilot.md). Off by
    # default: it adds one model call per eligible reply. Drafts are queued for
    # a person; an approved email goes through the ordinary send chokepoint.
    "reply_copilot_enabled": (False, "Draft a grounded, claim-verified answer to "
                              "every interested / question / objection reply "
                              "(email and LinkedIn) and queue it in the inbox "
                              "for approve / edit / discard. Never drafts for "
                              "opt-outs, suppressed or excluded contacts."),
    "reply_copilot_auto_send_allowed": (False, "Allow accounts to opt in to "
                                        "auto-sending Reply Copilot answers to "
                                        "'send me a time' EMAIL replies. Off = "
                                        "every draft waits for a person."),
    # F08 -- meetings-per-dollar ledger (docs/features/f08-meetings-per-dollar.md).
    # Read-only reporting; off until an admin turns it on.
    "meetings_ledger_enabled": (False, "Show the meetings-per-dollar ledger "
                                "(cost per qualified conversation, booked and "
                                "held meeting per campaign, with CSV/PDF "
                                "export) on Revenue & ROI."),
    "meetings_ledger_infra_cents_per_1k_sends": (0, "Infrastructure cost the "
                                                 "meetings ledger allocates, in "
                                                 "US cents per 1,000 sends "
                                                 "(hosting, database, queue). 0 "
                                                 "= not included; the ledger "
                                                 "says so."),
    # F07 -- Spend Guard + Cost Forecast (docs/features/f07-spend-guard.md).
    # Off by default: turning it on forecasts every strategy / lead-sourcing /
    # Quickstart run, enforces each account's own monthly cap (on top of the
    # plan's daily cap, never instead of it), meters every paid data-provider
    # call and sends 50/80/100% alerts. Prices default to 0 = credits are
    # counted but not priced; nothing is invented.
    "spend_guard_enabled": (False, "Forecast the cost of every strategy, "
                            "lead-sourcing and Quickstart run, let each account "
                            "set a monthly spend cap (with 50/80/100% alerts) "
                            "that refuses runs and stops AI/data calls at the "
                            "cap, and show the usage page."),
    "spend_guard_apollo_usd_per_credit": (0.0, "USD Spend Guard charges per "
                                          "Apollo credit (one search result or "
                                          "one enrich). 0 = credits counted, "
                                          "not priced."),
    "spend_guard_hunter_usd_per_credit": (0.0, "USD Spend Guard charges per "
                                          "Hunter request (finder or verifier). "
                                          "0 = counted, not priced."),
    # F09 -- 30-Day Pilot Mode (docs/features/f09-pilot-mode.md). Off by
    # default: it adds an hourly sweep that emails the account owner. It
    # never touches billing and never contacts a prospect.
    "pilot_mode_enabled": (False, "Let accounts run a 30-day pilot: success "
                           "criteria locked on day 1, a weekly scorecard "
                           "email to the owner, a day-30 verdict from the "
                           "meetings-per-dollar ledger and a full export. "
                           "Never changes a plan or billing."),
    "pilot_mode_verdict_grace_hours": (24, "Hours after a pilot's 30 days end "
                                       "before its verdict is frozen, so a "
                                       "meeting marked held or a reply "
                                       "classified just after the end still "
                                       "counts (0-168; values above 168 are "
                                       "treated as 168)."),
    # Annual billing (docs/features/annual-billing.md). Off by default: it
    # offers a new way to pay. Switching it off only stops NEW annual
    # checkouts -- existing annual subscriptions keep their webhooks, cancel
    # path and renewal notices (legal duties never depend on this flag).
    "annual_billing_enabled": (False, "Offer an annual subscription (monthly "
                               "price x ANNUAL_MONTHS_CHARGED, billed yearly, "
                               "auto-renews) next to monthly on the pricing "
                               "page and at checkout. Off only hides it for "
                               "new checkouts; existing annual plans keep "
                               "their renewal notices and cancel path."),
    # F10 -- Champion Tracker + staleness check
    # (docs/features/f10-champion-tracker.md). Off by default: it holds sends
    # whose role data is old and spends Apollo/Hunter units re-verifying.
    # A departure it has RECORDED blocks the old address even after it is
    # switched off -- see the spec.
    "champion_tracker_enabled": (False, "Hold a message when the prospect's "
                                 "title/company is older than the staleness "
                                 "limit until enrichment re-confirms it; block "
                                 "every message to people confirmed to have "
                                 "left; turn a champion's job change into a "
                                 "re-engagement lead at the new company."),
    "champion_staleness_days": (90, "Role data older than this many days is "
                                "re-verified before a send (60-365)."),
    "champion_refresh_run_cap": (100, "Most provider role checks one champion "
                                 "sweep run may make, across all accounts."),
    "champion_account_monthly_units": (200, "Most provider units the champion "
                                       "tracker may spend per account in a "
                                       "rolling 30 days (role checks + email "
                                       "lookups at the new company)."),
    # Daily send approval (docs/features/daily-send-approval.md). ON by
    # default: a founder decision (2026-10-11), not an optional feature.
    # Automatic sends wait (deferred, never dropped) until the account owner or
    # a workspace owner/manager approves today's automatic sending, per
    # channel; LinkedIn needs an explicit acknowledgement that LinkedIn may
    # restrict accounts that use automation. Off = the previous behaviour.
    "daily_send_approval_enabled": (True, "Hold every AUTOMATIC send until the "
                                    "account owner (or a workspace owner/"
                                    "manager) approves today's automatic "
                                    "sending for that channel. Approval is "
                                    "valid until the end of the account's "
                                    "local day. Held messages wait; they are "
                                    "never dropped."),
    "daily_send_approval_channels": ("email,linkedin,whatsapp,phone",
                                     "Comma-separated channels whose automatic "
                                     "sends need today's approval (email, "
                                     "linkedin, whatsapp, phone). A value with "
                                     "no valid channel name gates every "
                                     "channel."),
    # Autopilot launch + best time to send (docs/features/autopilot-launch.md).
    # Founder report 2026-10-11 ("when Pipeline is done it doesn't send"): the
    # OPTION is offered by default; each strategy's own opt-in stays OFF until
    # a person turns it on and confirms the lawful-basis text. Every automatic
    # send still passes the whole send chokepoint, daily approval included.
    "autopilot_launch_enabled": (True, "Offer Autopilot: when a strategy's "
                                 "research finishes and verification passes, "
                                 "find leads, write the sequence and start "
                                 "outreach automatically -- only for "
                                 "strategies whose owner opted in and "
                                 "confirmed a lawful basis. Every send still "
                                 "passes every gate (daily approval, "
                                 "compliance, review, caps)."),
    "autopilot_lead_target": (25, "Leads Autopilot sources for a strategy "
                              "that has none yet (also capped by the plan "
                              "and leads_max_per_run)."),
    "best_time_scheduling_enabled": (True, "Place each scheduled message at "
                                     "the best slot in the prospect's local "
                                     "time (channel/industry priors, the "
                                     "strategy's send-time plan, then the "
                                     "account's own reply rates) instead of "
                                     "as soon as sending hours open. Off = "
                                     "the previous scheduling."),
    "best_time_min_sends": (30, "Sends on a channel an account needs before "
                            "its own reply rates by hour are blended into "
                            "best-time scheduling."),
    # Local business mode (docs/features/local-business-mode.md). ON by
    # default: it only changes targeting and prompt context for BUSINESS
    # buyers near a local business (no new send path, no consumer contact).
    # Off = business type / service area are refused and ignored.
    "local_business_mode_enabled": (True, "Let a product be a local business "
                                    "(restaurant, clinic, shop, local service) "
                                    "with a service area: research, lead "
                                    "search and messages target BUSINESS "
                                    "buyers near it. Business buyers only."),
    # Customer win-back (same spec, Part B). OFF by default until founder/legal
    # confirm the consent readings: it messages CONSUMERS (the business's own
    # customers) on a recorded opt-in basis. Healthcare is refused whatever
    # this says (HIPAA).
    "customer_winback_enabled": (False, "Let a local business import its OWN "
                                 "customers with recorded opt-in consent and "
                                 "send them simple win-back email / WhatsApp "
                                 "template campaigns through every send "
                                 "check. Healthcare businesses are always "
                                 "refused. Off = imports refused and queued "
                                 "win-back messages cancelled."),
    # Email network (docs/features/email-network.md). OFF by default until
    # founder/legal confirm the GDPR / CASL / PECR readings in the spec. Off =
    # imports, Gmail scans, drafts, saves, attaching and add-to-campaign are
    # refused (409); status, stored recommendations and DELETE keep working.
    # A saved first email ALREADY attached to a campaign lead is still sent by
    # the normal send path, with every check (the flag gates entry, not sends).
    "email_network_enabled": (False, "Let users upload their own email contacts "
                              "(Google / Outlook CSV, vCard) or a mailbox export, "
                              "or scan their connected Gmail -- headers only, "
                              "never bodies or subjects -- to find warm B2B "
                              "matches for their products."),
}


class SettingTypeError(ValueError):
    pass


def _coerce(key: str, value: Any) -> Any:
    default, _ = DEFAULTS[key]
    # bool is a subclass of int: check it first, or True would validate as an
    # int setting and False as a float.
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise SettingTypeError(f"{key} must be true or false")
        return value
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingTypeError(f"{key} must be a whole number")
        if value < 0:
            raise SettingTypeError(f"{key} must not be negative")
        return value
    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingTypeError(f"{key} must be a number")
        return float(value)
    if isinstance(default, str):
        if not isinstance(value, str) or not value.strip():
            raise SettingTypeError(f"{key} must be a non-empty string")
        return value.strip()[:200]
    return value


def get(db: Session, key: str) -> Any:
    """The stored value, or the default. Unknown keys raise KeyError."""
    from app.db.models import SystemSetting  # noqa: PLC0415

    if key not in DEFAULTS:
        raise KeyError(f"unknown system setting {key!r}")
    row = db.execute(
        select(SystemSetting).where(SystemSetting.key == key)
    ).scalar_one_or_none()
    if row is None:
        return DEFAULTS[key][0]
    try:
        return _coerce(key, (row.value_json or {}).get("v"))
    except SettingTypeError:
        # A row written by something other than set() (a psql session, a
        # migration) with the wrong type must not take a feature down.
        return DEFAULTS[key][0]


def set(db: Session, key: str, value: Any, *, actor_user_id=None) -> Any:  # noqa: A001
    from app.db.models import SystemSetting  # noqa: PLC0415

    if key not in DEFAULTS:
        raise KeyError(f"unknown system setting {key!r}")
    value = _coerce(key, value)
    row = db.execute(
        select(SystemSetting).where(SystemSetting.key == key)
    ).scalar_one_or_none()
    if row is None:
        row = SystemSetting(key=key)
        db.add(row)
    row.value_json = {"v": value}
    row.updated_by_user_id = actor_user_id
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return value


def all_settings(db: Session) -> list[dict]:
    from app.db.models import SystemSetting  # noqa: PLC0415

    stored = {r.key: r for r in db.execute(select(SystemSetting)).scalars()}
    out = []
    for key, (default, description) in DEFAULTS.items():
        row = stored.get(key)
        out.append({
            "key": key,
            "value": get(db, key),
            "default": default,
            "type": type(default).__name__,
            "description": description,
            "is_default": row is None,
            "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
        })
    return out
