"""Vectorae CLI — main Typer application.

Registered commands::

    clienthunter init               First-time setup
    clienthunter run                Intake wizard + live pipeline
    clienthunter status             Strategy / campaign table and detail view
    clienthunter serve [stop|logs]  Self-host via Docker Compose
    clienthunter leads [list|export]
    clienthunter campaign [pause|resume]
    clienthunter version

Entry point declared in pyproject.toml::

    [project.scripts]
    clienthunter = "clienthunter.cli:app"
"""
from __future__ import annotations

import typer

from clienthunter._version import __version__
from clienthunter.cli._campaign import campaign_app
from clienthunter.cli._console import console
from clienthunter.cli._init import init
from clienthunter.cli._leads import leads_app
from clienthunter.cli._run import run
from clienthunter.cli._serve import serve_app
from clienthunter.cli._status import status

# ---------------------------------------------------------------------------
# Root application
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="clienthunter",
    help=(
        "Vectorae — AI-powered client acquisition.\n\n"
        "Start here: [bold]clienthunter init[/bold]\n"
        "Launch a campaign: [bold]clienthunter run[/bold]\n"
        "Watch progress: [bold]clienthunter status[/bold]"
    ),
    add_completion=True,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,  # never show local vars (could contain tokens)
    pretty_exceptions_short=True,
)

# ---------------------------------------------------------------------------
# Top-level commands (registered as functions, not sub-apps)
# ---------------------------------------------------------------------------

app.command("init", help="Interactive first-time setup — configure and authenticate.")(init)
app.command("run", help="Intake wizard: product → past clients → pipeline → live progress.")(run)
app.command("status", help="Pipeline progress, verification passes, and campaign stats.")(status)

# ---------------------------------------------------------------------------
# Sub-apps
# ---------------------------------------------------------------------------

app.add_typer(serve_app, name="serve")
app.add_typer(leads_app, name="leads")
app.add_typer(campaign_app, name="campaign")

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------


@app.command("version")
def version_cmd(
    json_out: bool = typer.Option(False, "--json", help="JSON output."),
) -> None:
    """Print the installed package version."""
    if json_out:
        from clienthunter.cli._console import print_json
        print_json({"version": __version__})
        return
    console.print(f"clienthunter {__version__}")
