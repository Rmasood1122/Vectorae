"""Reply classification — one Claude call per inbound message.

Classes and their routing (implemented in app/services/sequence_engine.py
+ app/workers/outreach_tasks.py):
    interested / question / objection / not_interested
        -> sequence stops, lead becomes REPLIED, reply stored for the user
    unsubscribe_request  -> EXACTLY like an unsubscribe click: suppression
                            + hard stop, immediately
    out_of_office        -> sequence pauses and reschedules (does not stop)
    bounce               -> bounce outcome + campaign bounce-rate check

Victorae does NOT auto-reply to humans in M3.
"""

import logging
import re

from app.services import prompt_safety
from app.services.anthropic_client import JUDGE_TEMPERATURE, get_client

logger = logging.getLogger(__name__)

REPLY_CLASSES = [
    "interested",
    "question",
    "objection",
    "not_interested",
    "unsubscribe_request",
    "out_of_office",
    "bounce",
    # Feature Group 9: a machine, not a person (see app/services/reply_fraud.py).
    "automated_response",
]

_SYSTEM = (
    "You are Victorae's inbound reply classifier. Classify one inbound "
    "email into exactly one class. Respond with ONLY a JSON object: "
    '{"classification": "<class>"} where <class> is one of: '
    + ", ".join(REPLY_CLASSES) + ". "
    "Rules: any request to stop contacting (e.g. 'stop emailing me', "
    "'remove me', 'take me off your list') is unsubscribe_request. "
    "Automated delivery-failure notices (mailer-daemon, 'address not "
    "found', 'undeliverable') are bounce. Auto-replies about absence are "
    "out_of_office. Other automatic messages -- receipt acknowledgements, "
    "helpdesk tickets, no-reply bots -- are automated_response."
)

# Output contract (Phase 3 item 2): the class set the prompt names, which is
# exactly the set _classify maps. Sent only to models that support it.
CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {"classification": {"type": "string", "enum": list(REPLY_CLASSES)}},
    "required": ["classification"],
    "additionalProperties": False,
}

# F-AI-02: the sender, subject and body are written by whoever sent the email.
# They go in as delimited, escaped data, under an explicit notice.
_PROMPT = """{notice}

FROM: {from_address}
SUBJECT: {subject}

BODY:
{body}

Classify this inbound email."""


def classify_reply(from_address: str, subject: str | None, body: str,
                   session=None) -> str:
    """Returns one of REPLY_CLASSES; unparseable/unknown answers degrade to
    'question' (safe: stops the sequence and asks the human to look).

    Feature Group 9: an engaged class then gets the automated-reply second
    pass (reply_fraud.second_pass), unless the admin turned
    `reply_fraud_detection_enabled` off."""
    from app.services import reply_copilot, reply_fraud  # noqa: PLC0415

    label = _classify(from_address, subject, body)
    # A plain opt-out ("stop emailing me", "unsubscribe", "remove me", "delete
    # my data") is an unsubscribe_request whatever the model said: a mislabel
    # would leave the lead unsuppressed and the sequence running. Same
    # detector as Reply Copilot and reply intelligence; quoted text (our own
    # footer) and the sender's signature are ignored. Mail from a mailer
    # daemon keeps the model's label, so a bounce that quotes our footer is
    # still a bounce.
    if (label != "unsubscribe_request" and not _is_mailer_daemon(from_address)
            and reply_copilot.opt_out_language(_without_signature(body))):
        logger.warning("reply classified %r but asks to opt out; recorded as "
                       "unsubscribe_request", label)
        return "unsubscribe_request"

    enabled = True
    if session is not None:
        from app.services import system_settings  # noqa: PLC0415

        enabled = bool(system_settings.get(session, "reply_fraud_detection_enabled"))
    return reply_fraud.second_pass(label, from_address, subject, body, enabled=enabled)


_MAILER_DAEMON = re.compile(r"^(?:mailer-daemon|postmaster|mail-daemon)@", re.I)


# A signature starts at a line that is "--" (the "-- " delimiter, often
# without its trailing space). A newsletter footer in it is not an opt-out.
_SIGNATURE = re.compile(r"^--[ \t]*$", re.M)


def _without_signature(body: str | None) -> str:
    return _SIGNATURE.split(body or "", maxsplit=1)[0]


def _is_mailer_daemon(from_address: str | None) -> bool:
    address = (from_address or "").strip()
    if "<" in address:
        address = address.rsplit("<", 1)[1].rstrip(">")
    return bool(_MAILER_DAEMON.match(address.strip()))


def _classify(from_address: str, subject: str | None, body: str) -> str:
    try:
        data = get_client().complete_json(
            temperature=JUDGE_TEMPERATURE,
            system=_SYSTEM,
            prompt=_PROMPT.format(
                notice=prompt_safety.NOTICE,
                from_address=prompt_safety.inline(from_address),
                subject=prompt_safety.inline(subject or "(none)"),
                body=prompt_safety.block((body or "")[:4000]),
            ),
            schema=CLASSIFICATION_SCHEMA,
        )
        cls = str(data.get("classification", "")).strip().lower()
        if cls in REPLY_CLASSES:
            return cls
        logger.warning("classifier returned unknown class %r — using 'question'", cls)
    except Exception:
        logger.exception("reply classification failed — using 'question'")
    return "question"
