"""
cli.py — Command-line interface.

New commands in this file:
  item  3 — serve: threaded Flask server with explicit threading=True
  item  5 — validate: dry-run health check without running a query
  item  6 — diff: show context files that are stale vs their manifests
  item  7 — test: run a smoke-test query against each manifest
  item 14 — schedule: save/list/run scheduled queries (cron-file approach)
"""
import csv
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import List, Optional

import click
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from tabletalk.app import app
from tabletalk.interfaces import QuerySession
from tabletalk.utils import apply_schema, check_manifest_staleness, initialize_project

_theme = Theme(
    {
        "info": "dim cyan",
        "warning": "yellow",
        "error": "bold red",
        "success": "bold green",
        "sql": "bold cyan",
        "muted": "dim white",
    }
)
console = Console(theme=_theme)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(format="%(levelname)s: %(message)s", level=level, stream=sys.stderr)


def _print_sql(sql: str) -> None:
    console.print(
        Panel(
            Syntax(sql, "sql", theme="monokai", word_wrap=True),
            title="Generated SQL",
            border_style="cyan",
        )
    )


def _print_results(results: list) -> None:
    if not results:
        console.print("[muted]No rows returned.[/muted]")
        return
    columns = list(results[0].keys())
    table = Table(
        show_header=True,
        header_style="bold magenta",
        border_style="dim",
        row_styles=["", "dim"],
    )
    for col in columns:
        table.add_column(col, overflow="fold", max_width=40)
    for row in results[:500]:
        table.add_row(*[str(row.get(c, "")) for c in columns])
    n = len(results)
    console.print(table)
    if n > 500:
        console.print(f"[muted]... showing 500 of {n} rows[/muted]")
    else:
        console.print(f"[muted]{n} row{'s' if n != 1 else ''}[/muted]")


def _stream_sql_live(generator) -> str:
    """Stream SQL tokens with a live display; return full SQL string."""
    parts: List[str] = []
    with Live("", refresh_per_second=20, console=console) as live:
        for chunk in generator:
            parts.append(chunk)
            live.update(Text("".join(parts), style="cyan"))
    return "".join(parts)


_DB_TYPES = [
    "postgres",
    "snowflake",
    "duckdb",
    "azuresql",
    "bigquery",
    "mysql",
    "sqlite",
]
_INSTALL_HINTS = {
    "postgres": "uv add 'tabletalk[postgres]'",
    "snowflake": "uv add 'tabletalk[snowflake]'",
    "duckdb": "uv add 'tabletalk[duckdb]'",
    "azuresql": "uv add 'tabletalk[azuresql]'",
    "mysql": "uv add 'tabletalk[mysql]'",
    "bigquery": "uv add 'tabletalk[bigquery]'",
    "sqlite": "",
}


def _prompt_db_config(db_type: str) -> dict:
    cfg: dict = {"type": db_type}
    if db_type == "postgres":
        cfg["host"] = click.prompt("  Host", default="localhost")
        cfg["port"] = click.prompt("  Port", default=5432, type=int)
        cfg["database"] = click.prompt("  Database")
        cfg["user"] = click.prompt("  User")
        cfg["password"] = click.prompt("  Password", hide_input=True)
    elif db_type == "snowflake":
        console.print("  [muted]account format: myorg.us-east-1[/muted]")
        cfg["account"] = click.prompt("  Account")
        cfg["user"] = click.prompt("  User")
        cfg["password"] = click.prompt("  Password", hide_input=True)
        cfg["database"] = click.prompt("  Database")
        cfg["warehouse"] = click.prompt("  Warehouse")
        cfg["schema"] = click.prompt("  Schema", default="PUBLIC")
        role = click.prompt("  Role (blank to skip)", default="")
        if role:
            cfg["role"] = role
    elif db_type == "duckdb":
        cfg["database_path"] = click.prompt("  Database path", default=":memory:")
    elif db_type == "azuresql":
        console.print("  [muted]server format: myserver.database.windows.net[/muted]")
        cfg["server"] = click.prompt("  Server")
        cfg["database"] = click.prompt("  Database")
        cfg["user"] = click.prompt("  User")
        cfg["password"] = click.prompt("  Password", hide_input=True)
        cfg["port"] = click.prompt("  Port", default=1433, type=int)
    elif db_type == "bigquery":
        cfg["project_id"] = click.prompt("  GCP Project ID")
        cfg["use_default_credentials"] = click.confirm(
            "  Use default GCP credentials?", default=True
        )
        if not cfg["use_default_credentials"]:
            cfg["credentials"] = click.prompt("  Path to service account JSON")
    elif db_type == "mysql":
        cfg["host"] = click.prompt("  Host", default="localhost")
        cfg["port"] = click.prompt("  Port", default=3306, type=int)
        cfg["database"] = click.prompt("  Database")
        cfg["user"] = click.prompt("  User")
        cfg["password"] = click.prompt("  Password", hide_input=True)
    elif db_type == "sqlite":
        cfg["database_path"] = click.prompt("  Database path")
    return cfg


def _default_profile_name(cfg: dict) -> str:
    t = cfg.get("type", "db")
    if t in ("postgres", "mysql"):
        return f"{cfg.get('user','user')}_{cfg.get('database','db')}"
    if t == "snowflake":
        return f"{cfg.get('user','user')}_{cfg.get('database','db').lower()}"
    if t in ("duckdb", "sqlite"):
        base = os.path.splitext(os.path.basename(cfg.get("database_path", ":memory:")))[0]
        return base if base and base != ":" else t
    if t == "azuresql":
        return f"{cfg.get('database','db')}_azuresql"
    if t == "bigquery":
        return cfg.get("project_id", "bigquery").replace("-", "_")
    return f"my_{t}"


def _test_connection(cfg: dict) -> tuple:
    try:
        from tabletalk.factories import get_db_provider

        provider = get_db_provider(cfg)
        _ = provider.get_client()
        return True, "Connection successful"
    except ImportError as e:
        hint = _INSTALL_HINTS.get(cfg.get("type", ""), "")
        msg = f"Missing driver: {e}"
        if hint:
            msg += f"\n  Install: {hint}"
        return False, msg
    except Exception as e:
        return False, f"Connection failed: {e}"


# ── CLI group ───────────────────────────────────────────────────────────────────


@click.group()
@click.option("--verbose", is_flag=True, default=False)
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """tabletalk — natural language to SQL, the dbt companion."""
    ctx.ensure_object(dict)
    _setup_logging(verbose)


# ── init ────────────────────────────────────────────────────────────────────────


@cli.command()
def init() -> None:
    """Initialize a new tabletalk project."""
    initialize_project()


# ── apply ───────────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("project_folder", default=os.getcwd())
def apply(project_folder: str) -> None:
    """Introspect the database and regenerate manifests."""
    if not os.path.isdir(project_folder):
        console.print(f"[error]Not a directory: {project_folder}[/error]")
        return
    if not os.path.exists(os.path.join(project_folder, "tabletalk.yaml")):
        console.print("[error]tabletalk.yaml not found. Run 'tabletalk init' first.[/error]")
        return
    with console.status("[cyan]Introspecting schema…[/cyan]"):
        apply_schema(project_folder)
    console.print("[success]✓ Manifests updated.[/success]")


# ── validate (item 5) ───────────────────────────────────────────────────────────


@cli.command()
@click.argument("project_folder", default=os.getcwd())
@click.option("--skip-db", is_flag=True, default=False, help="Skip database connectivity test.")
def validate(project_folder: str, skip_db: bool) -> None:
    """
    Dry-run validation: check config, context files, manifests, and DB connectivity.

    Exits with code 1 if any check fails so it can be used in CI pipelines.
    """
    errors: List[str] = []
    warnings: List[str] = []

    # 1. tabletalk.yaml
    config_path = os.path.join(project_folder, "tabletalk.yaml")
    if not os.path.exists(config_path):
        console.print("[error]✗ tabletalk.yaml not found. Run 'tabletalk init'.[/error]")
        sys.exit(1)

    import yaml

    with open(config_path) as f:
        config = yaml.safe_load(f)

    required_keys = {"llm", "contexts", "output"}
    missing = required_keys - set(config or {})
    if missing:
        errors.append(f"tabletalk.yaml missing keys: {', '.join(missing)}")
    else:
        console.print("[success]✓ tabletalk.yaml — valid[/success]")

    # 2. Context files
    contexts_path = os.path.join(project_folder, config.get("contexts", "contexts"))
    if not os.path.isdir(contexts_path):
        errors.append(f"Contexts folder not found: {contexts_path}")
    else:
        context_files = [f for f in os.listdir(contexts_path) if f.endswith(".yaml")]
        if not context_files:
            warnings.append("No context files found in contexts/")
        for cf in context_files:
            try:
                with open(os.path.join(contexts_path, cf)) as f:
                    ctx_data = yaml.safe_load(f)
                if not isinstance(ctx_data, dict):
                    errors.append(f"Invalid YAML in {cf}")
                elif "name" not in ctx_data:
                    warnings.append(f"{cf}: missing 'name' field")
                else:
                    console.print(f"[success]✓ {cf}[/success]")
            except Exception as e:
                errors.append(f"{cf}: parse error — {e}")

    # 3. Manifest staleness
    if check_manifest_staleness(project_folder):
        warnings.append(
            "One or more context files are newer than their manifests — "
            "run 'tabletalk apply' to refresh."
        )
    else:
        console.print("[success]✓ Manifests are up to date[/success]")

    # 4. Database connectivity
    if not skip_db:
        try:
            provider_config = (
                {"profile": config["profile"]}
                if "profile" in config
                else config.get("provider", {})
            )
            if provider_config:
                ok, msg = _test_connection(provider_config)
                if ok:
                    console.print(f"[success]✓ Database — {msg}[/success]")
                else:
                    errors.append(f"Database connectivity: {msg}")
            else:
                warnings.append("No database provider configured — skipping connectivity test.")
        except Exception as e:
            errors.append(f"Database check failed: {e}")

    # 5. LLM config completeness
    llm = config.get("llm", {})
    if not llm.get("provider"):
        errors.append("llm.provider not set in tabletalk.yaml")
    if not llm.get("api_key"):
        errors.append("llm.api_key not set in tabletalk.yaml")
    else:
        console.print(
            f"[success]✓ LLM — {llm.get('provider')} / {llm.get('model', 'default model')}[/success]"
        )

    # Summary
    for w in warnings:
        console.print(f"[warning]⚠  {w}[/warning]")
    for e in errors:
        console.print(f"[error]✗ {e}[/error]")

    if errors:
        console.print(f"\n[error]Validation failed with {len(errors)} error(s).[/error]")
        sys.exit(1)
    elif warnings:
        console.print(f"\n[warning]Validation passed with {len(warnings)} warning(s).[/warning]")
    else:
        console.print("\n[success]All checks passed.[/success]")


# ── diff (item 6) ───────────────────────────────────────────────────────────────


@cli.command()
@click.argument("project_folder", default=os.getcwd())
def diff(project_folder: str) -> None:
    """
    Show which context files are stale (newer than their manifests)
    and what tables are in each context vs what was last applied.
    """
    import yaml

    config_path = os.path.join(project_folder, "tabletalk.yaml")
    if not os.path.exists(config_path):
        console.print("[error]tabletalk.yaml not found.[/error]")
        return

    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    contexts_folder = os.path.join(project_folder, config.get("contexts", "contexts"))
    manifest_folder = os.path.join(project_folder, config.get("output", "manifest"))

    if not os.path.isdir(contexts_folder):
        console.print("[error]Contexts folder not found.[/error]")
        return

    any_diff = False
    for context_file in sorted(os.listdir(contexts_folder)):
        if not context_file.endswith(".yaml"):
            continue

        context_path = os.path.join(contexts_folder, context_file)
        manifest_file = os.path.join(
            manifest_folder, context_file.replace(".yaml", ".txt")
        )

        context_mtime = os.path.getmtime(context_path)
        manifest_exists = os.path.exists(manifest_file)
        manifest_mtime = os.path.getmtime(manifest_file) if manifest_exists else 0

        if not manifest_exists:
            console.print(
                f"[warning]NEW[/warning]  {context_file} — "
                "no manifest yet (run 'tabletalk apply')"
            )
            any_diff = True
            continue

        if context_mtime <= manifest_mtime:
            console.print(f"[success]OK[/success]   {context_file}")
            continue

        # Context is newer — show table-level diff
        any_diff = True
        console.print(f"\n[warning]STALE[/warning] {context_file}")

        try:
            with open(context_path) as f:
                ctx_data = yaml.safe_load(f)
            schema_list = ctx_data.get("datasets") or ctx_data.get("schemas", [])
            context_tables: set = set()
            for schema_item in schema_list:
                sname = schema_item.get("name", "")
                for t in schema_item.get("tables", []):
                    tname = t if isinstance(t, str) else t.get("name", "")
                    context_tables.add(f"{sname}.{tname}")
        except Exception:
            context_tables = set()

        try:
            manifest_tables: set = set()
            with open(manifest_file) as f:
                for line in f:
                    # manifest table lines: schema.table|desc|fields...
                    if "|" in line and not line.startswith(("DATA_SOURCE", "CONTEXT", "DATASET", "TABLES")):
                        manifest_tables.add(line.split("|")[0].strip())
        except Exception:
            manifest_tables = set()

        added = context_tables - manifest_tables
        removed = manifest_tables - context_tables

        t = Table(show_header=True, border_style="dim")
        t.add_column("Change")
        t.add_column("Table")
        for tbl in sorted(added):
            t.add_row("[success]+ added[/success]", tbl)
        for tbl in sorted(removed):
            t.add_row("[error]- removed[/error]", tbl)
        if added or removed:
            console.print(t)
        else:
            console.print("  [muted]Table list unchanged; column or description may have changed.[/muted]")

    if not any_diff:
        console.print("\n[success]All manifests are up to date — nothing to apply.[/success]")
    else:
        console.print("\n[muted]Run 'tabletalk apply' to regenerate stale manifests.[/muted]")


# ── test (item 7) ───────────────────────────────────────────────────────────────


@cli.command("test")
@click.argument("project_folder", default=os.getcwd())
@click.option(
    "--question",
    default="What tables are available and how many rows does each have?",
    show_default=True,
    help="Test question to run against each manifest.",
)
@click.option("--execute", "do_execute", is_flag=True, default=False,
              help="Also execute the generated SQL.")
def test_cmd(project_folder: str, question: str, do_execute: bool) -> None:
    """
    Smoke-test: generate SQL for a test question against every manifest,
    then optionally execute and report pass/fail per manifest.
    """
    if not os.path.isdir(project_folder):
        console.print(f"[error]Not a directory: {project_folder}[/error]")
        return

    manifest_folder = os.path.join(project_folder, "manifest")
    if not os.path.exists(manifest_folder):
        console.print("[error]No manifests found. Run 'tabletalk apply' first.[/error]")
        return

    manifest_files = [f for f in os.listdir(manifest_folder) if f.endswith(".txt")]
    if not manifest_files:
        console.print("[error]No manifests found. Run 'tabletalk apply' first.[/error]")
        return

    try:
        session = QuerySession(project_folder)
    except Exception as e:
        console.print(f"[error]Could not create session: {e}[/error]")
        return

    passed = 0
    failed = 0

    for mf in sorted(manifest_files):
        console.print(f"\n[bold]Testing:[/bold] {mf}")
        try:
            manifest_data = session.load_manifest(mf)
            sql = session.generate_sql(manifest_data, question)
            if not sql.strip():
                raise ValueError("LLM returned empty SQL")
            console.print(f"  [success]✓ SQL generated[/success]")

            if do_execute:
                with console.status("  [cyan]Executing…[/cyan]"):
                    results = session.execute_sql(sql)
                console.print(
                    f"  [success]✓ Executed — {len(results)} row(s) returned[/success]"
                )
            passed += 1
        except Exception as e:
            console.print(f"  [error]✗ {e}[/error]")
            failed += 1

    console.print(
        f"\n[bold]Results:[/bold] "
        f"[success]{passed} passed[/success]  "
        f"{'[error]' if failed else '[muted]'}{failed} failed{'[/error]' if failed else '[/muted]'}"
    )
    if failed:
        sys.exit(1)


# ── query ───────────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("project_folder", default=os.getcwd())
@click.option(
    "--execute", "do_execute", is_flag=True, default=False,
    help="Execute generated SQL and show results.",
)
@click.option(
    "--explain", "do_explain", is_flag=True, default=False,
    help="Stream an AI explanation of the results (requires --execute).",
)
@click.option(
    "--output", type=click.Path(), default=None,
    help="Save results to CSV (requires --execute).",
)
@click.option(
    "--no-context", is_flag=True, default=False,
    help="Disable multi-turn conversation context.",
)
def query(
    project_folder: str,
    do_execute: bool,
    do_explain: bool,
    output: Optional[str],
    no_context: bool,
) -> None:
    """Start an interactive conversational query session."""
    if not os.path.isdir(project_folder):
        console.print(f"[error]Not a directory: {project_folder}[/error]")
        return

    manifest_folder = os.path.join(project_folder, "manifest")
    if not os.path.exists(manifest_folder):
        console.print("[error]Manifest folder not found. Run 'tabletalk apply' first.[/error]")
        return

    manifest_files = [f for f in os.listdir(manifest_folder) if f.endswith(".txt")]
    if not manifest_files:
        console.print("[error]No manifests found. Run 'tabletalk apply' first.[/error]")
        return

    if check_manifest_staleness(project_folder):
        console.print(
            "[warning]⚠  Context files are newer than manifests — "
            "run 'tabletalk apply' to refresh.[/warning]"
        )

    def select_manifest() -> str:
        console.print("\n[bold]Available manifests:[/bold]")
        for i, f in enumerate(manifest_files, 1):
            console.print(f"  [cyan]{i}.[/cyan] {f}")
        while True:
            sel = click.prompt("Select manifest", type=str)
            try:
                return manifest_files[int(sel) - 1]
            except (IndexError, ValueError):
                console.print("[error]Invalid selection.[/error]")

    try:
        session = QuerySession(project_folder)
    except Exception as e:
        console.print(f"[error]{e}[/error]")
        return

    current_manifest = select_manifest()
    try:
        manifest_data = session.load_manifest(current_manifest)
    except FileNotFoundError as e:
        console.print(f"[error]{e}[/error]")
        return

    conversation: list = []

    console.print(f"\n[bold]Manifest:[/bold] {current_manifest}")
    console.print("[muted]Commands: change | history | clear | stats | exit[/muted]\n")

    while True:
        try:
            user_input = click.prompt("[bold cyan]>[/bold cyan]", prompt_suffix=" ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[muted]Bye.[/muted]")
            break

        cmd = user_input.lower()

        if cmd == "exit":
            console.print("[muted]Bye.[/muted]")
            break

        elif cmd == "change":
            new_manifest = select_manifest()
            try:
                manifest_data = session.load_manifest(new_manifest)
                current_manifest = new_manifest
                conversation = []
                console.print(f"[success]✓ Switched to: {current_manifest}[/success]")
            except FileNotFoundError as e:
                console.print(f"[error]{e}[/error]")

        elif cmd == "clear":
            conversation = []
            console.print("[muted]Conversation context cleared.[/muted]")

        elif cmd == "history":
            entries = session.get_history(limit=10)
            if not entries:
                console.print("[muted]No history yet.[/muted]")
            else:
                for entry in reversed(entries):
                    ts = entry["timestamp"][:19]
                    console.print(
                        Panel(
                            Syntax(entry["sql"], "sql", theme="monokai"),
                            title=(
                                f"[bold]{entry['question']}[/bold]  "
                                f"[muted]{ts} · {entry['manifest']}[/muted]"
                            ),
                            border_style="dim",
                        )
                    )

        elif cmd == "stats":
            # item 25 — show usage stats in interactive mode
            stats = session.get_usage_stats(limit=100)
            t = Table(show_header=True, border_style="dim")
            t.add_column("Metric")
            t.add_column("Value", justify="right")
            t.add_row("Queries (last 100)", str(stats["query_count"]))
            t.add_row("Total prompt tokens", str(stats["total_prompt_tokens"]))
            t.add_row("Total completion tokens", str(stats["total_completion_tokens"]))
            t.add_row(
                "Avg generation time",
                f"{stats['avg_generation_ms']} ms" if stats["avg_generation_ms"] else "n/a",
            )
            t.add_row(
                "Avg execution time",
                f"{stats['avg_execution_ms']} ms" if stats["avg_execution_ms"] else "n/a",
            )
            console.print(t)

        else:
            # Generate SQL (streaming with live preview)
            console.print()
            ctx_to_use = conversation if not no_context else []
            import time as _time

            gen_start = _time.monotonic()
            try:
                raw = _stream_sql_live(
                    session.generate_sql_conversational(
                        manifest_data, user_input, ctx_to_use
                    )
                )
            except RuntimeError as e:
                console.print(f"[error]{e}[/error]")
                continue

            generation_ms = (_time.monotonic() - gen_start) * 1000

            sql = re.sub(r"```(?:sql)?\n?", "", raw, flags=re.IGNORECASE)
            sql = re.sub(r"```", "", sql).strip()

            _print_sql(sql)

            # Update conversation context (item 11 — respect max_conv_messages)
            if not no_context:
                conversation = (
                    conversation
                    + [
                        {"role": "user", "content": user_input},
                        {"role": "assistant", "content": sql},
                    ]
                )[-session.max_conv_messages:]

            # Execute
            results: list = []
            execution_ms = 0.0
            if do_execute:
                with console.status("[cyan]Executing…[/cyan]"):
                    try:
                        exec_start = _time.monotonic()
                        results = session.execute_sql(sql)
                        execution_ms = (_time.monotonic() - exec_start) * 1000
                    except RuntimeError as e:
                        console.print(f"[error]Execution error: {e}[/error]")
                        continue

                _print_results(results)

                # Save CSV
                if output and results:
                    with open(output, "w", newline="") as f:
                        cols = list(results[0].keys())
                        writer = csv.DictWriter(f, fieldnames=cols)
                        writer.writeheader()
                        writer.writerows(results)
                    console.print(f"[success]✓ Results saved to {output}[/success]")

                # Explain (item 13 — flag-driven)
                if do_explain and results:
                    console.print()
                    explain_parts: list = []
                    with Live("", refresh_per_second=20, console=console) as live:
                        for chunk in session.explain_results_stream(
                            user_input, sql, results
                        ):
                            explain_parts.append(chunk)
                            live.update(
                                Text("".join(explain_parts), style="italic dim white")
                            )
                    console.print()

            # Persist history with metrics (items 25, 26)
            from tabletalk.interfaces import QueryMetrics

            usage = getattr(session.llm_provider, "last_usage", {})
            metrics = QueryMetrics(
                generation_ms=generation_ms,
                execution_ms=execution_ms,
                row_count=len(results),
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            )
            session.save_history(current_manifest, user_input, sql, metrics=metrics)


# ── history ─────────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("project_folder", default=os.getcwd())
@click.option("--limit", default=20, show_default=True)
def history(project_folder: str, limit: int) -> None:
    """Show recent query history."""
    if not os.path.isdir(project_folder):
        console.print(f"[error]Not a directory: {project_folder}[/error]")
        return
    try:
        s = QuerySession(project_folder)
        entries = s.get_history(limit=limit)
    except Exception as e:
        console.print(f"[error]{e}[/error]")
        return
    if not entries:
        console.print("[muted]No history.[/muted]")
        return
    for entry in reversed(entries):
        ts = entry["timestamp"][:19]
        m = entry.get("metrics", {})
        subtitle = f"[muted]{ts} · {entry['manifest']}"
        if m:
            subtitle += (
                f" · gen {m.get('generation_ms', '?')}ms"
                f" · {m.get('row_count', '?')} rows"
            )
        subtitle += "[/muted]"
        console.print(
            Panel(
                Syntax(entry["sql"], "sql", theme="monokai"),
                title=f"[bold]{entry['question']}[/bold]  {subtitle}",
                border_style="dim",
            )
        )


# ── serve (item 3 — threaded) ───────────────────────────────────────────────────


@cli.command()
@click.option("--port", default=5000, show_default=True)
@click.option("--debug", is_flag=True, default=False)
@click.option(
    "--workers",
    default=4,
    show_default=True,
    help="Number of threads for concurrent request handling.",
)
def serve(port: int, debug: bool, workers: int) -> None:
    """
    Start the web UI.

    Uses Flask's threaded server (item 3) so multiple users can stream
    SQL generation concurrently. For production workloads, run behind
    gunicorn: gunicorn 'tabletalk.app:app' -w 4 -b 0.0.0.0:5000
    """
    console.print(
        f"[bold cyan]tabletalk[/bold cyan] web UI → "
        f"[link]http://localhost:{port}[/link]  "
        f"[muted]({workers} threads)[/muted]"
    )
    app.run(debug=debug, port=port, threaded=True)


# ── connect ─────────────────────────────────────────────────────────────────────


@cli.command()
@click.option("--from-dbt", "from_dbt", metavar="PROFILE", default=None,
              help="Import from ~/.dbt/profiles.yml")
@click.option("--target", default="dev", show_default=True)
@click.option("--test-only", metavar="PROFILE_NAME", default=None)
def connect(
    from_dbt: Optional[str], target: str, test_only: Optional[str]
) -> None:
    """Configure a database connection profile."""
    from tabletalk.profiles import save_profile

    if test_only:
        from tabletalk.profiles import get_profile

        cfg = get_profile(test_only)
        if cfg is None:
            console.print(f"[error]Profile '{test_only}' not found.[/error]")
            return
        console.print(f"Testing [bold]{test_only}[/bold]…")
        ok, msg = _test_connection(cfg)
        console.print(
            f"{'[success]✓' if ok else '[error]✗'} {msg}"
            f"[/{'success' if ok else 'error'}]"
        )
        return

    if from_dbt:
        from tabletalk.profiles import import_from_dbt

        console.print(
            f"Importing dbt profile [bold]{from_dbt}[/bold] (target: {target})…"
        )
        cfg = import_from_dbt(from_dbt, target=target)
        if cfg is None:
            console.print("[error]Could not import profile.[/error]")
            return
        default_name = f"{from_dbt}_{target}".replace("-", "_")
        profile_name = click.prompt("Profile name", default=default_name)
        ok, msg = _test_connection(cfg)
        console.print(
            f"{'[success]✓' if ok else '[warning]⚠'} {msg}"
            f"[/{'success' if ok else 'warning'}]"
        )
        if not ok and not click.confirm("Save anyway?", default=False):
            return
        save_profile(profile_name, cfg)
        _echo_saved(profile_name)
        return

    # Interactive wizard
    console.print("[bold]Database type:[/bold]")
    for i, t in enumerate(_DB_TYPES, 1):
        hint = _INSTALL_HINTS.get(t, "")
        suffix = f"  [muted]({hint})[/muted]" if hint else ""
        console.print(f"  [cyan]{i}.[/cyan] {t}{suffix}")

    while True:
        sel = click.prompt("Type (number or name)", type=str)
        if sel in _DB_TYPES:
            db_type = sel
            break
        try:
            db_type = _DB_TYPES[int(sel) - 1]
            break
        except (IndexError, ValueError):
            console.print("[error]Invalid.[/error]")

    console.print(f"\n[bold]Configure {db_type}:[/bold]")
    cfg = _prompt_db_config(db_type)

    profile_name = click.prompt("\nProfile name", default=_default_profile_name(cfg))
    console.print("Testing connection…")
    ok, msg = _test_connection(cfg)
    console.print(
        f"{'[success]✓' if ok else '[error]✗'} {msg}"
        f"[/{'success' if ok else 'error'}]"
    )
    if not ok and not click.confirm("Save profile anyway?", default=False):
        return
    save_profile(profile_name, cfg)
    _echo_saved(profile_name)


def _echo_saved(profile_name: str) -> None:
    console.print(
        f"\n[success]✓ Saved as [bold]{profile_name}[/bold] "
        f"in ~/.tabletalk/profiles.yml[/success]"
    )
    console.print("\nAdd to [bold]tabletalk.yaml[/bold]:")
    console.print(f"  [cyan]profile: {profile_name}[/cyan]")


# ── profiles ─────────────────────────────────────────────────────────────────────


@cli.group()
def profiles() -> None:
    """Manage connection profiles."""
    pass


@profiles.command("list")
def profiles_list() -> None:
    from tabletalk.profiles import load_profiles

    all_p = load_profiles()
    if not all_p:
        console.print("[muted]No profiles. Run 'tabletalk connect'.[/muted]")
        return
    t = Table(show_header=True, header_style="bold")
    t.add_column("Profile")
    t.add_column("Type")
    t.add_column("Connection")
    for name, cfg in all_p.items():
        tp = cfg.get("type", "?")
        if tp in ("postgres", "mysql"):
            conn = f"{cfg.get('user')}@{cfg.get('host')}/{cfg.get('database')}"
        elif tp == "snowflake":
            conn = f"{cfg.get('user')}@{cfg.get('account')}/{cfg.get('database')}"
        elif tp in ("sqlite", "duckdb"):
            conn = cfg.get("database_path", "")
        elif tp == "azuresql":
            conn = f"{cfg.get('server')}/{cfg.get('database')}"
        elif tp == "bigquery":
            conn = cfg.get("project_id", "")
        else:
            conn = ""
        t.add_row(name, tp, conn)
    console.print(t)


@profiles.command("delete")
@click.argument("name")
def profiles_delete(name: str) -> None:
    from tabletalk.profiles import delete_profile

    if delete_profile(name):
        console.print(f"[success]✓ Deleted '{name}'[/success]")
    else:
        console.print(f"[error]Profile '{name}' not found.[/error]")


@profiles.command("test")
@click.argument("name")
def profiles_test(name: str) -> None:
    from tabletalk.profiles import get_profile

    cfg = get_profile(name)
    if cfg is None:
        console.print(f"[error]Profile '{name}' not found.[/error]")
        return
    console.print(f"Testing [bold]{name}[/bold]…")
    ok, msg = _test_connection(cfg)
    console.print(
        f"{'[success]✓' if ok else '[error]✗'} {msg}"
        f"[/{'success' if ok else 'error'}]"
    )


# ── schedule (item 14) ─────────────────────────────────────────────────────────


def _schedules_path(project_folder: str) -> str:
    return os.path.join(project_folder, ".tabletalk_schedules.json")


def _load_schedules(project_folder: str) -> list:
    path = _schedules_path(project_folder)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def _save_schedules(project_folder: str, schedules: list) -> None:
    with open(_schedules_path(project_folder), "w") as f:
        json.dump(schedules, f, indent=2)


@cli.group()
def schedule() -> None:
    """Manage scheduled queries (run periodically via cron or 'tabletalk schedule run')."""
    pass


@schedule.command("add")
@click.argument("name")
@click.argument("project_folder", default=os.getcwd())
@click.option("--question", required=True, help="Natural-language question to run.")
@click.option("--manifest", required=True, help="Manifest file name (e.g. sales.txt).")
@click.option(
    "--interval",
    default=60,
    show_default=True,
    help="Run interval in minutes.",
    type=int,
)
@click.option(
    "--output-dir",
    default=None,
    help="Directory to write CSV results (default: project folder).",
)
def schedule_add(
    name: str,
    project_folder: str,
    question: str,
    manifest: str,
    interval: int,
    output_dir: Optional[str],
) -> None:
    """Add a new scheduled query."""
    schedules = _load_schedules(project_folder)
    # Replace existing schedule with same name
    schedules = [s for s in schedules if s.get("name") != name]
    schedules.append(
        {
            "name": name,
            "question": question,
            "manifest": manifest,
            "interval_minutes": interval,
            "output_dir": output_dir or project_folder,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_run": None,
        }
    )
    _save_schedules(project_folder, schedules)
    console.print(
        f"[success]✓ Scheduled '{name}' every {interval} min — "
        f"run 'tabletalk schedule run' to execute due queries.[/success]"
    )
    console.print(
        f"[muted]  Tip: add to cron:  */{ interval } * * * * "
        f"tabletalk schedule run {project_folder}[/muted]"
    )


@schedule.command("list")
@click.argument("project_folder", default=os.getcwd())
def schedule_list(project_folder: str) -> None:
    """List all scheduled queries."""
    schedules = _load_schedules(project_folder)
    if not schedules:
        console.print("[muted]No schedules. Use 'tabletalk schedule add'.[/muted]")
        return
    t = Table(show_header=True, header_style="bold")
    t.add_column("Name")
    t.add_column("Manifest")
    t.add_column("Interval")
    t.add_column("Last Run")
    t.add_column("Question")
    for s in schedules:
        t.add_row(
            s["name"],
            s["manifest"],
            f"{s['interval_minutes']}m",
            (s.get("last_run") or "never")[:19],
            s["question"][:60],
        )
    console.print(t)


@schedule.command("remove")
@click.argument("name")
@click.argument("project_folder", default=os.getcwd())
def schedule_remove(name: str, project_folder: str) -> None:
    """Remove a scheduled query by name."""
    schedules = _load_schedules(project_folder)
    new = [s for s in schedules if s.get("name") != name]
    if len(new) == len(schedules):
        console.print(f"[error]Schedule '{name}' not found.[/error]")
        return
    _save_schedules(project_folder, new)
    console.print(f"[success]✓ Removed '{name}'[/success]")


@schedule.command("run")
@click.argument("project_folder", default=os.getcwd())
@click.option("--force", is_flag=True, default=False, help="Run all schedules regardless of interval.")
def schedule_run(project_folder: str, force: bool) -> None:
    """
    Execute all due scheduled queries.

    A schedule is 'due' when more than interval_minutes have elapsed since
    its last run (or it has never run). Designed to be called from a cron job.
    """
    schedules = _load_schedules(project_folder)
    if not schedules:
        console.print("[muted]No schedules configured.[/muted]")
        return

    try:
        session = QuerySession(project_folder)
    except Exception as e:
        console.print(f"[error]Session error: {e}[/error]")
        return

    now = datetime.now(timezone.utc)
    ran = 0

    for i, s in enumerate(schedules):
        last = s.get("last_run")
        if last and not force:
            elapsed_minutes = (
                now - datetime.fromisoformat(last)
            ).total_seconds() / 60
            if elapsed_minutes < s["interval_minutes"]:
                console.print(
                    f"[muted]Skipping '{s['name']}' "
                    f"(next run in {s['interval_minutes'] - elapsed_minutes:.0f}m)[/muted]"
                )
                continue

        console.print(f"[bold]Running:[/bold] {s['name']}")
        try:
            manifest_data = session.load_manifest(s["manifest"])
            sql = session.generate_sql(manifest_data, s["question"])
            results = session.execute_sql(sql)

            # Write results to CSV
            out_dir = s.get("output_dir", project_folder)
            os.makedirs(out_dir, exist_ok=True)
            ts_str = now.strftime("%Y%m%d_%H%M%S")
            out_file = os.path.join(out_dir, f"{s['name']}_{ts_str}.csv")
            if results:
                with open(out_file, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
                    writer.writeheader()
                    writer.writerows(results)
                console.print(
                    f"  [success]✓ {len(results)} rows → {out_file}[/success]"
                )
            else:
                console.print("  [muted](no rows returned)[/muted]")

            schedules[i]["last_run"] = now.isoformat()
            ran += 1
        except Exception as e:
            console.print(f"  [error]✗ {e}[/error]")

    _save_schedules(project_folder, schedules)
    console.print(f"\n[muted]Ran {ran} schedule(s).[/muted]")


if __name__ == "__main__":
    cli()
