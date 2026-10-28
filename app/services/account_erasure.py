"""GDPR account erasure -- the account holder's right to erasure (Art. 17).

DELETE /me (app/api/account_data.py) calls erase_account() after the password
and the typed confirmation are checked, any paid plan is cancelled
(cancel_billing, fail closed) and connected LinkedIn accounts are disconnected
at Unipile (disconnect_providers). Spec: docs/features/gdpr-erasure.md.

WHAT BELONGS TO THE ACCOUNT. account_export.OWNERSHIP is the single source of
truth: the same ownership paths that decide what the export contains decide
what erasure removes. Every OWNERSHIP table has exactly one decision in
ERASURE below, and tests/test_account_erasure.py fails when a table is added to
OWNERSHIP without one -- a new table cannot silently survive an erasure.

THE DECISIONS (founder/legal confirmation pending, see the spec):

  DELETE         the rows are deleted.
  TOMBSTONE      users: the row stays as a non-personal tombstone (id, created
                 date, a deleted marker); every personal column is cleared.
                 Kept, not deleted, because billing rows (E1) and the consent
                 proof (E2) still reference the id, and because outstanding
                 access tokens then resolve to an account that says "deleted"
                 (401) instead of to nothing.
  KEEP_BILLING   E1 -- invoices and subscription mirrors, kept for
                 BILLING_RETENTION_YEARS for tax/accounting record-keeping.
                 They hold amounts, dates, statuses and Stripe ids, never a
                 name or email; links to deleted prospects are cleared.
  KEEP_PROOF     E2 -- the consent ledger, and the per-send compliance
                 decisions, kept as proof that an opt-out was honoured and a
                 send was lawful. Links to deleted prospects/messages are
                 cleared. consent_events.identifier stays in clear text: the
                 suppression model has no hashed form to convert it to
                 (suppression_list stores the address itself), and the global
                 suppression list -- which is what actually keeps an
                 unsubscribed person unsubscribed -- is not account data and
                 is never touched here.
  KEEP_SEALED    the hash-chained activity trail. Its payload is ids, steps and
                 classifications (no personal data) and PostgreSQL refuses to
                 delete or alter it (migration 0042), by design.
  KEEP_SCRUBBED  security records: the rows (action, time) stay as a record of
                 what happened to the account; IP address, user agent, email
                 and free-form details are cleared.

E3 -- WORKSPACES. Deleting an OWNER removes the owner's workspaces and every
membership and invitation in them; the members' own accounts are untouched.
Deleting a MEMBER removes only that member's own user data and memberships
(plus invitations addressed to their email). Other accounts' rows that point
at the deleted person (an author, an approver, an assignee) lose that pointer,
exactly as a hard delete's ON DELETE SET NULL would do.

THE RECORD. One security_audit_events row (ACCOUNT_DELETED) is written in the
same transaction, with row counts and the decisions applied -- no email, no IP,
no user agent. It is keyed by the tombstone's id, so "was account X erased,
and when?" has an answer that is not itself personal data.

Nothing here commits; the caller commits once, so the erasure and its record
land together or not at all.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, Boolean, Table, and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.db.base import Base
from app.db.models import (
    EnrollmentStatus,
    MeetingParticipantRole,
    Message,
    MessageStatus,
    OptInStatus,
    PlanTier,
    SequenceEnrollment,
    User,
)
from app.services import account_export, security_audit, send_holds

logger = logging.getLogger(__name__)

# E1: how long kept billing rows are retained. 7 years covers the longest of
# the common US federal record-keeping periods (IRS: 3 years generally, 6-7 in
# specific cases) and the UK's 6; some EU states require longer (Germany 10).
# NEEDS FOUNDER/LEGAL CONFIRMATION. Nothing purges them automatically yet: the
# number is stated in the policy and recorded on every erasure, and a purge
# job is a separate, reviewed change.
BILLING_RETENTION_YEARS = 7

# What the person types to confirm. Exact, case-sensitive.
CONFIRMATION_PHRASE = "DELETE"

# The tombstone's markers. `.invalid` is reserved by RFC 2606: no mail to it can
# ever be delivered, and no real address can collide with it.
TOMBSTONE_DOMAIN = "deleted.invalid"
TOMBSTONE_REASON = "account_deleted"

DELETE = "delete"
TOMBSTONE = "tombstone"
KEEP_BILLING = "keep_billing"
KEEP_PROOF = "keep_proof"
KEEP_SEALED = "keep_sealed"
KEEP_SCRUBBED = "keep_scrubbed"

# Rows kept (not deleted), with the decision and why.
KEPT: dict[str, str] = {
    "users": TOMBSTONE,
    "billing_subscriptions": KEEP_BILLING,       # E1
    "billable_meetings": KEEP_BILLING,           # E1
    # E1 + auto-renewal law: proof that each renewal / trial notice was sent
    # (kind, date, amount -- no name or address; `error` is cleared below).
    "billing_renewal_notices": KEEP_BILLING,
    "consent_events": KEEP_PROOF,                # E2
    "compliance_audit_log": KEEP_PROOF,          # E2 (send-decision proof, refs cleared)
    "activity_audit_events": KEEP_SEALED,        # hash chain, no PII, PG refuses DELETE
    "security_audit_events": KEEP_SCRUBBED,
    "account_security_events": KEEP_SCRUBBED,
}

# Every other OWNERSHIP table, listed BY NAME (not "everything else"): a table
# added to OWNERSHIP tomorrow must be put here or in KEPT on purpose, or
# tests/test_account_erasure.py fails.
DELETED: frozenset[str] = frozenset({
    "api_keys", "api_usage", "attribution_entries", "auth_sessions",
    # Connector consent records (docs/features/mcp-connector.md); the
    # sessions they started are auth_sessions rows, deleted above.
    "oauth_authorization_codes",
    "autopilot_launches",  # opt-in proof stays in compliance_audit_log (E2)
    "daily_send_approvals",  # the decisions themselves stay in compliance_audit_log (E2)
    # Local business win-back import log (attestation, counts, refused line
    # numbers). The proof of each consent is consent_events, KEPT as E2.
    "customer_imports",
    "calendar_availability", "calendar_booking_pages", "calendar_bookings", "calls",
    "campaign_costs", "champion_moves", "channel_suggestions", "chat_messages",
    "chat_sessions", "claim_verification_log", "client_sending_domains",
    "client_workspaces", "compliance_rules", "crm_activities", "crm_custom_field_values",
    "crm_custom_fields", "crm_lead_meta", "crm_lead_tags", "crm_notes", "crm_saved_views",
    "crm_tags", "deals", "deliverability_checks", "device_tokens", "displacement_alerts",
    "enrichment_attempts", "external_crm_connections", "external_crm_links",
    "gmail_accounts", "guardrail_exclusions", "guardrail_library", "inbound_replies",
    "integration_tokens", "lead_batches", "lead_signals", "leads", "linkedin_accounts",
    "linkedin_connection_matches", "linkedin_connections", "linkedin_imports",
    "linkedin_network_messages", "mailbox_health", "meeting_outcomes",
    "meeting_participants", "meeting_prep_briefs", "meetings", "messages", "outcomes",
    "past_clients", "pilot_scorecards", "pilots", "products", "quickstart_drafts",
    "quickstart_runs", "reengagement_attempts", "reengagement_plans",
    "reply_copilot_settings", "reply_drafts", "reply_sentiment_weeks", "research_steps",
    "roi_snapshots", "roleplay_sessions", "roleplay_turns", "send_reviews",
    "sequence_enrollments", "sequence_reviews", "sequence_steps", "sequences",
    "share_links", "site_assets", "site_pages", "spend_alerts", "spend_caps",
    "spend_forecasts", "strategies", "strategy_model_outputs", "strategy_uncertain_zones",
    "strategy_versions", "support_tickets", "tool_integration_settings",
    "tutorial_progress", "voice_profiles", "webhook_deliveries", "webhook_targets",
    # Voice twin: the clone is deleted AT THE PROVIDER first
    # (disconnect_providers -> voice_twin.provider_deletions_for_erasure).
    "voice_clones", "voice_twin_settings",
    # Email network: the account's imported contacts, scores, saved first
    # emails and import records (docs/features/email-network.md).
    "email_contacts", "email_contact_matches", "email_network_imports",
    "email_network_messages",
    "whatsapp_optins", "workspace_invitations", "workspace_members", "workspaces",
    # Agent supervisor run log (docs/features/vectorae-agents.md).
    "agent_runs",
    # F-DB-01: the account's own learned playbook rates (migration 0093).
    # Pre-0093 pooled rows have no owner and are not this account's data.
    "playbook_scores",
})

ERASURE: dict[str, str] = {**{name: DELETE for name in DELETED}, **KEPT}

# NOT DELETED, AND NOT IN KEPT (voice twin, review fix M3): voice_clone_deletions.
# When the ElevenLabs delete of a voice clone fails during erasure, a tombstone
# row (provider + the provider's opaque voice id + attempts; no owner column,
# nothing personal) is written there and SURVIVES the erasure, because it is
# the only way to finish deleting the person's biometric voice model at the
# provider (GDPR Art. 17 / BIPA s.15(a) -- needs founder/legal confirmation).
# It is not an OWNERSHIP table (it has no owner), so it cannot be in KEPT; the
# hourly voice_clone_housekeeping task retries it, raises an operator alert
# (task_errors + security_audit_events) and deletes the row on success.
PROVIDER_DELETION_QUEUE = "voice_clone_deletions"

# Tables account_export EXCLUDES (no exportable content) that still hang off
# users.id: their rows for the account are deleted too.
EXCLUDED_USER_TABLES: dict[str, str] = {
    "email_verification_tokens": "user_id",
    "phone_verification_codes": "user_id",
    "password_reset_tokens": "user_id",   # hashed reset links (docs/features/password-reset.md)
}

# KEEP_SCRUBBED: the columns cleared on kept security rows.
SCRUBBED_COLUMNS: dict[str, tuple[str, ...]] = {
    "security_audit_events": ("ip", "user_agent", "details_json"),
    "account_security_events": ("email", "ip", "country", "details_json"),
    # Free text on kept billing rows: a support note or a provider error can
    # name a person. Amounts, dates and Stripe ids stay (E1).
    "billing_subscriptions": ("support_note",),
    "billing_renewal_notices": ("error",),
}

# users columns that survive on the tombstone. Every other column is reset to
# NULL (or its non-null empty value), so a column added later is cleared by
# default rather than leaked by default.
TOMBSTONE_KEEPS = frozenset({
    "id", "created_at", "updated_at", "email", "plan", "is_suspended", "suspended_at",
    "suspended_reason",
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _table(name: str) -> Table:
    return Base.metadata.tables[name]


def tombstone_email(user_id: uuid.UUID) -> str:
    return f"deleted-{user_id.hex}@{TOMBSTONE_DOMAIN}"


def is_tombstone(user: User | None) -> bool:
    """True for an account erased by erase_account(). Keyed on the email
    marker AND the suspension reason, so an admin clearing the suspension does
    not make a tombstone look like a live account."""
    if user is None:
        return False
    email = getattr(user, "email", None)
    email = email.lower() if isinstance(email, str) else ""
    return (email.endswith("@" + TOMBSTONE_DOMAIN)
            or getattr(user, "suspended_reason", None) == TOMBSTONE_REASON)


# --------------------------------------------------------------------------
# Billing: stop every future charge, or refuse the deletion (fail closed)
# --------------------------------------------------------------------------

# The note a not-yet-charged meeting carries when the account that owes it is
# deleted. Non-personal on purpose: it stays on a billing row that is kept.
WAIVED_ON_ACCOUNT_DELETION = "waived: account deleted before the charge date"
_UNCHARGED = ("pending", "disputed")

BILLING_NOT_CONFIGURED = (
    "Your paid plan cannot be cancelled automatically because payments are not "
    "configured on this server, so nothing was deleted. Contact support to delete "
    "your account.")
BILLING_NOT_CANCELLED = (
    "The payment provider did not confirm that your plan is cancelled, so nothing "
    "was deleted. Try again in a few minutes, or contact support.")


class BillingNotCancellable(Exception):
    """A live subscription could not be confirmed cancelled: the deletion
    must not go ahead (Stripe would keep charging a deleted customer)."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _stripe_subscription_gone(stripe, subscription_id: str) -> bool:
    """After a failed cancel: is the subscription already cancelled, or gone
    (Stripe 404 / resource_missing)? Either way nothing more will be charged,
    so a stale local row must not block the deletion forever."""
    try:
        data = stripe.retrieve_subscription(subscription_id)
    except stripe.StripeError as exc:
        return "HTTP 404" in str(exc)
    return (data or {}).get("status") in ("canceled", "incomplete_expired")


def cancel_billing(db: Session, user_id: uuid.UUID, now: datetime | None = None) -> dict:
    """Stop every future charge for an account that is being deleted.

    A subscription that exists IN STRIPE (it has a Stripe id and is not a stub)
    is cancelled there NOW, not at period end -- the account is about to stop
    existing. If that cannot be confirmed (no Stripe key on this server, or
    Stripe refuses or cannot be reached and the subscription is not already
    cancelled), BillingNotCancellable is raised BEFORE anything local changes,
    and the caller refuses the deletion. No refund for the current period is
    issued (Stripe's DELETE does not prorate by default) -- a founder decision.
    Pay-per-meeting meetings still inside their grace period are waived so the
    charge sweep never bills a deleted account. Does not commit.
    """
    from app.integrations import stripe_billing as stripe  # noqa: PLC0415
    from app.services import billing  # noqa: PLC0415

    now = now or _now()
    result = {"subscription": "none", "meetings_waived": 0}
    sub = billing.get_subscription(db, user_id)
    if sub is not None and sub.status != "canceled":
        in_stripe = bool(sub.stripe_subscription_id) and not sub.is_stub
        if in_stripe:
            if settings.billing_stub_mode:
                # A real subscription and no key to cancel it with: a local
                # "canceled" would be a lie Stripe keeps charging through.
                raise BillingNotCancellable(BILLING_NOT_CONFIGURED)
            try:
                stripe.cancel_subscription_now(sub.stripe_subscription_id)
                result["subscription"] = "canceled_in_stripe"
            except stripe.StripeError as exc:
                if not _stripe_subscription_gone(stripe, sub.stripe_subscription_id):
                    raise BillingNotCancellable(BILLING_NOT_CANCELLED) from exc
                result["subscription"] = "already_canceled_in_stripe"
        else:
            logger.warning("account deletion: cancelling subscription %s locally (%s)",
                           sub.id, "stub" if sub.is_stub else "no Stripe subscription")
            result["subscription"] = "canceled_locally"
        sub.status, sub.canceled_at, sub.cancel_at_period_end = "canceled", now, False
        user = db.get(User, user_id)
        if user is not None:
            user.plan = PlanTier.FREE
    elif sub is not None:
        result["subscription"] = "already_canceled"
    meetings = _table("billable_meetings")
    result["meetings_waived"] = db.execute(
        update(meetings).where(meetings.c.user_id == user_id,
                               meetings.c.status.in_(_UNCHARGED))
        .values(status="waived", resolution_note=WAIVED_ON_ACCOUNT_DELETION)
        .execution_options(synchronize_session=False)).rowcount or 0
    return result


# --------------------------------------------------------------------------
# Outside Vectorae: providers and files
# --------------------------------------------------------------------------


def disconnect_providers(db: Session, user_id: uuid.UUID) -> dict:
    """Disconnect the account's LinkedIn accounts AT UNIPILE, so the provider
    drops the stored LinkedIn session too -- deleting our row alone would
    leave it there. Best effort, BEFORE the erasure (the per-account Unipile
    credentials are erased with it): a failure is logged and recorded on the
    erasure record, never a reason to keep the person's data.

    Not done (no adapter support / founder decision, see the spec): revoking
    Google, HubSpot, Salesforce or Slack grants on the provider side, and
    deleting the Stripe Customer object.
    """
    from app.db.models import LinkedInAccount  # noqa: PLC0415
    from app.integrations import unipile_linkedin  # noqa: PLC0415

    # Voice twin: a Professional Voice Clone of the owner's voice is biometric
    # data held AT ELEVENLABS; delete it there before our rows go. Only added
    # to the record when the account ever had one.
    from app.services import voice_twin  # noqa: PLC0415

    voices = voice_twin.provider_deletions_for_erasure(db, user_id)
    extra = {"elevenlabs_voices": voices} if voices is not None else {}
    ids = list(db.execute(select(LinkedInAccount.unipile_account_id)
                          .where(LinkedInAccount.user_id == user_id)).scalars())
    if not ids:
        return {"unipile": "none", **extra}
    try:
        client = unipile_linkedin.get_client(db, user_id)
    except Exception:  # noqa: BLE001
        logger.exception("account deletion: could not build the Unipile client")
        client = None
    if client is None:
        return {"unipile": "not_configured", "accounts": len(ids), **extra}
    done = failed = 0
    for account_id in ids:
        try:
            client.delete_account(account_id)
            done += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning("account deletion: Unipile account %s not disconnected: %s",
                           account_id, exc)
    return {"unipile": {"disconnected": done, "failed": failed}, **extra}


_VOICEMAIL_FILE = re.compile(r"^[0-9a-f]{32}\.mp3$")


def _voicemail_paths(urls) -> list[Path]:
    from app.services.phone_calls import VOICEMAIL_DIR  # noqa: PLC0415

    folder = Path(settings.media_dir) / VOICEMAIL_DIR
    out = []
    for url in urls:
        name = (url or "").rsplit("/", 1)[-1]
        if _VOICEMAIL_FILE.match(name):     # never a path a URL could steer
            out.append(folder / name)
    return out


def collect_media_files(db: Session, user_id: uuid.UUID) -> list[Path]:
    """Files on disk that belong to the account, found BEFORE its rows go:
    uploaded theme backgrounds (media/bg_<user hex>_*), generated voicemail
    audio of its calls (media/voicemails/<hex>.mp3) and its workspaces'
    uploaded logos (media/branding/<workspace hex>-*)."""
    media = Path(settings.media_dir)
    paths = list(media.glob(f"bg_{user_id.hex}_*")) if media.is_dir() else []
    calls = _table("calls")
    paths += _voicemail_paths(db.execute(
        select(calls.c.voicemail_audio_url).where(
            account_export.owner_clause("calls", user_id),
            calls.c.voicemail_audio_url.isnot(None))).scalars())
    branding = media / "branding"
    if branding.is_dir():
        for workspace_id in db.execute(_owned_ids("workspaces", user_id)).scalars():
            paths += list(branding.glob(f"{workspace_id.hex}-*"))
    return paths


def delete_files(paths) -> int:
    """Best effort, after the commit: a file that cannot be removed is logged,
    never a reason to undo an erasure that already happened."""
    removed = 0
    for path in paths:
        try:
            Path(path).unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as exc:  # pragma: no cover - filesystem dependent
            logger.error("erasure: could not remove %s: %s", path, exc)
    return removed


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------


def _owned_ids(name: str, user_id: uuid.UUID):
    table = _table(name)
    return select(table.c.id).where(account_export.owner_clause(name, user_id))


def _stop_outreach(db: Session, user_id: uuid.UUID) -> dict:
    """Cancel every message that could still go out and stop every enrollment.

    The rows are deleted later in the same transaction; this step is what a
    concurrent dispatcher would see if it read them first, and it is the count
    the erasure record reports. A message already claimed by a send task
    (SENDING) is left alone: that send is in flight and cannot be recalled.
    """
    owned_messages = account_export.owner_clause("messages", user_id)
    cancellable = or_(Message.status.in_([MessageStatus.SCHEDULED,
                                          MessageStatus.AWAITING_REVIEW]),
                      send_holds.held_clause())
    cancelled = db.execute(
        update(Message).where(owned_messages, cancellable)
        .values(status=MessageStatus.CANCELLED, error="account deleted")
        .execution_options(synchronize_session=False)).rowcount or 0
    stopped = db.execute(
        update(SequenceEnrollment)
        .where(account_export.owner_clause("sequence_enrollments", user_id),
               SequenceEnrollment.status.in_([EnrollmentStatus.ACTIVE,
                                              EnrollmentStatus.PAUSED]))
        .values(status=EnrollmentStatus.STOPPED, stop_reason="account deleted")
        .execution_options(synchronize_session=False)).rowcount or 0
    return {"messages_cancelled": cancelled, "enrollments_stopped": stopped}


def kept_links_to_deleted() -> list[tuple[str, str, str]]:
    """(kept table, column, deleted table) for every FK from a kept table to a
    table whose rows are deleted. Those columns are cleared first."""
    links = []
    for name in KEPT:
        table = _table(name)
        for fk in table.foreign_keys:
            target = fk.column.table.name
            if ERASURE.get(target) == DELETE:
                links.append((name, fk.parent.name, target))
    return sorted(links)


def _clear_kept_links(db: Session, user_id: uuid.UUID) -> None:
    for name, column, target in kept_links_to_deleted():
        table = _table(name)
        db.execute(update(table).where(table.c[column].in_(_owned_ids(target, user_id)))
                   .values({column: None}).execution_options(synchronize_session=False))


def _anonymise_kept(db: Session, user_id: uuid.UUID, email: str) -> None:
    # E1: a dispute reason or admin note is free text a person typed, and can
    # name a prospect. The note the deletion itself wrote is kept.
    meetings = _table("billable_meetings")
    db.execute(update(meetings)
               .where(meetings.c.user_id == user_id,
                      or_(meetings.c.resolution_note.is_(None),
                          meetings.c.resolution_note != WAIVED_ON_ACCOUNT_DELETION))
               .values(resolution_note=None).execution_options(synchronize_session=False))
    for name, columns in SCRUBBED_COLUMNS.items():
        table = _table(name)
        clause = account_export.owner_clause(name, user_id)
        if "email" in table.c and email:
            # A refused sign-in or sign-up (a VPN block, say) is recorded with
            # NO user id -- only the address that tried. Same person, same data.
            clause = or_(clause, func.lower(table.c.email) == email)
        db.execute(update(table).where(clause)
                   .values({c: None for c in columns})
                   .execution_options(synchronize_session=False))


def _extra_clause(name: str, user_id: uuid.UUID, email: str):
    """E3 additions to the ownership clause."""
    table = _table(name)
    if name == "workspace_members":
        # Everyone's membership in a workspace this account OWNS.
        return table.c.workspace_id.in_(_owned_ids("workspaces", user_id))
    if name == "workspace_invitations" and email:
        # Invitations addressed to this person, in anyone's workspace.
        return func.lower(table.c.email) == email
    return None


def deletion_order() -> list[str]:
    """DELETE tables, children before parents (reverse FK order), so every
    nested ownership subquery still finds its parent rows and no FK is ever
    left pointing at a deleted row."""
    return [t.name for t in reversed(Base.metadata.sorted_tables)
            if ERASURE.get(t.name) == DELETE]


def _delete_owned(db: Session, user_id: uuid.UUID, email: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in deletion_order():
        table = _table(name)
        clause = account_export.owner_clause(name, user_id)
        extra = _extra_clause(name, user_id, email)
        if extra is not None:
            clause = or_(clause, extra)
        counts[name] = db.execute(delete(table).where(clause)
                                  .execution_options(synchronize_session=False)).rowcount or 0
    for name, column in EXCLUDED_USER_TABLES.items():
        table = _table(name)
        counts[name] = db.execute(delete(table).where(table.c[column] == user_id)
                                  .execution_options(synchronize_session=False)).rowcount or 0
    return {k: v for k, v in counts.items() if v}


def actor_columns() -> list[tuple[str, str]]:
    """(table, column) for every ON DELETE SET NULL foreign key to users.id
    that is NOT a table's ownership column: an author, approver, assignee,
    reviewer. Ownership columns are excluded so the kept proof rows keep
    their owner and the ownership clauses keep working."""
    out = []
    for table in Base.metadata.sorted_tables:
        own = account_export.OWNERSHIP.get(table.name)
        for fk in table.foreign_keys:
            if fk.column.table.name != "users" or fk.ondelete != "SET NULL":
                continue
            if own == [fk.parent.name]:
                continue
            out.append((table.name, fk.parent.name))
    return sorted(out)


def _clear_actor_references(db: Session, user_id: uuid.UUID) -> None:
    for name, column in actor_columns():
        table = _table(name)
        db.execute(update(table).where(table.c[column] == user_id)
                   .values({column: None}).execution_options(synchronize_session=False))


def _tombstone(db: Session, user: User, now: datetime) -> None:
    """Clear every personal column on the users row; keep the id."""
    table = _table("users")
    values: dict = {}
    for column in table.columns:
        if column.name in TOMBSTONE_KEEPS:
            continue
        if column.nullable:
            values[column.name] = None
        elif isinstance(column.type, Boolean):
            values[column.name] = False
        elif isinstance(column.type, JSON):
            values[column.name] = {}
        else:
            # A new NOT NULL column with no obvious empty value: decide it here
            # (or in TOMBSTONE_KEEPS) rather than guess.
            raise ValueError(f"users.{column.name}: no tombstone value defined")
    values.update(email=tombstone_email(user.id), plan=PlanTier.FREE,
                  is_suspended=True, suspended_at=now, suspended_reason=TOMBSTONE_REASON)
    db.execute(update(table).where(table.c.id == user.id).values(values)
               .execution_options(synchronize_session=False))


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def erase_account(db: Session, user: User, *, now: datetime | None = None,
                  billing: dict | None = None, providers: dict | None = None) -> dict:
    """Erase everything `user` owns, per ERASURE. Does not commit.

    Order: stop outreach -> clear kept rows' links to rows about to go ->
    scrub kept rows -> delete (children first) -> clear other accounts'
    pointers to this person -> tombstone the users row -> write the record.
    """
    now = now or _now()
    user_id = user.id
    email = (user.email or "").strip().lower()

    stopped = _stop_outreach(db, user_id)
    _clear_kept_links(db, user_id)
    _anonymise_kept(db, user_id, email)
    deleted = _delete_owned(db, user_id, email)
    _clear_actor_references(db, user_id)
    _tombstone(db, user, now)

    summary = {
        "deleted_at": now.isoformat(),
        "rows_deleted": deleted,
        "outreach": stopped,
        "billing": billing or {},
        "providers": providers or {},
        "kept": {name: decision for name, decision in sorted(KEPT.items())},
        "billing_retention_years": BILLING_RETENTION_YEARS,
    }
    # No request: the record must not carry the person's IP or user agent.
    security_audit.record(db, action=security_audit.ACCOUNT_DELETED, request=None,
                          user_id=user_id, target_type="account", target_id=user_id,
                          details=summary)
    db.flush()
    db.expire_all()
    logger.info("account %s erased (%d rows deleted)", user_id, sum(deleted.values()))
    return summary


# --------------------------------------------------------------------------
# One prospect's correspondence (the lead-level GDPR delete, app/api/leads.py)
# --------------------------------------------------------------------------

# Written into NOT NULL text columns whose content is erased.
ERASED_TEXT = "[erased]"
LEAD_ERASED_ERROR = "lead erased on request (GDPR)"


# Written into NOT NULL address columns: undeliverable (RFC 2606), so a
# reminder or notification that still reads the row can never reach anyone.
ERASED_EMAIL = "erased@" + TOMBSTONE_DOMAIN

# What a lead erasure does to each table that can hold the person's data, as
# (table, columns -> value) for an UPDATE, or (table, DELETE). Rows are found
# by `lead_id`, or through the lead's messages / meetings (see _lead_rows).
# tests/test_lead_erasure_correspondence.py fills every listed column and
# checks it is gone.
LEAD_SCRUB: dict[str, dict | str] = {
    "inbound_replies": {"from_address": ERASED_TEXT, "subject": None, "body": ERASED_TEXT,
                        "ai_draft_response": None, "intent_reason": None,
                        "authenticity_signals_json": None, "thread_ref": None,
                        # 0086: the email's Message-ID / References can name
                        # the sender's host or address; erased with the rest.
                        "rfc_message_id": None, "rfc_references": None},
    "send_reviews": {"subject_snapshot": None, "body_snapshot": None,
                     "edited_subject": None, "edited_body": None,
                     "decision_note": None, "triggers_json": None},
    "attribution_entries": {"subject_snapshot": None, "body_snapshot": None,
                            "evidence_json": None},
    "claim_verification_log": {"claim_text": ERASED_TEXT, "evidence_excerpt": None,
                               "evidence_source": None, "replacement_text": None,
                               "unsupported_json": None},
    "meeting_outcomes": {"notes": None, "followup_subject": None, "followup_body": None,
                         "draft_error": None},
    "calls": {"to_number": ERASED_TEXT, "transcript": None, "voicemail_text": None,
              "voicemail_audio_url": None, "recording_url": None, "script_json": None,
              "analysis_json": None, "error": None,
              # Voice twin: an inbound caller's number and the live-control link.
              "from_number": None, "control_url": None},
    "calendar_bookings": {"invitee_name": ERASED_TEXT, "invitee_email": ERASED_EMAIL,
                          "invitee_phone": None, "invitee_timezone": None, "notes": None,
                          "answers": None, "meeting_link": None},
    "meetings": {"title": None, "meeting_url": None, "raw_notes": None, "transcript": None,
                 "recording_url": None, "ai_notes": None, "summary": None,
                 "action_items": None, "key_points": None, "next_steps": None,
                 "sentiment": None},
    "meeting_participants": {"name": None, "email": None},
    "meeting_prep_briefs": {"content_md": None, "sections_json": None, "profile_json": None,
                            "opening_script": None, "script_json": None, "error": None,
                            "meeting_url": None},
    "deals": {"notes": None},
    "reengagement_plans": {"reason_text": None},
    "crm_activities": {"from_value": None, "to_value": None, "meta_json": None},
    "roleplay_sessions": {"persona_json": None, "objectives_json": None,
                          "feedback_json": None},
    # Decision: the opt-in record's phone and evidence are erased, not kept as
    # proof. The proof that this number must never be contacted again is the
    # suppression-list entry and the consent-ledger erasure event written by
    # the same request; a second copy of the number here adds no protection.
    "whatsapp_optins": {"phone": ERASED_TEXT, "evidence": None, "consent_text": None},
    "crm_notes": DELETE,                  # free text about the person; no counts read it
    "displacement_alerts": DELETE,        # a post excerpt and a drafted DM to the person
    "crm_custom_field_values": DELETE,    # values typed about the person
}


def _lead_rows(name: str, lead_id: uuid.UUID):
    """WHERE clause: the rows of `name` that belong to this lead."""
    table = _table(name)
    messages = select(Message.id).where(Message.lead_id == lead_id)
    bookings_t = _table("calendar_bookings")
    meetings_t = _table("meetings")
    lead_meetings = select(meetings_t.c.id).where(or_(
        meetings_t.c.lead_id == lead_id,
        meetings_t.c.booking_id.in_(select(bookings_t.c.id)
                                    .where(bookings_t.c.lead_id == lead_id))))
    if name == "meetings":
        return table.c.id.in_(lead_meetings)
    if name == "meeting_participants":
        # The host is the account holder, not the prospect.
        return and_(table.c.meeting_id.in_(lead_meetings),
                    table.c.role != MeetingParticipantRole.HOST)
    clauses = [table.c.lead_id == lead_id]
    if "message_id" in table.c:
        clauses.append(table.c.message_id.in_(messages))
    return or_(*clauses)


def erase_lead_correspondence(db: Session, lead_id: uuid.UUID,
                              now: datetime | None = None) -> dict:
    """Erase what one prospect wrote, what was written to them, and what was
    recorded about them, for the lead-level GDPR delete (app/api/leads.py).

    messages: anything that could still go out -- SCHEDULED, AWAITING_REVIEW,
    guard-held AND a SENDING message whose send is being rendered -- is
    cancelled first; then subject, body, personalization sources and provider
    error text are cleared on every message. A send task that claimed a
    message re-asserts its claim right before transmitting
    (confirm_send_claim): after this, that fails, so the task neither sends
    nor keeps what it rendered.

    Every table in LEAD_SCRUB: the listed columns are cleared (or the rows
    deleted). What the counts and dashboards read -- statuses, channels,
    classifications, scores, timestamps, amounts -- stays, so campaign
    numbers do not silently change after an erasure. Generated voicemail audio
    files of the person's calls are removed. Does not commit.
    """
    from app.db.models import ReengagementPlan, WhatsAppOptIn  # noqa: PLC0415

    now = now or _now()
    pending = or_(Message.status.in_([MessageStatus.SCHEDULED, MessageStatus.SENDING,
                                      MessageStatus.AWAITING_REVIEW]),
                  send_holds.held_clause())
    cancelled = db.execute(
        update(Message).where(Message.lead_id == lead_id, pending)
        .values(status=MessageStatus.CANCELLED, error=LEAD_ERASED_ERROR)
        .execution_options(synchronize_session=False)).rowcount or 0
    messages = db.execute(
        update(Message).where(Message.lead_id == lead_id)
        # unsubscribe_token: since 2026-10-11 it carries the address
        # (encrypted). The copy in the sent email keeps working without it.
        .values(subject=None, body=None, personalization_json=None, unsubscribe_token=None)
        .execution_options(synchronize_session=False)).rowcount or 0
    db.execute(
        update(Message).where(Message.lead_id == lead_id,
                              or_(Message.error.is_(None), Message.error != LEAD_ERASED_ERROR))
        .values(error=None).execution_options(synchronize_session=False))

    calls = _table("calls")
    files = _voicemail_paths(db.execute(select(calls.c.voicemail_audio_url).where(
        _lead_rows("calls", lead_id), calls.c.voicemail_audio_url.isnot(None))).scalars())

    counts = {"messages": messages, "messages_cancelled": cancelled}
    for name, action in LEAD_SCRUB.items():
        table = _table(name)
        where = _lead_rows(name, lead_id)
        if action == DELETE:
            counts[name] = db.execute(delete(table).where(where)
                                      .execution_options(synchronize_session=False)).rowcount or 0
        else:
            counts[name] = db.execute(update(table).where(where).values(action)
                                      .execution_options(synchronize_session=False)).rowcount or 0
    # A plan to come back to the person later is cancelled, not just blanked.
    db.execute(update(ReengagementPlan)
               .where(ReengagementPlan.lead_id == lead_id,
                      ReengagementPlan.status.in_(["scheduled", "due"]))
               .values(status="cancelled", cancelled_reason="lead erased on request")
               .execution_options(synchronize_session=False))
    db.execute(update(WhatsAppOptIn)
               .where(WhatsAppOptIn.lead_id == lead_id, WhatsAppOptIn.revoked_at.is_(None))
               .values(status=OptInStatus.OPTED_OUT, revoked_at=now)
               .execution_options(synchronize_session=False))
    counts["files_removed"] = delete_files(files)
    # Kept for the counts above, under the old key name.
    counts["replies"] = counts.pop("inbound_replies")
    return counts


# --------------------------------------------------------------------------
# The send path's side of the race (app/workers/outreach_tasks.py)
# --------------------------------------------------------------------------


class SendClaimLost(Exception):
    """A claimed message is no longer SENDING when the task is about to
    transmit -- erased, or changed by someone else. The task must not send."""


def confirm_send_claim(db: Session, message_id: uuid.UUID) -> bool:
    """Re-assert the SENDING claim immediately before transmitting.

    The claim pattern of send_message_impl: a CONDITIONAL UPDATE the database
    arbitrates. It matches only while the row is still SENDING -- a lead or
    account erasure that ran after the claim has moved it to CANCELLED (or
    deleted it), and then this matches 0 rows and the send fails closed. On
    PostgreSQL the UPDATE also row-locks the message until the task commits
    after transmitting, so an erasure that starts now waits for the in-flight
    send and then clears what it rendered.
    """
    result = db.execute(update(Message)
                        .where(Message.id == message_id,
                               Message.status == MessageStatus.SENDING)
                        .values(status=MessageStatus.SENDING)
                        .execution_options(synchronize_session=False))
    return (result.rowcount or 0) == 1


def lead_is_erased(db: Session, lead_id: uuid.UUID | None) -> bool:
    """The lead row is gone (account erased) or is a GDPR tombstone."""
    from app.db.models import Lead  # noqa: PLC0415

    if lead_id is None:
        return True
    lead = db.get(Lead, lead_id)
    return lead is None or bool((lead.enrichment_json or {}).get("gdpr_deleted"))


def scrub_provenance(provenance: dict | None) -> dict | None:
    """Keep WHERE each field came from (source, confidence, observed_at) and
    drop WHAT it was: the value snapshot and the free-text detail."""
    if not provenance:
        return provenance
    out = {}
    for field, entry in provenance.items():
        if isinstance(entry, dict):
            out[field] = {**entry, "value": None, "detail": None}
        else:
            out[field] = None
    return out
