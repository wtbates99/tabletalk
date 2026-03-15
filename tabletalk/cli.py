import csv
import logging
import os
import re
import sys
from typing import List, Optional

import click
from rich.columns import Columns
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
    console.print(Panel(Syntax(sql, "sql", theme="monokai", word_wrap=True), title="Generated SQL", border_style="cyan"))


def _print_results(results: list) -> None:
    if not results:
        console.print("[muted]No rows returned.[/muted]")
        return
    columns = list(results[0].keys())
    table = Table(show_header=True, header_style="bold magenta", border_style="dim", row_styles=["", "dim"])
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


_DB_TYPES = ["postgres", "snowflake", "duckdb", "azuresql", "bigquery", "mysql", "sqlite"]
_INSTALL_HINTS = {
    "postgres":  "uv add 'tabletalk[postgres]'",
    "snowflake": "uv add 'tabletalk[snowflake]'",
    "duckdb":    "uv add 'tabletalk[duckdb]'",
    "azuresql":  "uv add 'tabletalk[azuresql]'",
    "mysql":     "uv add 'tabletalk[mysql]'",
    "bigquery":  "uv add 'tabletalk[bigquery]'",
    "sqlite":    "",
}


def _prompt_db_config(db_type: str) -> dict:
    cfg: dict = {"type": db_type}
    if db_type == "postgres":
        cfg["host"]     = click.prompt("  Host", default="localhost")
        cfg["port"]     = click.prompt("  Port", default=5432, type=int)
        cfg["database"] = click.prompt("  Database")
        cfg["user"]     = click.prompt("  User")
        cfg["password"] = click.prompt("  Password", hide_input=True)
    elif db_type == "snowflake":
        console.print("  [muted]account format: myorg.us-east-1[/muted]")
        cfg["account"]   = click.prompt("  Account")
        cfg["user"]      = click.prompt("  User")
        cfg["password"]  = click.prompt("  Password", hide_input=True)
        cfg["database"]  = click.prompt("  Database")
        cfg["warehouse"] = click.prompt("  Warehouse")
        cfg["schema"]    = click.prompt("  Schema", default="PUBLIC")
        role = click.prompt("  Role (blank to skip)", default="")
        if role:
            cfg["role"] = role
    elif db_type == "duckdb":
        cfg["database_path"] = click.prompt("  Database path", default=":memory:")
    elif db_type == "azuresql":
        console.print("  [muted]server format: myserver.database.windows.net[/muted]")
        cfg["server"]   = click.prompt("  Server")
        cfg["database"] = click.prompt("  Database")
        cfg["user"]     = click.prompt("  User")
        cfg["password"] = click.prompt("  Password", hide_input=True)
        cfg["port"]     = click.prompt("  Port", default=1433, type=int)
    elif db_type == "bigquery":
        cfg["project_id"]             = click.prompt("  GCP Project ID")
        cfg["use_default_credentials"] = click.confirm("  Use default GCP credentials?", default=True)
        if not cfg["use_default_credentials"]:
            cfg["credentials"] = click.prompt("  Path to service account JSON")
    elif db_type == "mysql":
        cfg["host"]     = click.prompt("  Host", default="localhost")
        cfg["port"]     = click.prompt("  Port", default=3306, type=int)
        cfg["database"] = click.prompt("  Database")
        cfg["user"]     = click.prompt("  User")
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


# ── query ───────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("project_folder", default=os.getcwd())
@click.option("--execute", "do_execute", is_flag=True, default=False,
              help="Execute generated SQL and show results.")
@click.option("--explain", "do_explain", is_flag=True, default=False,
              help="Stream an AI explanation of the results (requires --execute).")
@click.option("--output", type=click.Path(), default=None,
              help="Save results to CSV (requires --execute).")
@click.option("--no-context", is_flag=True, default=False,
              help="Disable multi-turn conversation context.")
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
        console.print("[warning]⚠  Context files are newer than manifests — run 'tabletalk apply' to refresh.[/warning]")

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

    conversation: list = []  # multi-turn context

    console.print(f"\n[bold]Manifest:[/bold] {current_manifest}")
    console.print("[muted]Commands: change | history | clear | exit[/muted]\n")

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
                    console.print(Panel(
                        Syntax(entry["sql"], "sql", theme="monokai"),
                        title=f"[bold]{entry['question']}[/bold]  [muted]{ts} · {entry['manifest']}[/muted]",
                        border_style="dim",
                    ))

        else:
            # Generate SQL (streaming with live preview)
            console.print()
            ctx_to_use = conversation if not no_context else []
            try:
                raw = _stream_sql_live(
                    session.generate_sql_conversational(manifest_data, user_input, ctx_to_use)
                )
            except RuntimeError as e:
                console.print(f"[error]{e}[/error]")
                continue

            sql = re.sub(r"```(?:sql)?\n?", "", raw, flags=re.IGNORECASE)
            sql = re.sub(r"```", "", sql).strip()

            _print_sql(sql)

            # Update conversation context
            if not no_context:
                conversation += [
                    {"role": "user", "content": user_input},
                    {"role": "assistant", "content": sql},
                ]
                conversation = conversation[-20:]

            session.save_history(current_manifest, user_input, sql)

            # Execute
            if do_execute:
                with console.status("[cyan]Executing…[/cyan]"):
                    try:
                        results = session.execute_sql(sql)
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

                # Explain
                if do_explain and results:
                    console.print()
                    explain_parts: list = []
                    with Live("", refresh_per_second=20, console=console) as live:
                        for chunk in session.explain_results_stream(user_input, sql, results):
                            explain_parts.append(chunk)
                            live.update(Text("".join(explain_parts), style="italic dim white"))
                    console.print()


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
        console.print(Panel(
            Syntax(entry["sql"], "sql", theme="monokai"),
            title=f"[bold]{entry['question']}[/bold]  [muted]{ts} · {entry['manifest']}[/muted]",
            border_style="dim",
        ))


# ── serve ───────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--port", default=5000, show_default=True)
@click.option("--debug", is_flag=True, default=False)
def serve(port: int, debug: bool) -> None:
    """Start the web UI."""
    console.print(f"[bold cyan]tabletalk[/bold cyan] web UI → [link]http://localhost:{port}[/link]")
    app.run(debug=debug, port=port)


# ── connect ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--from-dbt", "from_dbt", metavar="PROFILE", default=None,
              help="Import from ~/.dbt/profiles.yml")
@click.option("--target", default="dev", show_default=True)
@click.option("--test-only", metavar="PROFILE_NAME", default=None)
def connect(from_dbt: Optional[str], target: str, test_only: Optional[str]) -> None:
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
        console.print(f"{'[success]✓' if ok else '[error]✗'} {msg}[/{'success' if ok else 'error'}]")
        return

    if from_dbt:
        from tabletalk.profiles import import_from_dbt
        console.print(f"Importing dbt profile [bold]{from_dbt}[/bold] (target: {target})…")
        cfg = import_from_dbt(from_dbt, target=target)
        if cfg is None:
            console.print("[error]Could not import profile.[/error]")
            return
        default_name = f"{from_dbt}_{target}".replace("-", "_")
        profile_name = click.prompt("Profile name", default=default_name)
        ok, msg = _test_connection(cfg)
        console.print(f"{'[success]✓' if ok else '[warning]⚠'} {msg}[/{'success' if ok else 'warning'}]")
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
    console.print(f"{'[success]✓' if ok else '[error]✗'} {msg}[/{'success' if ok else 'error'}]")
    if not ok and not click.confirm("Save profile anyway?", default=False):
        return
    save_profile(profile_name, cfg)
    _echo_saved(profile_name)


def _echo_saved(profile_name: str) -> None:
    console.print(f"\n[success]✓ Saved as [bold]{profile_name}[/bold] in ~/.tabletalk/profiles.yml[/success]")
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
    console.print(f"{'[success]✓' if ok else '[error]✗'} {msg}[/{'success' if ok else 'error'}]")


if __name__ == "__main__":
    cli()
