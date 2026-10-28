"""``clienthunter run`` — intake wizard + live pipeline progress.

Interactive mode:
    Guides the user through product description → past clients →
    strategy creation → live pipeline progress.

Non-interactive modes:
    --product-id UUID   Use an existing product, skip straight to strategy creation.
    --file FILE         Read product + past clients from a YAML/JSON file; no prompts.

File format (YAML or JSON)::

    name: My SaaS
    description: Project management for remote teams
    type: product          # "product" or "skill"
    user_email: you@example.com
    past_clients:
      - details: Acme Corp, 80-person SaaS, Head of Engineering
        acquisition_story: Found via a LinkedIn post about remote work tools
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import typer
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich import box

from clienthunter.cli._console import _get_client, console, err_console, handle_error
from clienthunter.exceptions import ClientHunterError
from clienthunter.models import StrategyStatus

_POLL_INTERVAL = 5   # seconds between status polls
_TERMINAL_STATUSES = {"verified", "failed", "needs_human_review"}


# ---------------------------------------------------------------------------
# Progress rendering
# ---------------------------------------------------------------------------


def _render_progress(s: StrategyStatus) -> Panel:
    """Build a Rich renderable for the live strategy progress display."""

    # --- Pipeline table ---
    pipeline_table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    pipeline_table.add_column("Phase", style="bold", width=7)
    pipeline_table.add_column("Title", min_width=28)
    pipeline_table.add_column("Progress", min_width=22)
    pipeline_table.add_column("Steps", justify="right", width=8)
    pipeline_table.add_column("", width=3)

    for pp in s.progress:
        label = f"[muted]{pp.pipeline.upper()}[/muted]"
        pipeline_table.add_section()
        pipeline_table.add_row(label, "", "", "", "")
        for ph in pp.phases:
            pct = ph.done / ph.total if ph.total else 0
            bar = _bar(pct)
            if ph.done == ph.total:
                style, icon = "phase.done", "✓"
            elif ph.done > 0:
                style, icon = "phase.running", "↻"
            else:
                style, icon = "phase.pending", "…"
            pipeline_table.add_row(
                f"  {ph.phase}",
                f"[{style}]{ph.title}[/{style}]",
                f"[{style}]{bar}[/{style}]",
                f"{ph.done}/{ph.total}",
                f"[{style}]{icon}[/{style}]",
            )

    # --- Verification passes ---
    passes = s.verification
    if passes:
        v_table = Table(box=box.SIMPLE, show_header=False)
        v_table.add_column("", width=3)
        v_table.add_column("Pass")
        v_table.add_column("", width=8)
        for p in passes:
            result = p.get("result", "…")
            name = p.get("name", f"Pass {p.get('pass_no', '?')}")
            if result == "PASS":
                icon, style = "✓", "phase.done"
            elif result == "FAIL":
                icon, style = "✗", "error"
            else:
                icon, style = "·", "phase.pending"
            v_table.add_row(
                f"[{style}]{icon}[/{style}]",
                f"[{style}]{name}[/{style}]",
                f"[{style}]{result}[/{style}]",
            )
    else:
        v_table = None

    # Assemble
    from rich.columns import Columns
    from rich import get_console

    status_color = {
        "pending": "yellow", "researching": "cyan", "verifying": "cyan",
        "verified": "green", "executing": "green",
        "needs_human_review": "yellow", "failed": "red",
    }.get(s.status, "white")

    header = (
        f"[bold]Strategy[/bold] {s.id}   "
        f"Flow: [bold]{s.flow_type}[/bold]   "
        f"Status: [bold {status_color}]{s.status}[/bold {status_color}]"
    )
    if s.error:
        header += f"\n[error]Error:[/error] {s.error}"

    from rich.console import Group
    renderables = [pipeline_table]
    if v_table:
        renderables.append("\n[bold]Verification Passes[/bold]")
        renderables.append(v_table)

    return Panel(Group(header, *renderables), title="[bold]Vectorae — Pipeline[/bold]",
                 border_style="cyan")


def _bar(pct: float, width: int = 18) -> str:
    filled = int(pct * width)
    return "█" * filled + "░" * (width - filled)


# ---------------------------------------------------------------------------
# File loader
# ---------------------------------------------------------------------------


def _load_file(path: Path) -> dict:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            err_console.print("[error]pyyaml is required for YAML files.[/error] pip install pyyaml")
            raise typer.Exit(1)
        return yaml.safe_load(text)
    elif suffix == ".json":
        return json.loads(text)
    else:
        err_console.print(f"[error]Unsupported file type:[/error] {suffix}. Use .yaml or .json.")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------


def run(
    product_id: Optional[str] = typer.Option(
        None, "--product-id", "-p",
        help="Use an existing product by UUID — skip creation, go straight to strategy.",
    ),
    file: Optional[Path] = typer.Option(
        None, "--file", "-f",
        help="YAML or JSON file with product + past_clients — fully non-interactive.",
        exists=True, dir_okay=False, readable=True,
    ),
    no_follow: bool = typer.Option(
        False, "--no-follow",
        help="Fire-and-forget: create strategy and exit without watching progress.",
    ),
    flow_type: Optional[str] = typer.Option(
        None, "--flow",
        help='Force flow: "with_clients" or "no_clients". Usually auto-inferred.',
    ),
) -> None:
    """Intake wizard: describe your product → add past clients → launch pipeline.

    Runs interactively by default; use --file or --product-id for scripting.
    """
    ch = _get_client()

    # ── 1. Resolve product ────────────────────────────────────────────────
    prod = None
    clients_to_add: list[dict] = []

    if file:
        # Non-interactive file mode
        data = _load_file(file)
        if not product_id:
            console.print(f"[info]Reading product from {file}…[/info]")
            try:
                prod = ch.products.create(
                    name=data["name"],
                    description=data["description"],
                    type=data.get("type", "product"),
                    user_email=data["user_email"],
                )
                console.print(f"[success]✓ Product created:[/success] {prod.id}")
            except ClientHunterError as exc:
                handle_error(exc)
        clients_to_add = data.get("past_clients", [])

    elif product_id:
        # Use existing product
        try:
            prod = ch.products.get(product_id)
            console.print(f"[info]Using existing product:[/info] {prod.name} ({prod.id})")
        except ClientHunterError as exc:
            handle_error(exc)

    else:
        # Interactive wizard
        console.rule("[bold]Vectorae[/bold] — New Campaign")
        console.print()
        console.print("[muted]Describe the product or skill you want clients for.[/muted]\n")

        p_name = Prompt.ask("Product / skill name")
        console.print("  Describe what you offer (be specific — Claude uses this to research your market).")
        p_desc = Prompt.ask("Description")
        p_type_raw = Prompt.ask("Type", choices=["product", "skill"], default="product")
        p_email = Prompt.ask("Your email address")

        try:
            prod = ch.products.create(
                name=p_name, description=p_desc, type=p_type_raw, user_email=p_email
            )
            console.print(f"\n[success]✓ Product saved:[/success] {prod.id}\n")
        except ClientHunterError as exc:
            handle_error(exc)

        # ── 2. Past clients? ──────────────────────────────────────────────
        has_clients = Confirm.ask(
            "Do you have [bold]past clients[/bold] for this product/skill?",
            default=False,
        )
        if has_clients:
            console.print(
                "\n[info]Add past clients.[/info]  "
                "The more detail the better — Claude extracts patterns like "
                "industry, company size, acquisition channel, and trigger event.\n"
            )
            while True:
                console.print(f"[bold]Client #{len(clients_to_add) + 1}[/bold]")
                details = Prompt.ask("  Who were they? (company, size, role, industry)")
                story = Prompt.ask("  How did you acquire them?")
                clients_to_add.append({"details": details, "acquisition_story": story})
                if not Confirm.ask("  Add another client?", default=False):
                    break
            console.print()

    # ── 3. Add past clients if any ───────────────────────────────────────
    assert prod is not None
    if clients_to_add:
        with console.status(f"[info]Saving {len(clients_to_add)} past client(s)…[/info]"):
            try:
                ch.products.add_past_clients(prod.id, clients_to_add)
                console.print(f"[success]✓ {len(clients_to_add)} past client(s) saved.[/success]")
            except ClientHunterError as exc:
                handle_error(exc)

    # ── 4. Create strategy (enqueues pipeline) ───────────────────────────
    with console.status("[info]Creating strategy and launching pipeline…[/info]"):
        try:
            strategy = ch.strategies.create(product_id=prod.id, flow_type=flow_type)
        except ClientHunterError as exc:
            handle_error(exc)

    total_steps = 144 if strategy.flow_type == "no_clients" else 72
    console.print(
        f"[success]✓ Strategy created:[/success] {strategy.id}  "
        f"[muted]({total_steps}-step pipeline launching in the cloud)[/muted]"
    )

    if no_follow:
        console.print(
            f"\nTrack progress:\n"
            f"  [bold]clienthunter status --strategy {strategy.id}[/bold]\n"
            f"  [bold]clienthunter status --strategy {strategy.id} --watch[/bold]"
        )
        return

    # ── 5. Live progress ─────────────────────────────────────────────────
    console.print("\n[muted]Following pipeline (Ctrl-C to detach — pipeline keeps running)[/muted]\n")
    try:
        with Live(console=console, refresh_per_second=0.5, transient=False) as live:
            while True:
                try:
                    status = ch.strategies.progress(strategy.id)
                except ClientHunterError as exc:
                    live.stop()
                    handle_error(exc)

                live.update(_render_progress(status))

                if status.status in _TERMINAL_STATUSES:
                    break
                time.sleep(_POLL_INTERVAL)
    except KeyboardInterrupt:
        pass

    # ── 6. Final summary ─────────────────────────────────────────────────
    status = ch.strategies.progress(strategy.id)
    console.print()
    if status.status == "verified":
        console.print(
            "[success]✓ Strategy verified — outreach execution is beginning.[/success]\n"
            f"Track campaign: [bold]clienthunter status --strategy {strategy.id}[/bold]"
        )
    elif status.status == "needs_human_review":
        console.print(
            "[warning]⚠ One or more verification passes need review.[/warning]\n"
            "Check the strategy document in the web UI and resolve flagged items."
        )
    else:
        console.print(
            f"[error]✗ Pipeline ended with status: {status.status}[/error]"
        )
        if status.error:
            console.print(f"  Error: {status.error}")
