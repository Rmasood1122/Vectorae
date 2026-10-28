"""SIGNALFORGE — Control API client.

Verified against SIGNALFORGE app/control/{api,commands,envelope,auth}.py:

  POST /control/commands/{name}   header X-Control-Key: <CLAUDE_OPERATOR_KEY>
      Always HTTP 200 with an envelope:
        {"success": bool, "data": {...} | null,
         "errors": [{"code": str, "message": str, "details": {}}], ...}
      Branch on errors[0].code, never on the HTTP status.
  GET  /control/health            same key; a success envelope means the key
                                  is valid. (/control/capabilities is public
                                  and proves nothing about the key.)

Commands used, and the scope each needs:
  start_research   RESEARCH       -> {research_job_id, final_status, draft_id,
                                      icp_score, warnings}  (synchronous, <=300s)
  get_icp_score    READ           -> {overall_score, breakdown, icp_config_version}
  get_prospect     READ           -> {..., latest_draft: email_drafts row}

Vectorae's operator key holds the default scopes only: READ, RESEARCH,
AI_GENERATION. It never creates prospects (that needs WRITE) — the user
creates them in their own SIGNALFORGE instance and Vectorae works from the
prospect_id. APPROVAL and SEND are never used — the SQL gate in SIGNALFORGE
alone decides whether anything leaves, and Vectorae must never be a way
around it.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CONTROL_KEY_HEADER = "X-Control-Key"
TONES = {"PROFESSIONAL", "CONVERSATIONAL", "CONCISE"}
RESEARCH_DEPTHS = {"QUICK", "STANDARD", "DEEP"}

# Envelope error codes that retrying cannot fix.
PERMANENT_CODES = {
    "UNKNOWN_COMMAND", "INVALID_INPUT", "PERMISSION_DENIED", "UNAUTHENTICATED",
    "WRONG_CREDENTIAL_TYPE", "APPROVAL_REQUIRED", "NOT_FOUND", "COMMAND_DISABLED",
}

# start_research is synchronous and SIGNALFORGE allows it 300s; leave headroom.
RESEARCH_TIMEOUT_SECONDS = 330.0


class SignalForgeError(Exception):
    def __init__(self, message: str, status: int | None = None, error_code: str | None = None):
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        if error_code is not None:
            self.permanent = error_code in PERMANENT_CODES
        else:
            self.permanent = status is not None and 400 <= status < 500 and status != 429


def _post_command(
    *,
    base_url: str,
    operator_key: str,
    command: str,
    payload: dict[str, Any],
    timeout: float = 60.0,
) -> dict:
    url = base_url.rstrip("/") + f"/control/commands/{command}"
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=payload, headers={CONTROL_KEY_HEADER: operator_key})
    except httpx.RequestError as exc:
        logger.warning("SIGNALFORGE request failed: %s", exc)
        raise SignalForgeError(
            f"Network error reaching SIGNALFORGE ({type(exc).__name__}).") from exc

    try:
        envelope = r.json()
    except ValueError:
        envelope = None
    if r.status_code != 200 or not isinstance(envelope, dict):
        raise SignalForgeError(f"SIGNALFORGE returned HTTP {r.status_code}: {r.text[:300]}",
                               status=r.status_code)

    if not envelope.get("success"):
        errors = envelope.get("errors") or [{}]
        first = errors[0] if isinstance(errors[0], dict) else {}
        code = first.get("code") or "UNKNOWN"
        raise SignalForgeError(f"{command}: {code}: {first.get('message', 'command failed')}",
                               error_code=code)
    return envelope.get("data") or {}


def start_research(*, base_url: str, operator_key: str, prospect_id: int,
                   tone: str = "PROFESSIONAL") -> dict:
    """Run research up to the human approval gate. Blocks for up to ~300s.

    Idempotent in SIGNALFORGE: an already-active job is returned, not duplicated.
    """
    if tone not in TONES:
        raise ValueError(f"tone must be one of {sorted(TONES)}, got {tone!r}")
    return _post_command(base_url=base_url, operator_key=operator_key,
                         command="start_research",
                         payload={"prospect_id": prospect_id, "tone": tone},
                         timeout=RESEARCH_TIMEOUT_SECONDS)


def get_icp_score(*, base_url: str, operator_key: str, prospect_id: int) -> dict:
    """ICP score: {overall_score, breakdown, icp_config_version}."""
    return _post_command(base_url=base_url, operator_key=operator_key,
                         command="get_icp_score", payload={"prospect_id": prospect_id})


def get_prospect(*, base_url: str, operator_key: str, prospect_id: int) -> dict:
    """Full prospect summary, including latest_draft (the newest email_drafts row)."""
    return _post_command(base_url=base_url, operator_key=operator_key,
                         command="get_prospect", payload={"prospect_id": prospect_id})


def health_check(*, base_url: str, operator_key: str) -> bool:
    """True if SIGNALFORGE is reachable AND accepts the operator key."""
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(base_url.rstrip("/") + "/control/health",
                           headers={CONTROL_KEY_HEADER: operator_key})
        return r.status_code == 200 and r.json().get("success") is True
    except Exception:
        return False
