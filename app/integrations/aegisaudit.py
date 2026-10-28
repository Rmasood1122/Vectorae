"""AegisAudit — HTTP client wrapper.

Verified against AegisAudit packages/server/src/routes/audits.ts and
packages/shared/src/schemas:

  POST /api/audits              {prompt, answer, mode, providers, consentToSendData}
                                -> 202 {"auditId": str, "status": "running"}
  GET  /api/audits/{id}/events  SSE of AuditEvent:
                                  {"type": "progress",  "progress": {...}}
                                  {"type": "completed", "result": AuditResult}
                                  {"type": "failed",    "error": str, ...}
                                A finished audit is replayed as one `completed`
                                event, so subscribing late loses nothing.
  GET  /api/health              200 healthy, 503 degraded

AuditResult carries the verdict at result.decision.decision and the 0-100
score at result.scores.overall. This module blocks until the audit ends and
returns the AuditResult dict.

No auth — AegisAudit is an internal service.
"""
from __future__ import annotations

import json
import logging

import httpx

logger = logging.getLogger(__name__)

AUDIT_MODES = {"standard", "deep", "forensic", "maximum"}
PROVIDER_IDS = {"openrouter", "gemini", "deterministic", "mock"}
# Every value of AegisAudit's DECISIONS enum, best last.
DECISIONS = {"not_ready", "human_review", "review", "ready_for_human_review"}


class AegisAuditError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status
        self.permanent = status is not None and 400 <= status < 500


def _error_text(r: httpx.Response) -> str:
    try:
        body = r.json()
        return str(body.get("message") or body.get("error") or body)[:300]
    except ValueError:
        return r.text[:300]


def start_audit(
    client: httpx.Client,
    base_url: str,
    *,
    prompt: str,
    answer: str,
    mode: str,
    providers: list[str],
    consent_to_send_data: bool,
) -> str:
    r = client.post(base_url.rstrip("/") + "/api/audits", json={
        "prompt": prompt,
        "answer": answer,
        "mode": mode,
        "title": "Vectorae draft audit",
        "providers": providers,
        "consentToSendData": consent_to_send_data,
    })
    if r.status_code not in (200, 201, 202):
        raise AegisAuditError(f"AegisAudit returned HTTP {r.status_code}: {_error_text(r)}",
                              status=r.status_code)
    audit_id = r.json().get("auditId")
    if not audit_id:
        raise AegisAuditError("AegisAudit accepted the audit but returned no auditId")
    return audit_id


def wait_for_result(client: httpx.Client, base_url: str, audit_id: str) -> dict:
    url = base_url.rstrip("/") + f"/api/audits/{audit_id}/events"
    with client.stream("GET", url) as response:
        if response.status_code != 200:
            response.read()
            raise AegisAuditError(
                f"AegisAudit event stream returned HTTP {response.status_code}",
                status=response.status_code)
        # SSE: "data:" lines carry JSON AuditEvents; ":" lines are heartbeats.
        for line in response.iter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                logger.debug("AegisAudit non-JSON SSE line: %s", line[:200])
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "completed":
                return event.get("result") or {}
            if event.get("type") == "failed":
                raise AegisAuditError(f"AegisAudit audit failed: {event.get('error', 'unknown')}")
    raise AegisAuditError("AegisAudit event stream ended without a completed event")


def audit_response(
    *,
    base_url: str,
    prompt: str,
    answer: str,
    mode: str = "deep",
    providers: list[str] | None = None,
    consent_to_send_data: bool = False,
    timeout: float = 120.0,
) -> dict:
    """Run one audit to completion and return the AuditResult dict.

    Defaults to the local `deterministic` provider, which sends nothing out.
    Naming an external provider (openrouter, gemini) also requires
    consent_to_send_data=True, or AegisAudit refuses with 403.

    Raises AegisAuditError on HTTP errors, a failed audit, or a timeout.
    """
    if mode not in AUDIT_MODES:
        raise ValueError(f"mode must be one of {sorted(AUDIT_MODES)}, got {mode!r}")
    providers = providers or ["deterministic"]
    unknown = set(providers) - PROVIDER_IDS
    if unknown:
        raise ValueError(f"unknown AegisAudit providers: {sorted(unknown)}")

    # The read timeout bounds the gap between SSE frames; AegisAudit sends a
    # heartbeat every 15s, so a silent stream for `timeout` seconds is dead.
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
            audit_id = start_audit(client, base_url, prompt=prompt, answer=answer, mode=mode,
                                   providers=providers,
                                   consent_to_send_data=consent_to_send_data)
            return wait_for_result(client, base_url, audit_id)
    except httpx.RequestError as exc:
        logger.warning("AegisAudit request failed: %s", exc)
        raise AegisAuditError(
            f"Network error reaching AegisAudit ({type(exc).__name__}).") from exc


def health_check(base_url: str, timeout: float = 5.0) -> bool:
    """True if AegisAudit reports itself healthy (GET /api/health -> 200)."""
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(base_url.rstrip("/") + "/api/health")
            return r.status_code == 200
    except httpx.RequestError:
        return False
