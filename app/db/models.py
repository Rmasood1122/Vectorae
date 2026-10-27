"""Victorae database models — Milestone 1 / Chunk 2.

Implements every table from project knowledge section G:
users, products, past_clients, strategies, research_steps, leads,
sequences, messages, outcomes, playbook_scores, suppression_list.

Design rules applied here:
- UUID primary keys (client-generated, portable across Postgres/SQLite
  so the test suite can run on SQLite with zero model changes).
- All enums are stored as VARCHAR (native_enum=False): adding a value
  later is a data-free migration, and SQLite tests behave identically.
- created_at/updated_at on every mutable table (TimestampMixin).
- research_steps has a UNIQUE (strategy_id, pipeline, step_no) —
  the schema itself guarantees "resume never duplicates a step".
- leads has UNIQUE (strategy_id, email) and (strategy_id, external_id)
  so M2's dedupe rule is enforced at the database level too.
- suppression_list rows must carry an email or a phone (CHECK).
"""

import enum
import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    false,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# --------------------------------------------------------------------------
# Enums (stored as VARCHAR — see module docstring)
# --------------------------------------------------------------------------


class PlanTier(str, enum.Enum):
    FREE = "free"
    STARTER = "starter"      # M8-C5 plan enforcement tier
    PRO = "pro"              # legacy: no longer sold, kept for existing rows
    ENTERPRISE = "enterprise"
    # Section E (migration 0040). VARCHAR-backed, so these are data-free
    # additions -- see the module docstring.
    GROWTH = "growth"
    SCALE = "scale"
    PAY_PER_MEETING = "pay_per_meeting"


class ProductType(str, enum.Enum):
    PRODUCT = "product"
    SKILL = "skill"


class FlowType(str, enum.Enum):
    WITH_CLIENTS = "with_clients"  # Flow 1: 72-step strategy pipeline
    NO_CLIENTS = "no_clients"      # Flow 2: 72 strategy + 72 GTM = 144 steps


class StrategyStatus(str, enum.Enum):
    PENDING = "pending"                      # created, pipeline not started
    RESEARCHING = "researching"              # pipeline running
    VERIFYING = "verifying"                  # 10x verification loop running
    VERIFIED = "verified"                    # all 10 passes green — may execute
    NEEDS_HUMAN_REVIEW = "needs_human_review"  # a pass hit its retry cap
    EXECUTING = "executing"                  # M2+: lead sourcing/outreach live
    FAILED = "failed"                        # unrecoverable pipeline error
    # F-AI-01: the run stopped because the next model call could have taken it
    # past its dollar budget. Everything already paid for is saved; raising the
    # budget and resuming continues from the same step.
    BUDGET_EXHAUSTED = "budget_exhausted"


class PipelineKind(str, enum.Enum):
    STRATEGY = "strategy"  # the core 72 (phases 1-8)
    GTM = "gtm"            # the second 72, Flow 2 only


class LeadStatus(str, enum.Enum):
    # M2 sourcing chain: sourced -> enriched -> email_found -> verified|flagged|dropped
    SOURCED = "sourced"
    ENRICHED = "enriched"
    EMAIL_FOUND = "email_found"
    VERIFIED = "verified"      # Hunter: deliverable
    FLAGGED = "flagged"        # Hunter: risky
    DROPPED = "dropped"        # Hunter: undeliverable / suppressed
    # M3+ outreach lifecycle:
    CONTACTED = "contacted"
    REPLIED = "replied"
    MEETING_BOOKED = "meeting_booked"
    # Feature 5 (migration 0037): the stage between a meeting and a decision.
    # VARCHAR-backed like every enum here, so adding it touches no column.
    PROPOSAL_SENT = "proposal_sent"
    # Feature Group 7: what happens AFTER the meeting, written by "Log Meeting
    # Outcome" (app/services/meeting_followup.py) and by the HubSpot/Salesforce
    # deal-stage webhooks. VARCHAR-backed, so a data-free addition -- no
    # migration touches this column.
    OPPORTUNITY = "opportunity"    # met; interested or needs a follow-up
    CLOSED_WON = "closed_won"
    CLOSED_LOST = "closed_lost"
    DISQUALIFIED = "disqualified"  # met; not a fit


class BatchStage(str, enum.Enum):
    """Lifecycle of an M2 lead-sourcing batch (resumable per stage)."""
    SOURCING = "sourcing"
    ENRICHING = "enriching"
    FINDING_EMAILS = "finding_emails"
    VERIFYING = "verifying"
    FINALIZED = "finalized"
    FAILED = "failed"


class ChannelType(str, enum.Enum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    LINKEDIN = "linkedin"
    # Feature Group 6: AI voice calls (Vapi / ElevenLabs). VARCHAR-backed, so
    # a data-free addition.
    PHONE = "phone"


class SequenceStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    # Feature Group 8: an SDR asked to launch it; nothing sends until a
    # manager approves. VARCHAR(32)-backed, so no column change.
    PENDING_APPROVAL = "pending_approval"


class MessageStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    SENDING = "sending"     # claimed by a send task (idempotency guard)
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BOUNCED = "bounced"
    # M4 Chunk 3:
    SKIPPED = "skipped"            # engine skipped (e.g. no WhatsApp opt-in);
                                   # the sequence CONTINUES to its next step
    NEEDS_TEMPLATE = "needs_template"  # free-form send failed closed (24h
                                   # window expired) or template no longer
                                   # approved — needs user attention"
    # Part 1 Feature 5: rendered, held for explicit human approval, NOT sent.
    # VARCHAR-backed like every enum here (models._enum), so adding it touches
    # no column. A message in this state is queued, never lost: approving it
    # returns it to SCHEDULED and the next dispatch tick sends it.
    AWAITING_REVIEW = "awaiting_review"


class EnrollmentStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"       # e.g. out-of-office; auto-resumes at paused_until
    STOPPED = "stopped"     # hard stop — can NEVER send again for this lead
    COMPLETED = "completed"  # all steps sent


class OutcomeEvent(str, enum.Enum):
    SENT = "sent"
    OPENED = "opened"
    REPLIED = "replied"
    BOOKED = "booked"
    WON = "won"
    LOST = "lost"
    BOUNCED = "bounced"
    UNSUBSCRIBED = "unsubscribed"
    # M4/M7/M8 additions (VARCHAR-backed enum — data-free change)
    OPTED_OUT = "opted_out"            # WhatsApp STOP
    NOTIFICATION_SENT = "notification_sent"  # M7 push audit
    CIRCUIT_OPENED = "circuit_opened"  # M8-C3 circuit breaker audit
    AB_PROMOTED = "ab_promoted"        # M8-C2 promotion idempotency marker
    # Feature A2: a tracked link in an outreach email was followed (first click
    # per message+URL). VARCHAR-backed, so a data-free addition.
    CLICKED = "clicked"


class WhatsAppTemplateStatus(str, enum.Enum):
    """Lifecycle of a Meta message template (M4). Only APPROVED templates
    may ever be sent to cold contacts — enforced by the adapter + Chunk 2
    management flows."""
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"


class WhatsAppTemplateCategory(str, enum.Enum):
    """Meta template categories relevant to outreach.
    # TODO: verify against current WhatsApp docs (exact category values;
    Meta also has AUTHENTICATION, which Victorae deliberately never uses)."""
    MARKETING = "marketing"
    UTILITY = "utility"


class WhatsAppStepKind(str, enum.Enum):
    """How a WhatsApp sequence step sends (M4 Chunk 3).
    TEMPLATE: Meta-approved template — the only kind allowed cold.
    TEXT: free-form — ONLY dispatches inside an open 24h customer-service
    window (reply-handling steps); fails closed to needs_template."""
    TEMPLATE = "template"
    TEXT = "text"


class OptInStatus(str, enum.Enum):
    OPTED_IN = "opted_in"
    OPTED_OUT = "opted_out"
    UNKNOWN = "unknown"


class OptInSource(str, enum.Enum):
    WEB_FORM = "web_form"              # hosted /optin/whatsapp/{token} page
    INBOUND_MESSAGE = "inbound_message"  # the prospect messaged US first
    MANUAL_IMPORT = "manual_import"    # CSV import — evidence REQUIRED
    API = "api"                        # recorded via the opt-in API


# --------------------------------------------------------------------------
# M9 — native CRM layer
# --------------------------------------------------------------------------


class CrmActivityKind(str, enum.Enum):
    """What a `crm_activities` row records.

    Deliberately NOT the same vocabulary as OutcomeEvent. `outcomes` is the
    learning loop's immutable event log (M8 aggregates it nightly and the A/B
    sweep reads it); this is a UI-facing audit trail of what a HUMAN or the
    pipeline did to a lead. Some events exist in both — a reply produces an
    OutcomeEvent.REPLIED for the learning loop AND a REPLY_RECEIVED row for
    the activity feed — because collapsing them would mean either polluting
    the learning loop with note-added noise or losing the feed's notes.
    """
    LEAD_CREATED = "lead_created"
    STATUS_CHANGED = "status_changed"
    NOTE_ADDED = "note_added"
    NOTE_DELETED = "note_deleted"
    TAG_ADDED = "tag_added"
    TAG_REMOVED = "tag_removed"
    FIELD_CHANGED = "field_changed"      # custom field or crm_lead_meta edit
    OWNER_CHANGED = "owner_changed"
    REPLY_RECEIVED = "reply_received"
    MEETING_BOOKED = "meeting_booked"
    # Engagement Hub. Both are VARCHAR-backed additions to an existing enum,
    # which is a data-free change (see the module docstring) -- no migration
    # touches this column.
    MEETING_COMPLETED = "meeting_completed"
    TASK_CREATED = "task_created"
    # Feature expansion. Same data-free VARCHAR addition.
    MEETING_PREP_READY = "meeting_prep_ready"
    MEETING_OUTCOME_LOGGED = "meeting_outcome_logged"
    FOLLOWUP_EMAIL_SENT = "followup_email_sent"
    DEAL_CREATED = "deal_created"


class CrmViewType(str, enum.Enum):
    DASHBOARD = "dashboard"
    GRID = "grid"


class CrmFieldType(str, enum.Enum):
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    BOOL = "bool"
    SELECT = "select"


# --------------------------------------------------------------------------
# Engagement Hub — calendar, bookings, meetings
# --------------------------------------------------------------------------


class BookingStatus(str, enum.Enum):
    """Lifecycle of one slot booked on a public booking page.

    CANCELLED is a soft delete: DELETE /calendar/bookings/{id} sets this and
    clears `slot_key`, which both frees the slot for someone else and keeps
    the row for the lead's timeline. A cancelled meeting that vanished from
    history would take its CRM activity with it.
    """
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class MeetingPlatform(str, enum.Enum):
    """Where the call actually happens.

    CUSTOM is not a fallback for "we could not integrate" -- it is the
    platform-agnostic path the product promises: paste any join URL (Whereby,
    a phone bridge, the client's own Webex) and Victorae still runs the
    notes, transcript and summary around it.
    """
    GOOGLE_MEET = "google_meet"
    ZOOM = "zoom"
    TEAMS = "teams"
    CUSTOM = "custom"


class MeetingStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class MeetingParticipantRole(str, enum.Enum):
    HOST = "host"
    CLIENT = "client"
    OBSERVER = "observer"


def _enum(e: type[enum.Enum]) -> Enum:
    """VARCHAR-backed enum column storing the enum *values*."""
    return Enum(
        e,
        native_enum=False,
        length=32,
        values_callable=lambda x: [i.value for i in x],
    )


# --------------------------------------------------------------------------
# Mixins
# --------------------------------------------------------------------------


class PageType(str, enum.Enum):
    """What kind of marketing page this is -- it decides the schema.org
    markup the generator emits, so it is CHECK-constrained in the database
    rather than left as a free VARCHAR like the enums above."""
    LANDING = "landing"
    BLOG = "blog"
    CASE_STUDY = "case_study"
    COMPARISON = "comparison"
    LOCATION = "location"


class PageStatus(str, enum.Enum):
    """draft -> published -> archived. `published` is the one that decides
    whether a page is written into the public static export, which is why it
    is CHECK-constrained too."""
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class AssetType(str, enum.Enum):
    IMAGE = "image"
    CSS = "css"
    JS = "js"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    # M5: JWT auth — PBKDF2 hash, nullable so pre-auth rows keep working
    # until their owner sets a password via signup.
    password_hash: Mapped[str | None] = mapped_column(String(300), nullable=True)
    plan: Mapped[PlanTier] = mapped_column(_enum(PlanTier), default=PlanTier.FREE)
    # Migration 0069: the name the user chose to be shown in the app. Display
    # only; never used for sending, identity checks or access decisions.
    full_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    theme_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # M8-C3 (migration 0009_m8c3): admin + suspension controls
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_suspended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    suspended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    suspended_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # M8-C5 (migration 0011_m8c5): 7-step onboarding state
    onboarding_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Feature 1 (migration 0015_email_verification): signup email verification.
    #
    # server_default="0" (not just `default=False`) is deliberate. `default` is
    # applied by Python on INSERT; rows written by anything that is not this
    # ORM — a migration backfill, a psql session, a future bulk import — would
    # get NULL, and `not None` is falsy, so those users would be locked out by
    # the get_current_user gate. The server default makes the database itself
    # answer the question.
    #
    # Pre-existing rows are backfilled to TRUE by migration 0015: they signed
    # up before this feature existed and must not be locked out of an account
    # they already own. Only signups from 0015 onward start FALSE.
    email_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Feature Group 2 (migration 0025): voice-matched messaging. The samples
    # are kept so the user can edit and re-run them; only the extracted
    # PROFILE ever reaches an outreach prompt (see style_profile.py).
    style_profile_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    style_samples_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    style_profile_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # --- Identity, location and phone verification (migration 0039) --------
    # identity_required is TRUE only for accounts created after 0039 (signup
    # sets it). Every pre-existing row keeps the server default FALSE, so the
    # new onboarding gate and the phone gate never lock out an account that
    # signed up before either existed -- the same lesson as 0015's backfill.
    identity_required: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    # ISO 3166-1 alpha-2, upper case.
    personal_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # individual | company
    account_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Where the company is registered/operating. May differ from where the
    # person sits -- a Karachi founder of a Delaware C-corp is ordinary.
    company_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    identity_submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    signup_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # What IP geolocation said when the identity was submitted.
    geo_detected_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # match | mismatch | unknown -- unknown is "we could not tell", never "ok".
    geo_check_status: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # NULL (nothing to review) | pending | cleared | confirmed_risk
    geo_review_status: Mapped[str | None] = mapped_column(
        String(20), nullable=True, index=True
    )
    geo_reviewed_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    geo_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    geo_review_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # E.164. Only written once the code has been proved.
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    phone_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    phone_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Migration 0081: when the password was last changed or reset. Every
    # refresh token (and access token) issued before it is refused
    # (auth_sessions._issued_before_password_change), legacy session-less
    # tokens included -- "signed out on every device" is enforced, not hoped.
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    products: Mapped[list["Product"]] = relationship(back_populates="user")
    email_verification_tokens: Mapped[list["EmailVerificationToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    password_reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    tutorial_progress: Mapped[list["TutorialProgress"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    chat_sessions: Mapped[list["ChatSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    support_tickets: Mapped[list["SupportTicket"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ChatSession(TimestampMixin, Base):
    """One support-chat conversation (Feature 3).

    Sessions exist so a follow-up like "what about the second one?" has
    something to resolve against, and so "start a new chat" can mean something
    other than deleting history.

    RETENTION: purged after SUPPORT_CHAT_RETENTION_DAYS by
    app/workers/support_tasks.py, keyed on last_message_at rather than
    created_at -- an old session someone is still using is not stale, and
    deleting a live conversation out from under a user is worse than keeping it
    a few days longer.
    """

    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="chat_sessions")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan",
        order_by="ChatMessage.seq",
    )
    # NO delete cascade -- deleting a session must NULL the tickets that
    # reference it, never delete them. The database says the same thing via
    # ON DELETE SET NULL, but SQLite only enforces FK actions with
    # PRAGMA foreign_keys=ON (off in the unit-test harness), so without this
    # relationship the ORM left a dangling session id behind on SQLite and
    # the two databases disagreed. Declaring it here makes the ORM null the
    # column itself, identically everywhere.
    tickets: Mapped[list["SupportTicket"]] = relationship(
        back_populates="chat_session"
    )


class ChatMessage(Base):
    """One turn in a support chat.

    `role` is 'user' or 'assistant'. Both are stored: an assistant reply that
    cannot be read back is a reply nobody can audit, and the whole point of
    grounding the bot is being able to answer "why did it say that?".

    The diagnostic columns exist for exactly that question:
      reason      why the user got this text (answered / off_topic /
                  low_confidence / model_error / malformed_response ...)
      confidence  what the model claimed about itself
      faq_ids     which knowledge-base entries were actually cited, filtered
                  to ids that really exist so a hallucinated citation is never
                  stored as a real one

    NO TimestampMixin: a chat message is immutable once written, so an
    updated_at column would only ever record that something went wrong.
    """

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True,
        nullable=False,
    )
    # Position within the conversation, 0-based.
    #
    # Ordering by created_at ALONE is not safe: both turns of one exchange are
    # written inside a single request and can land on the same timestamp, at
    # which point the tiebreak was a random UUID primary key -- so a transcript
    # could come back with the answer before the question. It failed
    # intermittently, which is exactly how an ordering bug reaches production.
    #
    # Message order within a conversation is a real domain concept, so it gets
    # a real column rather than depending on clock resolution.
    seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0",
                                     nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Assistant messages only; NULL on user turns.
    reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    faq_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    suggest_ticket: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True,
        nullable=False,
    )

    session: Mapped["ChatSession"] = relationship(back_populates="messages")


class SupportTicket(TimestampMixin, Base):
    """A question the AI could not answer, escalated to a human.

    Stored in the database and NOT emailed. Email delivery is deferred with the
    rest of Feature 1's transport work -- and a ticket that is only emailed is
    a ticket that is lost when the relay is down, so storing it is the durable
    half regardless of whether mail is ever added on top.

    `chat_session_id` is nullable and ON DELETE SET NULL: a ticket must outlive
    the 30-day chat purge. Losing the conversation that produced a ticket is
    acceptable; losing the ticket is not.
    """

    __tablename__ = "support_tickets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    chat_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True
    )
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="open", server_default="open", index=True,
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="support_tickets")
    chat_session: Mapped["ChatSession | None"] = relationship(
        back_populates="tickets"
    )


class TutorialCatalogue(TimestampMixin, Base):
    """One tutorial video (Task 3, migration 0018).

    MOVED HERE FROM CODE. Feature 2 kept the catalogue in
    app/services/tutorials.py so that swapping a placeholder id was a reviewed
    one-line diff. That decision was reversed deliberately: adding a video now
    happens in the admin UI, with no deploy. app/services/tutorials.py keeps
    the same nine entries as SEED_CATALOGUE -- migration 0018 inserts them and
    nothing reads them at runtime.

    `slug` is still the permanent identifier. tutorial_progress rows point at
    it by string, so RENAMING A SLUG STILL ORPHANS every user's progress for
    that video -- moving the catalogue into a table did not change that, and
    deliberately did NOT add a foreign key: an FK added retroactively fails on
    any progress row whose tutorial was deleted, and progress outliving a
    deleted tutorial is the behaviour we want (undelete restores it).

    is_published defaults FALSE. A tutorial with no youtube_id would otherwise
    appear to users as a permanent "coming soon" card, which is worse than not
    appearing at all -- the Learn tab looks broken rather than short.
    """

    __tablename__ = "tutorial_catalogue"

    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True,
                                      nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    # NULL until a real video exists. Not an empty string and not a fake id:
    # a fake id renders YouTube's own "Video unavailable" error, which looks
    # exactly like a bug in this app.
    youtube_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0,
                                            server_default="0", nullable=False)
    is_published: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", index=True, nullable=False
    )


class TutorialProgress(TimestampMixin, Base):
    """One user's progress through one tutorial video (Feature 2).

    The video CATALOGUE is not in the database -- it lives in
    app/services/tutorials.py, and this table references it by `tutorial_slug`.
    See that module's docstring for why. The practical consequence is that a
    slug is a permanent identifier: RENAMING ONE ORPHANS every progress row
    pointing at it. Titles can change freely; slugs cannot.

    There is deliberately NO foreign key to a tutorials table, because there is
    no such table. The API validates the slug against the catalogue before
    writing, so an unknown slug is a 404 rather than a silently-stored orphan.

    UNIQUE (user_id, tutorial_slug): one row per user per video. Without it a
    client that fires two progress updates in quick succession -- which a video
    player does constantly -- creates duplicate rows, and "have I finished
    this?" stops having a single answer.

    WHY percent AND position_seconds BOTH EXIST, AND WHY THEY UPDATE
    DIFFERENTLY:
      position_seconds is "where do I resume", so it takes the LATEST value --
        a user who scrubs back to the start expects to resume at the start.
      percent is "how much of this have I seen", so it takes the MAXIMUM --
        scrubbing back must not erase what has already been watched, or a
        rewatch would undo a completion.
    Storing only one of them cannot express both behaviours.
    """

    __tablename__ = "tutorial_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "tutorial_slug",
                         name="uq_tutorial_progress_user_id_tutorial_slug"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    tutorial_slug: Mapped[str] = mapped_column(String(100), index=True,
                                               nullable=False)
    # Resume point, in seconds. Latest value wins.
    position_seconds: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    # Reported by the player; nullable because the catalogue may not know it
    # and the player has not loaded yet on the first update.
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Furthest point reached, 0-100. Monotonic; only a reset lowers it.
    percent: Mapped[float] = mapped_column(
        Float, default=0.0, server_default="0", nullable=False
    )
    completed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_watched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="tutorial_progress")


class EmailVerificationToken(Base):
    """One outstanding signup-verification link.

    THE RAW TOKEN IS NEVER STORED. Only sha256(token) is, in `token_hash`.
    A verification link is a bearer credential — anyone holding it can mark an
    account verified — so a leaked database dump must not hand an attacker a
    working link for every pending signup. The raw value exists exactly twice:
    in the email, and in the query string of the click. Lookup is by hash.

    Single use: `used_at` is stamped on the first successful click, and the
    verify endpoint refuses a token that already has one. Without that, a
    forwarded email or a mail-scanner prefetch keeps re-verifying an account
    the user may since have had suspended.

    Rows are kept after use rather than deleted, so "this link was already
    used" is distinguishable from "this link never existed" — the difference
    between a helpful message and a confusing one.
    """

    __tablename__ = "email_verification_tokens"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # sha256 hex digest of the raw token — 64 chars, unique so a hash
    # collision or a duplicated insert surfaces as an error, not a silent
    # second live link for the same value.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True,
                                            nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="email_verification_tokens")


class PasswordResetToken(Base):
    """One emailed "reset your password" link (migration 0081).

    Same storage rule as EmailVerificationToken, for a stronger reason: a reset
    link sets a new password, so it is a full account-takeover credential while
    it lives. Only sha256(token) is stored; the raw value exists in the email
    and in the confirm request, nowhere else.

    Single use and short-lived: `used_at` is stamped on a successful reset AND
    on every outstanding link when a new one is issued or a reset succeeds, so
    at most one link per account can ever work, and only until `expires_at`
    (PASSWORD_RESET_TTL_MINUTES, default 60). See app/services/password_reset.py.
    """

    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True,
                                            nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Where the request came from, for the account owner's security review.
    requested_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="password_reset_tokens")


class Product(TimestampMixin, Base):
    """A product OR a skill the user wants clients for."""

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    type: Mapped[ProductType] = mapped_column(_enum(ProductType))
    # --- Local business mode (migration 0088) -----------------------------
    # restaurant | healthcare | retail | local_service | other -- see
    # app/services/local_business.py::BUSINESS_TYPES. NULL = not a local
    # business, which is every product created before the feature: their
    # prompts, sourcing and sends are exactly what they were.
    business_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Set the first time the product is `healthcare` and NEVER cleared: a
    # product that was a clinic keeps customer win-back refused forever, even
    # if its business type is changed later (its customer list would still be
    # a patient list). See local_business.change_business_type.
    healthcare_locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="products")
    past_clients: Mapped[list["PastClient"]] = relationship(back_populates="product")
    strategies: Mapped[list["Strategy"]] = relationship(back_populates="product")


class PastClient(TimestampMixin, Base):
    """Flow 1 input: one past client + how they were acquired."""

    __tablename__ = "past_clients"

    id: Mapped[uuid.UUID] = _uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    details: Mapped[str] = mapped_column(Text)
    acquisition_story: Mapped[str] = mapped_column(Text)
    # Filled by the Chunk 3 pattern-recognition service (Claude):
    # industry, company_size, buyer_role, deal_size, channel,
    # trigger_event, sales_cycle_length.
    extracted_patterns_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    product: Mapped["Product"] = relationship(back_populates="past_clients")


class VoiceProfile(Base):
    """Feature 3 (migration 0035) — the founder's own writing voice.

    Extracted from up to 20 of their LinkedIn posts and applied to every
    outgoing message for this product, so outreach reads like the person
    whose name is on it rather than like a template.

    ONE PER PRODUCT, enforced by the schema. A product with two profiles has
    no defined answer to "which voice does this email go out in?", so
    re-analysing updates this row rather than inserting another.

    NOT a TimestampMixin table: `last_analyzed_at` is the only "when" that
    means anything here (it is what the UI shows and what a staleness check
    would read), and an `updated_at` that also moves when nothing was
    re-analysed would be a second, contradictory answer to the same question.

    `raw_posts_json` is kept for auditability and re-extraction. It NEVER
    reaches an outreach prompt -- only the extracted dimensions do, exactly
    as in app/services/style_profile.py -- so a phrase from the founder's own
    post cannot be pasted verbatim into a stranger's inbox.
    """

    __tablename__ = "voice_profiles"

    id: Mapped[uuid.UUID] = _uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), unique=True
    )
    raw_posts_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # {avg_sentence_length, punctuation_style, opens_with, uses_numbers,
    #  emoji_usage, paragraph_length, vocabulary_level} -- see
    #  app/services/voice_profiler.py::DIMENSIONS for the allowed values.
    style_dimensions_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Up to five phrases the founder uses repeatedly.
    sample_phrases: Mapped[list | None] = mapped_column(JSON, nullable=True)
    last_analyzed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    post_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    product: Mapped["Product"] = relationship()


class Strategy(TimestampMixin, Base):
    __tablename__ = "strategies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    flow_type: Mapped[FlowType] = mapped_column(_enum(FlowType))
    status: Mapped[StrategyStatus] = mapped_column(
        _enum(StrategyStatus), default=StrategyStatus.PENDING, index=True
    )
    # Chunk 5 verification loop appends one entry per pass attempt:
    # {pass_no, name, attempt, result: PASS|FAIL, fix_description?, ts}
    verified_passes_json: Mapped[list] = mapped_column(JSON, default=list)
    # Assembled final strategy document (written when pipeline completes).
    strategy_document: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Flow 2 only: assembled GTM document.
    gtm_document: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # F-AI-01 (migration 0066): the most this strategy's pipeline + verification
    # may spend on model calls, in millionths of a US dollar (the unit
    # api_usage.cost_micros uses). NULL = no limit, which only legacy rows have;
    # every new strategy is given one by the API.
    budget_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # F-AI-01b: how many of each phase's 9 steps this run executes (2..9). The
    # phase synthesis (step 9) is always among them. NULL = all nine, which is
    # what every strategy created before this column did.
    steps_per_phase: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Outreach campaign state (M3): "active" | "paused_bounce_rate" | "paused_manual"
    campaign_state: Mapped[str] = mapped_column(String(30), default="active")
    campaign_pause_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # M8-C1 (migration 0008_m8c2): SHA-256 of sorted canonical ICP/tactic JSON —
    # links a strategy to its playbook_scores rows (O(1) upsert key).
    pattern_key: Mapped[str | None] = mapped_column(
        String(200), nullable=True, index=True
    )
    # 0014: the exact canonical dict that was hashed into pattern_key. Stored
    # so the bucket is auditable and so a recompute/backfill can rehash offline
    # instead of re-asking the model for the ICP and tactic profile (which is
    # not deterministic across runs, and would silently re-bucket a strategy
    # that never changed). See icp_extraction.canonical_pattern_payload.
    pattern_inputs_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # --- Part 1 Feature 11 (migration 0063) -------------------------------
    # WHICH CLIENT this campaign is run for, when the account is an agency.
    # NULL = the agency's own work, which is what every existing strategy is
    # -- not a client that does not exist. An account that never creates a
    # client workspace behaves exactly as it does today.
    client_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("client_workspaces.id", ondelete="SET NULL"),
        nullable=True, index=True
    )
    # M8-C2: winning variant set by auto-promotion. Affects FUTURE message
    # rendering only — in-flight rows (sent_at IS NOT NULL) are never touched.
    default_variant: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # M8-C4 (migration 0010_m8c4): Pearson correlation between message
    # personalization score and reply outcome, computed nightly.
    personalization_correlation: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    # --- Feature Group 1 (migration 0024) --------------------------------
    # Live market signals (news + Apollo company data for the ICP) fetched
    # once, BEFORE Phase 1, and injected into the Phase 1 and 3 prompts. The
    # exact payload the prompts saw is kept so a strategy stays explainable
    # after the news has moved on. See app/services/market_intel.py.
    market_signals_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    market_signals_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # NULL = consensus never ran; "complete" / "partial" (the second model
    # failed on some sections). See app/services/consensus.py.
    consensus_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # The idle-campaign mutation sweep's once-per-window guard.
    last_mutation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # --- Feature Group 3 (migration 0028): smart send time ----------------
    # The user's toggle, and the top engagement windows it schedules into:
    # {"windows": [{dow, hour, score, share, opens, replies}], "opens": n,
    #  "replies": n}. Computed by app/services/send_windows.py once the
    # campaign has `send_time_min_opens` opens; NULL until then.
    smart_send_time: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    send_windows_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    send_windows_computed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # --- Autopilot launch (migration 0087): best time to send -------------
    # The send-time plan made when the pipeline completes (per-channel local
    # weekday/hour windows + reasons, from the ICP priors, refined by one
    # metered model call) and the person's own override window. See
    # app/services/send_time.py. NULL = no plan yet / no override.
    send_time_plan_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    send_time_override_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # --- Feature 1 (migration 0033): pipeline health score ----------------
    # One 0-100 number the founder can read instead of four dashboards, plus
    # the band and the sentence the UI shows. Recomputed every six hours by
    # app/workers/health_tasks.py from live outcome/enrollment/lead data --
    # these columns are a CACHE, not the source of truth, so
    # GET /strategies/{id}/health recomputes when they are stale or NULL.
    # NULL = never scored (a campaign created before the sweep last ran),
    # which reads differently from a genuine score of 0.
    pipeline_health_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    health_band: Mapped[str | None] = mapped_column(String(20), nullable=True)
    health_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    health_message: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # --- Feature 5 (migration 0048): opt-in post-sequence re-engagement ----
    # OFF by default and per campaign. Its caps are separate from the channel
    # daily cap -- a re-engagement send counts against BOTH -- and are clamped
    # to the admin ceilings in system_settings. See app/services/reengagement.py.
    reengagement_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    reengagement_delay_days: Mapped[int] = mapped_column(
        Integer, default=30, server_default=text("30"), nullable=False
    )
    reengagement_daily_cap: Mapped[int] = mapped_column(
        Integer, default=10, server_default=text("10"), nullable=False
    )
    reengagement_weekly_cap: Mapped[int] = mapped_column(
        Integer, default=25, server_default=text("25"), nullable=False
    )
    # --- Local business mode (migration 0088) -----------------------------
    # {city, postal_code, country, radius, unit, timezone} -- validated by
    # app/services/local_business.py::ServiceArea. NULL = no service area.
    # The radius is used in prompts and recorded on lead batches; it is NOT
    # sent to the lead provider (see docs/features/local-business-mode.md).
    service_area_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # What the business offers business buyers, in the owner's words.
    local_offer: Mapped[str | None] = mapped_column(String(500), nullable=True)

    product: Mapped["Product"] = relationship(back_populates="strategies")

    @property
    def business_type(self) -> str | None:
        """The product's local business type, read through the relationship.

        One source of truth (products.business_type); exposed here so a
        scheduler or a prompt that holds a strategy can read
        `strategy.business_type` without a second query."""
        product = self.product
        return product.business_type if product is not None else None
    research_steps: Mapped[list["ResearchStep"]] = relationship(
        back_populates="strategy"
    )
    leads: Mapped[list["Lead"]] = relationship(back_populates="strategy")
    sequences: Mapped[list["Sequence"]] = relationship(back_populates="strategy")


class ResearchStep(TimestampMixin, Base):
    """One persisted pipeline step output.

    The UNIQUE constraint below is the resumability guarantee from the
    project knowledge: a crashed run resumes from the last saved step and
    can never insert the same step twice.
    """

    __tablename__ = "research_steps"
    __table_args__ = (
        UniqueConstraint("strategy_id", "pipeline", "step_no", name="strategy_pipeline_step"),
        Index("ix_research_steps_strategy_phase", "strategy_id", "pipeline", "phase"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    pipeline: Mapped[PipelineKind] = mapped_column(
        _enum(PipelineKind), default=PipelineKind.STRATEGY
    )
    phase: Mapped[int] = mapped_column(Integer)      # 1..8
    step_no: Mapped[int] = mapped_column(Integer)    # 1..72 within the pipeline
    step_id: Mapped[str] = mapped_column(String(64))  # registry id, e.g. "s1.03"
    name: Mapped[str] = mapped_column(String(200))
    inputs_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(64))

    strategy: Mapped["Strategy"] = relationship(back_populates="research_steps")


class LeadBatch(TimestampMixin, Base):
    """One sourcing run for a strategy: source -> enrich -> find emails ->
    verify -> finalize. Stage + per-lead statuses make the whole chain
    resumable, consistent with the M1 engine's resumability rule."""

    __tablename__ = "lead_batches"

    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[BatchStage] = mapped_column(
        _enum(BatchStage), default=BatchStage.SOURCING, index=True
    )
    requested_leads: Mapped[int] = mapped_column(Integer)
    icp_criteria_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_provider: Mapped[str] = mapped_column(String(50), default="apollo")
    verifier_provider: Mapped[str] = mapped_column(String(50), default="hunter")
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    leads: Mapped[list["Lead"]] = relationship(back_populates="batch")


class Lead(TimestampMixin, Base):
    __tablename__ = "leads"
    __table_args__ = (
        # M2 dedupe rule, enforced by the schema: same email or same
        # provider person-id can only exist once per strategy.
        UniqueConstraint("strategy_id", "email", name="strategy_email"),
        UniqueConstraint("strategy_id", "source", "external_id", name="strategy_source_external"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lead_batches.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(50))  # "apollo", "manual", ...
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    enrichment_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[LeadStatus] = mapped_column(
        _enum(LeadStatus), default=LeadStatus.SOURCED, index=True
    )
    # --- WhatsApp channel (M4) -------------------------------------------
    # Opt-in is a compliance fact: recorded with WHEN and HOW (source), and
    # required by the adapter before ANY outbound WhatsApp message. Full
    # opt-in collection flows land in Chunk 2; the fields exist from Chunk 1
    # so the send-time guard is structural, never retrofitted.
    whatsapp_opted_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    whatsapp_opt_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    whatsapp_opt_in_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Timestamp of the last INBOUND WhatsApp message from this lead —
    # persisted by the webhook since Chunk 1; Chunk 3's 24h customer-service
    # window check reads it.
    whatsapp_last_inbound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # --- Feature Group 1: predictive lead scoring (migration 0024) -------
    # 0-100. NULL = never scored (leads sourced before the feature, or
    # scoring disabled), which sorts LAST rather than reading as zero.
    # Indexed because the leads list sorts by it by default.
    ai_booking_likelihood: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    ai_score_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # The factor values the score was built from (seniority, industry match,
    # verification, company signals, playbook history, heuristic baseline) --
    # so a score can always be explained, and re-derived without the model.
    ai_score_factors: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ai_scored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # --- Feature Group 2: hyper-personalization (migration 0025) ----------
    # Captured from Apollo at sourcing (person.linkedin_url); editable by hand.
    # Also the address Feature Group 5's LinkedIn channel sends to.
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # The lead's three most recent posts [{text, posted_at, url}] and when they
    # were fetched (cached 7 days). [] means "fetched, and they post nothing";
    # NULL means "never fetched" -- two different facts.
    linkedin_posts_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    linkedin_posts_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Last-30-day articles naming the company [{headline, summary, url,
    # source, published_at}] (cached 3 days).
    company_news_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    company_news_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # {status: suggested|recorded|skipped, title, script, on_screen,
    #  share_url, embed_id, suggested_at, recorded_at} -- see loom_video.py.
    loom_video_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # --- Feature Group 5: LinkedIn channel state (migration 0026) ---------
    # Unipile's id for the person; indexed because every inbound LinkedIn
    # event is matched back to a lead by it.
    linkedin_provider_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True, index=True
    )
    # The LEAD's Premium flag (Open Profile) -- decides InMail vs connection
    # request for step 1. NULL = not looked up yet.
    linkedin_is_premium: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # NULL (no request) | pending | connected
    linkedin_connection_status: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    linkedin_invited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    linkedin_connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The ONE connected account that owns this relationship; every later
    # touch goes from it (see app/services/linkedin_limits.py).
    linkedin_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("linkedin_accounts.id", ondelete="SET NULL"), nullable=True
    )
    linkedin_chat_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # --- Feature Group 6: AI phone calls (migration 0027) -----------------
    # PRIOR EXPRESS CONSENT to be called by an AI voice. Since the FCC's
    # February 2024 ruling, AI-generated voices are "artificial voice" under
    # the US TCPA, so an AI cold call (or voicemail drop) to a US number needs
    # it; the phone channel refuses to call without it unless an admin has
    # turned the requirement off (see phone_calls.consent_ok).
    phone_consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    phone_consent_source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Denormalized from the latest `calls` row so lists can show it cheaply.
    last_call_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_call_outcome: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # --- Feature 5 (migration 0037): what this lead is worth ---------------
    # Numeric, never Float: a pipeline summed in binary floating point
    # disagrees with itself by cents at scale. NULL means nobody has put a
    # number on this lead, which is not the same as a deal worth nothing --
    # the ROI sums skip NULL rather than reading it as zero.
    estimated_deal_value: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    # --- Feature A5 (migration 0045): live conversion probability ---------
    # 0..1, recomputed at send time and by the hourly sweep
    # (app/services/conversion_probability.py). NULL = never estimated.
    conversion_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    conversion_probability_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # active | cooling (sequence paused) | archived (sequence stopped) | won.
    # NULL behaves as active.
    engagement_state: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    # bounced | unsubscribed | not_interested | probability_below_archive
    kill_signal: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # The factors the estimate was built from, so it can always be explained.
    conversion_factors_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # --- Part 1 Feature 2 (migration 0054): F-P-T-A -----------------------
    # Four 0-100 sub-scores and a weighted overall, each with its own reason
    # and the evidence behind it. Deliberately NOT folded into
    # ai_booking_likelihood: a lead can be a perfect fit with no timing, or
    # desperate with no way to reach them, and one number erases that.
    # NULL = never scored, which sorts last rather than reading as zero.
    fpta_fit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fpta_problem: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fpta_timing: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fpta_access: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fpta_overall: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # {dimension: {score, reason, signals: [...], baseline}} -- so a score can
    # always be explained and re-derived without the model.
    fpta_reasons_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fpta_method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    fpta_scored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # --- Part 1 Feature 10 (migration 0062): where each field came from ----
    # {field: {source, confidence, observed_at, detail, value}}.
    #
    # A JSON column rather than the EAV table this schema uses for CUSTOM
    # FIELDS, and for the opposite reason: custom field values are sorted and
    # filtered server-side across thousands of rows, where JSON extraction
    # differs between SQLite and PostgreSQL. Provenance is never sorted,
    # filtered or aggregated -- it is read once, for one prospect, when a
    # person hovers a field and asks where it came from. One column read with
    # the lead it describes is the cheapest possible answer.
    #
    # NULL = no provenance recorded, which is NOT the same as "unknown
    # source": the service reports the difference rather than inventing a
    # source for history.
    provenance_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    provenance_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    strategy: Mapped["Strategy"] = relationship(back_populates="leads")
    batch: Mapped["LeadBatch | None"] = relationship(back_populates="leads")
    messages: Mapped[list["Message"]] = relationship(back_populates="lead")
    outcomes: Mapped[list["Outcome"]] = relationship(back_populates="lead")


class DisplacementAlert(Base):
    """Feature 4 (migration 0036) — a lead just complained in public.

    Written by the twelve-hourly sweep when a lead's recent LinkedIn post
    names a competitor tool or describes the pain this product removes. It
    carries the evidence (the matched keywords and the post excerpt) and a DM
    that references that specific post, so acting on it is one read and one
    click rather than a research task.

    No updated_at: the only mutation an alert ever receives is a status
    change, and `acted_at` records that precisely. A generic updated_at would
    be a second, vaguer answer to the same question.

    LIFECYCLE. pending -> acted | dismissed. A pending alert past `expires_at`
    is stale and is filtered out of the pending list rather than deleted --
    the record of "we saw this and did nothing" is worth keeping.
    """

    __tablename__ = "displacement_alerts"
    __table_args__ = (
        # Both hot paths are "the most recent alerts for this lead": the
        # 14-day dedup check on every sweep, and the alerts list.
        Index("ix_displacement_alerts_lead_created", "lead_id",
              text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    # The exact keywords/phrases that fired, so an alert is always explainable
    # and a noisy trigger group can be identified from the data.
    matched_keywords: Mapped[list | None] = mapped_column(JSON, nullable=True)
    post_excerpt: Mapped[str] = mapped_column(Text)
    post_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # NULL when the DM could not be generated -- the alert is still worth
    # showing, because the evidence is the valuable half.
    suggested_dm: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pending | acted | dismissed
    alert_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False
    )
    acted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    lead: Mapped["Lead"] = relationship()


class ROISnapshot(Base):
    """Feature 5 (migration 0037) — one campaign's results on one day.

    Written by the 01:00 UTC sweep (app/workers/roi_tasks.py) so the ROI
    dashboard and the shareable proof card can render a trend without
    recomputing six aggregates per day per campaign on every page load.

    UNIQUE (strategy_id, snapshot_date) is what makes the daily sweep
    idempotent: a retried or double-scheduled run UPDATES the day's row
    instead of appending a second one, so a day can never be counted twice.

    Money columns are Numeric, never Float -- see Lead.estimated_deal_value.

    No updated_at: a snapshot is a statement about one day, and the only
    write it ever receives is that day's recomputation.
    """

    __tablename__ = "roi_snapshots"
    __table_args__ = (
        UniqueConstraint("strategy_id", "snapshot_date", name="roi_strategy_day"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    snapshot_date: Mapped[date] = mapped_column(Date)
    meetings_booked: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    pipeline_value: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=0, server_default=text("0"), nullable=False
    )
    messages_sent: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    reply_rate: Mapped[float] = mapped_column(
        Float, default=0.0, server_default=text("0"), nullable=False
    )
    time_saved_hours: Mapped[float] = mapped_column(
        Float, default=0.0, server_default=text("0"), nullable=False
    )
    revenue_attributed: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=0, server_default=text("0"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    strategy: Mapped["Strategy"] = relationship()


class Sequence(TimestampMixin, Base):
    """An outreach sequence (a named cadence of messages on one channel)."""

    __tablename__ = "sequences"

    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[ChannelType] = mapped_column(_enum(ChannelType))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[SequenceStatus] = mapped_column(
        _enum(SequenceStatus), default=SequenceStatus.DRAFT
    )
    booking_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # --- Feature Group 8 (migration 0030): the manager approval workflow --
    # `approval_payload_json` is the launch request the SDR made (which lead
    # statuses to enroll), replayed verbatim when a manager approves.
    approval_requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approval_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # --- Local business mode (migration 0088) -----------------------------
    # prospects (B2B leads, every sequence before 0088) | customers (the
    # business's own opted-in customers, win-back). Enrollment never mixes
    # the two and the send path cancels a message whose lead is of the other
    # audience -- see app/services/local_business.py::customer_send_block.
    audience: Mapped[str] = mapped_column(
        String(20), default="prospects", server_default=text("'prospects'"),
        nullable=False
    )

    strategy: Mapped["Strategy"] = relationship(back_populates="sequences")
    steps: Mapped[list["SequenceStep"]] = relationship(
        back_populates="sequence", order_by="SequenceStep.step_no"
    )
    enrollments: Mapped[list["SequenceEnrollment"]] = relationship(back_populates="sequence")
    messages: Mapped[list["Message"]] = relationship(back_populates="sequence")


class SequenceStep(TimestampMixin, Base):
    """One ordered step of a sequence. `template` is the messaging brief
    Claude personalizes per lead (not the final copy). `delay_days` is the
    wait after the PREVIOUS step's send."""

    __tablename__ = "sequence_steps"
    __table_args__ = (
        UniqueConstraint("sequence_id", "step_no", name="sequence_step"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    sequence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sequences.id", ondelete="CASCADE"), index=True
    )
    step_no: Mapped[int] = mapped_column(Integer)          # 1 = initial email
    template: Mapped[str] = mapped_column(Text)
    variant: Mapped[str] = mapped_column(String(20), default="A")
    delay_days: Mapped[int] = mapped_column(Integer, default=3)
    # --- M4 Chunk 3: multi-channel sequences -----------------------------
    # NULL channel = "use the sequence's channel" — every M3 sequence keeps
    # working unchanged. A non-null value overrides per step (e.g. step 1
    # email, step 3 WhatsApp template follow-up).
    channel: Mapped[ChannelType | None] = mapped_column(
        _enum(ChannelType), nullable=True
    )
    # WhatsApp-only fields (NULL for email steps):
    whatsapp_kind: Mapped[WhatsAppStepKind | None] = mapped_column(
        _enum(WhatsAppStepKind), nullable=True
    )
    whatsapp_template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("whatsapp_templates.id", ondelete="SET NULL"), nullable=True
    )
    # {"1": "lead.full_name", "2": "brief: their biggest onboarding pain"}
    # — 'lead.<field>' fills deterministically; anything else is a brief
    # Claude fills from lead enrichment + strategy messaging.
    variable_mapping_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # --- Engagement Hub: automated follow-up (migration 0020) -------------
    # `delay_days` schedules the NEXT step after this one SENDS. These two
    # govern something different: what happens when the lead does not reply
    # to this step at all. The sweep in app/workers/outreach_tasks.py
    # (check_followup_due) reads them per step.
    #
    # Both carry a server_default as well as a Python default because
    # migration 0020 adds them to a table that already has rows: without one
    # the ALTER cannot make the column NOT NULL, and a NULL delay would make
    # the sweep's arithmetic silently skip every pre-existing step.
    followup_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    followup_delay_hours: Mapped[int] = mapped_column(
        Integer, default=72, server_default=text("72"), nullable=False
    )
    # --- Feature Group 5 (migration 0026): LinkedIn steps only ------------
    # auto | connect | message | inmail. `auto` = message if connected,
    # InMail for a Premium lead, else a connection request. NULL on every
    # non-LinkedIn step.
    linkedin_action: Mapped[str | None] = mapped_column(String(20), nullable=True)

    sequence: Mapped["Sequence"] = relationship(back_populates="steps")

    def effective_channel(self, sequence: "Sequence") -> ChannelType:
        return self.channel or sequence.channel


class SequenceEnrollment(TimestampMixin, Base):
    """Per-(sequence, lead) state. Hard stop conditions set status=STOPPED
    with a reason; the engine refuses to send for stopped enrollments —
    templates never see or control this."""

    __tablename__ = "sequence_enrollments"
    __table_args__ = (
        UniqueConstraint("sequence_id", "lead_id", name="sequence_lead"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    sequence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sequences.id", ondelete="CASCADE"), index=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[EnrollmentStatus] = mapped_column(
        _enum(EnrollmentStatus), default=EnrollmentStatus.ACTIVE, index=True
    )
    stop_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    # --- Part 1 Feature 3 (migration 0055): completion guarantee ----------
    # The number of steps this prospect was enrolled INTO, frozen at
    # enrollment. Editing the sequence later must not retroactively turn a
    # completed enrollment into an incomplete one. NULL = enrolled before
    # this feature.
    planned_steps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Incremented once per send that advances the sequence -- not by a skip,
    # and not again when a transient failure is retried.
    steps_sent: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stopped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The fixed vocabulary behind the free-text `stop_reason`, decided at
    # WRITE time (app/services/sequence_completion.categorize) so an
    # unrecognised reason lands in "other" and is logged, rather than
    # quietly disappearing into a breakdown nobody reads.
    stop_category: Mapped[str | None] = mapped_column(
        String(30), nullable=True, index=True
    )

    sequence: Mapped["Sequence"] = relationship(back_populates="enrollments")
    lead: Mapped["Lead"] = relationship()


class Message(TimestampMixin, Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    sequence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sequences.id", ondelete="CASCADE"), index=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[ChannelType] = mapped_column(_enum(ChannelType))
    step_no: Mapped[int] = mapped_column(Integer, default=1)
    template: Mapped[str] = mapped_column(Text)                     # step brief snapshot
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)   # final rendered body, persisted BEFORE send
    variant: Mapped[str] = mapped_column(String(20), default="A")  # A/B testing
    # M4 Chunk 3: how a WhatsApp message sends + which template row it uses
    # (snapshot at scheduling time; NULL for email messages).
    whatsapp_kind: Mapped[WhatsAppStepKind | None] = mapped_column(
        _enum(WhatsAppStepKind), nullable=True
    )
    whatsapp_template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("whatsapp_templates.id", ondelete="SET NULL"), nullable=True
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    thread_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    unsubscribe_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    sender_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)  # gmail account id
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[MessageStatus] = mapped_column(
        _enum(MessageStatus), default=MessageStatus.SCHEDULED
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # M8-C4 (migration 0010_m8c4): field-presence personalization score
    # computed at send time (0.0–1.0).
    personalization_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Feature Group 2 (migration 0025): what this message was written FROM --
    # {linkedin_post_url, news_url, style_profile: bool, loom_cta: bool}.
    # Kept so "why did the email open with that?" has an answer, and so the
    # learning loop can later compare post-hooked against news-hooked emails.
    personalization_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Feature Group 5 (migration 0026): what a LinkedIn send actually did
    # (connect / message / inmail -- `auto` resolved) and from which account.
    linkedin_action: Mapped[str | None] = mapped_column(String(20), nullable=True)
    linkedin_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("linkedin_accounts.id", ondelete="SET NULL"), nullable=True
    )
    # --- Feature Group 3 (migration 0028): email open tracking ------------
    # First genuine open (the tracking pixel, minus the prefetch window --
    # see app/services/open_tracking.py) and how many times it was loaded.
    # The OPENED outcome row is written once, on the first open; the counter
    # is what "opened 4 times" in the lead timeline reads.
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    open_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    # --- Feature 5 (migration 0048) -----------------------------------------
    # WHY this message exists when it is not an ordinary sequence step. NULL for
    # every scheduled step (their rows are unchanged); "reengagement" for the
    # opt-in post-sequence touch. A real indexed column, not outcome meta_json,
    # because the re-engagement cap counts these rows at send time and a JSON
    # predicate is spelled differently on SQLite and PostgreSQL.
    origin: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

    # F-DB-03 (migration 0065): the dispatcher's every-tick scan is
    # WHERE status = 'scheduled' AND scheduled_at <= now ORDER BY scheduled_at.
    __table_args__ = (
        Index("ix_messages_status_scheduled_at", "status", "scheduled_at"),
        # Migration 0087: the best-time learning refresh scans recent SENT
        # messages (status = 'sent' AND sent_at >= now - 180 days).
        Index("ix_messages_status_sent_at", "status", "sent_at"),
    )

    sequence: Mapped["Sequence"] = relationship(back_populates="messages")
    lead: Mapped["Lead"] = relationship(back_populates="messages")


class Outcome(Base):
    """Immutable event log feeding the M8 learning loop. No updated_at."""

    __tablename__ = "outcomes"
    __table_args__ = (Index("ix_outcomes_lead_event", "lead_id", "event"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    # 0012: nullable — outcomes also audits system-level events
    # (ab_promoted, circuit_opened) which have a strategy but no lead.
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    # M8-C1 (migration 0008_m8c2): denormalized learning-loop dimensions.
    # strategy_id lets the nightly aggregation and A/B sweep GROUP BY strategy
    # without a 3-table join; variant snapshots the message variant at event
    # time (messages can be re-variant-ed later; outcomes are immutable).
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    variant: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    event: Mapped[OutcomeEvent] = mapped_column(_enum(OutcomeEvent))
    # M4 Chunk 3: the channel this event happened on — a REAL, queryable
    # column (not buried in meta_json) because M8's learning loop compares
    # channel performance. 'system' for channel-less events (GDPR delete).
    channel: Mapped[str] = mapped_column(String(20), default="email",
                                         nullable=False, index=True)
    meta_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    lead: Mapped["Lead"] = relationship(back_populates="outcomes")


class PlaybookScore(TimestampMixin, Base):
    """Learned effectiveness of a tactic, aggregated nightly (M8).

    M8-C1 (migration 0008_m8c2) reshapes this from the M1 (pattern_key, metric)
    scalar rows into per-variant rows carrying both rates. `metric` is kept
    nullable for backwards compatibility with M1 rows.

    F-DB-01 (migration 0093): rows are per ACCOUNT, keyed on (user_id,
    pattern_key, variant) -- the upsert key used by the nightly aggregation.
    Before 0093 a row pooled the outcomes of every account whose strategy
    shared a pattern_key, and pipeline prompts presented that pool as "this
    account's" results. Rows written before 0093 have user_id NULL; no reader
    returns them and the aggregation never updates them.
    """

    __tablename__ = "playbook_scores"
    __table_args__ = (
        UniqueConstraint("user_id", "pattern_key", "variant",
                         name="uq_playbook_scores_owner_pattern_variant"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    pattern_key: Mapped[str] = mapped_column(String(200), index=True)
    metric: Mapped[str | None] = mapped_column(String(50), nullable=True)  # legacy M1
    variant: Mapped[str] = mapped_column(String(20), default="A", nullable=False)
    # F-DB-04: always a copy of reply_rate; nullable since 0095 and being
    # removed (see that migration). Read reply_rate instead.
    score: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.0)
    reply_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    booking_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    is_reliable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_aggregated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # M8-C4 (migration 0010_m8c4): exponential score decay metadata
    effective_sample_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    decay_half_life_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    oldest_outcome_ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # new/rising/falling/stable. String(16) as created by migration 0010; the
    # model said 20, which only SQLite (no length check) accepted.
    trend: Mapped[str | None] = mapped_column(String(16), nullable=True)


class GmailAccount(TimestampMixin, Base):
    """A user's connected Gmail account. OAuth tokens are stored ONLY as
    Fernet ciphertext (app/services/crypto.py) — encrypted at rest.
    token_expires_at is duplicated in plaintext purely so the refresh
    check does not need a decrypt; it is not sensitive."""

    __tablename__ = "gmail_accounts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    email_address: Mapped[str | None] = mapped_column(String(320), nullable=True)
    token_ciphertext: Mapped[str] = mapped_column(Text)  # encrypted JSON token set
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scopes: Mapped[str] = mapped_column(String(500), default="")
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SuppressionEntry(Base):
    """Contacts who must NEVER be messaged again. Checked at sourcing time
    (M2) and again before every send (M3+). Compliance rule, no exceptions."""

    __tablename__ = "suppression_list"
    __table_args__ = (
        CheckConstraint(
            "(email IS NOT NULL) OR (phone IS NOT NULL)",
            name="email_or_phone_present",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str | None] = mapped_column(
        String(320), nullable=True, unique=True, index=True
    )
    phone: Mapped[str | None] = mapped_column(
        String(50), nullable=True, unique=True, index=True
    )
    reason: Mapped[str] = mapped_column(String(200))  # unsubscribed, gdpr_delete...
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class InboundReply(TimestampMixin, Base):
    """An inbound message matched (or attempted) to a lead, with its
    classification. Stored for the user to act on — Victorae never
    auto-replies to humans in M3."""

    __tablename__ = "inbound_replies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=True, index=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    account_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # M4 Chunk 3: which channel this inbound arrived on.
    channel: Mapped[str] = mapped_column(String(20), default="email",
                                         nullable=False, index=True)
    thread_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    from_address: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    classification: Mapped[str | None] = mapped_column(String(40), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Migration 0086 (gaps-sweep-2 #6): the email's own Message-ID and its
    # References chain (RFC 5322 3.6.4), sanitised by email_threading. An
    # answer sets In-Reply-To / References from them so it threads in the
    # prospect's mail client. NULL for non-email channels and for replies
    # stored before 0086. Erased with the rest of the reply (account_erasure).
    rfc_message_id: Mapped[str | None] = mapped_column(String(502), nullable=True)
    rfc_references: Mapped[str | None] = mapped_column(Text, nullable=True)
    # --- Feature 2 (migration 0034): reply intelligence -------------------
    # `classification` above answers "how does the SYSTEM route this?"
    # (unsubscribe, bounce, out-of-office). These answer a different
    # question -- "what does the HUMAN do next?" -- which is why both exist.
    # BUYING_SIGNAL | OBJECTION | NOT_NOW | WRONG_PERSON; NULL = never
    # classified, which is not the same as "no category fit".
    reply_category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    category_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_next_action: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # A DRAFT, never a sent message. Nothing in this system replies to a
    # human on its own -- the sender reads this, edits it, and sends it.
    ai_draft_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    classified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # NOT_NOW only: the day to come back. Defaulted to +30 days and editable
    # to 60 or 90 through PATCH /crm/replies/{id}/intelligence.
    reschedule_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # --- Feature A3 (migration 0043): real-time authenticity ---------------
    # A third question beside routing (`classification`) and next action
    # (`reply_category`): is a REAL BUYER on the other end, and how sure are
    # we? Scored the moment the reply is stored; see reply_authenticity.py.
    # genuine | out_of_office | auto_responder | bot | bounce; NULL = not scored
    authenticity_kind: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    authenticity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    buyer_intent_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    authenticity_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    authenticity_signals_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    authenticity_scored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # --- Part 1 Feature 1 (migration 0053): positive reply classification ---
    # A FOURTH question, and the only one the reply-rate metric cares about:
    # was this reply POSITIVE? interested | neutral | objection | not_now |
    # unsubscribe, with the model's own confidence and a one-line reason.
    # NULL = never classified (not the same as "classified as neutral").
    # `intent_source` is "model" or "rules" -- machine mail and explicit
    # unsubscribes are decided deterministically and never cost a model call.
    intent_label: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    intent_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    intent_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    intent_source: Mapped[str | None] = mapped_column(String(10), nullable=True)
    intent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # --- Part 1 Feature 6 (migration 0058): the unified inbox ---------------
    # "Handled" is per REPLY, not per prospect: someone who replies twice in a
    # week has one thread and two things to answer, and a per-prospect flag
    # would let the second be cleared by a decision made about the first.
    # It exists at all because the two cases that matter most -- a reply
    # answered OUTSIDE Victorae, and a reply that needs no answer -- produce
    # no outbound row, so "latest inbound is newer than latest outbound"
    # alone would show them as outstanding forever.
    handled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    handled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class WhatsAppTemplate(TimestampMixin, Base):
    """A Meta message template. Chunk 2 lifecycle:
        draft -> submitted -> approved | rejected (+ rejection_reason)
    Approved templates are IMMUTABLE in our db — an edit creates a new
    row with version+1 and supersedes_id pointing at the old one (Meta
    also versions templates under the same name). Only status=approved
    templates are ever sendable — enforced in the adapter's send() guard."""

    __tablename__ = "whatsapp_templates"
    __table_args__ = (
        # Meta identifies templates by (name, language); we add version so
        # edits of approved templates can create a new draft under the
        # same identity.
        UniqueConstraint("name", "language", "version",
                         name="template_name_language_version"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200), index=True)
    language: Mapped[str] = mapped_column(String(20))  # e.g. "en_US"
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    category: Mapped[WhatsAppTemplateCategory] = mapped_column(
        _enum(WhatsAppTemplateCategory),
        default=WhatsAppTemplateCategory.MARKETING,
    )
    status: Mapped[WhatsAppTemplateStatus] = mapped_column(
        _enum(WhatsAppTemplateStatus), default=WhatsAppTemplateStatus.DRAFT
    )
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # {"1": "prospect first name", "2": "their company", ...} — tells the
    # sequence engine (Chunk 3) what each {{n}} placeholder means.
    variable_descriptions_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Meta's own template id, filled on submit/sync.
    meta_template_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("whatsapp_templates.id", ondelete="SET NULL"), nullable=True
    )


class WhatsAppOptIn(Base):
    """APPEND-ONLY audit trail of WhatsApp consent events per lead.

    Opt-in is a compliance fact, not a boolean: every row records WHO
    (lead + phone), WHAT (opted_in/opted_out), HOW (source), the EVIDENCE
    (URL / message id / import note) and the exact consent text shown.
    Rows are never updated or deleted — a revocation is a NEW opted_out
    row. The lead's current status is the latest row; Lead.whatsapp_opted_in
    is only a denormalized cache of that, maintained by
    app/services/whatsapp_optin.py."""

    __tablename__ = "whatsapp_optins"

    id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    phone: Mapped[str] = mapped_column(String(20))  # normalized E.164 (+digits)
    status: Mapped[OptInStatus] = mapped_column(_enum(OptInStatus))
    source: Mapped[OptInSource] = mapped_column(_enum(OptInSource))
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProcessedWebhook(Base):
    """Idempotency ledger for webhook deliveries (Calendly/WhatsApp retries)."""

    __tablename__ = "processed_webhooks"
    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="provider_event"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    provider: Mapped[str] = mapped_column(String(40))
    event_id: Mapped[str] = mapped_column(String(200))
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

# --------------------------------------------------------------------------
# M7 — mobile push notifications
# --------------------------------------------------------------------------


class DeviceToken(TimestampMixin, Base):
    """FCM device token registered by the mobile app (M7 Chunk 3).

    Upsert semantics: (user_id, token) unique — re-registering the same token
    updates the row instead of duplicating it.
    """

    __tablename__ = "device_tokens"
    __table_args__ = (
        # Globally unique on token alone, matching devices.py's
        # ON CONFLICT (token) upsert and its documented token-transfer
        # behaviour. An FCM registration token identifies one app install, so
        # two accounts must not both hold a row for the same physical device.
        # See migration 0013_device_token_unique.
        UniqueConstraint("token", name="uq_device_tokens_token"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token: Mapped[str] = mapped_column(String(512), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), default="android")
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# --------------------------------------------------------------------------
# M8-C1/C3 — encrypted provider tokens (migrations 0008_m8c2 + 0009_m8c3)
# --------------------------------------------------------------------------


class IntegrationToken(TimestampMixin, Base):
    """Provider credentials at rest (Apollo/Hunter/Gmail/WhatsApp/Calendly).

    Written by app.integrations.token_store.TokenStore — values are stored in
    `encrypted_value` (Fernet, migration 0009_m8c3); the plaintext `value`
    column exists only for the 0009 backfill and is nulled by it.
    """

    __tablename__ = "integration_tokens"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", "token_kind", name="user_provider_kind"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    # NULL = SYSTEM scope: a deployment-wide credential set by an admin (the
    # OpenAI key behind the consensus engine, the Slack app's client secret).
    # Nullable since migration 0023; see app/services/credentials.py.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    provider: Mapped[str] = mapped_column(String(40), index=True)   # apollo, hunter, gmail...
    token_kind: Mapped[str] = mapped_column(String(40), default="api_key")
    value: Mapped[str | None] = mapped_column(Text, nullable=True)  # legacy plaintext (backfilled → NULL)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # encrypted_value column is ADDED by migration 0009_m8c3
    encrypted_value: Mapped[str | None] = mapped_column(Text, nullable=True)


# --------------------------------------------------------------------------
# M8-C4 — subject line intelligence (migration 0010_m8c4)
# --------------------------------------------------------------------------


class SubjectLinePattern(Base):
    """Aggregated performance of a subject-line pattern class per channel."""

    __tablename__ = "subject_line_patterns"
    __table_args__ = (
        UniqueConstraint("pattern_type", "channel", name="pattern_channel"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    pattern_type: Mapped[str] = mapped_column(String(50), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="gmail")
    # No longer written (2026-10-27): it held another account's sent subject
    # line. Each nightly write sets it NULL.
    example: Mapped[str | None] = mapped_column(String(500), nullable=True)
    avg_reply_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_meeting_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    is_reliable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Named as migration 0010 creates it; the raw SQL in
    # app/services/subject_intelligence.py writes this column.
    last_updated: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# --------------------------------------------------------------------------
# M8-C5 — reliable outbound webhooks (migration 0011_m8c5)
# --------------------------------------------------------------------------


class WebhookTarget(TimestampMixin, Base):
    """A user-registered outbound webhook destination (HMAC-signed deliveries)."""

    __tablename__ = "webhook_targets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    event_types: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # --- Feature Group 4 (migration 0029) ---------------------------------
    # api | zapier | make -- who registered it. A Zapier REST-hook
    # subscription is removed by Zapier itself when the Zap is turned off.
    source: Mapped[str] = mapped_column(
        String(20), default="api", server_default="api", nullable=False
    )
    # Why the target stopped receiving (e.g. "410 Gone from receiver").
    disabled_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)


class WebhookDelivery(Base):
    """Delivery log for outbound webhooks — 5-retry exponential backoff."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[uuid.UUID] = _uuid_pk()
    target_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("webhook_targets.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(60), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Feature Group 4 (migration 0029): the M8 code wrote this column and it
    # never existed; the delivery log shows it.
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# --------------------------------------------------------------------------
# M9 — native CRM layer (migration 0019_m9_crm)
# --------------------------------------------------------------------------
#
# Design note that governs this whole block: NOT ONE existing table is
# modified. `leads`, `strategies` and `outcomes` are read by M1–M8 code
# paths (the sourcing chain, the sequence engine, the nightly learning
# loop) and by the published SDK; adding a column to `leads` for the CRM's
# owner field or its custom-field bag would put M9's UI concerns inside the
# row every one of those reads. So CRM-only per-lead state lives in
# `crm_lead_meta`, and user-defined fields live in a definition + value pair
# — both joined on lead_id, both droppable without touching the lead.


class CrmNote(TimestampMixin, Base):
    """A free-text note a user wrote on a lead.

    Editable (updated_at moves) but `created_at` is never rewritten — the
    activity feed orders by when the note was WRITTEN, and letting an edit
    jump a three-week-old note to the top of the timeline would make the
    feed lie about the sequence of events.
    """

    __tablename__ = "crm_notes"
    __table_args__ = (
        Index("ix_crm_notes_lead_created", "lead_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    # The note's author. SET NULL rather than CASCADE: deleting a user must
    # not silently erase the notes that are still attached to a live lead.
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    body: Mapped[str] = mapped_column(Text)

    lead: Mapped["Lead"] = relationship()


class CrmActivity(Base):
    """Append-only, UI-facing audit trail per lead. No updated_at.

    This is NOT `outcomes`. `outcomes` is the M8 learning loop's immutable
    event log: the nightly aggregation, the A/B sweep and the playbook
    scorer all read it, filtered by strategy_id/variant/channel, and it is
    deliberately narrow. Writing "note added" or "tag removed" rows into it
    would put UI noise inside every one of those aggregates and change the
    denominators the learning loop computes its rates from.

    `actor_user_id` is NULL for machine-generated activity (the sourcing
    chain, a webhook), which is how the feed distinguishes "you moved this"
    from "the pipeline moved this".
    """

    __tablename__ = "crm_activities"
    __table_args__ = (
        Index("ix_crm_activities_lead_ts", "lead_id", "ts"),
        # The account-wide feed (dashboard page 4) orders by ts across every
        # lead the user owns; without this it is a full scan + filesort once
        # the table has real volume.
        Index("ix_crm_activities_strategy_ts", "strategy_id", "ts"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    # Denormalized from lead.strategy_id for the same reason
    # outcomes.strategy_id is denormalized: the account-wide feed filters by
    # strategy and would otherwise need a join on every page.
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[CrmActivityKind] = mapped_column(_enum(CrmActivityKind), index=True)
    # Human-readable before/after for status and field changes. Stored as
    # text, not as foreign keys: an activity row must stay readable after
    # whatever it references has been renamed or deleted.
    from_value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    to_value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    meta_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # BOTH defaults, and the Python one is load-bearing.
    #
    # For this table ordering IS the feature -- it is a timeline, read newest
    # first -- and `func.now()` alone cannot order it. On SQLite
    # CURRENT_TIMESTAMP has one-second resolution, so every event in the same
    # second ties; on PostgreSQL now() is transaction-scoped, so every row
    # written by one request ties by construction. Ties fall through to the
    # `id DESC` tiebreaker, which on a random UUID4 is stable but meaningless
    # -- the feed would show two events in the correct order only by luck, and
    # the keyset cursor would skip or repeat across a tied group.
    #
    # A Python-side default is evaluated once per row at insert, in
    # microseconds, and is not transaction-scoped, so it actually orders. The
    # server_default stays as the backstop for a row written by anything that
    # is not this ORM (a migration backfill, a psql session), same reasoning
    # as users.email_verified.
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    lead: Mapped["Lead"] = relationship()


class CrmTag(TimestampMixin, Base):
    """A user-defined label. Scoped per user, not per strategy, so one tag
    ("warm intro", "budget confirmed") is reusable across every campaign.

    `color_token` names a THEME TOKEN (primary / accent / success / warning /
    destructive / muted), never a hex value. The theme engine recolors the
    whole app from CSS variables; a tag storing #16a34a would be the one
    thing on screen that ignores the user's chosen preset.
    """

    __tablename__ = "crm_tags"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="crm_tag_user_name"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(60))
    color_token: Mapped[str] = mapped_column(
        String(20), default="muted", server_default="muted", nullable=False
    )


class CrmLeadTag(Base):
    """lead to tag association. The unique constraint makes double-tagging
    impossible at the schema level, so the bulk-tag endpoint can be naively
    idempotent instead of reading before every write."""

    __tablename__ = "crm_lead_tags"
    __table_args__ = (
        UniqueConstraint("lead_id", "tag_id", name="crm_lead_tag_unique"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crm_tags.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CrmSavedView(TimestampMixin, Base):
    """A named filters+sort+columns combination, persisted SERVER-side.

    localStorage was the cheaper option and the wrong one: a saved view is
    the user's own work (they built the filter set), and putting it in
    browser storage means it is invisible on their phone, gone when they
    clear site data, and unrecoverable when they switch machines.

    `is_default` is kept unique per (user, view_type) by the service layer
    rather than by a partial unique index — SQLite and PostgreSQL both
    support partial indexes, but their syntax diverges enough that the
    constraint would need two dialect branches in the migration to express a
    rule that is one UPDATE at the call site.
    """

    __tablename__ = "crm_saved_views"
    __table_args__ = (
        UniqueConstraint("user_id", "view_type", "name",
                         name="crm_view_user_type_name"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    view_type: Mapped[CrmViewType] = mapped_column(
        _enum(CrmViewType), default=CrmViewType.GRID, index=True
    )
    filters_json: Mapped[dict] = mapped_column(JSON, default=dict)
    sort_json: Mapped[list] = mapped_column(JSON, default=list)
    # [{key, width, visible, order}] — column WIDTHS live here too, so a
    # view restores the exact layout, not just the data selection.
    columns_json: Mapped[list] = mapped_column(JSON, default=list)
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )


class CrmCustomField(TimestampMixin, Base):
    """Definition of a user-defined column on the grid.

    `key` is the stable machine name a saved view's columns_json references;
    `label` is what the header shows. They are separate so that renaming a
    field does not orphan every saved view that displays it.
    """

    __tablename__ = "crm_custom_fields"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="crm_field_user_key"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(60))
    label: Mapped[str] = mapped_column(String(100))
    field_type: Mapped[CrmFieldType] = mapped_column(
        _enum(CrmFieldType), default=CrmFieldType.TEXT
    )
    # SELECT only: the allowed choices. Validated on write, so a value that
    # is not in this list can never be stored.
    options_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )


class CrmCustomFieldValue(TimestampMixin, Base):
    """One field's value for one lead (EAV).

    WHY EAV RATHER THAN A JSON BAG ON `leads`:
      1. A JSON column on `leads` is a schema change to a table that M1–M8
         and the published SDK all read. This table is additive, and it can
         be dropped without touching a lead row.
      2. The grid sorts and filters on custom fields SERVER-side, across
         5,000+ rows. JSON extraction is spelled differently in SQLite
         (json_extract) and PostgreSQL (->>), with different NULL and
         collation behaviour, so the test suite would exercise a different
         query than production runs. A plain indexed column does not have
         that problem.

    THE COST, stated plainly: reading N leads with their custom values is
    two queries, not one. The grid endpoint issues exactly one extra
    `WHERE lead_id IN (...)` for the page's <=200 ids and assembles the rows
    in Python — bounded, not N+1.

    `value_text` is the canonical sortable/filterable form and is ALWAYS
    populated (numbers zero-padded, dates ISO-8601, bools "true"/"false") so
    that lexical ordering matches semantic ordering. `value_json` carries the
    typed original so a round-trip never loses precision.
    """

    __tablename__ = "crm_custom_field_values"
    __table_args__ = (
        UniqueConstraint("field_id", "lead_id", name="crm_field_value_unique"),
        Index("ix_crm_field_values_field_text", "field_id", "value_text"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    field_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crm_custom_fields.id", ondelete="CASCADE"), index=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    value_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    value_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class CrmLeadMeta(TimestampMixin, Base):
    """CRM-only per-lead state — the columns that would otherwise have been
    added to `leads`.

    `stage_entered_at` earns this table on its own. Lead velocity ("how long
    has this one been sitting in contacted?") needs the moment the CURRENT
    status was entered, and nothing in M1–M8 records it: `leads.updated_at`
    moves on any write at all, including a WhatsApp opt-in timestamp that
    says nothing about the stage. Leads that last changed status before M9
    shipped have no meta row; the dashboard falls back to `leads.updated_at`
    for those and labels the figure estimated, rather than quietly averaging
    two different measurements together.
    """

    __tablename__ = "crm_lead_meta"
    __table_args__ = (
        UniqueConstraint("lead_id", name="crm_lead_meta_lead"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    priority: Mapped[str | None] = mapped_column(String(20), nullable=True)
    next_action_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stage_entered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    lead: Mapped["Lead"] = relationship()


# --------------------------------------------------------------------------
# Engagement Hub — self-built calendar (migration 0021)
# --------------------------------------------------------------------------
#
# WHY THIS IS NOT CALENDLY
# app/integrations/calendly.py stays exactly where it is: an account that
# already runs on Calendly keeps its links, its webhook and its bookings. The
# tables below are the OWNED path — availability the user edits in-app, a
# public booking page on our own domain, and bookings that land in the same
# database as the lead they belong to. That last part is the point: a Calendly
# booking arrives as a webhook that has to be matched back to a lead by a
# tagged UTM parameter (see calendly.py's tenant-tagging note for how fragile
# that is), whereas a booking made here already knows which page it came from
# and therefore which user owns it.
#
# TABLE NAMES: the brief spells these singular (calendar_booking_page). Every
# other table in this schema is plural, and a schema that is plural except in
# one corner is a trap for anyone writing a raw query, so they are pluralised
# here. The MODEL names are what application code touches, and those match the
# brief exactly.


class CalendarAvailability(TimestampMixin, Base):
    """One recurring weekly window a user is bookable in.

    Multiple rows per weekday are intended and normal — "9-12 and 14-17" is
    two rows, not one row with a hole in it, because a single row with a break
    would need a nested structure that no query can filter on.

    `timezone` lives on the ROW, not on the user: the times are wall-clock
    times in it ("I take calls at 9am Karachi time"), and storing them in UTC
    would move the user's morning by an hour twice a year. Slot generation in
    app/services/calendar_service.py converts to UTC per day, so DST is
    resolved at the day being offered rather than when the rule was written.
    """

    __tablename__ = "calendar_availability"
    __table_args__ = (
        CheckConstraint("day_of_week >= 0 AND day_of_week <= 6",
                        name="calendar_availability_dow"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # 0 = Monday .. 6 = Sunday — Python's datetime.weekday(), so the slot
    # generator never has to translate between two week conventions.
    day_of_week: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    timezone: Mapped[str] = mapped_column(
        String(64), default="UTC", server_default="UTC", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )


class CalendarBookingPage(TimestampMixin, Base):
    """A shareable page (/book/<slug>) that turns availability into a
    bookable offer.

    A user can have several: "30-min intro" and "60-min technical deep dive"
    are different durations, different questions and different links over the
    same underlying availability.
    """

    __tablename__ = "calendar_booking_pages"
    __table_args__ = (
        # Global, not per-user: the slug IS the public URL. Two users cannot
        # both own /book/intro-call.
        UniqueConstraint("slug", name="calendar_booking_page_slug"),
        CheckConstraint("duration_minutes IN (15, 30, 45, 60)",
                        name="calendar_booking_page_duration"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    slug: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_minutes: Mapped[int] = mapped_column(
        Integer, default=30, server_default=text("30"), nullable=False
    )
    # Padding AFTER each booking, so back-to-back calls are impossible.
    buffer_minutes: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    # NULL = unlimited. A sentinel like 0 would read as "nobody may book",
    # which is the opposite of what an unset field should mean.
    max_bookings_per_day: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    # [{"key": "budget", "label": "What is your budget?", "type": "text",
    #   "required": true}] — answers land in calendar_bookings.answers.
    custom_questions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )


class CalendarBooking(TimestampMixin, Base):
    """One booked slot.

    `slot_key` IS THE DOUBLE-BOOKING GUARD, and it is why this table has a
    column the brief does not list. Two people can load the same booking page
    and submit the same 10:00 slot in the same second; a check-then-insert in
    the handler loses that race, and a partial unique index (WHERE status <>
    'cancelled') is PostgreSQL-only, so the test suite would exercise a
    different constraint than production.

    Instead: slot_key holds the slot's UTC start while the booking is live and
    is set to NULL when it is cancelled. NULLs do not collide in a UNIQUE
    constraint on either PostgreSQL or SQLite, so the constraint blocks a
    second live booking of the same slot and still lets a cancelled slot be
    re-booked. One insert, no read, no race.
    """

    __tablename__ = "calendar_bookings"
    __table_args__ = (
        UniqueConstraint("booking_page_id", "slot_key",
                         name="calendar_booking_live_slot"),
        Index("ix_calendar_bookings_page_start", "booking_page_id", "start_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    booking_page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calendar_booking_pages.id", ondelete="CASCADE"), index=True
    )
    invitee_name: Mapped[str] = mapped_column(String(200))
    invitee_email: Mapped[str] = mapped_column(String(320), index=True)
    invitee_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The invitee's own IANA zone, captured at booking time. Confirmation
    # emails render the time in it; without it a person in Auckland gets a
    # confirmation for a time they have to convert by hand.
    invitee_timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meeting_link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[BookingStatus] = mapped_column(
        _enum(BookingStatus), default=BookingStatus.CONFIRMED, index=True
    )
    # SET NULL, not CASCADE: deleting a lead must not erase the fact that a
    # meeting happened — the invitee's own record of it is this row.
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    answers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # See the class docstring: the UTC slot start while live, NULL once
    # cancelled. Never read by application logic — the constraint is the point.
    slot_key: Mapped[str | None] = mapped_column(String(40), nullable=True)

    booking_page: Mapped["CalendarBookingPage"] = relationship()
    lead: Mapped["Lead | None"] = relationship()


# --------------------------------------------------------------------------
# Engagement Hub — meetings (migration 0022)
# --------------------------------------------------------------------------


class Meeting(TimestampMixin, Base):
    """A call: where it happens, what was said, and what to do next.

    `booking_id` IS NULLABLE because meetings are also created by hand — the
    prospect proposed a time over email, or the call was booked before this
    feature existed. A NOT NULL booking would make the manual path impossible
    and force fake bookings to be written to satisfy the schema.

    `lead_id` is carried here as well as on the booking. It is not redundant:
    a manually created meeting has no booking to inherit it from, and Feature
    4's "Meetings" tab on a lead needs one indexed column to query rather than
    a LEFT JOIN through bookings that only resolves half the rows.

    THE TWO TIME PAIRS ARE DIFFERENT FACTS. start_at/end_at are what was
    scheduled; actual_start_at/actual_end_at are when Start/End Meeting were
    pressed. The AI summary prompt uses the actual duration, because "we
    booked 60 minutes and it ran 12" is a signal about the call, and folding
    it into the scheduled figure would erase it.
    """

    __tablename__ = "meetings"
    __table_args__ = (
        Index("ix_meetings_host_start", "host_user_id", "start_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("calendar_bookings.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    host_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    platform: Mapped[MeetingPlatform] = mapped_column(
        _enum(MeetingPlatform), default=MeetingPlatform.CUSTOM
    )
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    meeting_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # The provider's own id for the event (Google Calendar eventId, Zoom
    # meeting id). Kept so a later cancel or reschedule can address the right
    # object instead of parsing it back out of the join URL.
    external_event_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actual_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actual_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[MeetingStatus] = mapped_column(
        _enum(MeetingStatus), default=MeetingStatus.SCHEDULED, index=True
    )
    # What the user typed during the call. Never overwritten by the AI —
    # ai_notes is a separate column precisely so a generated summary can never
    # destroy a human's own record of what was said.
    raw_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    recording_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{"text": "...", "owner": "us"|"client", "due": "2026-09-14",
    #   "done": false}] — `done` is written back by the checkbox list in the UI.
    action_items: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # The remaining three fields of meeting_ai.generate_meeting_summary()'s
    # contract. Separate columns rather than a blob inside ai_notes because
    # the UI renders them as distinct affordances (a bullet list, a next-steps
    # list, a sentiment chip), and re-parsing them out of prose on every
    # render is how a summary panel starts lying.
    key_points: Mapped[list | None] = mapped_column(JSON, nullable=True)
    next_steps: Mapped[list | None] = mapped_column(JSON, nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # --- Feature 3 (migration 0049): recording-provider transcripts ---------
    # recording_bot_id is the ONLY key a recording webhook is matched to a
    # meeting by (unique), never an id carried in the payload. transcript_status
    # is requesting | pending | received | failed | timed_out; summary_source is
    # notes | transcript. See app/services/meeting_recording.py.
    recording_bot_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, unique=True, index=True
    )
    transcript_status: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    transcript_deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    summary_source: Mapped[str | None] = mapped_column(String(20), nullable=True)

    booking: Mapped["CalendarBooking | None"] = relationship()
    lead: Mapped["Lead | None"] = relationship()
    participants: Mapped[list["MeetingParticipant"]] = relationship(
        back_populates="meeting", cascade="all, delete-orphan"
    )


class MeetingParticipant(Base):
    """Who was on the call. No updated_at — join/leave times are facts, and a
    row is written once per person per meeting."""

    __tablename__ = "meeting_participants"

    id: Mapped[uuid.UUID] = _uuid_pk()
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    role: Mapped[MeetingParticipantRole] = mapped_column(
        _enum(MeetingParticipantRole), default=MeetingParticipantRole.CLIENT
    )
    joined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    left_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    meeting: Mapped["Meeting"] = relationship(back_populates="participants")


# --------------------------------------------------------------------------
# Feature expansion — foundation (migration 0023)
# --------------------------------------------------------------------------


class SystemSetting(Base):
    """One admin-controlled, deployment-wide setting.

    The allowed keys, their types and their defaults are declared in
    app/services/system_settings.py::DEFAULTS, not here: a key with no row
    reads as its default, so an empty table is a correctly configured
    deployment. `value_json` wraps the value as {"v": ...} so a bare boolean
    or number round-trips through every JSON column implementation the same.
    """

    __tablename__ = "system_settings"
    __table_args__ = (
        UniqueConstraint("key", name="system_setting_key"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    value_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------------
# Feature Group 7 — meeting preparation, outcomes, deals (migration 0023)
# --------------------------------------------------------------------------


class MeetingPrepStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class MeetingOutcomeKind(str, enum.Enum):
    """The five choices behind "Log Meeting Outcome"."""
    INTERESTED = "interested"
    NEEDS_FOLLOW_UP = "needs_follow_up"
    NOT_A_FIT = "not_a_fit"
    CLOSED_WON = "closed_won"
    CLOSED_LOST = "closed_lost"


class DealStage(str, enum.Enum):
    OPEN = "open"
    WON = "won"
    LOST = "lost"


class Deal(TimestampMixin, Base):
    """Revenue, linked back to the lead and campaign that produced it.

    MONEY IS INTEGER CENTS. A Float column cannot represent 0.10 exactly, and
    the revenue dashboard sums these across campaigns -- summing floats is how
    an ROI figure ends up off by a cent that nobody can explain. Numeric would
    also work on PostgreSQL but round-trips through SQLite as a float with a
    warning, and the test suite runs on SQLite.

    `strategy_id` IS "the campaign": in this schema one strategy owns one
    outreach campaign (see Strategy.campaign_state). It is denormalized from
    the lead rather than joined through it, because a deal can outlive its
    lead (GDPR delete sets lead_id NULL) and still has to count toward the
    campaign that earned it.
    """

    __tablename__ = "deals"
    __table_args__ = (
        Index("ix_deals_user_close", "user_id", "close_date"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    value_cents: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0"), nullable=False
    )
    currency: Mapped[str] = mapped_column(
        String(3), default="USD", server_default="USD", nullable=False
    )
    stage: Mapped[DealStage] = mapped_column(
        _enum(DealStage), default=DealStage.OPEN, index=True
    )
    close_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    # manual | meeting_outcome | hubspot | salesforce
    source: Mapped[str] = mapped_column(
        String(30), default="manual", server_default="manual", nullable=False
    )
    # The CRM's own id for this deal once synced (HubSpot deal id,
    # Salesforce Opportunity id), so a stage-change webhook can find it.
    external_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    lead: Mapped["Lead | None"] = relationship()


class MeetingPrepBrief(TimestampMixin, Base):
    """The document a user reads before a booked call.

    ONE ROW PER BOOKING, NOT PER GENERATION. (lead, source, external_ref) is
    unique -- external_ref is the Calendly event URI or our own booking id --
    so a Calendly retry, or the same booking reaching both paths, cannot
    produce two briefs, and "Regenerate" rewrites this row in place.

    The row doubles as the REMINDER LEDGER. reminder_24h_sent_at and
    reminder_1h_sent_at are claimed by a conditional UPDATE before a reminder
    goes out, which is what makes the five-minute sweep at-most-once across
    any number of workers. `cancelled_at` stops both.

    `content_md` is the rendered brief; `sections_json` and `profile_json` are
    the structured halves it was rendered from, kept so the UI can lay the
    brief out as cards and the one-hour reminder can lift the opening script
    without re-parsing Markdown.
    """

    __tablename__ = "meeting_prep_briefs"
    __table_args__ = (
        UniqueConstraint("lead_id", "source", "external_ref",
                         name="meeting_prep_brief_source_ref"),
        Index("ix_meeting_prep_briefs_start", "meeting_start_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL"), nullable=True
    )
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("calendar_bookings.id", ondelete="SET NULL"), nullable=True
    )
    # calendly | leadpilot_calendar | manual
    source: Mapped[str] = mapped_column(String(30))
    external_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    meeting_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    meeting_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[MeetingPrepStatus] = mapped_column(
        _enum(MeetingPrepStatus), default=MeetingPrepStatus.PENDING, index=True
    )
    content_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    sections_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    profile_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    opening_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reminder_24h_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reminder_1h_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # --- Part 2 (migration 0064): the call script the SELLER owns -----------
    # {opening, discovery: [...], objections: [{objection, response}],
    #  close, notes}. Its own column rather than another key in
    # `sections_json` because that blob is MODEL OUTPUT, rewritten whenever
    # "Regenerate" is pressed -- and an edit a regeneration silently
    # overwrites is an edit nobody makes twice. This one is written by a
    # person, or seeded once while it is still empty.
    script_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    script_edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    script_edited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Part 2.3: this meeting has practice as a required pre-meeting step. Off
    # by default -- making every call require a rehearsal is how a checklist
    # becomes something people click through without reading.
    practice_required: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )

    lead: Mapped["Lead"] = relationship()


class RoleplaySession(TimestampMixin, Base):
    """Part 2 -- one practice run against a prospect's brief.

    WHY THE SCORES ARE COLUMNS AND THE FEEDBACK IS JSON. The five scores are
    what "am I getting better?" is measured on, so they are queried and
    charted across sessions; the prose feedback is read once, for one session.
    Putting the scores in the JSON would mean extracting them differently on
    SQLite and PostgreSQL to draw one line on a chart.

    `lead_id` and `brief_id` are SET NULL: a practice history is about the
    SELLER, and losing the prospect it was practised against must not erase
    the evidence that they improved.
    """

    __tablename__ = "roleplay_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    brief_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meeting_prep_briefs.id", ondelete="SET NULL"), nullable=True
    )
    # active | completed | abandoned
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default=text("'active'"),
        nullable=False, index=True
    )
    # easy | realistic | hostile
    difficulty: Mapped[str] = mapped_column(
        String(20), default="realistic", server_default=text("'realistic'"),
        nullable=False
    )
    persona_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    objectives_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    turn_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    feedback_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    score_overall: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_discovery: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_objections: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_tone: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_close: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class RoleplayTurn(Base):
    """One line of a practice conversation.

    ROWS, NOT A JSON ARRAY on the session. A roleplay is appended to one line
    at a time by a live UI; a JSON array means read-modify-write on every
    turn, which loses a line whenever two requests overlap. UNIQUE
    (session_id, turn_no) turns a duplicated submit into a conflict rather
    than a duplicated line.

    No updated_at: what was said was said.
    """

    __tablename__ = "roleplay_turns"
    __table_args__ = (
        UniqueConstraint("session_id", "turn_no", name="roleplay_turn_order"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roleplay_sessions.id", ondelete="CASCADE"), index=True
    )
    turn_no: Mapped[int] = mapped_column(Integer)
    # seller | prospect
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MeetingOutcome(TimestampMixin, Base):
    """One "Log Meeting Outcome" submission and the follow-up it produced.

    Append-only in spirit: a second submission for the same lead is a second
    row (the meeting went well, then the deal fell through a week later), and
    the lead's status reflects the latest. `previous_status` / `new_status`
    record exactly what this submission changed, so an accidental click is
    reversible by reading one row.

    The follow-up email lives here, not only in Gmail. Gmail is where the user
    sends it from, but a revoked grant, a missing scope or a deleted draft
    must not lose the text the model wrote -- the UI can always show it and
    offer "copy" when `draft_status` is not `draft_saved`.
    """

    __tablename__ = "meeting_outcomes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL"), nullable=True
    )
    brief_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meeting_prep_briefs.id", ondelete="SET NULL"), nullable=True
    )
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("deals.id", ondelete="SET NULL"), nullable=True
    )
    outcome: Mapped[MeetingOutcomeKind] = mapped_column(_enum(MeetingOutcomeKind))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    followup_subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    followup_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pending | draft_saved | sent | not_connected | reauth_required |
    # suppressed | blocked | no_address | generation_failed | failed
    # (`blocked`: an F05 exclusion / F10 departure / earlier opt-out stopped it;
    # the reason is in draft_error -- app/services/recipient_guards.py)
    draft_status: Mapped[str] = mapped_column(
        String(30), default="pending", server_default="pending", nullable=False
    )
    draft_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    gmail_draft_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    gmail_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    lead: Mapped["Lead"] = relationship()


# --------------------------------------------------------------------------
# Feature Group 1 — AI intelligence layer (migration 0024)
# --------------------------------------------------------------------------


class StrategyModelOutput(Base):
    """One model's raw answer to one research step, when consensus ran.

    Claude's answer is ALSO the step's research_steps.output -- it stays the
    canonical text the document is assembled from. This table keeps both
    models' raw outputs side by side, which is what the "compare" view on an
    uncertain zone shows and what an audit of a disagreement needs.

    Unique per (strategy, pipeline, step, provider): a resumed pipeline that
    re-runs a step replaces its row rather than accumulating copies.
    """

    __tablename__ = "strategy_model_outputs"
    __table_args__ = (
        UniqueConstraint("strategy_id", "pipeline", "step_no", "provider",
                         name="strategy_model_output_step_provider"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    pipeline: Mapped[PipelineKind] = mapped_column(_enum(PipelineKind))
    phase: Mapped[int] = mapped_column(Integer)
    step_no: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(20))   # anthropic | openai
    model: Mapped[str] = mapped_column(String(64))
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StrategyUncertainZone(Base):
    """A section where Claude and GPT-4o meaningfully disagreed.

    "Meaningfully" is the consensus judge's call, instructed to ignore
    wording, ordering and emphasis and to flag only different
    recommendations, contradictory facts, or a different target
    segment/channel/price. Only medium and high severity are stored.
    `similarity` is the TF-IDF cosine of the two outputs -- a second, dumb
    signal kept next to the judge's verdict so a zone is never just the
    opinion of a model about two models.
    """

    __tablename__ = "strategy_uncertain_zones"
    __table_args__ = (
        Index("ix_strategy_uncertain_zones_step", "strategy_id", "pipeline", "step_no"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    pipeline: Mapped[PipelineKind] = mapped_column(_enum(PipelineKind))
    phase: Mapped[int] = mapped_column(Integer)
    step_no: Mapped[int] = mapped_column(Integer)
    section_title: Mapped[str] = mapped_column(String(200))
    topic: Mapped[str] = mapped_column(String(300))
    claude_position: Mapped[str] = mapped_column(Text)
    gpt_position: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(10))    # medium | high
    similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StrategyVersion(TimestampMixin, Base):
    """A revision of a strategy's document -- today, a MUTATION.

    Version 1 is a snapshot of the document as the pipeline produced it,
    written the first time anything mutates it, so "what did we start from"
    is always answerable. Later versions point at their parent.

    A mutation is PROPOSED, never applied on its own: it can change the
    messaging every future email is written from, and that is the user's
    call. `status`: original | proposed | applied | superseded | dismissed.
    """

    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version_no", name="strategy_version_no"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    version_no: Mapped[int] = mapped_column(Integer)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True
    )
    document: Mapped[str] = mapped_column(Text)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # {diagnosis, messaging_angle, channel, icp_refinement}
    changes_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # The outcome numbers the mutation was reasoned from, frozen.
    outcome_snapshot_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    trigger: Mapped[str] = mapped_column(String(30))   # original | idle_no_replies | manual
    status: Mapped[str] = mapped_column(
        String(20), default="proposed", server_default="proposed", nullable=False
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# --------------------------------------------------------------------------
# Feature Group 5 — LinkedIn channel (migration 0026)
# --------------------------------------------------------------------------


class LinkedInAccount(TimestampMixin, Base):
    """One LinkedIn account a user connected through Unipile.

    The user signs in on Unipile's hosted page; Victorae stores only the
    Unipile account id, never a LinkedIn credential. Several per user is the
    point: sends rotate across them (linkedin_limits.pick_account) so no one
    account exceeds its daily ceiling.

    Daily usage lives in Redis (TTL'd per day), not here -- it is a counter
    that must be atomic across workers and forgotten at midnight. What lives
    here is what outlasts a day: whether the account can send InMail, how many
    InMail credits it has, and how many it has spent.
    """

    __tablename__ = "linkedin_accounts"
    __table_args__ = (
        UniqueConstraint("unipile_account_id", name="linkedin_account_unipile_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    unipile_account_id: Mapped[str] = mapped_column(String(120))
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    profile_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Premium / Sales Navigator on OUR side -- required to send InMail.
    has_premium: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    # NULL = unknown (Unipile did not report a balance); sending InMail is
    # then allowed and the provider is the judge.
    inmail_credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inmail_sent_total: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    # ok | credentials_needed | restricted -- from Unipile account events.
    status: Mapped[str] = mapped_column(
        String(30), default="ok", server_default="ok", nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class LinkedInSuppression(Base):
    """LinkedIn profiles that must never be contacted on LinkedIn again.

    A table beside `suppression_list` rather than a third column on it:
    suppression_list's CHECK constraint requires an email or a phone, and
    rewriting that constraint in a SQLite batch migration is exactly the kind
    of change that silently drops a constraint. lead_tasks.is_suppressed
    checks both, so every send path honours both.

    `profile` is the normalised public identifier (the slug after
    linkedin.com/in/, lower-cased), so two spellings of one URL match.
    """

    __tablename__ = "linkedin_suppressions"
    __table_args__ = (
        UniqueConstraint("profile", name="linkedin_suppression_profile"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    profile: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(String(200))
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------------
# Feature Group 6 — AI phone calls (migration 0027)
# --------------------------------------------------------------------------


class CallOutcome(str, enum.Enum):
    VOICEMAIL_DROPPED = "voicemail_dropped"   # voicemail detected, message left
    VOICEMAIL = "voicemail"                   # voicemail detected, no message left
    NO_ANSWER = "no_answer"
    ANSWERED = "answered"                     # a conversation, not yet classified
    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    FAILED = "failed"


class Call(TimestampMixin, Base):
    """One AI call: outbound to a lead, or (voice twin, migration 0089)
    inbound from someone who called the account's own number.

    Created in the send path BEFORE the provider is asked to dial, so the
    script and voicemail that were prepared survive a provider failure. The
    provider's end-of-call webhook fills in what happened; the transcript
    analysis (Claude) fills `analysis_json` and settles `outcome`.

    `provider_call_id` is unique: the webhook finds the call by it, and a
    retried webhook delivery updates the same row.

    INBOUND CALLS FROM A NUMBER THAT IS NOT A LEAD have lead_id NULL. Their
    owner is `user_id` (account_export.DIRECT_OWNER_FALLBACK), so they are
    still exported and erased with the account.
    """

    __tablename__ = "calls"
    __table_args__ = (
        UniqueConstraint("provider", "provider_call_id", name="call_provider_id"),
        Index("ix_calls_strategy_created", "strategy_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    # NULL only for an inbound call from a number that matches none of the
    # account's leads (migration 0089).
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=True
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    # Voice twin (migration 0089). outbound | inbound
    direction: Mapped[str] = mapped_column(
        String(10), default="outbound", server_default="outbound", nullable=False
    )
    # True when the account owner's own cloned voice spoke on this call.
    voice_clone_used: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    # Inbound: the caller's number (E.164). Personal data: erased with the lead.
    from_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # The provider's live-call control URL (Vapi monitor.controlUrl). A
    # capability URL: never returned by the API, redacted from exports.
    control_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider: Mapped[str] = mapped_column(String(20))       # vapi | elevenlabs
    provider_call_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    to_number: Mapped[str] = mapped_column(String(40))
    # queued | ringing | in_progress | ended | failed
    status: Mapped[str] = mapped_column(
        String(20), default="queued", server_default="queued", nullable=False
    )
    outcome: Mapped[CallOutcome | None] = mapped_column(_enum(CallOutcome), nullable=True,
                                                        index=True)
    # {first_message, objective, talking_points, objection_handling,
    #  questions, close}
    script_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    voicemail_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    voicemail_audio_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    recording_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    ended_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # {outcome, summary, objections[], interest_signals[], next_step, sentiment}
    analysis_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lead: Mapped["Lead"] = relationship()


# ==========================================================================
# Feature Group 3 -- analytics & revenue intelligence (migration 0028)
# ==========================================================================


class CampaignCost(TimestampMixin, Base):
    """A cost the user records against a campaign (or the whole account).

    `strategy_id` NULL = an account-wide cost (the Apollo subscription, a
    sales tool): the revenue report spreads those across campaigns by their
    share of sends in the period, so a campaign's cost per meeting is not
    flattered just because its data bill was paid centrally. Time is a cost
    too -- `hours` is kept so "4.5 h at $60" stays legible after the fact.

    Integer cents, for the same reason as Deal.value_cents.
    """

    __tablename__ = "campaign_costs"
    __table_args__ = (
        Index("ix_campaign_costs_user_incurred", "user_id", "incurred_on"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # data | tools | ai | time | ads | other
    category: Mapped[str] = mapped_column(String(20), default="other")
    description: Mapped[str] = mapped_column(String(300), default="")
    amount_cents: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0"), nullable=False
    )
    currency: Mapped[str] = mapped_column(
        String(3), default="USD", server_default="USD", nullable=False
    )
    hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    incurred_on: Mapped[datetime] = mapped_column(Date)


class ApiUsage(Base):
    """Metered model/API spend, attributed to the campaign that caused it.

    Written by app/services/usage_meter.py when a metering scope closes (the
    pipeline run, one send, a scoring batch). `cost_micros` is millionths of
    a US dollar -- one Claude call costs fractions of a cent, and integer
    cents would round most of them to zero. The price used is the admin's
    configured estimate AT THE TIME of the call, so changing a price later
    does not rewrite history. Immutable: no updated_at.
    """

    __tablename__ = "api_usage"
    __table_args__ = (
        Index("ix_api_usage_strategy_ts", "strategy_id", "ts"),
        Index("ix_api_usage_user_ts", "user_id", "ts"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(30))       # anthropic | openai
    model: Mapped[str] = mapped_column(String(80), default="")
    purpose: Mapped[str] = mapped_column(String(40), default="other")
    calls: Mapped[int] = mapped_column(Integer, default=1)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_micros: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReplySentimentWeek(TimestampMixin, Base):
    """One campaign-week of reply classifications (Monday-start, UTC).

    Written by the weekly Beat task; the objection-spike alert compares a
    week with the one before and stamps `alerted_at` so a re-run never alerts
    twice for the same week. Only HUMAN replies are counted -- out-of-office,
    bounces and automated responses are not sentiment.
    """

    __tablename__ = "reply_sentiment_weeks"
    __table_args__ = (
        UniqueConstraint("strategy_id", "week_start", name="sentiment_strategy_week"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    week_start: Mapped[datetime] = mapped_column(Date)
    total: Mapped[int] = mapped_column(Integer, default=0)
    interested: Mapped[int] = mapped_column(Integer, default=0)
    question: Mapped[int] = mapped_column(Integer, default=0)
    objection: Mapped[int] = mapped_column(Integer, default=0)
    not_interested: Mapped[int] = mapped_column(Integer, default=0)
    unsubscribe: Mapped[int] = mapped_column(Integer, default=0)
    objection_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ==========================================================================
# Feature Group 4 -- CRM & ecosystem (migration 0029)
# ==========================================================================


class CrmConnection(TimestampMixin, Base):
    """One user's connection to an external CRM (HubSpot or Salesforce).

    Tokens are NOT here -- they live encrypted in TokenStore under the
    provider name. This row holds what is not secret and must be queryable:
    which HubSpot portal / Salesforce org the user connected (an inbound
    webhook names the portal, and this is how it finds the user), the
    Salesforce instance URL, and the sync health the settings page shows.
    """

    # "external_" keeps these apart from M9's native CRM tables (crm_*), which
    # tests/test_crm_migration.py pins to migration 0019 by prefix.
    __tablename__ = "external_crm_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="crm_connection_user_provider"),
        Index("ix_external_crm_connections_provider_account", "provider", "account_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(20))          # hubspot | salesforce
    account_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    account_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    instance_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # connected | error | revoked
    status: Mapped[str] = mapped_column(
        String(20), default="connected", server_default="connected", nullable=False
    )
    last_push_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_pull_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    settings_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class CrmSyncLink(TimestampMixin, Base):
    """A Victorae record <-> the CRM record it is synced with.

    Both directions are unique: one lead is one HubSpot contact, and one
    HubSpot contact is one lead, per user. That is what makes sync
    idempotent -- a retried push updates the linked record instead of
    creating a duplicate contact, and an inbound change finds exactly one
    local row to apply to. `local_id` has no foreign key because it points at
    a lead OR a deal (`local_type`); deleting either removes its links in
    app/services/crm_sync.py.
    """

    __tablename__ = "external_crm_links"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", "local_type", "local_id",
                         name="crm_link_local"),
        UniqueConstraint("user_id", "provider", "remote_type", "remote_id",
                         name="crm_link_remote"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(20))
    local_type: Mapped[str] = mapped_column(String(10))        # lead | deal
    local_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    remote_type: Mapped[str] = mapped_column(String(20))       # contact | deal | lead | opportunity
    remote_id: Mapped[str] = mapped_column(String(64))
    last_pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_pulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remote_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ApiKey(Base):
    """A long-lived personal API key (Zapier, Make, scripts).

    Access tokens expire in minutes, which no Zap can live with. The key is
    shown ONCE at creation; only its SHA-256 is stored, so a database leak
    does not leak working keys. `prefix` (the first characters) is kept in
    clear so the user can tell keys apart in the list.
    """

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ==========================================================================
# Feature Group 8 -- workspaces, roles, approvals, white label (migration 0030)
# ==========================================================================


class Workspace(TimestampMixin, Base):
    """A team around ONE owner's account.

    The workspace's data is its owner's data: every product, strategy, lead
    and integration keeps its existing `user_id` ownership, and a member
    acting in the workspace acts on the owner's records (app/services/auth.py
    ::get_current_user resolves that). Nothing that already scopes by
    Product.user_id had to change, which is the point -- thirteen modules
    resolve ownership that way.

    Every user has exactly one workspace they own (created on first use);
    they may be a member of any number of others.
    """

    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    # SDR campaign launches wait for a manager (the owner can turn it off).
    approval_required: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    # --- white label ---------------------------------------------------------
    white_label_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    brand_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    primary_color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    support_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    custom_domain: Mapped[str | None] = mapped_column(
        String(253), unique=True, index=True, nullable=True
    )
    domain_verification_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    domain_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ClientWorkspace(TimestampMixin, Base):
    """Part 1 Feature 11 -- one CLIENT an agency runs outreach for.

    WHY NOT `Workspace`. That is a TEAM around one owner: `owner_user_id` is
    UNIQUE and its docstring says "the workspace's data is its owner's data".
    An agency's SDRs are shared across every client; the clients are separate
    books of business. Conflating them would mean either a login per client
    (and re-inviting the same three SDRs to each) or breaking the unique
    constraint thirteen modules' ownership resolution rests on.

    So a client hangs OFF the team workspace rather than replacing it, and
    isolation is achieved by scoping -- reporting, the sending-domain pool and
    the billing view all filter by this row.
    """

    __tablename__ = "client_workspaces"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="client_workspace_slug"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(60))
    # active | paused | archived
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default=text("'active'"), nullable=False
    )
    contact_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    billing_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    billing_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # MONEY IS INTEGER CENTS, for the reason Deal.value_cents gives: a float
    # column cannot represent 0.10 exactly, and these are summed per client.
    monthly_fee_cents: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0"), nullable=False
    )
    per_meeting_fee_cents: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0"), nullable=False
    )
    currency: Mapped[str] = mapped_column(
        String(3), default="USD", server_default="USD", nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ClientSendingDomain(TimestampMixin, Base):
    """Part 1 Feature 11 -- a domain reserved for one client's outreach.

    The pool is defined over DOMAINS rather than mailbox rows because that is
    what the isolation is actually about: a client's prospects must never see
    another client's sending domain, whichever mailbox on it happens to send.

    AN EMPTY POOL MEANS NO RESTRICTION. An agency that has not set pools up
    sends exactly as it does today; the pool only ever narrows.
    """

    __tablename__ = "client_sending_domains"
    __table_args__ = (
        UniqueConstraint("client_workspace_id", "domain", name="client_sending_domain_once"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client_workspaces.id", ondelete="CASCADE"), index=True
    )
    domain: Mapped[str] = mapped_column(String(253), index=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)


class WorkspaceMember(Base):
    """A user's role in a workspace: owner | manager | sdr | viewer."""

    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="workspace_member"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WorkspaceInvitation(Base):
    """An emailed invitation. Only the token's SHA-256 is stored; the link in
    the email is the only copy of the token."""

    __tablename__ = "workspace_invitations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ==========================================================================
# Feature Group 9 -- trust & deliverability (migration 0031)
# ==========================================================================


class DeliverabilityCheck(Base):
    """One health or blacklist check of one sending domain. Append-only: the
    history is what the health chart and the "newly listed?" test read."""

    __tablename__ = "deliverability_checks"
    __table_args__ = (
        Index("ix_deliverability_checks_user_kind_checked", "user_id", "kind", "checked_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    domain: Mapped[str] = mapped_column(String(253))
    kind: Mapped[str] = mapped_column(String(20))            # health | blacklist
    source: Mapped[str] = mapped_column(String(20))          # dns | mailreach | mxtoolbox | dnsbl
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    listed_on: Mapped[list | None] = mapped_column(JSON, nullable=True)
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MailboxHealth(TimestampMixin, Base):
    """Part 1 Feature 4 -- CURRENT reputation state of one sending mailbox.

    `deliverability_checks` (FG9) stays what it is: an append-only log of
    DOMAIN checks. The domain is the wrong grain for throttling -- two
    mailboxes on one domain can have very different reputations, and only one
    of them should be slowed down. This table is one upserted row per mailbox,
    read by the send gate on every send.

    `mailbox_ref` is EXACTLY the value the send path writes to
    `messages.sender_ref` (a Gmail account id, "linkedin:<id>",
    "whatsapp:<phone id>"), which is what makes volume and complaints
    countable per mailbox. It is a string, not a foreign key, because not
    every sending identity lives in one table.
    """

    __tablename__ = "mailbox_health"
    __table_args__ = (
        UniqueConstraint("user_id", "mailbox_ref", name="mailbox_health_ref"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    mailbox_ref: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(String(320), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # healthy | throttled | paused. Indexed because the send gate and the
    # settings page both filter on it.
    state: Mapped[str] = mapped_column(
        String(20), default="healthy", server_default=text("'healthy'"),
        nullable=False, index=True
    )
    # The reduced daily allowance while throttled. NULL = no reduction.
    throttle_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    reasons_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    spf_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    dkim_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    dmarc_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    dmarc_policy: Mapped[str | None] = mapped_column(String(20), nullable=True)
    complaint_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    # F03 (migration 0072): where complaint_rate came from -- "postmaster"
    # (Google Postmaster Tools, the real Gmail spam rate) or "proxy". NULL is
    # "proxy": every row written before 0072 or with the Autopilot off.
    complaint_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    bounce_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    sends_today: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    sends_7d: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    daily_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Resuming after a reputation pause is a HUMAN decision, same rule as the
    # bounce pause and the blacklist pause -- so who did it is recorded.
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resumed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ComplianceAuditLog(Base):
    """What Victorae knew and decided, per send, about the rules that apply
    to its recipient -- region, regime, the checks made and the outcome.
    Append-only. Survives a GDPR erase with the lead reference nulled, so the
    fact that a lawful decision was made outlives the personal data."""

    __tablename__ = "compliance_audit_log"
    __table_args__ = (
        Index("ix_compliance_audit_log_user_ts", "user_id", "ts"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(20))
    region: Mapped[str | None] = mapped_column(String(10), nullable=True)
    regime: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # sent | blocked_suppressed | skipped_no_consent | ...
    decision: Mapped[str] = mapped_column(String(30))
    checks_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ToolIntegrationSettings(TimestampMixin, Base):
    """Per-tool key/value config for AegisAudit, PostIQ and SIGNALFORGE
    (migration 0032). user_id NULL is a workspace-level (admin) setting;
    otherwise the row belongs to that user (e.g. their PostIQ Web App URL).
    Secrets are Fernet-encrypted and flagged by is_encrypted -- see
    app/services/tool_integrations.py."""

    __tablename__ = "tool_integration_settings"
    __table_args__ = (
        UniqueConstraint("user_id", "tool_name", "config_key", name="uq_tool_integration"),
        # NULLs are distinct in the constraint above, so workspace-level rows
        # need their own partial unique index.
        Index("uq_tool_integration_workspace", "tool_name", "config_key", unique=True,
              postgresql_where=text("user_id IS NULL"),
              sqlite_where=text("user_id IS NULL")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    tool_name: Mapped[str] = mapped_column(String(64))       # aegisaudit | postiq | signalforge
    config_key: Mapped[str] = mapped_column(String(128))
    config_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_encrypted: Mapped[bool] = mapped_column(Boolean, default=False)


# --------------------------------------------------------------------------
# Website builder (migration 0038)
# --------------------------------------------------------------------------


def _checked_enum(e: type[enum.Enum], length: int) -> Enum:
    """VARCHAR-backed enum at an EXACT length.

    `_enum` above hardcodes length=32. These three columns are pinned to the
    widths migration 0038 declares (30 / 20 / 20), and
    tests/test_website_builder_migration.py diffs the two -- so the helper
    takes the length rather than the column silently widening.
    """
    return Enum(e, native_enum=False, length=length,
                values_callable=lambda x: [i.value for i in x])


class SitePage(Base):
    """One AI-generated, SEO-optimised marketing page.

    This is the whole CMS. A page is generated by Claude
    (app/services/website_builder.py), stored here as a complete HTML5
    document, scored deterministically against on-page SEO rules, and
    exported to static files. There is no Webflow, no WordPress and no
    external service in the path -- which is the point: the pages that sell
    the product cannot depend on a vendor the product does not control.

    NOT a TimestampMixin table. It carries four different "when"s that each
    mean something specific -- created_at, last_generated_at, published_at,
    and generation_version -- and a generic updated_at that moves when any
    column changes would be a fifth, vaguer answer that nothing reads.

    `slug` IS the public URL path, so it is UNIQUE at the schema level: two
    rows claiming the same path have no defined answer for what gets served,
    and the static export writes one file per slug.

    `workspace_id` NULL means a global/admin page -- the leadpilot.io
    marketing site itself, which belongs to no customer workspace.
    """

    __tablename__ = "site_pages"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_site_pages_slug"),
        # The list screen filters on both, always together.
        Index("ix_site_pages_status_type", "status", "page_type"),
        Index("ix_site_pages_target_keyword", "target_keyword"),
        CheckConstraint(
            "page_type IN ('landing', 'blog', 'case_study', 'comparison', 'location')",
            name="ck_site_pages_page_type"),
        CheckConstraint("status IN ('draft', 'published', 'archived')",
                        name="ck_site_pages_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    page_type: Mapped[PageType] = mapped_column(_checked_enum(PageType, 30))
    slug: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200))
    # 70 and 165 are not arbitrary: they are where Google truncates a title
    # and a description in the SERP. A column that allows more allows a page
    # to ship with an ellipsis where its call to action should be.
    meta_title: Mapped[str] = mapped_column(String(70))
    meta_description: Mapped[str] = mapped_column(String(165))
    target_keyword: Mapped[str] = mapped_column(String(200))
    # The content brief the page was generated from. Kept so a regenerate
    # reproduces the same page rather than unrelated copy at the same URL --
    # see migration 0038's note on why this is not in the original spec.
    brief: Mapped[str] = mapped_column(Text, default="", server_default="",
                                       nullable=False)
    secondary_keywords: Mapped[list] = mapped_column(
        JSON, default=list, server_default=text("'[]'"), nullable=False
    )
    # The complete HTML5 document, head and all. Empty string until the
    # generation task has run -- NOT NULL, because "no page yet" is the empty
    # document, and a NULL here would reach the export as the string "None".
    html_content: Mapped[str] = mapped_column(Text, default="")
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reading_time_mins: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schema_markup_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    internal_links_json: Mapped[list] = mapped_column(
        JSON, default=list, server_default=text("'[]'"), nullable=False
    )
    status: Mapped[PageStatus] = mapped_column(
        _checked_enum(PageStatus, 20), default=PageStatus.DRAFT,
        server_default="draft", nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Bumped on every regenerate, so a page that was rewritten four times is
    # distinguishable from one generated once.
    generation_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )
    seo_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seo_issues_json: Mapped[list] = mapped_column(
        JSON, default=list, server_default=text("'[]'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    assets: Mapped[list["SiteAsset"]] = relationship(
        back_populates="page", lazy="select", cascade="all, delete-orphan"
    )


class SiteAsset(Base):
    """An image, stylesheet or script belonging to one page.

    Stored in the database rather than on disk so a page is a single
    self-contained row set: the export writes it out, and nothing depends on
    a filesystem that a container restart discards.
    """

    __tablename__ = "site_assets"
    __table_args__ = (
        CheckConstraint("asset_type IN ('image', 'css', 'js')",
                        name="ck_site_assets_asset_type"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("site_pages.id", ondelete="CASCADE"), index=True
    )
    asset_type: Mapped[AssetType] = mapped_column(_checked_enum(AssetType, 20))
    filename: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    page: Mapped["SitePage"] = relationship(back_populates="assets")


# --------------------------------------------------------------------------
# Identity, location and phone verification (migration 0039)
# --------------------------------------------------------------------------


class PhoneVerificationCode(Base):
    """One SMS one-time code sent to one number for one account.

    Only an HMAC of the code is stored (keyed by the JWT secret and bound to
    the user and the number), so a database read does not yield live codes and
    a code proved for one number cannot verify another. `attempts` counts wrong
    guesses against THIS code; past PHONE_OTP_MAX_ATTEMPTS it is dead even if
    the right code arrives later. No updated_at: the only mutations are the
    attempt counter and `consumed_at`, each recorded precisely.
    """

    __tablename__ = "phone_verification_codes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    phone_number: Mapped[str] = mapped_column(String(20))
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AccountSecurityEvent(Base):
    """Append-only account risk and verification audit (migration 0039).

    What the identity flow and the network guard observed and decided: a
    declared-vs-detected country mismatch, a signup or login refused for
    coming through a VPN, a phone verified, an admin's review decision.
    `user_id` is nullable because a blocked SIGNUP has no account yet --
    `email` carries who tried. Survives the user's deletion with the id
    nulled, like compliance_audit_log.
    """

    __tablename__ = "account_security_events"
    __table_args__ = (
        Index("ix_account_security_events_event_ts", "event", "ts"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    event: Mapped[str] = mapped_column(String(40))
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------------
# Billing (migration 0040) -- Section E
# --------------------------------------------------------------------------


class BillingSubscription(TimestampMixin, Base):
    """How one account pays: a monthly tier or pay-per-meeting.

    One row per account (UNIQUE user_id); switching plans updates it. Stripe
    is the source of truth for money and this row mirrors it -- statuses are
    Stripe's own (incomplete | trialing | active | past_due | canceled), set
    from its webhooks. `is_stub` marks a row activated WITHOUT Stripe (no
    STRIPE_SECRET_KEY configured), so a stub subscription can never be
    mistaken for a paying customer in a report.
    """

    __tablename__ = "billing_subscriptions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # RESTRICT, not CASCADE (migration 0081): billing rows are kept, anonymised,
    # when an account is erased (decision E1, docs/features/gdpr-erasure.md);
    # deleting the users row must not take them with it.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), unique=True, index=True
    )
    billing_model: Mapped[str] = mapped_column(String(20))   # monthly | pay_per_meeting
    tier: Mapped[str] = mapped_column(String(20))            # starter..enterprise | pay_per_meeting
    status: Mapped[str] = mapped_column(
        String(20), default="incomplete", server_default="incomplete", nullable=False
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_stub: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    # Migration 0081 (docs/features/annual-billing.md). When the current trial
    # began (Stripe `trial_start`; stub checkout time) -- a trial longer than
    # billing_catalog.LONG_TRIAL_DAYS gets a trial-ending notice.
    trial_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set once the account has had ANY trial, so a replaced subscription (whose
    # trial dates are cleared) never earns a second one.
    trial_used: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    # Affirmative auto-renewal consent, recorded when the checkout that carried
    # it COMPLETES (never on an abandoned one): when, which disclosure wording
    # (billing_catalog.DISCLOSURE_VERSION), the price and interval agreed to,
    # and the Stripe Checkout session (NULL for a stub checkout).
    renewal_consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consent_disclosure_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    consent_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    consent_interval: Mapped[str | None] = mapped_column(String(10), nullable=True)
    consent_checkout_session_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    # What Stripe actually bills (items.data[0].price from the subscription
    # webhook) -- the amount every renewal notice states. NULL until known.
    price_unit_amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_interval: Mapped[str | None] = mapped_column(String(10), nullable=True)
    price_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    # Something a person must resolve (e.g. a second subscription completed
    # while an annual year was paid: both are kept). Also a task_errors row.
    support_note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class BillingRenewalNotice(TimestampMixin, Base):
    """One auto-renewal notice to an account owner (migration 0081).

    kind: acknowledgment (an annual plan started) | trial_end (a trial that
    ends in a charge, 7 days ahead) | renewal (an annual renewal, 30 days
    ahead). UNIQUE (subscription_id, kind, period_key) is the idempotency key:
    the sweep INSERTs the row (status `sending`) BEFORE it sends, exactly like
    pilot_scorecards, so a renewal is noticed once however often the sweep
    runs. The row is also the record that the notice went out (no body kept).
    """

    __tablename__ = "billing_renewal_notices"
    __table_args__ = (
        UniqueConstraint("subscription_id", "kind", "period_key",
                         name="uq_billing_renewal_notice"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Explicit name: the naming convention's
    # fk_billing_renewal_notices_subscription_id_billing_subscriptions is 64
    # characters, one over PostgreSQL's 63-byte identifier limit.
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("billing_subscriptions.id", ondelete="CASCADE",
                   name="fk_billing_renewal_notices_subscription_id")
    )
    kind: Mapped[str] = mapped_column(String(20))
    period_key: Mapped[str] = mapped_column(String(120))
    charge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    days_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # sending | sent | failed
    status: Mapped[str] = mapped_column(
        String(20), default="sending", server_default=text("'sending'"), nullable=False
    )
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # When a stuck (`sending`) or given-up notice was raised to the admins
    # (ERROR log + a task_errors row) -- once, not every sweep.
    escalated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class BillableMeeting(TimestampMixin, Base):
    """One booked meeting a pay-per-meeting account owes for (migration 0040).

    Written by usage_meter.record_meeting_usage the moment a BOOKED outcome is
    flushed for a lead owned by a pay-per-meeting account. UNIQUE
    (user_id, dedupe_key) with dedupe_key = the lead id is the "once per
    prospect" rule enforced by the schema: a prospect who books, cancels and
    rebooks is one charge, never two.

    LIFECYCLE
      pending   inside the cancellation grace window (charge_after)
      charged   invoiced through Stripe (is_stub: marked charged without Stripe)
      waived    the meeting was cancelled before charge_after, or an admin
                upheld a dispute
      disputed  the customer disputed it inside the grace window; an admin decides
      failed    Stripe refused the charge (failure_reason)

    `lead_id`/`strategy_id` are SET NULL on delete so the billing record
    outlives a GDPR erase of the prospect -- an invoice must stay explicable.
    """

    __tablename__ = "billable_meetings"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="billable_meeting_once"),
        Index("ix_billable_meetings_status_charge_after", "status", "charge_after"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    # RESTRICT (migration 0081): see BillingSubscription.user_id.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True
    )
    outcome_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(80))
    # the BOOKED outcome's channel: calendly | leadpilot_calendar | ...
    source: Mapped[str] = mapped_column(String(20))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="usd", server_default="usd")
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    charge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    charged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stripe_invoice_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stripe_invoice_item_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_stub: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String(500), nullable=True)


# --------------------------------------------------------------------------
# Feature A1: fabrication-proof claim engine (migration 0041)
# --------------------------------------------------------------------------


class ClaimVerificationLog(Base):
    """One claim an AI-written message made about a prospect, and what the
    claim engine (app/services/claim_verification.py) decided about it.

    Append-only audit: verified claims are logged with the evidence that
    supported them (source + excerpt), stripped/rewritten ones with the tokens
    that could not be found and the replacement text, so "why did my email lose
    its opening line?" always has an answer. Written in the same transaction as
    the rendered message it describes. `lead_id`/`message_id` are SET NULL on
    delete: the record that a claim was refused outlives the prospect's data.
    """

    __tablename__ = "claim_verification_log"
    __table_args__ = (
        Index("ix_claim_verification_log_lead_created", "lead_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(20))
    # subject | body | first_message | voicemail | variable:<n>
    field: Mapped[str] = mapped_column(String(30))
    # funding | headcount | hiring | job_change | news | metrics | expansion |
    # their_content | other
    category: Mapped[str] = mapped_column(String(30))
    claim_text: Mapped[str] = mapped_column(Text)
    verdict: Mapped[str] = mapped_column(String(20))     # verified | stripped | rewritten
    extractor: Mapped[str] = mapped_column(String(10))   # rules | model
    evidence_source: Mapped[str | None] = mapped_column(String(300), nullable=True)
    evidence_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    unsupported_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    replacement_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------------
# Feature A2: tamper-evident send/reply audit trail (migration 0042)
# --------------------------------------------------------------------------


class ActivityAuditEvent(Base):
    """One send / open / click / reply / meeting-booked event, hash-chained.

    APPEND-ONLY AND TAMPER-EVIDENT (app/services/audit_trail.py):
      content_hash  sha256 of the row's canonical content, fixed at insert
      prev_hash     the previous sealed record's chain_hash for this account
      chain_hash    sha256(prev_hash + content_hash)
      seq_no        position in the account's chain (1, 2, 3 ...)
    Changing, inserting or deleting any sealed record breaks every chain_hash
    after it, which `verify_chain` detects and a signed export proves.

    Events are recorded at insert time (in the same transaction as the Outcome
    that caused them) and SEALED into the chain by a separate step, so two
    concurrent sends never race for the same seq_no inside a business
    transaction -- the sealer serialises per account instead.

    NO FOREIGN KEYS on the *_ref columns, deliberately: an FK with ON DELETE
    SET NULL would silently rewrite hashed content when a lead is erased, and
    the chain would then "prove" tampering that never happened. The refs are
    opaque ids and the payload carries no personal data, so the trail outlives
    a GDPR erase intact. PostgreSQL additionally refuses DELETE and content
    UPDATEs with a trigger (migration 0042); the ORM refuses them everywhere.
    """

    __tablename__ = "activity_audit_events"
    __table_args__ = (
        UniqueConstraint("user_id", "seq_no", name="audit_event_user_seq"),
        UniqueConstraint("outcome_ref", name="audit_event_outcome"),
        Index("ix_activity_audit_events_user_created", "user_id", "created_at"),
        Index("ix_activity_audit_events_lead_ref", "lead_ref"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    lead_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    strategy_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    message_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    outcome_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    event: Mapped[str] = mapped_column(String(20))      # sent | opened | clicked | replied | booked
    channel: Mapped[str] = mapped_column(String(20))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    seq_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chain_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------------
# Feature A4: cross-channel conversation + stagnation (migration 0044)
# --------------------------------------------------------------------------


class ChannelSuggestion(TimestampMixin, Base):
    """"This lead has gone quiet on email -- try LinkedIn."

    Written by the stagnation step (app/pipeline/channel_orchestrator.py) when
    a lead has had N sends on one channel with no genuine reply. The unified
    conversation thread itself is NOT a table: it is assembled from the
    source-of-truth rows (messages, inbound_replies, calls, bookings, meetings)
    by app/services/conversation_thread.py, so it can never disagree with them.
    This table holds the one thing those rows cannot: the recommendation and
    what was decided about it.

    LIFECYCLE
      suggested      waiting for a person
      accepted       a person switched the lead's next scheduled message
      auto_switched  the system did (system setting stagnation_auto_switch_enabled)
      dismissed      a person declined
    """

    __tablename__ = "channel_suggestions"
    __table_args__ = (
        Index("ix_channel_suggestions_lead_status", "lead_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    enrollment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sequence_enrollments.id", ondelete="SET NULL"), nullable=True
    )
    from_channel: Mapped[str] = mapped_column(String(20))
    to_channel: Mapped[str] = mapped_column(String(20))
    sends_without_reply: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(
        String(20), default="suggested", server_default="suggested", nullable=False
    )
    switched_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --------------------------------------------------------------------------
# Feature A6: client-facing shareable ROI dashboard (migration 0046)
# --------------------------------------------------------------------------


class ShareLink(TimestampMixin, Base):
    """A signed, revocable, expiring link to a read-only ROI dashboard.

    The link is the credential -- whoever holds it sees the dashboard, with no
    login -- so it is handled like an API key: the token is shown ONCE at
    creation and only its SHA-256 is stored (a database read yields no working
    link); `token_prefix` lets the owner tell links apart. Every link EXPIRES
    (expires_at is NOT NULL) and can be REVOKED at once. `strategy_id` NULL
    scopes it to the whole account (the workspace), a value to one campaign.
    """

    __tablename__ = "share_links"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=True
    )
    label: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_prefix: Mapped[str] = mapped_column(String(12))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    view_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )


# --------------------------------------------------------------------------
# Feature A7: pre-send adversarial review (migration 0047)
# --------------------------------------------------------------------------


class SequenceReview(TimestampMixin, Base):
    """One red-team pass over a sequence's content before it may launch.

    `content_hash` is a SHA-256 of everything that decides what a lead will
    receive (every step's channel, brief, WhatsApp template and variable
    mapping, the booking link). A review -- and an override of it -- applies
    only while the hash still matches: editing the copy after an override
    brings the gate back, so an override can never be used to wave through
    content nobody reviewed.

    status: passed | blocked | overridden. An override keeps the findings and
    records who, when and why (also written to account_security_events).
    """

    __tablename__ = "sequence_reviews"
    __table_args__ = (
        Index("ix_sequence_reviews_sequence_created", "sequence_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    sequence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sequences.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    findings_json: Mapped[list] = mapped_column(JSON, default=list)
    blocking_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    warning_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    # rules | rules+model
    reviewer: Mapped[str] = mapped_column(String(20))
    overridden_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    overridden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --------------------------------------------------------------------------
# Feature 5 — opt-in post-sequence re-engagement (migration 0048)
# --------------------------------------------------------------------------


class SendReview(TimestampMixin, Base):
    """Part 1 Feature 5 -- one message held for explicit human approval.

    SequenceReview (0047) reviews a sequence's CONTENT before launch, once per
    sequence. This is a different thing: one row per MESSAGE, created at send
    time, because the triggers are facts about the prospect at that moment
    (they objected last week, their deal stalled, they are a VP) that no
    pre-launch review of a template could know.

    The copy is SNAPSHOTTED here rather than read back from `messages`: the
    reviewer is approving specific words, and a later re-render must not
    change what was approved. `content_hash` is what the send path checks
    before transmitting.
    """

    __tablename__ = "send_reviews"
    __table_args__ = (
        UniqueConstraint("message_id", name="send_review_message"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE")
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # pending | approved | rejected
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default=text("'pending'"),
        nullable=False, index=True
    )
    # [{code, label, detail}] -- why this message needs a person.
    triggers_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    subject_snapshot: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # A reviewer may fix the copy instead of rejecting it. When set, THIS is
    # what gets sent -- the whole point of a human gate is that the human can
    # improve the message, not only veto it.
    edited_subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    edited_body: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReengagementAttempt(TimestampMixin, Base):
    """The claim that makes re-engagement once-per-enrollment, structurally.

    UNIQUE (enrollment_id) is the whole idempotency story, in the same spirit
    as research_steps' (strategy_id, pipeline, step_no): a retried task, two
    overlapping sweeps, or a worker killed after the insert can never produce a
    second re-engagement for the same enrollment, because the second INSERT
    fails. The row is written BEFORE any message exists.

    It is the claim, not the record of what was sent -- the message row is
    that. `status` is claimed | scheduled | sent | held, and `detail` carries
    send_message_impl's result (e.g. deferred_window) for the audit trail.
    """

    __tablename__ = "reengagement_attempts"
    __table_args__ = (
        UniqueConstraint("enrollment_id", name="reengagement_enrollment"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sequence_enrollments.id", ondelete="CASCADE")
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20))
    detail: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ConsentEvent(Base):
    """Part 1 Feature 9 -- append-only record of every change of consent.

    WHY THIS IS NOT ComplianceAuditLog. That table answers "on what basis did
    you SEND this message?", one row per send decision. This answers the
    question a regulator or a prospect actually asks: "when did they tell you
    to stop, and what did you do about it?" Different lifetime, different
    retention, different cardinality.

    WHY lead_id IS SET NULL. The point of the erasure event is that it
    survives the erasure. A ledger that deletes itself with the prospect
    record cannot prove the request was honoured -- the one moment it exists
    for. `identifier` is stored on the row for the same reason: after the lead
    is gone, the suppression still has to be matchable.

    No updated_at: append-only, like `outcomes`.
    """

    __tablename__ = "consent_events"
    __table_args__ = (
        Index("ix_consent_events_user_ts", "user_id", "ts"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # granted | withdrawn | suppressed | erased
    kind: Mapped[str] = mapped_column(String(30))
    # email | phone | linkedin | whatsapp | all
    channel: Mapped[str] = mapped_column(String(20))
    identifier: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    region: Mapped[str | None] = mapped_column(String(10), nullable=True)
    regime: Mapped[str | None] = mapped_column(String(40), nullable=True)
    basis: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # unsubscribe_link | reply | whatsapp_stop | manual | gdpr_request |
    # bounce | import | webhook
    source: Mapped[str] = mapped_column(String(40))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    meta_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AttributionEntry(TimestampMixin, Base):
    """Part 1 Feature 8 -- which touch earned this outcome, and how sure we are.

    WHY NOT A COLUMN ON `outcomes`. That table is the immutable event log the
    learning loop reads. Its `message_id` is the message that CAUSED the event
    only for events the send path writes itself (sent, opened, clicked); a
    booking arriving by webhook three days later has no message_id and no way
    to get one at write time. Attribution is a separate, LATER, re-computable
    judgement -- and one that has to record its own uncertainty, which an
    immutable log row cannot.

    THE METHOD AND CONFIDENCE ARE THE POINT. "This meeting came from step 2 on
    LinkedIn" is worth very different amounts depending on whether the prospect
    literally replied to that message or whether it was simply the last thing
    sent before they booked. A ledger that hides which of those happened will
    eventually be believed when it should not be.
    """

    __tablename__ = "attribution_entries"
    __table_args__ = (
        UniqueConstraint("outcome_kind", "outcome_id", name="attribution_outcome"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # meeting_booked | positive_reply | won
    outcome_kind: Mapped[str] = mapped_column(String(30))
    outcome_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    outcome_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # The credited touch. NULL with method="none" is an honest answer: an
    # inbound booking from someone we never messaged has no touch to credit.
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    step_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    hours_to_outcome: Mapped[float | None] = mapped_column(Float, nullable=True)
    # direct_reply | thread_match | last_touch | none
    method: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # The copy as it went out, kept so "which message earned this?" can be
    # answered by SHOWING it -- the message row can be re-rendered or deleted.
    subject_snapshot: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReengagementPlan(TimestampMixin, Base):
    """Part 1 Feature 7 -- a "not now" turned into a dated return visit.

    WHY NOT `reengagement_attempts` (0048). That table is the
    once-per-enrollment CLAIM for the opt-in post-sequence sweep; its whole
    purpose is the UNIQUE(enrollment_id) that stops a retry sending twice, and
    it carries no reason and no future date. This object has a different
    lifetime: it is created by a REPLY, it outlives the enrollment and even
    the sequence that produced it, a person can move or cancel it, and it is
    where the prospect's own words live.

    THE REASON IS STORED TWICE ON PURPOSE. `reason_kind` is what the UI groups
    and filters by; `reason_text` is what the follow-up message quotes. A hook
    built on their sentence survives being read back to them nine months
    later. A paraphrase does not.

    UNIQUE (source_reply_id): a webhook retry, a re-classification or two
    overlapping sweeps cannot produce two plans for one reply.
    """

    __tablename__ = "reengagement_plans"
    __table_args__ = (
        UniqueConstraint("source_reply_id", name="reengagement_plan_reply"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True
    )
    source_reply_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("inbound_replies.id", ondelete="SET NULL"), nullable=True
    )
    # budget | contract | timing | project | headcount | priority | unspecified
    reason_kind: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reason_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # A date the PROSPECT named ("after Q2", "in January"). NULL = they gave
    # no date, and the default interval was used instead -- two different
    # facts, and the UI says which.
    stated_return_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # scheduled | due | sent | cancelled
    status: Mapped[str] = mapped_column(
        String(20), default="scheduled", server_default=text("'scheduled'"),
        nullable=False, index=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    outcome: Mapped[str | None] = mapped_column(String(200), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)


# --------------------------------------------------------------------------
# Feature 6 — anonymised benchmarks (migration 0050)
# --------------------------------------------------------------------------


class BenchmarkBucket(Base):
    """One published benchmark: a metric's spread across ACCOUNTS for one
    (industry, channel) in the trailing window.

    Rows exist ONLY for buckets that cleared the minimum-accounts threshold.
    A suppressed bucket is never written, so nothing downstream -- API, admin
    query, export -- can read a number built from too few accounts. The nightly
    job replaces the whole table in one transaction.

    No user id, strategy id or raw count lives here: percentiles (rounded) and
    an account count only. See app/services/benchmarks.py.
    """

    __tablename__ = "benchmark_buckets"
    __table_args__ = (
        UniqueConstraint("industry", "channel", "metric", name="benchmark_bucket"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    industry: Mapped[str] = mapped_column(String(120))   # "*" = all industries
    channel: Mapped[str] = mapped_column(String(20))
    metric: Mapped[str] = mapped_column(String(20))      # reply_rate | meeting_rate | bounce_rate
    account_count: Mapped[int] = mapped_column(Integer)
    p25: Mapped[float] = mapped_column(Float)
    p50: Mapped[float] = mapped_column(Float)
    p75: Mapped[float] = mapped_column(Float)
    window_days: Mapped[int] = mapped_column(Integer)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------
# Feature 8 — configurable compliance rules (migration 0051)
# --------------------------------------------------------------------------


class ComplianceRule(TimestampMixin, Base):
    """One override of the send-time compliance baseline.

    `scope` is "global" or a workspace id (as a string), so UNIQUE (scope,
    region, channel) holds for global rows too -- a nullable workspace_id in
    the key would let two "global" rows for the same region coexist, because
    NULLs never collide. workspace_id is kept alongside for the cascade.

    Every rule field is NULLABLE = "not set here, inherit". The resolution,
    the hard bounds and the fail-closed rules live in
    app/services/compliance_rules.py -- this table only stores intent.
    """

    __tablename__ = "compliance_rules"
    __table_args__ = (
        UniqueConstraint("scope", "region", "channel", name="compliance_rule_scope"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    scope: Mapped[str] = mapped_column(String(40))
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    region: Mapped[str] = mapped_column(String(10))     # us|ca|eu|uk|sg|au|nz|other|unknown|*
    channel: Mapped[str] = mapped_column(String(20))    # email|whatsapp|linkedin|phone|*
    send_start_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    send_end_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skip_weekends: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    daily_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    consent_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    bounce_pause_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


# --------------------------------------------------------------------------
# Persistent sign-in sessions + security audit log (migration 0052)
# --------------------------------------------------------------------------


class AuthSession(Base):
    """One refresh token, as the server remembers it.

    The row id IS the refresh JWT's `jti`, so a token can be revoked (logout)
    and a replayed one recognised. Every refresh ROTATES: this row is marked
    revoked_reason="rotated" and a new row in the same `family_id` replaces
    it. Presenting a rotated token again after the grace window is the
    signature of a stolen token, and revokes the whole family -- the thief and
    the victim are both signed out, and the victim signs back in.

    The raw token is never stored: it is a signed JWT whose only secret part is
    the signature, and the jti alone cannot be turned back into one.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True, nullable=False)
    # False = "keep me signed in" was unticked (short server-side lifetime and
    # a browser-session cookie).
    persistent: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # rotated | logout | reuse_detected
    revoked_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Claude / ChatGPT connector (migration 0091, DECISIONS.md CONN-AS). Set
    # on sessions issued by the OAuth token endpoint: the registered client
    # (oauth_clients.client_id) and the scopes granted. NULL = a web/app
    # sign-in, exactly what every row before the connector is. A connector
    # session's refresh token is typ="mcp_refresh", so /auth/refresh refuses
    # it and the OAuth token endpoint refuses web refresh tokens.
    oauth_client_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    scope: Mapped[str | None] = mapped_column(String(200), nullable=True)


class OAuthClient(Base):
    """An OAuth client registered by RFC 7591 Dynamic Client Registration --
    in practice one per Claude or ChatGPT connection.

    Public clients only (token_endpoint_auth_method "none", PKCE S256
    required). There is no owner: registration is anonymous, and the person
    is bound later, at consent, to the authorization code and the session.
    Rows are kept while their sessions are in use; ChatGPT reuses its
    registered client for the life of a connection.
    """

    __tablename__ = "oauth_clients"

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    client_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Exact strings as registered; every one passed the allowlist in
    # app/services/oauth_server.py::redirect_uri_allowed.
    redirect_uris: Mapped[list] = mapped_column(JSON, nullable=False)
    scope: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OAuthAuthorizationCode(Base):
    """One /oauth/authorize request, from "pending consent" to "code used".

    Created unauthenticated (the client's redirect), shown on the consent page
    by its opaque id, bound to the signed-in user when they approve. The code
    itself is stored only as a SHA-256 hash, is single-use (used_at, claimed
    atomically) and short-lived; a second redemption revokes the session
    family the first one started (session_family_id).
    """

    __tablename__ = "oauth_authorization_codes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # oauth_clients.client_id, matched in code (app/services/oauth_server.py
    # looks the client up on every use). No foreign key: this is an
    # account-owned table and oauth_clients is not, and clients are never
    # deleted while a connection may still use them.
    client_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    scope: Mapped[str] = mapped_column(String(200), nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    # The client's opaque `state`, echoed back on the redirect.
    state: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # approved | denied; NULL = waiting for the person on the consent page.
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    code_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_family_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SecurityAuditEvent(Base):
    """Security-relevant account actions: destructive deletes, denied password
    confirmations, refresh-token reuse, sign-outs.

    Distinct from ActivityAuditEvent (the hash-chained send/reply trail a buyer
    verifies) -- this one answers "who did what to this account, and when".
    NO FOREIGN KEYS, on purpose: the record of a deletion must outlive both the
    deleted row and, if it comes to that, the account that deleted it.

    user_id is the person who signed in (the ACTOR); owner_user_id is whose
    data it was, which differs when a workspace member acts on the owner's
    records (app/services/auth.py::_workspace_principal).
    """

    __tablename__ = "security_audit_events"
    __table_args__ = (
        Index("ix_security_audit_events_user_created", "user_id", "created_at"),
        Index("ix_security_audit_events_target", "target_type", "target_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # strategy.deleted | strategy.delete_denied | auth.logout | auth.refresh_reuse_detected
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------------
# Private LinkedIn network (migration 0068)
# --------------------------------------------------------------------------
#
# Each row belongs to the ONE account that uploaded it (user_id) and is only
# ever read back for that account. There is deliberately no sharing between
# accounts: the people in a connections export are third parties who have not
# agreed to be shown to anyone else. Email addresses and phone numbers in the
# export are never stored. user_id is ON DELETE CASCADE everywhere so erasing an
# account erases its network.


class LinkedInImport(TimestampMixin, Base):
    __tablename__ = "linkedin_imports"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    rows_in_file: Mapped[int] = mapped_column(Integer, default=0)
    added: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)


class LinkedInConnection(TimestampMixin, Base):
    __tablename__ = "linkedin_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_linkedin_conn_user_dedupe"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    dedupe_key: Mapped[str] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(300))
    company: Mapped[str | None] = mapped_column(String(300), nullable=True)
    position: Mapped[str | None] = mapped_column(String(300), nullable=True)
    profile_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    connected_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class LinkedInConnectionMatch(TimestampMixin, Base):
    __tablename__ = "linkedin_connection_matches"
    __table_args__ = (
        UniqueConstraint("connection_id", "product_id", name="uq_linkedin_match_conn_product"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("linkedin_connections.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    score: Mapped[int] = mapped_column(Integer)
    segment: Mapped[str] = mapped_column(String(120))
    reasons_json: Mapped[list] = mapped_column(JSON, default=list)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LinkedInNetworkMessage(TimestampMixin, Base):
    """One message the user is writing to (or has sent to) one of THEIR OWN
    LinkedIn connections. The text is whatever the user last saved; nothing
    is ever sent from a draft. Deleted with the connection / the account."""

    __tablename__ = "linkedin_network_messages"
    __table_args__ = (
        UniqueConstraint("user_id", "connection_id", name="uq_linkedin_msg_user_conn"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("linkedin_connections.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    # draft -> approved -> (queued ->) sent | failed
    status: Mapped[str] = mapped_column(String(20), default="draft")
    channel: Mapped[str | None] = mapped_column(String(20), nullable=True)  # copy|unipile|bulk
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --------------------------------------------------------------------------
# Private email network (migration 0090, docs/features/email-network.md)
# --------------------------------------------------------------------------
#
# The LinkedIn network's twin for the people a user already emails. Same
# privacy boundary: every row belongs to the ONE account that imported it and
# is only read back for that account (no shared pool). DATA MINIMISATION: a
# contact row holds who (name, address, company, title), how often (counts),
# how recently (first/last seen) and whether it was two-way -- never a
# subject, a body, a Message-ID, an attachment or a signature. People the
# filters exclude (newsletters, role inboxes, personal domains, colleagues,
# suppressed / excluded people) are never stored; only counts by reason are.


class EmailNetworkImport(TimestampMixin, Base):
    """One upload (contacts file / mailbox export) or one Gmail scan.

    tally_json is TRANSIENT: a Gmail scan runs in chunks, and the running
    per-correspondent tallies (address, name, counts, dates) sit here only
    until the scan finishes, then it is cleared. Addresses already excluded by
    a fixed rule are held only as hashes, for counting unique people."""

    __tablename__ = "email_network_imports"
    __table_args__ = (
        # One ACTIVE Gmail scan per account (review round): two concurrent
        # "Scan my Gmail" requests cannot both start one.
        Index("uq_email_network_imports_active_scan", "user_id", unique=True,
              sqlite_where=text("kind = 'gmail_scan' AND status IN ('queued', 'running')"),
              postgresql_where=text("kind = 'gmail_scan' AND status IN ('queued', 'running')")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # google_csv | outlook_csv | csv | vcard | mbox | gmail_scan
    kind: Mapped[str] = mapped_column(String(20))
    # done | queued | running | failed | reconnect_required
    status: Mapped[str] = mapped_column(String(30), default="done")
    attested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attestation_version: Mapped[str | None] = mapped_column(String(10), nullable=True)
    rows_in_file: Mapped[int] = mapped_column(Integer, default=0)
    messages_scanned: Mapped[int] = mapped_column(Integer, default=0)
    people_found: Mapped[int] = mapped_column(Integer, default=0)
    added: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    excluded: Mapped[int] = mapped_column(Integer, default=0)
    excluded_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cap_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    page_token: Mapped[str | None] = mapped_column(String(500), nullable=True)
    chunk_seq: Mapped[int] = mapped_column(Integer, default=0)
    tally_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmailContact(TimestampMixin, Base):
    """One person the account emails or has in its address book. Counts are
    kept per source in source_counts_json ({"mbox": {...}, "gmail": {...}})
    and combined by MAX, not SUM, so a mailbox file and a Gmail scan of the
    same mailbox are not counted twice."""

    __tablename__ = "email_contacts"
    __table_args__ = (
        UniqueConstraint("user_id", "email", name="uq_email_contacts_user_id_email"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320))
    domain: Mapped[str] = mapped_column(String(253))
    full_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    company: Mapped[str | None] = mapped_column(String(300), nullable=True)
    company_from_domain: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sent_to_count: Mapped[int] = mapped_column(Integer, default=0)
    received_from_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    two_way: Mapped[bool] = mapped_column(Boolean, default=False)
    in_contacts_file: Mapped[bool] = mapped_column(Boolean, default=False)
    source_counts_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class EmailContactMatch(TimestampMixin, Base):
    __tablename__ = "email_contact_matches"
    __table_args__ = (
        UniqueConstraint("contact_id", "product_id",
                         name="uq_email_contact_matches_contact_id_product_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("email_contacts.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    score: Mapped[int] = mapped_column(Integer)
    icp_score: Mapped[int] = mapped_column(Integer, default=0)
    relationship_score: Mapped[int] = mapped_column(Integer, default=0)
    segment: Mapped[str] = mapped_column(String(120))
    reasons_json: Mapped[list] = mapped_column(JSON, default=list)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmailNetworkMessage(TimestampMixin, Base):
    """A warm first email the user is writing to one of THEIR OWN contacts.
    Never sent from here: once SAVED it can be attached to a campaign as that
    person's first email, and the campaign's normal send path sends it (with
    every check). Deleted with the contact / the account / the person's
    erasure."""

    __tablename__ = "email_network_messages"
    __table_args__ = (
        UniqueConstraint("user_id", "contact_id",
                         name="uq_email_network_messages_user_id_contact_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("email_contacts.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    # draft -> approved (saved) -> attached (to a campaign lead) -> used
    status: Mapped[str] = mapped_column(String(20), default="draft")
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)


# --------------------------------------------------------------------------
# F05 Guardrails Studio (migration 0072): per-account exclusion lists and the
# per-product approved-claims / forbidden-phrases library.
# --------------------------------------------------------------------------


class GuardrailExclusion(TimestampMixin, Base):
    """One entity this ACCOUNT must never pitch: a domain, a company or a
    contact. Account-owned (user_id) -- unlike suppression_list, which is the
    deployment-wide opt-out list and stays exactly as it is.

    `value` is NORMALISED (app/services/guardrails.py::normalize) and UNIQUE per
    (user_id, kind), which is also the lookup index the send path uses.
    Removal is a soft delete (`removed_at`) so the read-only production audit
    query in docs/features/f05-guardrails-studio.md can tell whether an
    exclusion was active at the moment of a send; re-adding reactivates the
    row and moves `active_since`.
    """

    __tablename__ = "guardrail_exclusions"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "value", name="uq_guardrail_exclusion"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(10))            # domain | company | contact
    value: Mapped[str] = mapped_column(String(320))          # normalised
    display_value: Mapped[str] = mapped_column(String(320))  # as entered / as the CRM had it
    # customer | open_deal | churned | partner | competitor | do_not_contact | other
    reason: Mapped[str] = mapped_column(String(20))
    # manual | csv | crm_local | crm_hubspot | crm_salesforce
    source: Mapped[str] = mapped_column(String(20))
    external_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    active_since: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GuardrailLibraryEntry(TimestampMixin, Base):
    """An approved claim about US, or a phrase this account never sends.

    `product_id` NULL = every product of the account. Approved claims feed
    claim_verification (sender claims); forbidden phrases feed the pre-launch
    review and the send-time check -- see app/services/guardrails.py.
    """

    __tablename__ = "guardrail_library"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(20))            # approved_claim | forbidden_phrase
    text: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )





class EnrichmentAttempt(TimestampMixin, Base):
    """F01: one provider attempt made by the email waterfall -- the per-attempt
    cost ledger.

    One row per finder call and per verifier call, INCLUDING cache hits
    (cached=True, cost_units=0), so "how much did finding this person's email
    cost, and who found it" has an answer. Deliberately holds no personal data:
    the email DOMAIN only, never the address or the name. Owned by the account
    (user_id, CASCADE) like every other ledger; the lead link is SET NULL so a
    deleted lead does not delete the record of what was spent on it.
    """

    __tablename__ = "enrichment_attempts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lead_batches.id", ondelete="SET NULL"), nullable=True
    )
    step: Mapped[str] = mapped_column(String(32))          # apollo | hunter | reverify
    provider: Mapped[str] = mapped_column(String(50))      # who was called
    operation: Mapped[str] = mapped_column(String(20))     # find_email | verify
    outcome: Mapped[str] = mapped_column(String(20))
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_units: Mapped[int] = mapped_column(Integer, default=0)
    claimed_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    email_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)



class LeadSignal(Base):
    """One dated "why now" event about a lead's company, derived ONLY from data
    the system already stored (company news, the lead's LinkedIn posts, the
    Apollo organisation record, the strategy's market intel). Nothing here is
    fetched; app/services/signal_radar.py re-shapes what is already on the lead.

    A table and not a JSON column on `leads` because the success metric joins
    a message's cited signal back to a dated, owned row, and because GDPR
    erasure must be able to delete exactly these rows.

    `user_id` is the owning account, denormalised so every read can carry an
    ownership filter and the account export can reach the table directly.
    """

    __tablename__ = "lead_signals"
    __table_args__ = (
        UniqueConstraint("lead_id", "dedupe_key", name="uq_lead_signals_lead_dedupe"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE")
    )
    # funding | hiring | launch | news | post
    signal_type: Mapped[str] = mapped_column(String(20))
    # newsapi | linkedin_post | apollo | market_intel
    source: Mapped[str] = mapped_column(String(30))
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    title: Mapped[str] = mapped_column(String(500))
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[float] = mapped_column(Float)
    dedupe_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )



class QuickstartRun(TimestampMixin, Base):
    """F06 (migration 0072) -- one Quickstart request: website URL + one-line
    offer -> ICP -> a small verified lead batch -> review-only drafts, while
    the strategy's full pipeline runs beside it.

    The run OWNS nothing that can send. Its drafts (quickstart_drafts) are not
    `messages` rows, so the dispatcher and the send chokepoint never see them.
    See docs/features/f06-quickstart.md.

    status: queued -> fetching -> extracting -> sourcing -> drafting -> ready,
    or failed (with `error`). Superseded is a separate fact (`superseded_at`):
    the strategy reached verification, so the run's own outcome stays readable.
    The stage timestamps are the success metric's instrumentation (time to
    first reviewable draft = first_draft_at - created_at).
    """

    __tablename__ = "quickstart_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE")
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    lead_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lead_batches.id", ondelete="SET NULL"), nullable=True
    )
    website_url: Mapped[str] = mapped_column(String(2048))
    offer: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(
        String(20), default="queued", server_default=text("'queued'"),
        nullable=False, index=True
    )
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    icp_criteria_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # {final_url, status_code, content_type, bytes, truncated, title}
    website_meta_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # The page's visible text (<= 20 000 chars), kept so the ICP stays
    # explainable. UNTRUSTED: never put into a prompt unwrapped.
    website_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    lead_target: Mapped[int] = mapped_column(Integer, default=25)
    budget_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # {requested, verified, drafted, skipped_suppressed, budget_reached, ...}
    detail_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    icp_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sourcing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_draft_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )



class QuickstartDraft(TimestampMixin, Base):
    """F06 -- one review-only first-touch draft for one lead of a Quickstart
    run. Never sent: there is no path from this table to `messages`.

    `user_id` is denormalised so every read filters on the owner directly.
    Deleted with the lead (GDPR erasure deletes it explicitly as well, since
    erasure tombstones the lead row rather than deleting it)."""

    __tablename__ = "quickstart_drafts"
    __table_args__ = (
        UniqueConstraint("run_id", "lead_id", name="uq_quickstart_draft_run_lead"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("quickstart_runs.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    # draft | discarded
    status: Mapped[str] = mapped_column(
        String(20), default="draft", server_default=text("'draft'"), nullable=False
    )
    # How many claim-verification decisions changed the copy (stripped/rewritten).
    claims_changed: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )


# --------------------------------------------------------------------------
# F04 Reply Copilot (migration 0077_reply_copilot)
# --------------------------------------------------------------------------


class ReplyDraft(TimestampMixin, Base):
    """F04 -- one grounded, claim-verified answer to one inbound reply,
    queued for a person to approve, edit, discard (email) or copy (LinkedIn).

    A draft is NOT a send. Approving an email draft creates an ordinary
    `messages` row (origin "reply_copilot") that goes through the send
    chokepoint like every other message; see app/services/reply_copilot.py
    and docs/features/f04-reply-copilot.md.

    `user_id` is the owning account (lead -> strategy -> product -> user),
    denormalised so every read filters on it directly. `reply_id` is UNIQUE:
    a redelivered reply can never create a second draft or overwrite one a
    person is editing. GDPR erasure deletes a lead's drafts explicitly (the
    lead row is tombstoned, so the FK cascade never fires)."""

    __tablename__ = "reply_drafts"
    __table_args__ = (
        UniqueConstraint("reply_id", name="uq_reply_drafts_reply_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    reply_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inbound_replies.id", ondelete="CASCADE")
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(20))           # email | linkedin
    intent: Mapped[str] = mapped_column(String(20))            # positive | objection | question
    meeting_time_request: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    # pending | approved | copied | discarded | superseded
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default=text("'pending'"),
        nullable=False, index=True
    )
    draft_subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    draft_body: Mapped[str] = mapped_column(Text)
    final_subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    final_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The "share of drafts sent unedited" metric: final != draft, whitespace-
    # normalised. Decided at approval, never recomputed.
    edited: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    # Every claim-verification decision at generation that was NOT verified
    # ({claim, verdict, category, replacement}) -- shown as "removed".
    claim_flags_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # What the draft was grounded in: playbook steps, approved-claim ids,
    # booking URL, number of forbidden phrases given to the model.
    grounding_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    send_result: Mapped[str | None] = mapped_column(String(60), nullable=True)
    auto_sent: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    reply_received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ReplyCopilotSettings(TimestampMixin, Base):
    """F04 -- one row per account: the per-account auto-send opt-in and the
    stop-attribution strictness (`answer_earlier_replies`).

    No row = the defaults (auto-send OFF, answer_earlier_replies ON). Auto-send additionally needs the
    deployment switch `reply_copilot_auto_send_allowed` (system settings)."""

    __tablename__ = "reply_copilot_settings"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_reply_copilot_settings_user_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    auto_send_meeting_time: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    # Migration 0081. True (the default, the original behaviour): an answer to
    # a prospect's LATER reply may pass the stop their EARLIER reply caused.
    # False (strict): an answer passes only the stop its own reply caused.
    answer_earlier_replies: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )



class SpendCap(TimestampMixin, Base):
    """F07 Spend Guard -- an account's own monthly spending ceiling.

    An ADDITIONAL ceiling: it never raises or replaces the plan's daily AI cap
    (plans.max_ai_spend_usd_per_day) or a strategy's budget; whichever is
    reached first stops the work. NULL monthly_cap_micros = no monthly cap
    (the row may exist only to hold the alert preference). One row per
    account, enforced by the unique index on user_id.
    See docs/features/f07-spend-guard.md.
    """

    __tablename__ = "spend_caps"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    monthly_cap_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    email_alerts: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )
    # Who changed it (a workspace member acting for the account). Actor, not owner.
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )



class SpendAlert(TimestampMixin, Base):
    """F07 -- one fired 50/80/100% alert. The UNIQUE (user_id, period,
    threshold) constraint is what makes "once per threshold per month" hold
    even when two workers evaluate the same account at the same moment."""

    __tablename__ = "spend_alerts"
    __table_args__ = (
        UniqueConstraint("user_id", "period", "threshold",
                         name="uq_spend_alert_user_period_threshold"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    period: Mapped[str] = mapped_column(String(7))          # YYYY-MM (UTC)
    threshold: Mapped[int] = mapped_column(Integer)          # 50 | 80 | 100
    cap_micros: Mapped[int] = mapped_column(BigInteger)
    spent_micros: Mapped[int] = mapped_column(BigInteger)
    email_status: Mapped[str] = mapped_column(String(20), default="skipped")



class SpendForecast(TimestampMixin, Base):
    """F07 -- the cost forecast taken BEFORE one run (strategy pipeline,
    lead-sourcing batch or Quickstart), allowed or refused.

    Kept so the success metric can be measured: spend_guard.reconcile()
    compares it with the api_usage and enrichment_attempts rows the run
    actually produced. No personal data."""

    __tablename__ = "spend_forecasts"
    __table_args__ = (
        Index("ix_spend_forecasts_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(20))       # strategy_run | enrichment_run | quickstart
    decision: Mapped[str] = mapped_column(String(10))   # allowed | refused
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lead_batches.id", ondelete="SET NULL"), nullable=True, index=True
    )
    requested_leads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_purposes: Mapped[list] = mapped_column(JSON, default=list)
    ai_input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    ai_output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    ai_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    data_units_json: Mapped[dict] = mapped_column(JSON, default=dict)
    data_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    total_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    month_spent_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    cap_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    detail_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)



class Pilot(TimestampMixin, Base):
    """F09 (migration 0077) -- one 30-day pilot of an account
    (docs/features/f09-pilot-mode.md).

    The buyer writes the success criteria on day 1 (`criteria_json`, locked:
    there is no edit path); the hourly sweep sends a weekly scorecard and, when
    `ends_at` passes, ends the pilot and stores its verdict. Every number comes
    from the F08 meetings ledger; the verdict keeps an aggregate snapshot of it
    so the verdict page reads the same after the pilot is over.

    ONE ACTIVE PILOT PER ACCOUNT, enforced by the schema: `active_owner_id`
    equals `user_id` while the pilot is active and is NULL once it ends, and it
    is UNIQUE (NULLs never collide, on SQLite and PostgreSQL alike).

    NOTHING HERE TOUCHES BILLING. `plan_at_start` / `plan_at_end` are records
    for the verdict page, never inputs to a plan change.
    """

    __tablename__ = "pilots"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    started_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    active_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, nullable=True, unique=True
    )
    # active | ended
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default=text("'active'"),
        nullable=False, index=True
    )
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)            # day 30, inclusive
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    criteria_json: Mapped[dict] = mapped_column(JSON)
    currency: Mapped[str] = mapped_column(String(3))
    strategy_ids_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    scorecard_emails: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    plan_at_start: Mapped[str | None] = mapped_column(String(30), nullable=True)
    plan_at_end: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # completed | ended_early
    end_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    verdict_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verdict_emailed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verdict_email_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    # NULL (not tried) | sending | sent | failed. Claimed as `sending` BEFORE
    # the send; a `sending` row is never retried automatically (a crash
    # between send and record must not mail the verdict twice).
    verdict_email_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    exported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )



class PilotScorecard(TimestampMixin, Base):
    """F09 -- one weekly scorecard email of a pilot (weeks 1-4).

    UNIQUE (pilot_id, week) is the idempotency key: the sweep INSERTs the row
    (status `sending`) BEFORE it sends, so a second sweep, an overlapping beat
    or a second worker cannot email the same week twice. `failed` rows are
    retried by compare-and-set on `attempts`. `summary_json` is what was
    emailed (aggregates only)."""

    __tablename__ = "pilot_scorecards"
    __table_args__ = (
        UniqueConstraint("pilot_id", "week", name="uq_pilot_scorecard_week"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    pilot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pilots.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    week: Mapped[int] = mapped_column(Integer)
    # sending | sent | failed
    status: Mapped[str] = mapped_column(
        String(20), default="sending", server_default=text("'sending'"), nullable=False
    )
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)



class ChampionMove(TimestampMixin, Base):
    """F10 (migration 0077) -- one person confirmed to have LEFT the company a
    lead was sourced at.

    Two jobs, both reasons it is a table and not a JSON key on the lead:

    1. THE DEPARTURE BLOCK. The send chokepoint cancels any message to the old
       lead, or to any lead of the same account carrying the old address
       (matched by `old_email_sha256` -- a hash, never the address). A row is
       a recorded fact: it keeps blocking even if the feature flag is later
       switched off, until a person dismisses it (`dismissed_at`).
    2. THE METRIC. "Champion re-engagements" counts rows with outcome
       `lead_created`; "sends after departure" joins sends back to
       `detected_at`.

    UNIQUE(old_lead_id): a re-run of the sweep, a retry or two overlapping
    checks cannot record the same departure twice. Owned by the account
    (user_id, CASCADE); both lead links are SET NULL so a deleted lead does not
    erase the block on its address. GDPR delete scrubs the company/title
    fields and the hash (app/services/champion_tracker.py::erase_for_lead) and
    keeps the row as a non-personal tombstone.
    """

    __tablename__ = "champion_moves"
    __table_args__ = (
        UniqueConstraint("old_lead_id", name="uq_champion_move_old_lead"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    old_lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    new_lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    old_email_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # positive_reply | customer | NULL (not a champion)
    champion_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    from_company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    from_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    to_company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    to_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    to_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # send_hold | refresh | manual
    trigger: Mapped[str] = mapped_column(String(20))
    # lead_created | not_champion | suppressed | excluded | existing_lead | no_company
    outcome: Mapped[str] = mapped_column(String(30))
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    dismiss_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)





class DailySendApproval(TimestampMixin, Base):
    """Daily send approval (migration 0081) -- one row per account per LOCAL
    calendar day: has a person approved today's AUTOMATIC sending, and for
    which channels?

    Founder decision 2026-10-11 (docs/features/daily-send-approval.md): the
    send chokepoint defers every automatic message on a gated channel until
    today's row says `approved` and lists that channel. The row is also the
    once-per-day notification claim (`notified_at`, set by a conditional
    UPDATE so concurrent workers notify once). Every decision is ALSO written
    to compliance_audit_log, which is the durable record.
    """

    __tablename__ = "daily_send_approvals"
    __table_args__ = (
        UniqueConstraint("user_id", "local_date", name="uq_daily_send_approval_user_date"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    # The account OWNER (workspace owner), never a member.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    local_date: Mapped[date] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(64))
    # pending | approved | declined
    status: Mapped[str] = mapped_column(String(20))
    channels_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    linkedin_risk_acknowledged: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    linkedin_warning_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    queued_counts_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The UTC instant a decision stops counting: the next local midnight in
    # the timezone in force WHEN it was decided. The send path checks this
    # instant, never a re-derived local date, so changing the account's
    # timezone later cannot stretch an approval.
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The person who decided (owner or manager), not necessarily the owner.
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notify_result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class AutopilotLaunch(TimestampMixin, Base):
    """Autopilot launch (migration 0087) -- one row per strategy.

    The per-strategy opt-in "when research finishes, find leads, write the
    sequence and start outreach automatically", the lawful-basis confirmation
    that turning it on requires (who, when, the text version AND the exact
    text), and the state of the automatic launch. See
    docs/features/autopilot-launch.md and app/services/autopilot.py.

    The confirmation is ALSO written to compliance_audit_log (kept on account
    erasure as proof); this row is the working state.
    """

    __tablename__ = "autopilot_launches"
    __table_args__ = (
        UniqueConstraint("strategy_id", name="uq_autopilot_launches_strategy_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    # off | waiting_for_research | waiting_for_review | blocked | sourcing |
    # waiting_for_channels | launched  (autopilot.STATES)
    state: Mapped[str] = mapped_column(String(30), default="off", server_default="off",
                                       nullable=False)
    state_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The PERSON who confirmed (in a workspace the acting member, not the owner).
    confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                          nullable=True)
    text_version: Mapped[str | None] = mapped_column(String(60), nullable=True)
    confirmed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lead_batches.id", ondelete="SET NULL"), nullable=True
    )
    # {"email": "<sequence uuid>", "linkedin": ...}
    sequences_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # {"email": {"state": "launched"|"waiting"|"blocked", "reason": str|None,
    #            "enrolled": n}}
    channels_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # {"<reason key>": "<iso ts>"} -- one notification per distinct reason.
    notified_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Conditional-UPDATE claim so two workers never create the batch or a
    # sequence twice (autopilot._claim).
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                        nullable=True)
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                         nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                             nullable=True)
    # --- review fixes ---------------------------------------------------
    # The lead batches Autopilot itself started (it never enrolls anyone else).
    batch_ids_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # The leads its own batches VERIFIED, snapshotted when the batch finished:
    # a lead added or flipped to verified by hand later is never in it.
    eligible_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # {lead_id: reason} -- leads enrollment would skip (suppressed, excluded,
    # departed); they leave the pool so no gate re-runs for them.
    unreachable_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # The ICP criteria extracted once (never re-bought on a retry).
    icp_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Back-off after a spend refusal: nothing is retried before this instant.
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                         nullable=True)
    # Sourcing-chain publication (a failed publish is retried, then given up).
    chain_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                                nullable=True)
    chain_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"),
                                                nullable=False)
    # The approved WhatsApp template the person chose for Autopilot.
    whatsapp_template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("whatsapp_templates.id", ondelete="SET NULL"), nullable=True
    )


class CustomerImport(Base):
    """Local business mode, Part B (migration 0088) -- one row per import of a
    business's OWN customer list for consent-based win-back.

    The proof of each person's consent is in consent_events (and
    whatsapp_optins); this row is the import itself: who uploaded it, when,
    into which campaign, under which business type, and the attestation the
    owner confirmed. Refused rows are kept as line numbers and reasons only --
    never the refused personal data (GDPR Art. 5(1)(c)).

    No updated_at: an import is a fact that happened once.
    """

    __tablename__ = "customer_imports"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True
    )
    business_type: Mapped[str] = mapped_column(String(30))
    attestation: Mapped[str] = mapped_column(Text)
    # local_business.ATTESTATION_VERSION the person confirmed.
    attestation_version: Mapped[str] = mapped_column(String(10))
    accepted_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    rejected_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    # [{line, reason}] -- no names, addresses or numbers.
    rejected_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------------
# Voice twin -- "Your voice on calls" (migration 0089, docs/features/voice-twin.md)
# --------------------------------------------------------------------------


class VoiceClone(TimestampMixin, Base):
    """One Professional Voice Clone of the account owner's OWN voice, from the
    written consent to its deletion.

    A voiceprint is biometric data (BIPA 740 ILCS 14, GDPR Art. 9): the row
    holds the exact consent text the person signed, who (user_id + typed
    signature) and when, BEFORE any recording leaves their browser. Victorae
    never stores the recordings themselves -- they are streamed to the
    provider and dropped (voice_twin.upload_sample).

    status: consented | collecting | verifying | training | ready | failed |
            revoke_pending | revoked
    Only `ready` is ever used on a call. `revoke_pending` means the provider
    delete has not succeeded yet: unusable, retried by a beat task.
    """

    __tablename__ = "voice_clones"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(
        String(20), default="elevenlabs", server_default="elevenlabs", nullable=False
    )
    provider_voice_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), default="consented", server_default="consented", nullable=False
    )
    consent_version: Mapped[str] = mapped_column(String(10))
    consent_text: Mapped[str] = mapped_column(Text)
    # The name the person typed as their signature.
    consent_signature: Mapped[str] = mapped_column(String(200))
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sample_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    sample_bytes: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    # The last provider status we read (fine-tuning state, verification) --
    # no audio, no transcript.
    provider_state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                          nullable=True)
    revoke_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                                 nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                        nullable=True)
    revoke_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )


class VoiceCloneDeletion(Base):
    """Review fix M3: a provider voice that must still be deleted AFTER its
    owner's rows are gone (an account erasure whose ElevenLabs delete failed).

    Deliberately NOT owned (no user column, no foreign key) and holding nothing
    personal -- the provider's name and its opaque voice id, the attempt count
    and the last provider error -- so it survives the erasure and the hourly
    task (voice_twin.retry_pending) can finish the deletion of the biometric
    voice model. The row is deleted as soon as the provider confirms.
    """

    __tablename__ = "voice_clone_deletions"
    __table_args__ = (
        UniqueConstraint("provider", "provider_voice_id",
                         name="uq_voice_clone_deletions_provider_voice"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    provider: Mapped[str] = mapped_column(String(20))
    provider_voice_id: Mapped[str] = mapped_column(String(120))
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                             nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class VoiceTwinSettings(TimestampMixin, Base):
    """The account's switches for AI calls in the owner's voice and for AI
    answering of inbound calls. One row per account; no row = everything off.

    inbound_phone_number_id is the provider phone-number id this account
    answers on. UNIQUE: one number answers for exactly one account, and the
    inbound webhook maps the provider's id to the account through this column
    only -- never through anything the caller sends.

    paused_at: set by the post-call check when an assistant turn claimed to be
    human. While set, no AI call of this account is placed or answered until a
    person resumes it (audited).
    """

    __tablename__ = "voice_twin_settings"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_voice_twin_settings_user_id"),
        UniqueConstraint("inbound_phone_number_id",
                         name="uq_voice_twin_settings_inbound_phone_number_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    use_clone_for_calls: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    answer_inbound: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    inbound_phone_number_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # The owner's own phone (E.164): live takeover and "put me through to
    # the owner" transfer here.
    transfer_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Owner-written notes for the receptionist (hours, what the business
    # does). Checked on save: no instruction to hide that it is an AI.
    assistant_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)



class AgentRun(Base):
    """One run of a Victorae agent (app/agents/), written by the master
    supervisor (app/agents/supervisor.py).

    The supervisor is stateless: API and Celery processes share nothing in
    memory, so this table IS its memory. Circuit-breaker state, error rate and
    latency for an agent are evaluated from its most recent rows on every
    call. Rows hold no prospect content -- name, timing, outcome, and a short
    error class only -- and are owned by the account the run was for.
    """

    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_agent_started_at", "agent", "started_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent: Mapped[str] = mapped_column(String(40), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    # "native" (the C++/Rust core) or "python" (the reference fallback).
    backend: Mapped[str] = mapped_column(String(10), nullable=False)
    # Exception class and a truncated message; never request data.
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    items: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class HelpChunk(Base):
    """One help-center chunk in the search index (Help AI v2, Phase 2).

    Built ONLY from docs/help-center by scripts/help_center/reindex.py. Nothing
    from a user's question or account is ever written here, and no row belongs
    to an account -- it is product documentation, identical for everyone.

    Two PostgreSQL-only columns are not mapped here and exist only after
    alembic 0094 runs on PostgreSQL with pgvector:
      embedding  halfvec(1024)  HNSW, halfvec_cosine_ops (m=16, ef_construction=64)
      body_tsv   tsvector GENERATED from heading + body, GIN-indexed
    app/services/help_index.py reads and writes them with SQL. Leaving them
    out of the model is what lets the SQLite suite create this table.
    """

    __tablename__ = "help_chunks"

    # Stable chunk id, "<article-id>#<heading-slug>" (help_center._chunk_article).
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    article_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    heading: Mapped[str] = mapped_column(String(300), nullable=False)
    # "Article title > Section heading" (merged sections joined with " + ").
    heading_path: Mapped[str] = mapped_column(Text, nullable=False)
    # Site link: "/help/<article-id>/" plus "#<heading anchor>" when the chunk
    # is not the article intro. Phase 4 cites from this, never from the model.
    url: Mapped[str] = mapped_column(String(400), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Empty by decision (DECISIONS HELPAI-VENDOR): contextualized embeddings
    # carry the article context, so no generated context sentence is stored.
    context_sentence: Mapped[str] = mapped_column(Text, nullable=False, default="",
                                                  server_default="")
    # sha256 of the embedding input (chunk text + its article's text).
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # NULL until a vector is stored for the current content_hash.
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                         nullable=True)
    # "<embedding model>@<dimension>" that produced the vector; "" when none.
    model: Mapped[str] = mapped_column(String(80), nullable=False, default="",
                                       server_default="")

# Product defect #2: fill outcomes.strategy_id / outcomes.variant on every new
# Outcome (the learning loop and A/B promotion read those columns). Imported
# last so the listener module can import the classes above.
from app.db import outcome_attribution as _outcome_attribution  # noqa: E402,F401
