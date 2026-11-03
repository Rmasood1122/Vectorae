#!/usr/bin/env python3
"""One-time LinkedIn off-boarding: end every Unipile session, purge scraped posts.

Founder decision 2026-11-02 (evals/vectorae, option a): Unipile and the
RapidAPI LinkedIn scraper are removed from Vectorae. After that code is gone,
Vectorae can no longer end the LinkedIn sessions Unipile holds for users who
connected an account, so this script does it ONCE, before the removal deploys.

WHAT IT DOES (dry run unless --apply)
  1. For EVERY row in linkedin_accounts not yet revoked (a paused account
     still holds a live session): DELETE https://<dsn>/api/v1/accounts/<id>
     at Unipile (which also revokes the LinkedIn session), then marks the row
     is_active=false, status='revoked'. A failure is reported and the row is
     left as it was, so a re-run retries it. Accounts a user disconnected
     before this script existed were deleted from our table without ending
     the session (evals/vectorae GAP-LI-03): only Unipile's dashboard can
     reach those, hence the backstop below. Credentials come from the environment, never a file:
       UNIPILE_DSN, UNIPILE_API_KEY
  2. Purges prospect LinkedIn data read through Unipile/RapidAPI:
       leads.linkedin_posts_json, leads.linkedin_posts_fetched_at,
       leads.enrichment_json["linkedin_posts"]
     (Phase 5B step 3.8: store no prospect profile data from LinkedIn.)

It prints counts only: never names, profile ids or post text.

USAGE (production: run by the founder, never by CI)
  DATABASE_URL=... UNIPILE_DSN=... UNIPILE_API_KEY=... \\
      python3 scripts/linkedin_unipile_offboard.py            # dry run
  ... python3 scripts/linkedin_unipile_offboard.py --apply    # do it
Then delete the accounts in Unipile's dashboard as a backstop, cancel the
Unipile subscription and revoke the RapidAPI key.

Exit codes: 0 done (or dry run), 1 some sessions could not be ended, 2 usage.
Self-contained on purpose (httpx + SQLAlchemy only): it must keep working
after app/integrations/unipile_linkedin.py is deleted.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection


@dataclass
class Report:
    accounts: int = 0
    ended: int = 0
    failed: int = 0
    leads_purged: int = 0
    errors: list[str] = field(default_factory=list)


def _base(dsn: str) -> str:
    return f"https://{dsn.strip().removeprefix('https://').rstrip('/')}/api/v1"


def end_sessions(conn: Connection, http: httpx.Client | None, dsn: str | None,
                 api_key: str | None, *, apply: bool, report: Report) -> None:
    rows = conn.execute(text(
        "SELECT id, unipile_account_id FROM linkedin_accounts "
        "WHERE status IS NULL OR status <> 'revoked'")).all()
    report.accounts = len(rows)
    if not rows or not apply:
        return
    if not (http and dsn and api_key):
        report.errors.append("UNIPILE_DSN and UNIPILE_API_KEY are required to end sessions")
        report.failed = len(rows)
        return
    for row_id, account_id in rows:
        try:
            resp = http.delete(f"{_base(dsn)}/accounts/{account_id}",
                               headers={"X-API-KEY": api_key, "accept": "application/json"})
        except httpx.HTTPError as exc:
            report.failed += 1
            report.errors.append(f"network error: {type(exc).__name__}")
            continue
        if resp.status_code in (200, 204, 404):     # 404: already gone at Unipile
            conn.execute(text("UPDATE linkedin_accounts SET is_active = :f, "
                              "status = 'revoked' WHERE id = :id"),
                         {"f": False, "id": row_id})
            report.ended += 1
        else:
            report.failed += 1
            report.errors.append(f"HTTP {resp.status_code}")


def purge_posts(conn: Connection, *, apply: bool, report: Report) -> None:
    import json  # noqa: PLC0415

    rows = conn.execute(text(
        "SELECT id, enrichment_json FROM leads WHERE linkedin_posts_json IS NOT NULL "
        "OR linkedin_posts_fetched_at IS NOT NULL OR enrichment_json IS NOT NULL")).all()
    targets = []
    for lead_id, enrichment in rows:
        data = enrichment
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                data = None
        has_enriched_posts = isinstance(data, dict) and "linkedin_posts" in data
        targets.append((lead_id, data, has_enriched_posts))
    flagged = set(conn.execute(text(
        "SELECT id FROM leads WHERE linkedin_posts_json IS NOT NULL "
        "OR linkedin_posts_fetched_at IS NOT NULL")).scalars())
    report.leads_purged = len(flagged | {t[0] for t in targets if t[2]})
    if not apply:
        return
    conn.execute(text("UPDATE leads SET linkedin_posts_json = NULL, "
                      "linkedin_posts_fetched_at = NULL WHERE linkedin_posts_json IS NOT NULL "
                      "OR linkedin_posts_fetched_at IS NOT NULL"))
    for lead_id, data, has_posts in targets:
        if has_posts:
            data = {k: v for k, v in data.items() if k != "linkedin_posts"}
            conn.execute(text("UPDATE leads SET enrichment_json = :d WHERE id = :id"),
                         {"d": json.dumps(data), "id": lead_id})


def run(database_url: str, *, apply: bool, dsn: str | None, api_key: str | None,
        http: httpx.Client | None = None) -> Report:
    report = Report()
    engine = create_engine(database_url)
    own_http = http is None and apply and bool(dsn and api_key)
    client = httpx.Client(timeout=20.0) if own_http else http
    try:
        with engine.begin() as conn:
            end_sessions(conn, client, dsn, api_key, apply=apply, report=report)
            purge_posts(conn, apply=apply, report=report)
    finally:
        if own_http and client is not None:
            client.close()
        engine.dispose()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
    args = ap.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    report = run(url, apply=args.apply, dsn=os.environ.get("UNIPILE_DSN"),
                 api_key=os.environ.get("UNIPILE_API_KEY"))
    mode = "APPLIED" if args.apply else "DRY RUN"
    print(f"{mode}: linkedin_accounts to end={report.accounts} ended={report.ended} "
          f"failed={report.failed}; leads with LinkedIn post data={report.leads_purged}")
    for err in sorted(set(report.errors)):
        print(f"  error: {err}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
