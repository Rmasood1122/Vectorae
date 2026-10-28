"""scripts/gate/brand_lint.py: no old product name outside .brand-allowlist.

The product is Vectorae (docs/program/DECISIONS.md VEC-NAME). What must hold:
  * every old-name variant is caught -- LeadPilot, ClientHunter and Victorae,
    in any case, with or without a space, hyphen, underscore or dot -- in file
    contents AND in file names;
  * an allowlist entry allows exactly what its regex contains on matching
    paths, nothing else on the same line and nothing in other files;
  * malformed entries, entries without a B:/C: reason and entries that allow
    nothing are errors;
  * binary files are checked by name only;
  * THE REPOSITORY ITSELF IS CLEAN. This last test is what makes the check
    permanent: it runs in the offline suite, so the CI `gate` job (which
    runs the whole suite) fails when an old name comes back.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gate" / "brand_lint.py"


def _load():
    spec = importlib.util.spec_from_file_location("brand_lint", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["brand_lint"] = module
    spec.loader.exec_module(module)
    return module


bl = _load()


def _lint(tmp_path, files: dict[str, str | bytes], allowlist: str = ""):
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    return bl.lint(tmp_path, sorted(files), allowlist)


VARIANTS = [
    "LeadPilot", "leadpilot", "LEADPILOT", "Leadpilot", "lEaDpIlOt",
    "Lead Pilot", "lead pilot", "lead-pilot", "Lead-Pilot", "lead_pilot", "LEAD_PILOT",
    "lead.pilot",
    "ClientHunter", "clienthunter", "CLIENTHUNTER", "Clienthunter",
    "Client Hunter", "client hunter", "client-hunter", "client_hunter", "CLIENT_HUNTER",
    "client.hunter",
    "Victorae", "victorae", "VICTORAE", "ViCtOrAe",
    "LeadPilot Enterprises", "ClientHunter Enterprise",
]


class TestVariants:
    @pytest.mark.parametrize("variant", VARIANTS)
    def test_every_variant_is_caught_in_contents(self, tmp_path, variant):
        report = _lint(tmp_path, {"page.md": f"Welcome to {variant} today.\n"})
        assert len(report.violations) == 1, report.violations
        assert report.violations[0].line == 1

    @pytest.mark.parametrize("variant", ["leadpilot", "Lead_Pilot", "client-hunter",
                                         "ClientHunter", "victorae"])
    def test_every_variant_is_caught_in_file_names(self, tmp_path, variant):
        report = _lint(tmp_path, {f"docs/{variant}-guide.md": "nothing here\n"})
        assert [(v.path, v.line) for v in report.violations] == [
            (f"docs/{variant}-guide.md", 0)]

    @pytest.mark.parametrize("clean", ["Vectorae", "vectorae", "VECTORAE",
                                       "client hunting",
                                       "vector", "victor", "leadpilo"])
    def test_the_new_name_and_near_misses_pass(self, tmp_path, clean):
        assert _lint(tmp_path, {"a.md": f"{clean}\n"}).ok

    def test_two_occurrences_on_a_line_are_two_violations(self, tmp_path):
        report = _lint(tmp_path, {"a.md": "LeadPilot and ClientHunter\n"})
        assert [v.column for v in report.violations] == [1, 15]

    def test_binary_files_are_checked_by_name_only(self, tmp_path):
        report = _lint(tmp_path, {"logo.png": b"\x89PNG\0\0LeadPilot wordmark",
                                  "leadpilot.png": b"\0\0"})
        assert [(v.path, v.line) for v in report.violations] == [("leadpilot.png", 0)]


class TestAllowlist:
    def test_an_entry_allows_what_its_regex_contains(self, tmp_path):
        allow = "app/* | X-LeadPilot-[A-Za-z-]+ | B: webhook header customers verify\n"
        report = _lint(tmp_path, {"app/hooks.py": 'HEADER = "X-LeadPilot-Signature"\n'}, allow)
        assert report.ok, report.violations

    def test_it_does_not_allow_the_rest_of_the_line(self, tmp_path):
        allow = "app/* | X-LeadPilot-[A-Za-z-]+ | B: webhook header\n"
        report = _lint(tmp_path, {"app/hooks.py": '"X-LeadPilot-Signature"  # LeadPilot\n'},
                       allow)
        assert [v.column for v in report.violations] == [28]

    def test_it_does_not_allow_other_paths(self, tmp_path):
        allow = "app/* | X-LeadPilot-[A-Za-z-]+ | B: webhook header\n"
        report = _lint(tmp_path, {"app/hooks.py": "X-LeadPilot-Event\n",
                                  "docs/hooks.md": "X-LeadPilot-Event\n"}, allow)
        assert [v.path for v in report.violations] == ["docs/hooks.md"]

    def test_the_glob_crosses_directories(self, tmp_path):
        allow = "docs/program/* | .* | C: dated records\n"
        report = _lint(tmp_path, {"docs/program/plans/old.md": "LeadPilot\n"}, allow)
        assert report.ok

    def test_it_covers_file_names_too(self, tmp_path):
        allow = "plugins/* | leadpilot | B: plugin name users installed\n"
        report = _lint(tmp_path, {"plugins/leadpilot/plugin.json": "{}\n"}, allow)
        assert report.ok

    def test_a_partial_match_does_not_allow(self, tmp_path):
        # The regex must CONTAIN the occurrence: "Lead" alone does not.
        allow = "* | Lead | B: wrong\n"
        report = _lint(tmp_path, {"a.md": "LeadPilot\n"}, allow)
        assert len(report.violations) == 1

    def test_comments_and_blank_lines_are_ignored(self, tmp_path):
        allow = "# a comment\n\n* | LeadPilot AB | C: another company\n"
        assert _lint(tmp_path, {"a.md": "LeadPilot AB\n"}, allow).ok

    @pytest.mark.parametrize("line, message", [
        ("* | LeadPilot", "expected"),
        ("* | LeadPilot | because", "B: or C:"),
        ("* | (unclosed | B: x", "bad regex"),
        (" | LeadPilot | B: x", "expected"),
    ])
    def test_a_malformed_entry_is_an_error(self, tmp_path, line, message):
        report = _lint(tmp_path, {"a.md": "fine\n"}, line + "\n")
        assert any(message in e for e in report.errors), report.errors

    def test_an_entry_that_allows_nothing_is_an_error(self, tmp_path):
        report = _lint(tmp_path, {"a.md": "fine\n"}, "* | LeadPilot | B: stale\n")
        assert any("stale entry" in e for e in report.errors)

    def test_the_allowlist_file_itself_is_not_linted(self, tmp_path):
        report = _lint(tmp_path, {bl.ALLOWLIST_NAME: "* | LeadPilot | B: x\n",
                                  "a.md": "LeadPilot\n"},
                       "* | LeadPilot | B: x\n")
        assert report.ok


class TestCommandLine:
    def test_exit_codes(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        (tmp_path / "a.md").write_text("Vectorae\n", encoding="utf-8")
        (tmp_path / bl.ALLOWLIST_NAME).write_text("", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        run = [sys.executable, str(SCRIPT), "--root", str(tmp_path)]
        assert subprocess.run(run, capture_output=True).returncode == 0
        (tmp_path / "b.md").write_text("LeadPilot\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        out = subprocess.run(run, capture_output=True, text=True)
        assert out.returncode == 1
        assert 'b.md:1:1: "LeadPilot"' in out.stdout
        missing = subprocess.run(run + ["--allowlist", str(tmp_path / "nope")],
                                 capture_output=True)
        assert missing.returncode == 2


def test_every_allowlist_entry_says_why_and_is_class_b_or_c():
    entries, errors = bl.parse_allowlist((ROOT / bl.ALLOWLIST_NAME).read_text(encoding="utf-8"))
    assert errors == []
    assert entries
    for entry in entries:
        assert entry.reason[:2] in ("B:", "C:")
        assert len(entry.reason) > 15, f"line {entry.lineno}: reason too short"


def test_the_repository_has_no_old_name_outside_the_allowlist():
    report = bl.lint(ROOT, bl.tracked_files(ROOT),
                     (ROOT / bl.ALLOWLIST_NAME).read_text(encoding="utf-8"))
    problems = report.errors + [str(v) for v in report.violations]
    assert not problems, (
        f"{len(problems)} problem(s). Rename display text to Vectorae, or add an "
        "identifier to .brand-allowlist with a B:/C: reason:\n" + "\n".join(problems[:40]))
