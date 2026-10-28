#!/usr/bin/env python3
"""Brand lint: no old product name outside the reviewed allowlist.

WHY
---
The product is Vectorae (docs/program/DECISIONS.md VEC-NAME, which reverses
VIC-NAME). Earlier rename passes (LeadPilot/ClientHunter -> Vectorae ->
Victorae -> Vectorae) left old names in places
people see, because find-and-replace cannot tell a display string from an
identifier a database row, a customer integration, an installed Android app
or a Redis key depends on. This check makes the split explicit and permanent:
every remaining occurrence of an old name must be covered by an entry in
`.brand-allowlist` that says why it stays, so a new one cannot slip back in.

WHAT IT CHECKS
--------------
Every git-tracked file: its PATH, and (for text files) every line, for the
old names in any case and with or without a separator:
    LeadPilot  Lead Pilot  lead-pilot  lead_pilot  lead.pilot  LEADPILOT ...
    ClientHunter  Client Hunter  client-hunter  client_hunter ...
    Victorae (the name used 2026-10-20..28, reverted to Vectorae)
Binary files (a NUL byte in the first 8 KiB) are checked by path only.

THE ALLOWLIST (.brand-allowlist, repo root)
-------------------------------------------
One entry per line:   <path glob> | <regex> | <B: or C:> <reason>
  * path glob   fnmatch-style, `*` also crosses `/` (docs/program/* = all below)
  * regex       Python regex searched in the line (or in the path, for a
                file-name hit). An occurrence is allowed when one match of the
                regex CONTAINS it, so an entry names the identifier, not just
                the file: `X-LeadPilot-[A-Za-z-]+`, not `.*`, wherever a
                narrower pattern exists.
  * reason      starts with `B:` (an identifier something outside the commit
                depends on) or `C:` (a founder decision: legal text, third-party
                names, dated historical records).
Blank lines and lines starting with `#` are comments. An entry that allows
nothing is an error (stale entries hide the real list).

Usage: python3 scripts/gate/brand_lint.py [--root DIR] [--allowlist FILE]
Exit 0 = clean, 1 = violations / stale or malformed entries, 2 = usage error.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

BRAND = re.compile(r"lead[ _.\-]?pilot|client[ _.\-]?hunter|victorae", re.IGNORECASE)
ALLOWLIST_NAME = ".brand-allowlist"
REASON_CLASSES = ("B:", "C:")
BINARY_SNIFF_BYTES = 8192


@dataclass
class Entry:
    lineno: int
    glob: str
    pattern: re.Pattern[str]
    reason: str
    used: int = 0

    def allows(self, path: str, text: str, start: int, end: int) -> bool:
        if not fnmatch.fnmatchcase(path, self.glob):
            return False
        return any(m.start() <= start and m.end() >= end for m in self.pattern.finditer(text))


@dataclass(frozen=True)
class Violation:
    path: str
    line: int          # 0 = the file's path itself
    column: int
    found: str
    text: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}:{self.column}" if self.line else f"{self.path} (file name)"
        return f"{where}: \"{self.found}\" | {self.text.strip()[:160]}"


@dataclass
class Report:
    violations: list[Violation] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations and not self.errors


def parse_allowlist(text: str) -> tuple[list[Entry], list[str]]:
    """Entries and malformed-line errors. Splits on the first two ' | '."""
    entries: list[Entry] = []
    errors: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(" | ", 2)
        if len(parts) != 3 or not all(p.strip() for p in parts):
            errors.append(f"{ALLOWLIST_NAME}:{lineno}: expected '<path glob> | <regex> | <reason>'")
            continue
        glob, pattern, reason = (p.strip() for p in parts)
        if not reason.startswith(REASON_CLASSES):
            errors.append(f"{ALLOWLIST_NAME}:{lineno}: reason must start with B: or C: ({reason[:40]!r})")
            continue
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            errors.append(f"{ALLOWLIST_NAME}:{lineno}: bad regex {pattern!r}: {exc}")
            continue
        entries.append(Entry(lineno, glob, compiled, reason))
    return entries, errors


def _is_binary(data: bytes) -> bool:
    return b"\0" in data[:BINARY_SNIFF_BYTES]


def _check_text(path: str, text: str, line_no: int, entries: list[Entry],
                report: Report) -> None:
    for match in BRAND.finditer(text):
        covering = [e for e in entries if e.allows(path, text, match.start(), match.end())]
        if covering:
            for entry in covering:
                entry.used += 1
        else:
            report.violations.append(
                Violation(path, line_no, match.start() + 1, match.group(0), text))


def lint(root: Path, files: list[str], allowlist_text: str) -> Report:
    report = Report()
    entries, report.errors = parse_allowlist(allowlist_text)
    for path in files:
        if path == ALLOWLIST_NAME:
            continue  # the allowlist necessarily names what it allows
        _check_text(path, path, 0, entries, report)
        try:
            data = (root / path).read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            continue  # deleted in the working tree, or a submodule
        if _is_binary(data):
            continue
        for line_no, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            _check_text(path, line, line_no, entries, report)
    for entry in entries:
        if entry.used == 0:
            report.errors.append(
                f"{ALLOWLIST_NAME}:{entry.lineno}: stale entry, allows nothing "
                f"({entry.glob} | {entry.pattern.pattern})")
    return report


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
    return [p for p in out.stdout.decode("utf-8").split("\0") if p]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="brand_lint", description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", default=None, help="repository root (default: git toplevel)")
    parser.add_argument("--allowlist", default=None, help=f"default: <root>/{ALLOWLIST_NAME}")
    args = parser.parse_args(argv)
    try:
        root = Path(args.root) if args.root else Path(subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
            check=True).stdout.strip())
        allowlist = Path(args.allowlist) if args.allowlist else root / ALLOWLIST_NAME
        allowlist_text = allowlist.read_text(encoding="utf-8")
        files = tracked_files(root)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"brand_lint: ERROR: {exc}", file=sys.stderr)
        return 2
    report = lint(root, files, allowlist_text)
    for error in report.errors:
        print(error)
    for violation in report.violations:
        print(violation)
    if report.ok:
        print(f"brand_lint: OK ({len(files)} files)")
        return 0
    print(f"brand_lint: {len(report.violations)} old-name occurrence(s) outside "
          f"{ALLOWLIST_NAME}, {len(report.errors)} allowlist error(s). Rename display "
          "text to Vectorae; add an identifier only with a B:/C: reason.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
