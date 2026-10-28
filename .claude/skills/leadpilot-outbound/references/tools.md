# Vectorae connector tools

Server: `https://api.calendarharvest.com/mcp` (remote MCP, OAuth). Every
tool acts on the signed-in user's own account. Lists: `limit` 1–50 (default
20), `offset`, and `total` / `has_more` in the result. Third-party text is
under `untrusted_content` in every result.

## Read tools (no confirmation needed)

| Tool | Arguments | Returns |
|---|---|---|
| `list_leads` | `strategy_id?`, `status?` (sourced, enriched, email_found, verified, flagged, dropped, contacted, replied, meeting_booked, proposal_sent, opportunity, closed_won, closed_lost, disqualified), `min_fpta?` 0–100, `search?` (name, company, email, title), `sort` fpta \| booking_likelihood \| newest, `limit`, `offset` | leads: id, strategy_id, status, source, fpta_overall, fpta_band, ai_booking_likelihood, url; name/title/company/email under untrusted_content |
| `get_lead` | `lead_id` | status, F-P-T-A (overall, fit, problem, timing, access, band, method, weights), reasons and signals, LinkedIn posts (≤5), company news (≤5), enrichment |
| `list_products` | `limit`, `offset` | products: id, name, type, business_type, past_clients, strategies |
| `list_strategies` | `limit`, `offset` | strategies: id, product, flow_type, status, campaign_state |
| `get_strategy` | `strategy_id` | status, campaign_state (+ pause reason), budget_usd, spent_usd, planned_steps, documents ready |
| `get_pipeline_status` | `strategy_id` | steps done per pipeline and phase, verification passes, error |
| `list_messages` | `status?` (scheduled, sending, sent, failed, cancelled, bounced, skipped, needs_template, awaiting_review), `channel?` (email, whatsapp, linkedin, phone), `strategy_id?`, `lead_id?`, `limit`, `offset` | messages without text: id, lead_id, channel, step_no, status, scheduled_at, sent_at, opens |
| `get_message` | `message_id` | the message with subject and body (untrusted_content) |
| `get_attribution_summary` | `strategy_id?`, `recent_limit` 0–20 | totals by step, channel, method; certain count; median hours to outcome; recent outcomes |
| `get_claim_verification` | `lead_id`, `limit` | verdict counts and the claim log for one lead |
| `search` | `query` | ChatGPT shape: `{results: [{id: "lead:<uuid>", title, url}]}`, ≤10 |
| `fetch` | `id` (from search) | ChatGPT shape: `{id, title, text, url, metadata}` |

## Write tools (the client asks the user first; need the write permission)

| Tool | Arguments | Effect |
|---|---|---|
| `create_strategy` | `product_id`, `flow_type?` (with_clients needs past clients; no_clients), `max_budget_usd?` | creates a strategy and starts its AI research; refused with the web app's message on a plan limit, AI spend cap, rate limit or too small a budget |
| `pause_campaign` | `strategy_id` | stops that strategy's sending |
| `resume_campaign` | `strategy_id` | lets scheduled messages send again; refused if the deliverability preflight blocks it |

There is no tool that sends, schedules or approves a message.

## Errors

A tool error names the problem: a malformed id ("… must be a Vectorae id"),
an id that is not the user's ("lead not found"), a missing permission ("needs
the 'leadpilot:write' permission" — the user reconnects and approves it), or
a business refusal copied from the web app (plan limit, spend cap, rate
limit, deliverability). Relay it; do not retry with guessed values.

## Connecting

- claude.ai / Desktop / mobile: Settings → Connectors → Add custom connector →
  `https://api.calendarharvest.com/mcp`, then sign in to Vectorae and Allow.
- Claude Code: install the `leadpilot` plugin, or
  `claude mcp add --transport http leadpilot https://api.calendarharvest.com/mcp`.
- ChatGPT: Settings → Apps → Developer Mode → create an app with the same URL.
