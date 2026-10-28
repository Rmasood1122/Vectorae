"""WhatsApp template management — Milestone 4, Chunk 2.

Lifecycle (mirrors Meta's):   draft -> submitted -> approved | rejected
                              rejected -> (edit) -> draft -> submitted ...
Approved templates are IMMUTABLE here: editing one creates a NEW row with
version+1 under the same (name, language), linked via supersedes_id.
Only status=approved templates are ever sendable — the adapter's send()
guard checks this table, so a template name passed in manually cannot
bypass approval.

The Meta Business Management API calls live on the adapter
(app/integrations/whatsapp.py) so they inherit the shared plumbing;
this module owns validation + state transitions and is directly testable
without HTTP (Chunk 4).

AI-assisted drafting: Claude turns the strategy's Phase 6 (messaging) and
Phase 2 (persona) outputs into 2-3 COMPLIANT draft candidates — clear
sender identity, honest value proposition, an opt-out instruction in the
body — and they are saved as drafts ONLY. Nothing is ever auto-submitted;
the user reviews and submits explicitly.
"""

import json
import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    PipelineKind,
    ResearchStep,
    Strategy,
    WhatsAppTemplate,
    WhatsAppTemplateCategory,
    WhatsAppTemplateStatus,
)
from app.services.anthropic_client import get_client

logger = logging.getLogger(__name__)


class TemplateError(ValueError):
    """Invalid template content or an illegal lifecycle transition."""


_PLACEHOLDER = re.compile(r"\{\{(\d+)\}\}")
# LEGAL GUARD (product defect #28): a MARKETING template must tell the
# recipient how to opt out -- an INSTRUCTION, not merely the word: a verb then
# STOP ("Reply STOP to opt out."), or STOP then "opt out"/"unsubscribe", within
# 25 characters and the same sentence. STOP must be in capitals (the keyword
# app/api/webhooks_whatsapp.py::_is_opt_out acts on); the verb and the
# opt-out words are case-insensitive. "NON-STOP deals", "BUS STOP" or a URL
# ending /STOP are not instructions. UTILITY templates (transactional) are not
# required to carry it. Mirrored in frontend/src/lib/whatsappTemplate.ts.
# Basis, needs founder/legal confirmation: WhatsApp Business Messaging Policy
# (honour opt-out requests); CASL s.6(2)(c), s.11; GDPR Art. 21(2)-(3).
_OPT_OUT_INSTRUCTION = re.compile(
    r"(?i:\b(?:reply|text|send|respond|message)\b)[^.\n]{0,25}\bSTOP\b"
    r"|\bSTOP\b[^.\n]{0,25}(?i:\b(?:opt[ -]?out|unsubscribe)\b)"
)
STANDARD_OPT_OUT = "Reply STOP to opt out."


def has_opt_out_instruction(body: str | None) -> bool:
    return bool(_OPT_OUT_INSTRUCTION.search(body or ""))


def _category_value(category) -> str:
    return str(getattr(category, "value", category) or "").lower()


def validate_opt_out(body: str | None, category) -> None:
    """Raise TemplateError if a MARKETING body has no opt-out instruction."""
    if _category_value(category) != WhatsAppTemplateCategory.MARKETING.value:
        return
    if not has_opt_out_instruction(body):
        raise TemplateError(
            "a marketing template must tell the recipient how to opt out, with "
            "the word STOP in capitals in an instruction, e.g. "
            f'"{STANDARD_OPT_OUT}" or "STOP to unsubscribe" (STOP is the '
            "keyword Vectorae acts on to stop messaging a contact)"
        )
# I1 consent hardening (T33 c-cold-pitch-labelled-utility). Meta classes a
# template by its CONTENT: anything promotional is MARKETING whatever label it
# carries. generate_drafts writes the first COLD touch, so a generated draft is
# kept as UTILITY only when its body shows none of these promotional signals
# (an offer, a value claim about what "we" do, an invitation to talk or buy).
# Deterministic, and it fails closed: any signal => MARKETING, which then gets
# the opt-out instruction. A plain confirmation or reminder stays UTILITY.
_PROMOTIONAL_SIGNAL = re.compile(
    r"(?i)\b(?:"
    r"(?:we|i)(?:'re| are| am)?\s+(?:help|helps|close|closes|offer|cut|save|"
    r"build|provide|deliver|work with|specialise|specialize|can)"
    r"|want|interested|open to|keen|fancy|worth a|would you like|shall we|"
    r"book a|schedule a|grab|learn more|check out|find out|"
    r"discount|offer|deal|deals|free|trial|demo|promo|promotion|sale|"
    r"price|pricing|save|saving|savings|limited|special|exclusive|"
    r"chat|quick call|\d+[- ]minute"
    r")\b"
)


def is_promotional(body: str | None) -> bool:
    """True when a body carries a promotional signal (see _PROMOTIONAL_SIGNAL)."""
    return bool(_PROMOTIONAL_SIGNAL.search(body or ""))


# Meta template names: lowercase letters, digits and underscores, at most 512
# characters (templates/overview, read 2026-10-27). 200 here on purpose: it is
# the width of whatsapp_templates.name.
_NAME_RE = re.compile(r"^[a-z0-9_]{1,200}$")


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def validate_body(body: str) -> dict[int, str]:
    """Validate the body text; return {placeholder_no: ''} slots found.

    Rules:
      - non-empty, within the configured length limit (Meta's body
        component maximum is 1024 characters, templates/components; that is
        the configured default)
      (the opt-out instruction depends on the category: validate_opt_out)
      - variable placeholders must be exactly {{1}}..{{k}} — sequential,
        starting at 1, no gaps, no duplicates ({{2}} without {{1}} is a
        Meta rejection anyway; we fail fast locally)
    """
    text = (body or "").strip()
    if not text:
        raise TemplateError("template body must not be empty")
    if len(text) > settings.whatsapp_template_body_max_chars:
        raise TemplateError(
            f"template body exceeds {settings.whatsapp_template_body_max_chars}"
            " characters"
        )
    numbers = [int(n) for n in _PLACEHOLDER.findall(text)]
    unique = sorted(set(numbers))
    if len(unique) != len(numbers):
        raise TemplateError("duplicate variable placeholders (each {{n}} once)")
    if unique != list(range(1, len(unique) + 1)):
        raise TemplateError(
            "variable placeholders must be sequential starting at {{1}} "
            f"— found {['{{%d}}' % n for n in unique] or 'none'}"
        )
    return {n: "" for n in unique}


def validate_name(name: str) -> str:
    n = (name or "").strip().lower()
    if not _NAME_RE.match(n):
        raise TemplateError(
            "template name must be lowercase letters, digits and "
            "underscores only (e.g. 'intro_offer_v1')"
        )
    return n


def _validate_variable_descriptions(
    slots: dict[int, str], descriptions: dict | None
) -> dict[str, str]:
    """Descriptions tell Chunk 3's sequence engine what to fill into each
    {{n}}. Every placeholder needs one; extras are rejected."""
    provided = {str(k): str(v) for k, v in (descriptions or {}).items()}
    expected = {str(n) for n in slots}
    missing = expected - set(provided)
    extra = set(provided) - expected
    if missing:
        raise TemplateError(
            f"missing variable descriptions for placeholder(s): {sorted(missing)}"
        )
    if extra:
        raise TemplateError(
            f"variable descriptions given for nonexistent placeholder(s): {sorted(extra)}"
        )
    return provided


# --------------------------------------------------------------------------
# CRUD / lifecycle
# --------------------------------------------------------------------------

_EDITABLE = (WhatsAppTemplateStatus.DRAFT, WhatsAppTemplateStatus.REJECTED)


def _latest_version(session: Session, name: str, language: str) -> int:
    versions = session.execute(
        select(WhatsAppTemplate.version)
        .where(WhatsAppTemplate.name == name,
               WhatsAppTemplate.language == language)
    ).scalars().all()
    return max(versions) if versions else 0


def create_draft(
    session: Session,
    *,
    name: str,
    language: str,
    category: WhatsAppTemplateCategory,
    body: str,
    variable_descriptions: dict | None = None,
    supersedes: WhatsAppTemplate | None = None,
) -> WhatsAppTemplate:
    name = validate_name(name)
    language = (language or "").strip()
    if not language:
        raise TemplateError("language is required (e.g. 'en_US')")
    slots = validate_body(body)
    validate_opt_out(body, category)
    descriptions = _validate_variable_descriptions(slots, variable_descriptions)

    row = WhatsAppTemplate(
        name=name,
        language=language,
        version=_latest_version(session, name, language) + 1,
        category=category,
        status=WhatsAppTemplateStatus.DRAFT,
        body=body.strip(),
        variable_descriptions_json=descriptions,
        supersedes_id=supersedes.id if supersedes is not None else None,
    )
    session.add(row)
    session.commit()
    return row


def update_template(
    session: Session,
    template: WhatsAppTemplate,
    *,
    body: str | None = None,
    category: WhatsAppTemplateCategory | None = None,
    variable_descriptions: dict | None = None,
) -> WhatsAppTemplate:
    """Edit in place ONLY while draft or rejected. Editing an approved
    template creates a NEW draft version instead (immutability); editing
    a submitted one is refused — sync or wait for Meta's verdict first."""
    if template.status is WhatsAppTemplateStatus.SUBMITTED:
        raise TemplateError(
            "template is submitted and awaiting Meta review — sync its "
            "status first; it cannot be edited while under review"
        )

    if template.status is WhatsAppTemplateStatus.APPROVED:
        # Immutable: spawn version+1 as a new draft.
        return create_draft(
            session,
            name=template.name,
            language=template.language,
            category=category or template.category,
            body=body if body is not None else (template.body or ""),
            variable_descriptions=(
                variable_descriptions
                if variable_descriptions is not None
                else template.variable_descriptions_json
            ),
            supersedes=template,
        )

    # draft | rejected: edit in place, back to a clean draft.
    validate_opt_out(body if body is not None else template.body,
                     category if category is not None else template.category)
    if body is not None:
        slots = validate_body(body)
        template.body = body.strip()
        template.variable_descriptions_json = _validate_variable_descriptions(
            slots,
            variable_descriptions
            if variable_descriptions is not None
            else template.variable_descriptions_json,
        )
    elif variable_descriptions is not None:
        slots = validate_body(template.body or "")
        template.variable_descriptions_json = _validate_variable_descriptions(
            slots, variable_descriptions
        )
    if category is not None:
        template.category = category
    template.status = WhatsAppTemplateStatus.DRAFT
    template.rejection_reason = None
    session.commit()
    return template


def submit_template(session: Session, template: WhatsAppTemplate,
                    adapter) -> WhatsAppTemplate:
    """Submit a draft/rejected template to Meta review via the Business
    Management API (adapter.submit_template). status -> submitted."""
    if template.status not in _EDITABLE:
        raise TemplateError(
            f"only draft or rejected templates can be submitted "
            f"(current status: {template.status.value})"
        )
    # Re-validate right before submission — the db row could predate a
    # rule change.
    validate_body(template.body or "")
    validate_opt_out(template.body, template.category)
    # Meta needs an example value per {{n}}; each placeholder's description
    # (required by create_draft) is the example the reviewer sees.
    descriptions = template.variable_descriptions_json or {}
    slots = validate_body(template.body or "")
    result = adapter.submit_template(
        name=template.name,
        language=template.language,
        category=template.category.value,
        body=template.body or "",
        examples=[str(descriptions.get(str(n), "")) for n in sorted(slots)],
    )
    template.meta_template_id = result.get("id") or template.meta_template_id
    template.status = WhatsAppTemplateStatus.SUBMITTED
    template.rejection_reason = None
    session.commit()
    return template


# Meta template statuses, uppercase (webhooks/reference/
# message_template_status_update, read 2026-10-27). A template Meta has
# stopped "cannot be sent", so it leaves APPROVED here; we have no paused
# state, so it is held as REJECTED with Meta's status named in the reason.
# FLAGGED, LOCKED, IN_APPEAL and LIMIT_EXCEEDED do not change whether a
# template can be sent, and stay unmapped (logged, ignored).
_META_STOPPED = ("PAUSED", "DISABLED", "ARCHIVED", "DELETED", "PENDING_DELETION")
_META_STATUS_TO_OURS = {
    "APPROVED": WhatsAppTemplateStatus.APPROVED,
    "REINSTATED": WhatsAppTemplateStatus.APPROVED,
    "UNARCHIVED": WhatsAppTemplateStatus.APPROVED,
    "REJECTED": WhatsAppTemplateStatus.REJECTED,
    "PENDING": WhatsAppTemplateStatus.SUBMITTED,
    **{status: WhatsAppTemplateStatus.REJECTED for status in _META_STOPPED},
}


def apply_meta_status(session: Session, template: WhatsAppTemplate,
                      meta_status: str, reason: str | None = None,
                      meta_template_id: str | None = None) -> WhatsAppTemplate:
    """Apply a status reported by Meta (from a sync poll OR a webhook
    event) to our row. Unknown statuses are logged and ignored — never
    guessed."""
    mapped = _META_STATUS_TO_OURS.get((meta_status or "").upper())
    if mapped is None:
        logger.info("unmapped Meta template status %r for %s — ignored",
                    meta_status, template.name)
        return template
    template.status = mapped
    if meta_template_id:
        template.meta_template_id = meta_template_id
    status_upper = (meta_status or "").upper()
    if status_upper in _META_STOPPED:
        reason = f"Meta status {status_upper}: this template cannot be sent" + (
            f" ({reason})" if reason and reason.upper() != "NONE" else "")
    template.rejection_reason = (
        reason if mapped is WhatsAppTemplateStatus.REJECTED else None
    )
    session.commit()

    # Notify only on a terminal Meta decision - SUBMITTED/PENDING is noise.
    #
    # RECIPIENT: admins, not a user. whatsapp_templates has NO owner column
    # (see app/db/models.py::WhatsAppTemplate): one Meta WhatsApp Business
    # Account per install makes templates a deployment-wide shared resource,
    # which is why the API is login-required but deliberately not
    # owner-scoped. There is therefore no single user to notify, and pushing
    # an approval to every user would leak that the template exists to
    # accounts that never touched it. Admins are the people who submit and
    # fix templates.
    if mapped in (WhatsAppTemplateStatus.APPROVED, WhatsAppTemplateStatus.REJECTED):
        from app.services import notifications  # noqa: PLC0415
        from app.services.notification_service import NotificationService  # noqa: PLC0415

        approved = mapped is WhatsAppTemplateStatus.APPROVED
        for admin_id in NotificationService._admin_user_ids():
            notifications.dispatch(notifications.notify_whatsapp_template_status(
                admin_id, template.name, approved=approved,
            ))

    return template


def sync_template(session: Session, template: WhatsAppTemplate,
                  adapter) -> WhatsAppTemplate:
    """Pull the current review status from Meta and apply it."""
    if template.status in (WhatsAppTemplateStatus.DRAFT,):
        raise TemplateError("template has not been submitted yet — nothing to sync")
    data = adapter.fetch_template_status(
        name=template.name, language=template.language,
        meta_template_id=template.meta_template_id,
    )
    if data is None:
        logger.info("Meta returned no status for template %s (%s)",
                    template.name, template.language)
        return template
    return apply_meta_status(
        session, template,
        meta_status=data.get("status", ""),
        reason=data.get("rejected_reason") or data.get("reason"),
        meta_template_id=data.get("id"),
    )


def sendable_template(session: Session, name: str,
                      language: str) -> WhatsAppTemplate | None:
    """The highest APPROVED version for (name, language) — what the
    adapter's send() guard consults. None => not sendable."""
    return session.execute(
        select(WhatsAppTemplate)
        .where(WhatsAppTemplate.name == name,
               WhatsAppTemplate.language == language,
               WhatsAppTemplate.status == WhatsAppTemplateStatus.APPROVED)
        .order_by(WhatsAppTemplate.version.desc())
        .limit(1)
    ).scalars().first()


# --------------------------------------------------------------------------
# AI-assisted drafting (drafts ONLY — never auto-submitted)
# --------------------------------------------------------------------------

_GENERATE_SYSTEM = (
    "You are Vectorae's WhatsApp template drafter. Produce COMPLIANT Meta "
    "message-template candidates for B2B outreach. Respond with ONLY a JSON "
    'object: {"templates": [ ... ]} containing 2 or 3 items, each: '
    '{"name": "<lowercase_snake_case>", "language": "<e.g. en_US>", '
    '"category": "marketing" | "utility", "body": "<text>", '
    '"variable_descriptions": {"1": "...", ...}}. '
    "HARD RULES for every body: (1) identify the sender by name/company in "
    "the first sentence — no anonymous messages; (2) an honest, specific "
    "value proposition drawn from the provided strategy — no invented "
    "claims, statistics, urgency or guarantees; (3) a marketing template "
    f"must end with exactly this opt-out instruction: '{STANDARD_OPT_OUT}' "
    "(the word STOP stays in capitals -- it is the keyword, the one "
    "exception to rule 6); (4) variable "
    "placeholders exactly {{1}}, {{2}}... sequential from 1, each described "
    "in variable_descriptions; (5) body under "
    f"{settings.whatsapp_template_body_max_chars} characters; "
    "(6) no emojis walls, no ALL CAPS, no misleading familiarity."
)

_GENERATE_PROMPT = """STRATEGY CONTEXT

Persona / ICP (Phase 2 outputs):
{persona}

Messaging & offer design (Phase 6 outputs):
{messaging}

Draft 2-3 WhatsApp template candidates for the FIRST cold touch to this
persona, following every hard rule."""

# Phase 3 item 2. No enum on category: the code lower-cases it and treats any
# unknown label as marketing. variable_descriptions is keyed by placeholder
# number, so {{1}}..{{20}} are listed; validate_body/_validate_variable_descriptions
# remain the check that they match the body. The code defaults language,
# category and variable_descriptions, so only name and body are required.
TEMPLATE_DRAFTS_SCHEMA = {
    "type": "object",
    "properties": {
        "templates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "language": {"type": "string"},
                    "category": {"type": "string"},
                    "body": {"type": "string"},
                    "variable_descriptions": {
                        "type": "object",
                        "properties": {str(n): {"type": "string"} for n in range(1, 21)},
                        "additionalProperties": False,
                    },
                },
                "required": ["name", "body"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["templates"],
    "additionalProperties": False,
}


def _phase_outputs(session: Session, strategy: Strategy, phase: int,
                   limit_chars: int = 3000) -> str:
    steps = session.execute(
        select(ResearchStep)
        .where(ResearchStep.strategy_id == strategy.id,
               ResearchStep.pipeline == PipelineKind.STRATEGY,
               ResearchStep.phase == phase)
        .order_by(ResearchStep.step_no.asc())
    ).scalars().all()
    joined = "\n\n".join(f"[{s.step_id} {s.name}]\n{s.output}" for s in steps)
    return joined[:limit_chars] or "(no outputs recorded for this phase)"


_LINK_IN_BODY = re.compile(r"https?://|\bwww\.", re.I)
# Rule 2 of _GENERATE_SYSTEM: no guarantees and no false urgency.
_GUARANTEE_OR_URGENCY = re.compile(
    r"\bguarantee[ds]?\b|\btoday only\b|\blimited[- ]time\b|\bact now\b|"
    r"\blast chance\b|\bhurry\b|\bends (?:today|tonight|soon)\b", re.I)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_SLOT = re.compile(r"\{\{\s*\d+\s*\}\}")


def generated_body_problems(body: str, source: str) -> list[str]:
    """Why a model-drafted template body breaks the drafter's hard rules
    ([] = none). Deterministic. A drafted body has no link (nothing in the
    research is ours to link to), no guarantee or urgency, and no number the
    strategy research does not contain."""
    problems = []
    if _LINK_IN_BODY.search(body):
        problems.append("it contains a link")
    hits = sorted({m.group(0).lower() for m in _GUARANTEE_OR_URGENCY.finditer(body)})
    if hits:
        problems.append(f"it promises or pressures ({', '.join(hits)})")
    known = set(_NUMBER.findall(source or ""))
    invented = sorted({n for n in _NUMBER.findall(_SLOT.sub("", body))
                       if n not in known})
    if invented:
        problems.append(f"it states numbers not in the research ({', '.join(invented)})")
    return problems


def generate_drafts(session: Session, strategy: Strategy) -> list[WhatsAppTemplate]:
    """Claude-drafted candidates from the strategy's own research. Saved
    as DRAFTS; the user reviews and submits each explicitly. Candidates
    that fail local validation are dropped (logged), not 'fixed' silently."""
    persona = _phase_outputs(session, strategy, phase=2)
    messaging = _phase_outputs(session, strategy, phase=6)
    source = f"{persona}\n{messaging}"
    data = get_client().complete_json(
        system=_GENERATE_SYSTEM,
        prompt=_GENERATE_PROMPT.format(persona=persona, messaging=messaging),
        schema=TEMPLATE_DRAFTS_SCHEMA,
    )
    candidates = data.get("templates") or []
    if not isinstance(candidates, list):
        raise TemplateError("model returned no template list")

    created: list[WhatsAppTemplate] = []
    for cand in candidates[:3]:
        try:
            category = WhatsAppTemplateCategory(
                str(cand.get("category", "marketing")).lower()
            )
        except ValueError:
            category = WhatsAppTemplateCategory.MARKETING
        body = str(cand.get("body", "")).strip()
        problems = generated_body_problems(body, source)
        if problems:
            # T33: _GENERATE_SYSTEM's rules are not controls. A candidate that
            # breaks them is dropped like any other invalid one, not fixed.
            logger.info("dropped generated template %r: %s", cand.get("name"),
                        "; ".join(problems))
            continue
        if category is not WhatsAppTemplateCategory.MARKETING and is_promotional(body):
            # T33: the model's label is not trusted for a promotional body.
            logger.info("generated template %r labelled %s is promotional; saved as marketing",
                        cand.get("name"), category.value)
            category = WhatsAppTemplateCategory.MARKETING
        if (category is WhatsAppTemplateCategory.MARKETING and body
                and not has_opt_out_instruction(body)):
            # Product defect #28: add the standard instruction rather than
            # dropping an otherwise usable candidate. The person still reviews
            # every draft before it is submitted.
            body = f"{body} {STANDARD_OPT_OUT}"
        try:
            row = create_draft(
                session,
                name=str(cand.get("name", "")),
                language=str(cand.get("language", "en_US")),
                category=category,
                body=body,
                variable_descriptions=cand.get("variable_descriptions") or {},
            )
            created.append(row)
        except TemplateError as exc:
            logger.info("dropped invalid generated template %r: %s",
                        cand.get("name"), exc)
    if not created:
        raise TemplateError(
            "no generated candidate passed validation — try again or "
            "write a draft manually"
        )
    return created


def get_template(session: Session, template_id: uuid.UUID) -> WhatsAppTemplate:
    row = session.get(WhatsAppTemplate, template_id)
    if row is None:
        raise TemplateError("template not found")
    return row


def serialize(template: WhatsAppTemplate) -> dict:
    return {
        "id": str(template.id),
        "name": template.name,
        "language": template.language,
        "version": template.version,
        "category": template.category.value,
        "status": template.status.value,
        "body": template.body,
        "variable_descriptions": template.variable_descriptions_json or {},
        "rejection_reason": template.rejection_reason,
        "meta_template_id": template.meta_template_id,
        "supersedes_id": str(template.supersedes_id) if template.supersedes_id else None,
        "created_at": template.created_at.isoformat() if template.created_at else None,
        "updated_at": template.updated_at.isoformat() if template.updated_at else None,
    }
