"""Tamper-evident send/reply audit trail (Feature A2).

WHAT IS RECORDED. Every Outcome with event sent / opened / clicked / replied /
booked, for a lead, becomes one `activity_audit_events` row -- added by a
before_flush listener to the SAME flush as the Outcome, so the activity and its
audit record commit together or not at all. Outcomes are written from a dozen
places (the send task, the open pixel, the click redirect, three reply routers,
Calendly, the native calendar); a listener is the only way an event added next
year cannot be forgotten. The payload is ids, step, variant and classification
only -- never an email address, a name or message text -- so the trail can be
handed to a third party and outlives a GDPR erase.

HOW IT IS TAMPER-EVIDENT
  content_hash = sha256(canonical JSON of the record's content)   at insert
  chain_hash   = sha256(prev_hash + content_hash)                  at seal
  prev_hash    = the previous sealed record's chain_hash (GENESIS for #1)
Records are SEALED in (occurred_at, created_at, id) order by `seal()`, which
serialises per account (a PostgreSQL advisory lock) -- recording never races
for a sequence number inside a send. Editing any sealed record changes its
content_hash; deleting or inserting one breaks the next link; `verify_chain`
reports exactly where.

APPEND-ONLY, THREE LAYERS: the ORM guard below refuses deletes, content edits
and re-seals from any session; PostgreSQL refuses them with a trigger
(migration 0042) even for raw SQL; and a signed export pins the chain head, so
a trail truncated AFTER an export (the one edit a hash chain alone cannot
reveal -- removing the newest records) no longer matches what the buyer holds.

SIGNED REPORTS. `build_report` seals, verifies and returns the trail as JSON
signed with Ed25519. The key is AUDIT_SIGNING_KEY (base64 32-byte seed) or,
unset, derived from SECRET_KEY with HKDF -- stable for a deployment, published
at GET /audit/public-key, so a buyer can verify a report offline.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import uuid
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import event, inspect as sa_inspect, select, text
from sqlalchemy.orm import Session

from app.db.models import ActivityAuditEvent, Lead, Outcome, Product, Strategy

logger = logging.getLogger(__name__)

AUDITED_EVENTS = frozenset({"sent", "opened", "clicked", "replied", "booked"})
REPORT_VERSION = "leadpilot.activity-audit.v1"
GENESIS = "0" * 64
_SAFE_PAYLOAD_KEYS = ("step_no", "variant", "source", "classification", "canceled", "kind",
                      "action", "calendly_event", "booking_id", "call_id")
_CONTENT_ATTRS = ("id", "user_id", "lead_ref", "strategy_ref", "message_ref", "outcome_ref",
                  "event", "channel", "occurred_at", "payload_json", "content_hash",
                  "created_at")
_CHAIN_ATTRS = ("seq_no", "prev_hash", "chain_hash", "sealed_at")


class AuditTamperError(RuntimeError):
    """An attempt to delete, edit or re-seal an audit record."""


# --------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _s(value) -> str | None:
    return str(value) if value is not None else None


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str).encode()


def content_fields(row: ActivityAuditEvent) -> dict:
    return {
        "id": _s(row.id), "user_id": _s(row.user_id), "lead_ref": _s(row.lead_ref),
        "strategy_ref": _s(row.strategy_ref), "message_ref": _s(row.message_ref),
        "outcome_ref": _s(row.outcome_ref), "event": row.event, "channel": row.channel,
        "occurred_at": _iso(row.occurred_at), "payload": row.payload_json or {},
    }


def content_hash(fields: dict) -> str:
    return hashlib.sha256(canonical(fields)).hexdigest()


def chain_hash(prev: str, content: str) -> str:
    return hashlib.sha256(f"{prev}{content}".encode()).hexdigest()


# --------------------------------------------------------------------------
# Recording (before_flush) and the append-only guard
# --------------------------------------------------------------------------


def _event_value(outcome: Outcome) -> str:
    return str(getattr(outcome.event, "value", outcome.event) or "")


def _payload(outcome: Outcome) -> dict:
    meta = outcome.meta_json or {}
    out = {key: meta[key] for key in _SAFE_PAYLOAD_KEYS
           if key in meta and isinstance(meta[key], (str, int, float, bool))}
    # Only a variant the WRITER set: one filled in afterwards by
    # app/db/outcome_attribution.py is not attested in the hashed payload.
    from app.db import outcome_attribution  # noqa: PLC0415

    if outcome.variant and not outcome_attribution.variant_was_filled(outcome):
        out.setdefault("variant", outcome.variant)
    url = meta.get("url")
    if isinstance(url, str) and "://" in url:
        out["url_host"] = url.split("://", 1)[1].split("/", 1)[0].split("?", 1)[0][:200]
    return out


def record_outcome(session: Session, outcome: Outcome,
                   now: datetime | None = None) -> ActivityAuditEvent | None:
    """Add (no commit) the audit record for one new Outcome, if it is audited."""
    if outcome.lead_id is None or _event_value(outcome) not in AUDITED_EVENTS:
        return None
    row = session.execute(
        select(Product.user_id, Lead.strategy_id).select_from(Lead)
        .join(Strategy, Strategy.id == Lead.strategy_id)
        .join(Product, Product.id == Strategy.product_id)
        .where(Lead.id == outcome.lead_id)
    ).first()
    if row is None:
        return None
    owner_id, strategy_id = row
    if outcome.id is None:
        outcome.id = uuid.uuid4()  # the column default only fires at INSERT
    record = ActivityAuditEvent(
        id=uuid.uuid4(), user_id=owner_id, lead_ref=outcome.lead_id,
        strategy_ref=strategy_id, message_ref=outcome.message_id, outcome_ref=outcome.id,
        event=_event_value(outcome), channel=str(outcome.channel or "unknown")[:20],
        occurred_at=outcome.ts or now or datetime.now(timezone.utc),
        payload_json=_payload(outcome),
    )
    record.content_hash = content_hash(content_fields(record))
    session.add(record)
    return record


@event.listens_for(Session, "before_flush")
def _audit_before_flush(session: Session, _flush_context, _instances) -> None:
    _guard_append_only(session)
    try:
        new = [obj for obj in session.new if isinstance(obj, Outcome)
               and obj.lead_id is not None and _event_value(obj) in AUDITED_EVENTS]
        if not new:
            return
        with session.no_autoflush:
            for outcome in new:
                record_outcome(session, outcome)
    except Exception:  # noqa: BLE001 -- see BUILD_DECISIONS.md A2
        # A send has already left the building by the time its SENT outcome is
        # flushed; failing that commit would lose the record that it happened
        # at all. So a recording failure is logged loudly instead.
        logger.exception("AUDIT TRAIL: could not record an activity event")


def _guard_append_only(session: Session) -> None:
    for obj in session.deleted:
        if isinstance(obj, ActivityAuditEvent):
            raise AuditTamperError("activity_audit_events is append-only: delete refused")
    for obj in session.dirty:
        if not isinstance(obj, ActivityAuditEvent):
            continue
        state = sa_inspect(obj)
        for attr in _CONTENT_ATTRS:
            if state.attrs[attr].history.has_changes():
                raise AuditTamperError(f"activity_audit_events.{attr} is immutable")
        for attr in _CHAIN_ATTRS:
            history = state.attrs[attr].history
            if history.has_changes() and any(v is not None for v in history.deleted):
                raise AuditTamperError("a sealed audit record cannot be re-sealed")


def install() -> None:
    """No-op marker; importing this module registers the listener."""
    return None


# --------------------------------------------------------------------------
# Sealing and verification
# --------------------------------------------------------------------------


def _lock(session: Session, user_id) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                        {"key": f"audit-trail:{user_id}"})


def seal(session: Session, user_id, now: datetime | None = None) -> int:
    """Chain every unsealed record for one account. Commits. Returns count."""
    now = now or datetime.now(timezone.utc)
    _lock(session, user_id)
    head = session.execute(
        select(ActivityAuditEvent)
        .where(ActivityAuditEvent.user_id == user_id, ActivityAuditEvent.seq_no.isnot(None))
        .order_by(ActivityAuditEvent.seq_no.desc()).limit(1)
    ).scalar_one_or_none()
    prev = head.chain_hash if head is not None else GENESIS
    seq = head.seq_no if head is not None else 0
    pending = session.execute(
        select(ActivityAuditEvent)
        .where(ActivityAuditEvent.user_id == user_id, ActivityAuditEvent.chain_hash.is_(None))
        .order_by(ActivityAuditEvent.occurred_at, ActivityAuditEvent.created_at,
                  ActivityAuditEvent.id)
    ).scalars().all()
    sealed = 0
    for record in pending:
        if content_hash(content_fields(record)) != record.content_hash:
            # Altered BEFORE it was sealed. Sealing it would launder the edit
            # into a valid-looking chain; leave it out and let verify report it.
            logger.error("AUDIT TRAIL: unsealed record %s fails its content hash; not sealed",
                         record.id)
            continue
        seq += 1
        record.seq_no, record.prev_hash = seq, prev
        record.chain_hash = chain_hash(prev, record.content_hash)
        record.sealed_at = now
        prev = record.chain_hash
        sealed += 1
    session.commit()
    return sealed


def seal_all(session: Session) -> dict:
    user_ids = session.execute(
        select(ActivityAuditEvent.user_id).where(ActivityAuditEvent.chain_hash.is_(None))
        .distinct()
    ).scalars().all()
    total = 0
    for user_id in user_ids:
        try:
            total += seal(session, user_id)
        except Exception:  # noqa: BLE001 -- one account must not block the rest
            logger.exception("AUDIT TRAIL: sealing account %s failed", user_id)
            session.rollback()
    return {"accounts": len(user_ids), "sealed": total}


def verify_chain(session: Session, user_id) -> dict:
    rows = session.execute(
        select(ActivityAuditEvent).where(ActivityAuditEvent.user_id == user_id)
    ).scalars().all()
    sealed = sorted((r for r in rows if r.seq_no is not None), key=lambda r: r.seq_no)
    unsealed = [r for r in rows if r.seq_no is None]
    problems: list[dict] = []
    prev, expected = GENESIS, 1
    for record in sealed:
        if record.seq_no != expected:
            problems.append({"seq_no": record.seq_no, "problem": "sequence_gap",
                             "expected": expected})
            expected = record.seq_no
        if content_hash(content_fields(record)) != record.content_hash:
            problems.append({"seq_no": record.seq_no, "problem": "content_modified"})
        if record.prev_hash != prev:
            problems.append({"seq_no": record.seq_no, "problem": "broken_link"})
        if chain_hash(record.prev_hash or "", record.content_hash) != record.chain_hash:
            problems.append({"seq_no": record.seq_no, "problem": "chain_hash_mismatch"})
        prev, expected = record.chain_hash, expected + 1
    for record in unsealed:
        if content_hash(content_fields(record)) != record.content_hash:
            problems.append({"seq_no": None, "record_id": str(record.id),
                             "problem": "content_modified"})
    return {"valid": not problems, "sealed": len(sealed), "unsealed": len(unsealed),
            "head_seq_no": sealed[-1].seq_no if sealed else 0,
            "head_hash": sealed[-1].chain_hash if sealed else GENESIS,
            "problems": problems[:200]}


# --------------------------------------------------------------------------
# Signing
# --------------------------------------------------------------------------


def _private_key():
    from cryptography.hazmat.primitives import hashes  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: PLC0415
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: PLC0415

    from app.config import settings  # noqa: PLC0415

    configured = (settings.audit_signing_key or "").strip()
    if configured:
        seed = base64.b64decode(configured)
        if len(seed) != 32:
            raise ValueError("AUDIT_SIGNING_KEY must be base64 of exactly 32 bytes")
    else:
        from app.services.auth import _secret  # noqa: PLC0415 -- the deployment secret

        seed = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"leadpilot-audit-trail",
                    info=b"ed25519-signing-seed").derive(_secret().encode())
    return Ed25519PrivateKey.from_private_bytes(seed)


def public_key() -> dict:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: PLC0415

    raw = _private_key().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return {"algorithm": "Ed25519", "key_id": hashlib.sha256(raw).hexdigest()[:16],
            "public_key": base64.b64encode(raw).decode()}


def sign(document: dict) -> dict:
    key = public_key()
    signature = _private_key().sign(canonical(document))
    return {**key, "value": base64.b64encode(signature).decode(),
            "signed_bytes": "canonical JSON of the report with the `signature` key removed "
                            "(sorted keys, no whitespace, UTF-8)"}


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------


def record_out(record: ActivityAuditEvent) -> dict:
    return {**content_fields(record), "content_hash": record.content_hash,
            "seq_no": record.seq_no, "prev_hash": record.prev_hash,
            "chain_hash": record.chain_hash, "sealed_at": _iso(record.sealed_at)}


def build_report(session: Session, user, *, lead_id=None, date_from: date | None = None,
                 date_to: date | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    seal(session, user.id, now=now)
    chain = verify_chain(session, user.id)
    query = select(ActivityAuditEvent).where(ActivityAuditEvent.user_id == user.id,
                                             ActivityAuditEvent.seq_no.isnot(None))
    if lead_id is not None:
        query = query.where(ActivityAuditEvent.lead_ref == lead_id)
    if date_from is not None:
        query = query.where(ActivityAuditEvent.occurred_at
                            >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
    if date_to is not None:
        query = query.where(ActivityAuditEvent.occurred_at
                            < datetime.combine(date_to + timedelta(days=1), time.min,
                                               tzinfo=timezone.utc))
    records = session.execute(query.order_by(ActivityAuditEvent.seq_no)).scalars().all()
    report = {
        "report_version": REPORT_VERSION,
        "generated_at": _iso(now),
        "account": {"user_id": str(user.id)},
        "scope": {"lead_id": _s(lead_id), "date_from": date_from.isoformat() if date_from else None,
                  "date_to": date_to.isoformat() if date_to else None,
                  "complete_chain": lead_id is None and date_from is None and date_to is None},
        "chain": {"valid": chain["valid"], "sealed_records": chain["sealed"],
                  "head_seq_no": chain["head_seq_no"], "head_hash": chain["head_hash"],
                  "problems": chain["problems"]},
        "summary": dict(Counter(r.event for r in records)),
        "records": [record_out(r) for r in records],
    }
    report["signature"] = sign(report)
    return report


def verify_report(report: dict) -> dict:
    """Check a report someone hands back: the signature, and every record's
    own hashes. Pure -- reads nothing from the database."""
    from cryptography.exceptions import InvalidSignature  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: PLC0415

    result = {"signature_valid": False, "signed_by_this_deployment": False,
              "records_consistent": True, "links_consistent": True, "problems": []}
    if not isinstance(report, dict) or not isinstance(report.get("signature"), dict):
        result["problems"].append("no signature")
        return result
    signature = report["signature"]
    unsigned = {k: v for k, v in report.items() if k != "signature"}
    ours = public_key()
    result["signed_by_this_deployment"] = signature.get("public_key") == ours["public_key"]
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(ours["public_key"]))
        key.verify(base64.b64decode(str(signature.get("value") or "")), canonical(unsigned))
        result["signature_valid"] = True
    except (InvalidSignature, ValueError, TypeError):
        result["problems"].append("signature does not match this deployment's key")

    previous: dict | None = None
    for record in report.get("records") or []:
        fields = {k: record.get(k) for k in ("id", "user_id", "lead_ref", "strategy_ref",
                                             "message_ref", "outcome_ref", "event", "channel",
                                             "occurred_at")}
        fields["payload"] = record.get("payload") or {}
        if content_hash(fields) != record.get("content_hash") or \
                chain_hash(record.get("prev_hash") or "", record.get("content_hash") or "") \
                != record.get("chain_hash"):
            result["records_consistent"] = False
            result["problems"].append(f"record seq {record.get('seq_no')} fails its hashes")
        if previous is not None and record.get("seq_no") == (previous.get("seq_no") or 0) + 1 \
                and record.get("prev_hash") != previous.get("chain_hash"):
            result["links_consistent"] = False
            result["problems"].append(f"record seq {record.get('seq_no')} breaks the chain")
        previous = record
    result["valid"] = (result["signature_valid"] and result["records_consistent"]
                       and result["links_consistent"])
    return result


def render_pdf(report: dict) -> bytes:
    """A human-readable rendering of the signed report (Pillow, no system
    libraries). The JSON is the signed artefact; the PDF says so and carries
    the signature so it can be matched to it."""
    chain, sig = report.get("chain") or {}, report.get("signature") or {}
    header = [
        ("Vectorae activity audit trail", "title"),
        (f"Account {report['account']['user_id']}  ·  generated {report['generated_at']}", "body"),
        (f"Scope: lead={report['scope']['lead_id'] or 'all'}  from={report['scope']['date_from'] or '—'}"
         f"  to={report['scope']['date_to'] or '—'}", "body"),
        (f"Chain {'VALID' if chain.get('valid') else 'INVALID'} · {chain.get('sealed_records', 0)} sealed "
         f"records · head #{chain.get('head_seq_no')} {chain.get('head_hash', '')[:32]}…", "head"),
        ("Summary: " + ", ".join(f"{k} {v}" for k, v in sorted((report.get('summary') or {}).items()))
         or "Summary: no events", "body"),
        (f"Signature: {sig.get('algorithm')} key {sig.get('key_id')} — verify the JSON export at "
         "POST /audit/verify or with the key from GET /audit/public-key", "body"),
        ("", "body"),
        ("  #    occurred (UTC)              event     channel    lead         chain hash", "head"),
    ]
    rows = [(f"{r.get('seq_no') or '':>4}  {str(r.get('occurred_at'))[:23]:<24}  "
             f"{r.get('event', ''):<8}  {r.get('channel', ''):<9}  {str(r.get('lead_ref') or '')[:8]:<11}  "
             f"{str(r.get('chain_hash') or '')[:24]}", "body") for r in report.get("records") or []]
    footer = [("", "body"), ("Signature value:", "head")]
    value = str(sig.get("value") or "")
    footer += [(value[i:i + 90], "body") for i in range(0, len(value), 90)]
    return render_lines_pdf(header + rows + footer)


def render_lines_pdf(lines: list[tuple[str, str]]) -> bytes:
    """Lay `(text, style)` lines onto A4-proportioned pages and return a PDF.

    `style` is "title", "head" or "body". Pillow only, no system libraries.
    Shared by the audit-trail export above and the F08 meetings-per-dollar
    ledger (app/services/meetings_ledger.py)."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    from app.services.roi_card import _font  # noqa: PLC0415

    width, height, margin, line = 1240, 1754, 80, 26
    body_font = _font(15)
    fonts = {"title": _font(34, bold=True), "head": _font(18, bold=True), "body": body_font}

    pages = []
    per_page = (height - 2 * margin) // line
    for start in range(0, max(len(lines), 1), per_page):
        page = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(page)
        y = margin
        for content, style in lines[start:start + per_page]:
            draw.text((margin, y), content, font=fonts.get(style, body_font), fill=(16, 24, 40))
            y += line
        draw.text((margin, height - margin + 20),
                  f"Page {len(pages) + 1}", font=body_font, fill=(102, 112, 133))
        pages.append(page)
    buffer = io.BytesIO()
    pages[0].save(buffer, format="PDF", save_all=True, append_images=pages[1:], resolution=150)
    return buffer.getvalue()
