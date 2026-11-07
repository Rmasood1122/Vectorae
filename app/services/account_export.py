"""GDPR account export -- every row this account owns, as JSON (Art. 15 / 20).

WHAT IS IN IT. Each table is reached through its OWNERSHIP path, a chain of
foreign-key columns that ends at users.id (for example leads: strategy_id ->
strategies.product_id -> products.user_id). Ownership edges only: actor columns
such as crm_notes.author_user_id, which point at a person who touched someone
else's record, are not ownership and are never followed. Every query is
filtered by the requesting account's own id; there is no path from one account
to another's rows.

COMPLETENESS IS TESTED, NOT HOPED FOR. tests/test_account_export.py walks the
whole schema and fails when a table that can hold this account's data is in
neither OWNERSHIP nor EXCLUDED, so a table added tomorrow cannot silently drop
out of the export.

WHAT IS LEFT OUT. Secrets and credentials (password hash, OAuth token
ciphertexts, webhook secrets, API key / share-link / verification hashes):
they are not personal data about the subject, and putting them in a
downloadable file would turn the export into a credential leak. Global tables
with no owner (suppression lists, aggregates, settings) are listed in
EXCLUDED with the reason.
"""

from __future__ import annotations

import base64
import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Table, and_, or_, select
from sqlalchemy.orm import Session

from app.db.base import Base

FORMAT_VERSION = 1

# table -> list of FK columns to follow, ending at users.id. [] means the table
# IS users (filtered by its primary key). Each hop must be a real foreign key
# in the model metadata; tests/test_account_export.py checks every path.
OWNERSHIP: dict[str, list[str]] = {
    "users": [],
    # --- owned directly -------------------------------------------------
    "account_security_events": ["user_id"],
    "activity_audit_events": ["user_id"],          # no FK by design (tamper-evident trail)
    "api_keys": ["user_id"],
    "api_usage": ["user_id"],
    "attribution_entries": ["user_id"],
    "auth_sessions": ["user_id"],
    # Connector consent: which client was approved, when, for which scopes
    # (the code itself is a one-way hash and never exported).
    "oauth_authorization_codes": ["user_id"],
    "billable_meetings": ["user_id"],
    "billing_renewal_notices": ["user_id"],        # auto-renewal notices sent
    "billing_subscriptions": ["user_id"],
    "credit_transaction_log": ["user_id"],         # pay-per-meeting credit audit trail
    "calendar_availability": ["user_id"],
    "calendar_booking_pages": ["user_id"],
    "campaign_costs": ["user_id"],
    "chat_sessions": ["user_id"],
    "claim_verification_log": ["user_id"],
    "compliance_audit_log": ["user_id"],
    "consent_events": ["user_id"],
    "customer_imports": ["user_id"],               # local business win-back imports (no PII)
    "voice_clones": ["user_id"],                   # voice twin: consent + clone metadata (no audio)
    "voice_twin_settings": ["user_id"],            # voice twin switches
    "agent_runs": ["user_id"],                     # agent supervisor run log
    "playbook_scores": ["user_id"],                # F-DB-01: per-account learning rates
    "daily_send_approvals": ["user_id"],           # daily send approval decisions
    "champion_moves": ["user_id"],                 # F10 (address hash only)
    "crm_custom_fields": ["user_id"],
    "crm_saved_views": ["user_id"],
    "crm_tags": ["user_id"],
    "deals": ["user_id"],
    "deliverability_checks": ["user_id"],
    "device_tokens": ["user_id"],
    "enrichment_attempts": ["user_id"],            # F01 cost ledger (no PII)
    # Email network (docs/features/email-network.md)
    "email_contacts": ["user_id"],
    "email_contact_matches": ["user_id"],
    "email_network_imports": ["user_id"],          # counts + attestation; tally transient
    "email_network_messages": ["user_id"],         # the user's own saved first emails
    "external_crm_connections": ["user_id"],
    "external_crm_links": ["user_id"],
    "gmail_accounts": ["user_id"],
    "guardrail_exclusions": ["user_id"],
    "guardrail_library": ["user_id"],
    "integration_tokens": ["user_id"],
    "lead_signals": ["user_id"],                   # F02 Signal Radar
    "linkedin_accounts": ["user_id"],
    "linkedin_connection_matches": ["user_id"],
    "linkedin_connections": ["user_id"],
    "linkedin_imports": ["user_id"],
    "linkedin_network_messages": ["user_id"],
    "linkedin_consents": ["user_id"],
    "linkedin_tasks": ["user_id"],
    "mailbox_health": ["user_id"],
    "meeting_prep_briefs": ["user_id"],
    "pilot_scorecards": ["user_id"],               # F09 weekly scorecards
    "pilots": ["user_id"],                         # F09 pilots + verdicts
    "meetings": ["host_user_id"],
    "products": ["user_id"],
    "quickstart_drafts": ["user_id"],              # F06
    "quickstart_runs": ["user_id"],                # F06
    "reply_copilot_settings": ["user_id"],         # F04
    "reply_drafts": ["user_id"],                   # F04
    "roleplay_sessions": ["user_id"],
    "security_audit_events": ["user_id"],          # no FK by design
    "share_links": ["user_id"],
    "spend_alerts": ["user_id"],                   # F07 Spend Guard (no PII)
    "spend_caps": ["user_id"],                     # F07
    "spend_forecasts": ["user_id"],                # F07
    "support_tickets": ["user_id"],
    "tool_integration_settings": ["user_id"],
    "tutorial_progress": ["user_id"],
    "webhook_targets": ["user_id"],
    "workspace_members": ["user_id"],
    "workspaces": ["owner_user_id"],
    # --- through a product ----------------------------------------------
    "past_clients": ["product_id", "user_id"],
    "voice_profiles": ["product_id", "user_id"],
    "strategies": ["product_id", "user_id"],
    # --- through a strategy ---------------------------------------------
    "autopilot_launches": ["strategy_id", "product_id", "user_id"],  # opt-in + state
    "lead_batches": ["strategy_id", "product_id", "user_id"],
    "leads": ["strategy_id", "product_id", "user_id"],
    "reply_sentiment_weeks": ["strategy_id", "product_id", "user_id"],
    "research_steps": ["strategy_id", "product_id", "user_id"],
    "roi_snapshots": ["strategy_id", "product_id", "user_id"],
    "sequences": ["strategy_id", "product_id", "user_id"],
    "strategy_model_outputs": ["strategy_id", "product_id", "user_id"],
    "strategy_uncertain_zones": ["strategy_id", "product_id", "user_id"],
    "strategy_versions": ["strategy_id", "product_id", "user_id"],
    # --- through a lead ---------------------------------------------------
    "calls": ["lead_id", "strategy_id", "product_id", "user_id"],
    "channel_suggestions": ["lead_id", "strategy_id", "product_id", "user_id"],
    "crm_activities": ["lead_id", "strategy_id", "product_id", "user_id"],
    "crm_custom_field_values": ["lead_id", "strategy_id", "product_id", "user_id"],
    "crm_lead_meta": ["lead_id", "strategy_id", "product_id", "user_id"],
    "crm_lead_tags": ["lead_id", "strategy_id", "product_id", "user_id"],
    "crm_notes": ["lead_id", "strategy_id", "product_id", "user_id"],
    "displacement_alerts": ["lead_id", "strategy_id", "product_id", "user_id"],
    "inbound_replies": ["lead_id", "strategy_id", "product_id", "user_id"],
    "meeting_outcomes": ["lead_id", "strategy_id", "product_id", "user_id"],
    "messages": ["lead_id", "strategy_id", "product_id", "user_id"],
    "outcomes": ["lead_id", "strategy_id", "product_id", "user_id"],
    "reengagement_attempts": ["lead_id", "strategy_id", "product_id", "user_id"],
    "reengagement_plans": ["lead_id", "strategy_id", "product_id", "user_id"],
    "sequence_enrollments": ["lead_id", "strategy_id", "product_id", "user_id"],
    "whatsapp_optins": ["lead_id", "strategy_id", "product_id", "user_id"],
    # --- through a sequence / message -------------------------------------
    "sequence_steps": ["sequence_id", "strategy_id", "product_id", "user_id"],
    "sequence_reviews": ["sequence_id", "strategy_id", "product_id", "user_id"],
    "send_reviews": ["message_id", "lead_id", "strategy_id", "product_id", "user_id"],
    # --- other parents ----------------------------------------------------
    "chat_messages": ["session_id", "user_id"],
    "calendar_bookings": ["booking_page_id", "user_id"],
    "meeting_participants": ["meeting_id", "host_user_id"],
    "roleplay_turns": ["session_id", "user_id"],
    "webhook_deliveries": ["target_id", "user_id"],
    "client_workspaces": ["workspace_id", "owner_user_id"],
    "client_sending_domains": ["client_workspace_id", "workspace_id", "owner_user_id"],
    "workspace_invitations": ["workspace_id", "owner_user_id"],
    "compliance_rules": ["workspace_id", "owner_user_id"],
    "site_pages": ["workspace_id", "owner_user_id"],
    "site_assets": ["page_id", "workspace_id", "owner_user_id"],
}

# Tables deliberately not exported, with the reason. Global tables have no
# owner column at all; the token tables hold one-way hashes (reset requests
# are exported through security_audit_events instead).
EXCLUDED: dict[str, str] = {
    "benchmark_buckets": "global aggregate, no owner",
    "linkedin_suppressions": "global do-not-contact list, no owner",
    "processed_webhooks": "provider event ids, no owner",
    "subject_line_patterns": "global aggregate, no owner",
    "suppression_list": "global do-not-contact list, no owner",
    "system_settings": "deployment configuration, no owner",
    "tutorial_catalogue": "product content, no owner",
    "help_chunks": "help-center search index built from docs/help-center, no owner",
    "whatsapp_templates": "global, no owner column",
    "task_errors": "operator diagnostics, no owner column",
    "email_verification_tokens": "one-way token hashes only",
    "password_reset_tokens": ("one-way token hashes; each request (time, IP) is "
                              "exported as a security_audit_events row"),
    "phone_verification_codes": "one-way code hashes only",
    "oauth_clients": ("connector OAuth client registrations (RFC 7591): anonymous, no "
                      "owner column; the person is bound at consent, in "
                      "oauth_authorization_codes and auth_sessions"),
    "voice_clone_deletions": ("voice twin: provider voice ids still to be deleted at "
                              "ElevenLabs after an erasure; no owner column, nothing "
                              "personal"),
}

# Credentials: never exported, on any table.
REDACTED_COLUMNS = frozenset({
    "password_hash", "token_hash", "key_hash", "code_hash", "token_ciphertext",
    "secret_encrypted", "encrypted_value", "unsubscribe_token",
    "domain_verification_token",
})
# Per-table credentials whose column name is too generic to redact everywhere.
REDACTED_BY_TABLE: dict[str, frozenset[str]] = {
    "integration_tokens": frozenset({"value"}),
    "device_tokens": frozenset({"token"}),
    # Voice twin: the provider's live-call control URL is a capability link.
    "calls": frozenset({"control_url"}),
}

# Rows whose FIRST ownership hop is NULL but which name their owner directly:
# an inbound call from a number that is no lead of the account (voice twin)
# has lead_id NULL and user_id = the account. table -> (first hop, owner column).
DIRECT_OWNER_FALLBACK: dict[str, tuple[str, str]] = {
    "calls": ("lead_id", "user_id"),
}
REDACTED = "[redacted]"

# Exported in this order so related rows read top-down; any table not listed
# follows alphabetically.
_ORDER = ("users", "products", "strategies", "leads", "sequences", "messages")


def _table(name: str) -> Table:
    return Base.metadata.tables[name]


def _fk_target(table: Table, column: str) -> Table | None:
    """The table a column's foreign key points at, or None if it has none."""
    for fk in table.c[column].foreign_keys:
        return fk.column.table
    return None


# Columns with no FK by design whose values are users.id.
_PLAIN_USER_COLUMNS = {("activity_audit_events", "user_id"),
                       ("security_audit_events", "user_id")}


def owner_clause(name: str, user_id: uuid.UUID):
    """WHERE clause selecting the rows of table `name` owned by `user_id`.

    Built hop by hop as nested IN (SELECT ...) subqueries along OWNERSHIP[name]:
    the last hop compares to the account's id, each earlier hop selects the
    parent ids that are themselves owned.
    """
    table = _table(name)
    path = OWNERSHIP[name]
    if not path:
        return table.c.id == user_id
    clause = _hop(table, path, user_id)
    fallback = DIRECT_OWNER_FALLBACK.get(name)
    if fallback is not None:
        hop, owner = fallback
        clause = or_(clause, and_(table.c[hop].is_(None), table.c[owner] == user_id))
    return clause


def _hop(table: Table, path: list[str], user_id: uuid.UUID):
    column = table.c[path[0]]
    if len(path) == 1:
        return column == user_id
    parent = _fk_target(table, path[0])
    if parent is None:
        raise ValueError(f"{table.name}.{path[0]} has no foreign key to follow")
    return column.in_(select(parent.c.id).where(_hop(parent, path[1:], user_id)))


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"base64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


def _row_out(name: str, row) -> dict:
    hidden = REDACTED_COLUMNS | REDACTED_BY_TABLE.get(name, frozenset())
    out = {}
    for key, value in row._mapping.items():
        if key in hidden or (name == "tool_integration_settings" and key == "config_value"
                             and row._mapping.get("is_encrypted")):
            out[key] = REDACTED if value is not None else None
        else:
            out[key] = _jsonable(value)
    return out


def _table_order() -> list[str]:
    rest = sorted(n for n in OWNERSHIP if n not in _ORDER)
    return [n for n in _ORDER if n in OWNERSHIP] + rest


def build(session: Session, user_id: uuid.UUID) -> dict:
    """The full export for one account. Read-only; never commits."""
    tables: dict[str, list[dict]] = {}
    for name in _table_order():
        if name not in Base.metadata.tables:
            continue
        table = _table(name)
        query = select(table).where(owner_clause(name, user_id))
        if "created_at" in table.c:
            query = query.order_by(table.c.created_at, *table.primary_key.columns)
        else:
            query = query.order_by(*table.primary_key.columns)
        tables[name] = [_row_out(name, row) for row in session.execute(query)]
    return {
        "format": "leadpilot-account-export",
        "format_version": FORMAT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "account_id": str(user_id),
        "redacted": sorted(REDACTED_COLUMNS
                           | {f"{t}.{c}" for t, cs in REDACTED_BY_TABLE.items() for c in cs}
                           | {"tool_integration_settings.config_value (when encrypted)"}),
        "not_exported": dict(sorted(EXCLUDED.items())),
        "row_counts": {name: len(rows) for name, rows in tables.items()},
        "tables": tables,
    }
