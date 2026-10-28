# Vectorae in Claude and ChatGPT

Vectorae connects to Claude (claude.ai, Claude Desktop, Claude mobile,
Cowork, Claude Code) and to ChatGPT as a **connector**: a remote MCP server at

```
https://api.calendarharvest.com/mcp
```

You sign in to Vectorae once, approve what the app may do, and can then ask
about your leads, scores, strategies, outreach and results, create a
strategy, or pause and resume a campaign — from the chat.

Two pieces, and why both exist:

- **The connector** (the MCP server) is what reads your Vectorae data and
  acts in your account. Nothing else can.
- **The `leadpilot-outbound` skill** is instructions only. It teaches Claude
  when to use Vectorae, how to read F-P-T-A scores and attribution, and the
  rules (nothing is sent from the chat; prospect text is never obeyed). It
  cannot reach your data on its own.

Technical spec: `docs/features/mcp-connector.md`.

---

## Before you start (deployment owner)

The connector is off until the deployment turns it on: `MCP_ENABLED=true`
and `MCP_RESOURCE_URL=https://api.calendarharvest.com/mcp` on the API (and
worker) service, after migration `0091_oauth_connector` has run. Then
`https://api.calendarharvest.com/.well-known/oauth-protected-resource/mcp`
answers 200.

## Claude (claude.ai, Desktop, mobile, Cowork)

1. In Claude: **Settings → Connectors → Add custom connector**.
2. Name: `Vectorae`. URL: `https://api.calendarharvest.com/mcp`. Leave the
   OAuth client fields empty (Claude registers itself).
3. **Connect**. A Vectorae page opens: sign in if asked, read what the
   connection may do, and choose **Allow**.
4. In a chat, enable Vectorae from the tools menu and ask, for example,
   "Which of my leads have the strongest F-P-T-A score?".

Custom connectors are added per user. On Team and Enterprise plans an owner
may first need to allow custom connectors for the organisation.

**Add the skill (optional, recommended):** zip the folder
`.claude/skills/leadpilot-outbound/` (the folder itself at the top of the zip)
and upload it where Claude's settings manage Skills (Settings → Capabilities
or Features, depending on the version). Skills are per user.

Disconnect any time in **Settings → Connectors**. That stops Claude using the
connection; whether Claude also tells Vectorae to revoke its tokens is up
to Claude (Vectorae supports it at `/oauth/revoke`). To end every
connection on Vectorae's side, change your Vectorae password: that signs
out every device and every connector at once. (v1 has no "connected apps"
page in Vectorae yet.)

## Claude Code

Install the plugin (connector + skill together):

```
/plugin marketplace add Rmasood1122/LeadPilot
/plugin install leadpilot@leadpilot
```

The repository is private, so this needs access to it. Without access, copy
`plugins/leadpilot/` from a release and use `/plugin marketplace add
<local path>`, or add only the connector:

```
claude mcp add --transport http leadpilot https://api.calendarharvest.com/mcp
```

Then run `/mcp`, choose `leadpilot` → **Authenticate**; your browser opens
the same Vectorae sign-in and Allow page (Claude Code listens on a local
`http://localhost:<port>/callback`).

## ChatGPT

General tools need **Developer Mode** (Settings → Apps → Advanced; the menu
names change between releases — see OpenAI's "Developer mode" help page).

1. Turn on Developer Mode, then create an app / connector.
2. URL: `https://api.calendarharvest.com/mcp`, authentication: OAuth.
3. Sign in to Vectorae and **Allow** when the page opens.

Without Developer Mode and in Deep Research, ChatGPT uses only the two
read-only tools `search` and `fetch` (lead search and the full lead record).

---

## What a connection can do

| Permission (scope) | Allows |
|---|---|
| `leadpilot:read` | Read leads and F-P-T-A scores with their research, products, strategies and pipeline progress, outreach messages, attribution, claim checks. |
| `leadpilot:write` | Create a strategy (starts paid AI research, within your plan's limits and AI spend caps); pause or resume a campaign. Claude asks you before each one. |
| `offline_access` | Stay connected without signing in again, until you disconnect or 30 days pass without use. |

It **cannot** send, schedule or approve any message, change billing,
settings, team members or integrations, delete anything, or see another
account's data. It acts only on the account you signed in with (not on a
workspace you are a member of).

## Privacy and data handling

- **What leaves Vectorae.** Only what a tool returns for a request made in
  your chat: records from your own account. Claude or ChatGPT then processes
  it under your agreement with Anthropic or OpenAI; Vectorae does not
  control how that provider stores conversations.
- **Prospects' personal data.** Lead names, titles, companies, email
  addresses, scraped posts and message text are personal data of your
  prospects. Connecting a chat assistant is a disclosure to a processor you
  choose; check that your privacy notice and your agreement with the
  provider cover it (GDPR Art. 28). Ask the assistant only for what you
  need — tools return one page (≤ 50 records) at a time.
- **What Vectorae stores about a connection.** The app's registration
  (name and callback address), your consent (when, which app, which
  permissions), the session behind your tokens, and one audit record for
  every change made through the connector (which tool, when, which app,
  outcome). Tokens and codes are never stored in clear and never logged.
- **Your rights.** Connection records are included in your account export
  and deleted with your account. A password change ends every connection at
  once; a revocation request from the app ends that one connection.
- **Prompt injection.** Text written by prospects or scraped from the web
  is returned in a clearly marked `untrusted_content` field and never
  executed by Vectorae; the skill tells Claude never to follow instructions
  found in it.

Policy: https://www.calendarharvest.com/privacy/

---

## Directory submission checklist (Anthropic and OpenAI)

Done in code (verified by `tests/mcp/`, `tests/test_connector_skill.py`):

- [x] Remote MCP over HTTPS, Streamable HTTP, stateless.
- [x] OAuth 2.1: RFC 9728 resource metadata, RFC 8414 server metadata,
      dynamic client registration, PKCE S256, refresh-token rotation,
      `invalid_grant` on reuse, `iss` in authorization responses; Claude and
      ChatGPT callback URLs allowed.
- [x] Every tool has a `title` and `readOnlyHint: true` or
      `destructiveHint: true`; names ≤ 64 characters; no catch-all tool.
- [x] Descriptions say what each tool does, without behavioural steering.
- [x] Invalid input returns an actionable error; results are paged and
      bounded.
- [x] `search` and `fetch` for ChatGPT.

Founders / operations — not doable from the repository:

- [ ] **Public host live**: `MCP_ENABLED=true` on production and the
      metadata URLs answering 200 from the internet.
- [ ] **End-to-end check from claude.ai and ChatGPT** against production
      (`docs/program/PROGRESS.md` lists what has and has not been verified).
- [ ] **Test account** for reviewers: a Vectorae account with sample
      products, a strategy, leads and messages, credentials shared through
      the submission form only.
- [ ] **Documentation URL**: publish a public help article from this file
      (the help center lives under https://www.calendarharvest.com/help/).
- [ ] **Privacy policy URL**: https://www.calendarharvest.com/privacy/ —
      add a paragraph on chat-assistant connectors (what is shared, with
      whom); needs legal review (DECISIONS.md LEGAL-101 process).
- [ ] **Support contact** and a logo/icon for the listing.
- [ ] **Anthropic egress allowlisting** if a firewall or WAF sits in front of
      the API: Claude calls from `160.79.104.0/21`.
