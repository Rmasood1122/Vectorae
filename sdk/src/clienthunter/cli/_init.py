"""``clienthunter init`` — first-time setup wizard.

Prompts for API URL and credentials, verifies connectivity with a health
check, and writes ``~/.clienthunter/config.toml`` (mode 0600).
"""
from __future__ import annotations

import typer
from rich.prompt import Confirm, Prompt

from clienthunter.cli._console import _get_client, console, err_console, handle_error
from clienthunter.config import Config, save as save_config
from clienthunter.exceptions import ClientHunterError

_DEFAULT_CLOUD_URL = "https://api.clienthunter.ai"
_DEFAULT_LOCAL_URL = "http://localhost:8000"


def init() -> None:
    """Interactive first-time setup: configure API URL, authenticate, verify connectivity."""
    console.rule("[bold]Vectorae[/bold] — Setup")
    console.print()

    # --- Where is the backend? ---
    use_cloud = Confirm.ask(
        "Connect to the [bold]hosted cloud[/bold] backend at clienthunter.ai?",
        default=True,
    )
    if use_cloud:
        default_url = _DEFAULT_CLOUD_URL
        console.print(
            "\n[muted]Campaigns run 24/7 in the cloud — your device can be off.[/muted]"
        )
    else:
        console.print(
            "\n[warning]Self-hosted mode:[/warning] campaigns run only while "
            "this machine is on.\nFor 24/7 operation, deploy the backend to a "
            "cloud host (see [link=https://docs.clienthunter.ai/deploy]docs[/link]).\n"
            "Run [bold]clienthunter serve[/bold] first to start the local stack."
        )
        default_url = _DEFAULT_LOCAL_URL

    api_url = Prompt.ask("Backend API URL", default=default_url).rstrip("/")

    # --- Auth method ---
    console.print()
    use_key = Confirm.ask("Authenticate with an [bold]API key[/bold] (skip to use email/password)?",
                          default=False)
    api_key: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None

    if use_key:
        api_key = Prompt.ask("API key", password=True)
    else:
        email = Prompt.ask("Email address")
        password = Prompt.ask("Password", password=True)

    # --- Verify connectivity ---
    console.print()
    with console.status("[info]Connecting to backend…[/info]"):
        try:
            ch = _get_client(api_url=api_url, api_key=api_key)
            ch.ping()

            if not use_key:
                pair = ch.auth.login(email, password)
                access_token = pair.access_token
                refresh_token = pair.refresh_token

        except ClientHunterError as exc:
            handle_error(exc)

    # --- Persist config ---
    cfg = Config(
        api_url=api_url,
        api_key=api_key,
        access_token=access_token,
        refresh_token=refresh_token,
    )
    try:
        save_config(cfg)
    except Exception as exc:
        err_console.print(f"[error]✗ Could not save config:[/error] {exc}")
        raise typer.Exit(1)

    console.print()
    console.print("[success]✓ Connected and authenticated.[/success]")
    console.print("[success]✓ Config saved to ~/.clienthunter/config.toml (mode 0600).[/success]")
    console.print()
    console.print("Next steps:")
    console.print("  [bold]clienthunter run[/bold]     — start the intake wizard")
    console.print("  [bold]clienthunter status[/bold]  — view all strategies")
    console.print("  [bold]clienthunter --help[/bold]  — full command reference")
