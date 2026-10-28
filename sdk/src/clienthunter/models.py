"""Typed response models for the Vectorae SDK.

These Pydantic models mirror the backend's response schemas exactly.
Field names, types, and optionality are derived directly from the backend's
``app/api/schemas.py``, router return shapes, and ``app/db/models.py`` enums.

Rules:
    - Models are read-only (``model_config = ConfigDict(frozen=True)``).
    - UUIDs are represented as ``str`` — the backend serialises them that way
      in plain-dict returns; Pydantic coerces UUID objects transparently.
    - Datetimes arrive as ISO-8601 strings from the API; we keep them as
      ``datetime`` after parsing so callers get proper objects.
    - Enum strings are kept as plain ``str`` with Literal hints where the
      full set is known — this avoids client breakage when the backend adds a
      new variant (SDK validation would reject the new value if we used Enum).
    - Every field has a docstring comment so IDE autocompletion is helpful.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared config
# ---------------------------------------------------------------------------

_frozen = ConfigDict(frozen=True, populate_by_name=True)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TokenPair(BaseModel):
    """Returned by POST /auth/login and POST /auth/refresh."""

    model_config = _frozen

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    """Returned by GET /auth/me."""

    model_config = _frozen

    id: str
    email: str
    plan: str  # "free" | "pro" | "enterprise"


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------


class Product(BaseModel):
    """Returned by POST /products and GET /products/{id}."""

    model_config = _frozen

    id: str
    name: str
    description: str
    type: str  # "product" | "skill"
    created_at: datetime


class PastClient(BaseModel):
    """One past client row, returned by POST /products/{id}/past-clients."""

    model_config = _frozen

    id: str
    details: str
    acquisition_story: str
    extracted_patterns_json: dict[str, Any] | None = None
    """Claude-extracted patterns: industry, company_size, channel, trigger_event, etc."""


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


class PhaseProgress(BaseModel):
    """Progress for one phase of the 72-step pipeline."""

    model_config = _frozen

    phase: int          # 1–8 for strategy pipeline, 1–8 for GTM pipeline
    title: str          # human-readable phase name
    done: int           # steps completed
    total: int          # steps in this phase (always 9)


class PipelineProgress(BaseModel):
    """Progress for one full 72-step pipeline."""

    model_config = _frozen

    pipeline: str       # "strategy" | "gtm"
    done: int           # total steps completed across all phases
    total: int          # 72
    phases: list[PhaseProgress]


class VerificationPass(BaseModel):
    """One of the 10 verification passes logged against a strategy."""

    model_config = _frozen

    pass_no: int | None = None
    name: str | None = None
    result: str | None = None   # "PASS" | "FAIL"
    fixes: str | None = None    # description of fixes applied on FAIL
    # The backend stores these as plain dicts; additional fields pass through
    model_config = ConfigDict(frozen=True, extra="allow")


class Strategy(BaseModel):
    """Returned by POST /products/{id}/strategies (minimal) and
    GET /strategies/{id} (full status).

    Use ``StrategyStatus`` for the detailed view with pipeline progress.
    """

    model_config = _frozen

    id: str
    flow_type: str          # "with_clients" | "no_clients"
    status: str             # see StrategyStatus enum values below
    created_at: datetime


# Convenience literals so callers can compare without magic strings
STRATEGY_STATUS_PENDING = "pending"
STRATEGY_STATUS_RESEARCHING = "researching"
STRATEGY_STATUS_VERIFYING = "verifying"
STRATEGY_STATUS_VERIFIED = "verified"
STRATEGY_STATUS_NEEDS_HUMAN_REVIEW = "needs_human_review"
STRATEGY_STATUS_EXECUTING = "executing"
STRATEGY_STATUS_FAILED = "failed"


class StrategyStatus(BaseModel):
    """Detailed strategy view — returned by GET /strategies/{id}.

    Contains pipeline progress (per-phase step counts) and verification
    pass results in addition to the basic status fields.
    """

    model_config = _frozen

    id: str
    flow_type: str
    status: str
    progress: list[PipelineProgress]
    """One PipelineProgress per active pipeline ("strategy" and optionally "gtm")."""
    verification: list[dict[str, Any]]
    """Raw verification pass records.  Use the VerificationPass model to parse."""
    strategy_document_ready: bool
    """True once all strategy-pipeline phases are done and all 10 passes are green."""
    gtm_document_ready: bool
    """True once the GTM pipeline is also done (Flow 2 / no_clients only)."""
    error: str | None = None
    """Set when status == "failed"; describes the unrecoverable error."""


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------


class Lead(BaseModel):
    """Summary lead row — returned in list results."""

    model_config = _frozen

    id: str
    status: str             # see LeadStatus enum in backend
    source: str             # e.g. "apollo"
    full_name: str | None = None
    title: str | None = None
    company: str | None = None
    email: str | None = None
    phone: str | None = None
    created_at: datetime


class LeadDetail(Lead):
    """Full lead detail — returned by GET /strategies/{id}/leads/{lead_id}."""

    strategy_id: str
    batch_id: str | None = None
    external_id: str | None = None
    enrichment_json: dict[str, Any] | None = None
    """Raw Apollo enrichment payload (company, seniority, LinkedIn URL, etc.)."""


class LeadList(BaseModel):
    """Paginated lead list — returned by GET /strategies/{id}/leads."""

    model_config = _frozen

    total: int
    limit: int
    offset: int
    items: list[Lead]


class LeadBatch(BaseModel):
    """A lead-sourcing batch (Apollo search run) — returned by POST /strategies/{id}/leads/source."""

    model_config = _frozen

    id: str
    strategy_id: str
    stage: str          # "sourcing" | "enriching" | "finding_emails" | "verifying" | "finalized" | "failed"
    requested_leads: int
    icp_criteria_json: dict[str, Any] | None = None
    summary_json: dict[str, Any] | None = None
    """Set when stage == "finalized": counts per status, email found %, etc."""
    created_at: datetime


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------


class SequenceStep(BaseModel):
    """One step in an outreach sequence."""

    model_config = _frozen

    step_no: int
    template: str           # messaging brief Claude personalises per lead
    variant: str            # A/B variant label, default "A"
    delay_days: int
    channel: str | None = None   # overrides sequence channel for this step
    whatsapp_kind: str | None = None    # "template" | "text"
    whatsapp_template_id: str | None = None
    variable_mapping: dict[str, str] | None = None


class Sequence(BaseModel):
    """An outreach sequence — returned by POST/GET /strategies/{id}/sequences."""

    model_config = _frozen

    id: str
    strategy_id: str
    name: str
    channel: str        # "email" | "whatsapp" | "linkedin"
    status: str         # "draft" | "active" | "paused" | "completed"
    booking_url: str | None = None
    steps: list[SequenceStep] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# WhatsApp Templates
# ---------------------------------------------------------------------------


class WhatsAppTemplate(BaseModel):
    """A Meta message template — returned by /whatsapp/templates endpoints.

    Only APPROVED templates may be sent to cold contacts.
    Status lifecycle: draft → submitted → approved | rejected.
    """

    model_config = _frozen

    id: str
    name: str
    language: str           # e.g. "en_US"
    version: int
    category: str           # "marketing" | "utility"
    status: str             # "draft" | "submitted" | "approved" | "rejected"
    body: str               # template text with {{1}} placeholders
    variable_descriptions: dict[str, str] = Field(default_factory=dict)
    """Maps placeholder index ("1") to a description of what fills it."""
    rejection_reason: str | None = None
    meta_template_id: str | None = None
    supersedes_id: str | None = None
    """If set, this is a new draft created because an approved template was edited."""
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Campaign / Analytics
# ---------------------------------------------------------------------------


class ChannelStats(BaseModel):
    """Per-channel send/reply metrics within a campaign overview."""

    model_config = ConfigDict(frozen=True, extra="allow")

    sent_total: int = 0
    replied: int = 0
    bounced: int = 0
    sends_today: int = 0
    daily_cap_today: int = 0


class CampaignOverview(BaseModel):
    """Returned by GET /strategies/{id}/campaign.

    Provides a real-time snapshot of lead counts, send volumes, reply rate,
    bounce rate, and per-channel statistics.  The ``channels`` dict contains
    ``email`` and ``whatsapp`` sub-objects (both typed as ``ChannelStats``
    with extra fields allowed for forward compatibility).
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    strategy_id: str
    campaign_state: str | None = None   # "active" | "paused_manual" | "paused_bounce"
    campaign_pause_reason: str | None = None
    leads_by_status: dict[str, int] = Field(default_factory=dict)
    sent_total: int = 0
    sends_today: int = 0
    daily_cap_today: int = 0
    channels: dict[str, dict[str, Any]] = Field(default_factory=dict)
    reply_rate: float = 0.0
    bounce_rate: float = 0.0
    bounce_pause_threshold: float = 0.03
    meetings_booked: int = 0
    unsubscribed: int = 0


class AnalyticsSeriesPoint(BaseModel):
    """One data point in the analytics time series."""

    model_config = _frozen

    bucket: str         # formatted date string matching the granularity
    channel: str        # "email" | "whatsapp"
    event: str          # "sent" | "opened" | "replied" | "booked" | etc.
    count: int


class Analytics(BaseModel):
    """Returned by GET /strategies/{id}/analytics.

    Contains a time series of outcome events and per-variant aggregates for
    A/B test tracking.  ``learning_insights`` is None until M8 is deployed.
    """

    model_config = _frozen

    strategy_id: str
    granularity: str        # "day" | "week" | "month"
    series: list[AnalyticsSeriesPoint]
    variants: dict[str, dict[str, int]]
    """Maps variant label (e.g. "A") to a dict of event counts."""
    learning_insights: dict[str, Any] | None = None
    """Populated by the M8 learning loop with playbook scores and A/B winners."""


class CampaignStateUpdate(BaseModel):
    """Returned by POST /strategies/{id}/campaign/pause and /resume."""

    model_config = _frozen

    campaign_state: str
    campaign_pause_reason: str | None = None


# ---------------------------------------------------------------------------
# Integrations
# ---------------------------------------------------------------------------


class GmailStatus(BaseModel):
    """Returned by GET /integrations/gmail/status."""

    model_config = _frozen

    connected: bool
    email_address: str | None = None
    scopes: list[str] | None = None
    token_expires_at: datetime | None = None
