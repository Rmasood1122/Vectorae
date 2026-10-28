"""
`clienthunter serve` — Start the Vectorae backend locally.

New in M8-C3: --check-env flag validates all required environment variables
before starting the server, so the user gets a clear diagnostic instead of
a cryptic startup error.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich import print as rprint

app = typer.Typer()
console = Console()


# ---------------------------------------------------------------------------
# Required environment variables and their descriptions
# ---------------------------------------------------------------------------

REQUIRED_VARS: list[tuple[str, str, str]] = [
    # (name, description, validator_hint)
    ("SECRET_KEY", "JWT signing key (64+ char hex)", "length >= 32"),
    ("ENCRYPTION_KEY", "Fernet key for secrets at rest", "length >= 44"),
    ("DATABASE_URL", "PostgreSQL connection URL", "starts with postgresql://"),
    ("REDIS_URL", "Redis connection URL", "starts with redis://"),
    ("ANTHROPIC_API_KEY", "Anthropic API key", "starts with sk-ant-"),
]

OPTIONAL_BUT_WARNED_VARS: list[tuple[str, str]] = [
    ("APOLLO_API_KEY", "Apollo.io — required for lead sourcing"),
    ("HUNTER_API_KEY", "Hunter.io — required for email verification"),
    ("GMAIL_CLIENT_ID", "Gmail — required for email outreach"),
    ("GMAIL_CLIENT_SECRET", "Gmail — required for email outreach"),
    ("WHATSAPP_PHONE_NUMBER_ID", "WhatsApp — required for WA outreach"),
    ("WHATSAPP_APP_SECRET", "WhatsApp — required for webhook signature verification"),
    ("CALENDLY_CLIENT_ID", "Calendly — required for booking integration"),
    ("CALENDLY_WEBHOOK_SECRET", "Calendly — required for webhook signature verification"),
    ("FIREBASE_PROJECT_ID", "Firebase — required for push notifications"),
]


def _validate_var(name: str, hint: str) -> tuple[bool, str]:
    """Validate a single env var. Returns (is_valid, detail_message)."""
    value = os.getenv(name, "")
    if not value:
        return False, "NOT SET"

    if hint == "length >= 32" and len(value) < 32:
        return False, f"TOO SHORT ({len(value)} chars, need 32+)"

    if hint == "length >= 44" and len(value) < 44:
        return False, f"TOO SHORT ({len(value)} chars, need 44+)"

    if hint.startswith("starts with "):
        prefix = hint.removeprefix("starts with ")
        if not value.startswith(prefix):
            return False, f"Expected to start with '{prefix}'"

    return True, "OK"


def check_env() -> tuple[bool, list[dict]]:
    """
    Run all environment variable checks.
    Returns (all_required_ok, results_list).
    """
    results = []
    all_ok = True

    for name, description, hint in REQUIRED_VARS:
        is_valid, detail = _validate_var(name, hint)
        if not is_valid:
            all_ok = False
        results.append({
            "name": name,
            "description": description,
            "status": "✓" if is_valid else "✗",
            "detail": detail,
            "required": True,
            "ok": is_valid,
        })

    for name, description in OPTIONAL_BUT_WARNED_VARS:
        value = os.getenv(name, "")
        is_set = bool(value)
        results.append({
            "name": name,
            "description": description,
            "status": "✓" if is_set else "⚠",
            "detail": "OK" if is_set else "NOT SET (feature disabled)",
            "required": False,
            "ok": is_set,
        })

    return all_ok, results


def print_env_report(results: list[dict]) -> None:
    """Print a Rich table of env var check results."""
    table = Table(title="Environment Check", show_lines=True)
    table.add_column("Variable", style="bold")
    table.add_column("Required", justify="center")
    table.add_column("Status", justify="center")
    table.add_column("Detail")
    table.add_column("Purpose")

    for r in results:
        status_style = "green" if r["ok"] else ("red" if r["required"] else "yellow")
        table.add_row(
            r["name"],
            "YES" if r["required"] else "no",
            f"[{status_style}]{r['status']}[/{status_style}]",
            f"[{status_style}]{r['detail']}[/{status_style}]",
            r["description"],
        )

    console.print(table)


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", help="Host to bind"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on"),
    workers: int = typer.Option(1, "--workers", "-w", help="Number of API workers"),
    check_env_only: bool = typer.Option(
        False, "--check-env",
        help="Validate environment variables and exit (don't start the server)",
    ),
    reload: bool = typer.Option(False, "--reload", help="Enable hot reload (development only)"),
) -> None:
    """
    Start the Vectorae backend server.

    Use --check-env to validate your environment before the first launch:
        clienthunter serve --check-env
    """
    all_ok, results = check_env()
    print_env_report(results)

    if check_env_only:
        if all_ok:
            rprint("\n[green]✓ All required environment variables are set.[/green]")
            rprint("You can start the server with: [bold]clienthunter serve[/bold]")
        else:
            rprint("\n[red]✗ Required environment variables are missing.[/red]")
            rprint("Copy [bold].env.production.example[/bold] → [bold].env.production[/bold] and fill in the missing values.")
            rprint("See [bold]DEPLOY.md[/bold] for setup instructions.")
        raise typer.Exit(code=0 if all_ok else 1)

    if not all_ok:
        rprint("\n[red]✗ Cannot start: required environment variables are missing (see above).[/red]")
        rprint("Use [bold]clienthunter serve --check-env[/bold] to see what's needed.")
        raise typer.Exit(code=1)

    rprint(f"\n[green]✓ Environment OK. Starting server on {host}:{port}...[/green]")

    cmd = [
        sys.executable, "-m", "uvicorn",
        "app.main:app",
        "--host", host,
        "--port", str(port),
        "--workers", str(workers),
    ]
    if reload:
        cmd.append("--reload")

    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        rprint("\n[yellow]Server stopped.[/yellow]")
    except subprocess.CalledProcessError as e:
        rprint(f"\n[red]Server exited with code {e.returncode}[/red]")
        raise typer.Exit(code=e.returncode)
