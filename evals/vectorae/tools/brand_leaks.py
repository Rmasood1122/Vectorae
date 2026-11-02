#!/usr/bin/env python3
"""Phase 1 brand-leak eval for the Vectorae program (evals/vectorae/).

Deterministic and offline. Scans every git-tracked file (path + text lines)
for the program regex, which is WIDER than scripts/gate/brand_lint.py: it also
matches calendarharvest / calendar harvest and leadpilot.com. Each occurrence
is classified into the program's classes:

  A  user-visible, must become Vectorae (URL slugs, media file names, UI text)
  B  externally bound (domain, mailboxes, OAuth, webhook headers, app id,
     service/host names, third-party listings) -> external_migration_register
  C  internal identifier (env vars, Celery names, DB names, SDK package, tests)
  D  legal / historical (non-affiliation statement, dated records, sealed tests)

Classification uses, in order: the path's surface, hard rules for the domain
and legal strings, then the reason of the covering .brand-allowlist entry
(the repo's reviewed classification). Rules are explicit below so a reviewer
can disagree with a specific one.

Usage:
  python3 evals/vectorae/tools/brand_leaks.py [--out evals/vectorae/brand_leaks.csv]
Prints a summary; exit 0 always (it is a measurement, not the gate --
brand_lint.py is the gate). --fail-on-a exits 1 if any class-A row exists.
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "gate"))
import brand_lint  # noqa: E402

PROGRAM_RE = re.compile(
    r"lead[\s_\-.]?pilot|calendar[\s_\-]?harvest|client[\s_\-]?hunter|victorae",
    re.IGNORECASE,
)

USER_SURFACES = (
    ("frontend/src/app/", "web: page/route"),
    ("frontend/src/components/", "web: component"),
    ("frontend/src/content/", "web: content data (rendered + JSON-LD)"),
    ("frontend/src/lib/", "web: client lib (strings may render)"),
    ("frontend/public/", "web: public asset"),
    ("frontend/android/app/src/main/res/", "android: resources"),
    ("frontend/android/", "android: project"),
    ("app/templates/", "email/pdf template"),
    ("app/", "backend (API responses, emails, logs)"),
    ("status-page/", "status page"),
    ("sdk/", "sdk"),
    ("plugins/", "claude plugin"),
    (".claude-plugin/", "claude plugin"),
)

# A: slugs and file names that ship in a public URL.
A_TOKEN = re.compile(
    r"what-is-leadpilot|leadpilot-vs-[\w-]+|leadpilot-walkthrough[\w.-]*"
    r"|apollo-clay-leadpilot-comparison",
    re.IGNORECASE,
)
DOMAIN = re.compile(r"calendar[\s_\-]?harvest", re.IGNORECASE)
LEGAL = re.compile(r"LeadPilot AB|named LeadPilot|formerly called LeadPilot|LeadPilot Enterprises"
                   r"|products named LeadPilot", re.IGNORECASE)

B_REASON = re.compile(
    r"header|OAuth scope|DNS TXT|applicationId|deep-link|App Links|keystore|Render service"
    r"|Vercel project|GitHub repository|social and directory|personal LinkedIn|client id registered"
    r"|plugin name|skill name|skill and plugin|MCP server name|hostnames/addresses|status-page deploy"
    r"|\bPlay\b|Firebase|consent-screen|PyPI|stored enum|key-derivation|stored with users|stored on users"
    r"|citation ids|URL slug|media file|published blog",
    re.IGNORECASE,
)
D_REASON = re.compile(r"dated|VIC-KEEP|founder text|founder decision|legal|third party|third-party"
                      r"|sealed", re.IGNORECASE)


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    return [p for p in out.stdout.decode().split("\0") if p]


def surface_of(path: str) -> str:
    if path.startswith(("tests/", "evals/", "scripts/", "frontend/src/tests/", "frontend/e2e/",
                        "frontend/tests/", "status-page/tests/", "sdk/tests/")):
        return "internal: tests/evals/scripts"
    if path.startswith("docs/help-center/"):
        return "docs: help-center source (published under /help/)"
    for prefix, name in USER_SURFACES:
        if path.startswith(prefix):
            return name
    if path.startswith("docs/") or path.endswith(".md"):
        return "internal: docs/records"
    return "internal: config/other"


def classify(path: str, line: str, match: str, entry_reason: str | None) -> tuple[str, str, str]:
    """-> (class, user_visible y/n, fix)"""
    surface = surface_of(path)
    # Backend code and the SDK are not rendered as-is: an identifier there is
    # only user-visible if it leaves in a response/header, which the allowlist
    # reason says (B). Display surfaces are the web, Android, help, plugins.
    internal = surface.startswith(("internal", "backend", "sdk"))
    if LEGAL.search(line) and re.search(r"lead\s?pilot", match, re.I):
        return "D", "y" if not internal else "n", "Founder/counsel decision; keep (non-affiliation / formerly / third-party)"
    if DOMAIN.fullmatch(match.replace(" ", "")) or DOMAIN.search(match):
        return ("B", "y" if not internal else "n",
                "Domain/mailbox: do not change; see domain_migration_plan.md (STOP GATE)")
    token = A_TOKEN.search(line)
    if token and token.start() <= line.lower().find(match.lower()) < token.end() + 1 and not internal:
        return "A", "y", "Rename to vectorae-*, add single-hop 301 in frontend/vercel.json, update canonical/sitemap/links"
    if entry_reason:
        if entry_reason.startswith("C:") and D_REASON.search(entry_reason):
            return "D", "n" if internal else "y", "Keep: " + entry_reason[:120]
        if B_REASON.search(entry_reason):
            return "B", "n" if internal else "y", "External dependency: register + founder action. " + entry_reason[:100]
        return "C", "n", "Internal identifier; separate tested refactor PR only. " + entry_reason[:100]
    if internal:
        return "C", "n", "Internal; review"
    return "A", "y", "UNALLOWLISTED user-surface occurrence: rename to Vectorae"


def scan() -> list[dict]:
    entries, _ = brand_lint.parse_allowlist((ROOT / ".brand-allowlist").read_text())
    rows: dict[tuple, dict] = {}
    for path in tracked_files():
        if path == ".brand-allowlist" or path.startswith("evals/vectorae/"):
            continue
        candidates = [(0, path)]
        try:
            data = (ROOT / path).read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            data = b""
        if data and not brand_lint._is_binary(data):
            candidates += list(enumerate(data.decode("utf-8", "replace").splitlines(), 1))
        for line_no, text in candidates:
            for m in PROGRAM_RE.finditer(text):
                reason = next((e.reason for e in entries
                               if e.allows(path, text, m.start(), m.end())), None)
                klass, visible, fix = classify(path, text, m.group(0), reason)
                key = (path, m.group(0).lower(), klass)
                row = rows.setdefault(key, {
                    "surface": surface_of(path) + (" [file name]" if line_no == 0 else ""),
                    "path": path, "lines": [], "match": m.group(0), "class": klass,
                    "user_visible": visible, "fix": fix,
                    "owner": "Rehan" if klass in ("B", "D") else "eng",
                    "status": "open" if klass == "A" else ("acknowledged" if klass != "C" else "documented"),
                })
                if line_no and len(row["lines"]) < 25:
                    row["lines"].append(line_no)
    out = []
    for i, row in enumerate(sorted(rows.values(), key=lambda r: ("ABCD".index(r["class"]), r["path"])), 1):
        lines = ",".join(map(str, row.pop("lines")))
        row["location"] = f"{row.pop('path')}:{lines}" if lines else row.pop("path", "")
        out.append({"id": f"BL-{i:04d}", **row})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "evals/vectorae/brand_leaks.csv"))
    ap.add_argument("--fail-on-a", action="store_true")
    args = ap.parse_args(argv)
    rows = scan()
    cols = ["id", "surface", "location", "match", "class", "user_visible", "fix", "owner", "status"]
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    counts = defaultdict(int)
    vis = defaultdict(int)
    for r in rows:
        counts[r["class"]] += 1
        if r["user_visible"] == "y":
            vis[r["class"]] += 1
    print(f"brand_leaks: {len(rows)} (file, match, class) rows -> {args.out}")
    for k in "ABCD":
        print(f"  class {k}: {counts[k]} rows, user-visible {vis[k]}")
    return 1 if args.fail_on_a and counts["A"] else 0


if __name__ == "__main__":
    sys.exit(main())
