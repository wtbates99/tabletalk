"""Focused command-line lifecycle for trusted data agents as code."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import click
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.theme import Theme

from tabletalk import __version__
from tabletalk.compiler import CompiledArtifact
from tabletalk.domain import TableTalkError, to_primitive
from tabletalk.evals import EvalRunner, load_eval_suite
from tabletalk.evals.reporters import junit_report
from tabletalk.factories import get_db_provider
from tabletalk.project import Project
from tabletalk.utils import initialize_project

EXIT_OPERATIONAL_FAILURE = 1
EXIT_PLAN_CHANGES = 2
EXIT_EVAL_FAILURE = 3
EXIT_VALIDATION_FAILURE = 4

console = Console(
    theme=Theme(
        {
            "error": "bold red",
            "muted": "dim",
            "success": "bold green",
            "warning": "yellow",
        }
    )
)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        format="%(levelname)s: %(message)s",
        level=logging.DEBUG if verbose else logging.WARNING,
        stream=sys.stderr,
    )


def _failure(error: Exception, *, exit_code: int = EXIT_OPERATIONAL_FAILURE) -> None:
    if isinstance(error, TableTalkError):
        console.print(f"[error]{error.message}[/error]")
        console.print(
            f"[muted]{error.code.value} · stage: {error.stage.value}[/muted]"
        )
    else:
        console.print(f"[error]{error}[/error]")
    raise click.exceptions.Exit(exit_code)


def _load_project(path: str) -> Project:
    try:
        return Project.load(path)
    except (TableTalkError, OSError, ValueError) as error:
        _failure(error, exit_code=EXIT_VALIDATION_FAILURE)
        raise AssertionError("unreachable")


def _candidates(
    project: Project,
    agent: str | None,
) -> tuple[CompiledArtifact, ...]:
    try:
        compiled = project.compile(agent)
    except (TableTalkError, OSError, ValueError) as error:
        _failure(error, exit_code=EXIT_VALIDATION_FAILURE)
        raise AssertionError("unreachable")
    if isinstance(compiled, CompiledArtifact):
        return (compiled,)
    return compiled


def _print_sql(sql: str) -> None:
    console.print(
        Panel(
            Syntax(sql, "sql", theme="monokai", word_wrap=True),
            title="SQL",
            border_style="cyan",
        )
    )


def _print_rows(rows: tuple[dict[str, Any], ...]) -> None:
    if not rows:
        console.print("[muted]No rows returned.[/muted]")
        return
    columns = list(rows[0])
    table = Table(show_header=True, header_style="bold magenta")
    for column in columns:
        table.add_column(column, overflow="fold", max_width=50)
    for row in rows:
        table.add_row(*(str(row.get(column, "")) for column in columns))
    console.print(table)


@click.group()
@click.version_option(__version__)
@click.option("--verbose", is_flag=True, help="Show diagnostic logging.")
def cli(verbose: bool) -> None:
    """Define, evaluate, apply, and ask trusted data agents as code."""
    _setup_logging(verbose)


@cli.command()
def init() -> None:
    """Create a working SQLite Agent project with free Ollama development."""
    initialize_project()


@cli.command("compile")
@click.argument("agent", required=False)
@click.option(
    "--project-folder",
    type=click.Path(file_okay=False),
    default=".",
    show_default=True,
)
@click.option("--check", is_flag=True, help="Compile twice and verify determinism.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["terminal", "json"]),
    default="terminal",
    show_default=True,
)
def compile_command(
    agent: str | None,
    project_folder: str,
    check: bool,
    output_format: str,
) -> None:
    """Compile canonical candidate artifacts without invoking a model."""
    project = _load_project(project_folder)
    candidates = _candidates(project, agent)
    if check:
        repeated = _candidates(project, agent)
        if [item.to_json() for item in candidates] != [
            item.to_json() for item in repeated
        ]:
            console.print("[error]Compilation is not deterministic.[/error]")
            raise click.exceptions.Exit(EXIT_VALIDATION_FAILURE)
    if output_format == "json":
        click.echo(
            json.dumps(
                [
                    {
                        "agent": item.agent.name,
                        "artifact_digest": item.digest,
                        "artifact": to_primitive(item),
                    }
                    for item in candidates
                ],
                indent=2,
                sort_keys=True,
            )
        )
        return
    for item in candidates:
        console.print(
            f"[success]✓ {item.agent.name}[/success] [muted]{item.digest}[/muted]"
        )
    if check:
        console.print("[success]✓ byte-identical repeated compilation[/success]")


@cli.command()
@click.argument("agent", required=False)
@click.option(
    "--project-folder",
    type=click.Path(file_okay=False),
    default=".",
    show_default=True,
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["terminal", "json"]),
    default="terminal",
    show_default=True,
)
@click.option("--detailed-exit-code", is_flag=True)
def plan(
    agent: str | None,
    project_folder: str,
    output_format: str,
    detailed_exit_code: bool,
) -> None:
    """Show semantic differences between candidate and applied Agents."""
    project = _load_project(project_folder)
    candidates = _candidates(project, agent)
    plans = list(
        project.plans(candidates, include_removals=agent is None)
    )
    if output_format == "json":
        click.echo(
            json.dumps(
                [to_primitive(item) for item in plans],
                indent=2,
                sort_keys=True,
            )
        )
    else:
        table = Table(title="Semantic plan", header_style="bold")
        table.add_column("Agent")
        table.add_column("Action")
        table.add_column("Semantic changes")
        table.add_column("Candidate")
        for item in plans:
            action = (
                "CREATE"
                if item.applied_digest is None
                else "UPDATE"
                if item.has_changes
                else "NO-OP"
            )
            table.add_row(
                item.agent,
                action,
                "\n".join(item.changes) or "—",
                item.candidate_digest[:12] if item.candidate_digest else "—",
            )
        console.print(table)
        count = sum(item.has_changes for item in plans)
        console.print(
            f"[warning]{count} Agent artifact(s) would change.[/warning]"
            if count
            else "[success]Nothing to apply.[/success]"
        )
    if detailed_exit_code and any(item.has_changes for item in plans):
        raise click.exceptions.Exit(EXIT_PLAN_CHANGES)


def _suite_paths(project: Project, agent: str | None) -> list[Path]:
    folder = project.root / str(project.config.get("evals", "evals"))
    paths = sorted((*folder.glob("*.yaml"), *folder.glob("*.yml")))
    if agent is None:
        return paths
    selected = []
    for path in paths:
        suite = load_eval_suite(path)
        if suite.agent == agent:
            selected.append(path)
    return selected


def _render_eval_terminal(results: list[Any]) -> None:
    for result in results:
        console.print(f"\n[bold]Suite: {result.suite_name}[/bold]")
        for case in result.cases:
            status = "[success]PASS[/success]" if case.passed else "[error]FAIL[/error]"
            console.print(
                f"{status} {case.case_name} "
                f"[muted]score={case.score:.2f} latency={case.trace.latency_ms:.0f}ms[/muted]"
            )
            for metric in case.metrics:
                marker = "✓" if metric.passed else "✗"
                console.print(f"  {marker} {metric.name}: {metric.score:.2f}")
        console.print(
            f"[bold]{result.passed_count} passed, {result.failed_count} failed[/bold]"
        )


def _combined_junit(results: list[Any]) -> str:
    root = ElementTree.Element("testsuites")
    for result in results:
        root.append(ElementTree.fromstring(junit_report(result)))
    return ElementTree.tostring(root, encoding="unicode")


@cli.command("eval")
@click.argument("agent", required=False)
@click.option("--case", "case_name", help="Run one case without issuing an apply receipt.")
@click.option(
    "--project-folder",
    type=click.Path(file_okay=False),
    default=".",
    show_default=True,
)
@click.option(
    "--format",
    "report_format",
    type=click.Choice(["terminal", "json", "junit"]),
    default="terminal",
    show_default=True,
)
@click.option("--output", type=click.Path(dir_okay=False))
def evaluate(
    agent: str | None,
    case_name: str | None,
    project_folder: str,
    report_format: str,
    output: str | None,
) -> None:
    """Run execution-based regression suites against exact candidates."""
    project = _load_project(project_folder)
    candidates = {
        candidate.agent.name: candidate
        for candidate in _candidates(project, agent)
    }
    results = []
    try:
        for path in _suite_paths(project, agent):
            suite = load_eval_suite(path)
            candidate = candidates.get(str(suite.agent))
            if candidate is None:
                continue
            if case_name:
                cases = [case for case in suite.cases if case.name == case_name]
                if not cases:
                    continue
                suite = replace(suite, cases=cases)
            results.append(
                EvalRunner(
                    suite,
                    project_folder=str(project.root),
                    candidate=candidate,
                ).run()
            )
    except (TableTalkError, OSError, ValueError, RuntimeError) as error:
        _failure(error)
    if not results:
        _failure(
            ValueError(
                f"No matching eval {'case' if case_name else 'suite'} was found."
            ),
            exit_code=EXIT_VALIDATION_FAILURE,
        )
    if report_format == "json":
        rendered = json.dumps(
            [result.to_dict() for result in results],
            indent=2,
            sort_keys=True,
        )
    elif report_format == "junit":
        rendered = _combined_junit(results)
    else:
        rendered = ""
        _render_eval_terminal(results)
    if output:
        Path(output).write_text(rendered + ("\n" if rendered else ""))
        console.print(f"[muted]Report written to {output}[/muted]")
    elif rendered:
        click.echo(rendered)
    if case_name:
        console.print(
            "[warning]A case-filtered run is diagnostic and cannot authorize apply.[/warning]"
        )
    if any(not result.passed for result in results):
        raise click.exceptions.Exit(EXIT_EVAL_FAILURE)


@cli.command()
@click.argument("agent", required=False)
@click.option(
    "--project-folder",
    type=click.Path(file_okay=False),
    default=".",
    show_default=True,
)
@click.option(
    "--auto-approve",
    is_flag=True,
    help="Apply without an interactive confirmation.",
)
def apply(agent: str | None, project_folder: str, auto_approve: bool) -> None:
    """Evaluate and atomically apply exact candidate Agent artifacts."""
    project = _load_project(project_folder)
    candidates = _candidates(project, agent)
    plans = list(
        project.plans(candidates, include_removals=agent is None)
    )
    changed = [item for item in plans if item.has_changes]
    if not changed:
        console.print("[success]Nothing to apply.[/success]")
        return
    for item in changed:
        console.print(
            f"[warning]{item.agent}: {', '.join(item.changes)}[/warning] "
            f"[muted]{item.candidate_digest}[/muted]"
        )
    if not auto_approve and not click.confirm("Apply these evaluated artifacts?"):
        console.print("[muted]Apply cancelled; state was not changed.[/muted]")
        return
    changed_names = {
        item.agent
        for item in plans
        if item.has_changes and item.candidate_digest is not None
    }
    for candidate in candidates:
        if candidate.agent.name not in changed_names:
            continue
        try:
            reports = project.evaluate(candidate)
        except (TableTalkError, OSError, ValueError, RuntimeError) as error:
            _failure(error, exit_code=EXIT_EVAL_FAILURE)
        if any(not report.passed for report in reports):
            console.print(
                f"[error]{candidate.agent.name}: required evaluation failed; "
                "state was not changed.[/error]"
            )
            raise click.exceptions.Exit(EXIT_EVAL_FAILURE)
    try:
        applied_agents = project.apply_many(
            candidates,
            remove_absent=agent is None,
        )
    except TableTalkError as error:
        _failure(error, exit_code=EXIT_EVAL_FAILURE)
    for applied in applied_agents:
        console.print(
            f"[success]✓ {applied.name} applied[/success] "
            f"[muted]{applied.artifact_digest}[/muted]"
        )
        for receipt in applied.eval_receipts:
            console.print(f"  [muted]eval receipt {receipt}[/muted]")


@cli.command()
@click.argument("agent")
@click.argument("question")
@click.option(
    "--project-folder",
    type=click.Path(file_okay=False),
    default=".",
    show_default=True,
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["terminal", "json"]),
    default="terminal",
    show_default=True,
)
@click.option("--show-sql", is_flag=True)
@click.option("--show-sources", is_flag=True)
@click.option("--show-assumptions", is_flag=True)
def ask(
    agent: str,
    question: str,
    project_folder: str,
    output_format: str,
    show_sql: bool,
    show_sources: bool,
    show_assumptions: bool,
) -> None:
    """Ask an applied Agent and expose evidence-backed output."""
    project = _load_project(project_folder)
    try:
        answer = project.ask(agent, question)
    except (TableTalkError, OSError, ValueError, RuntimeError) as error:
        if output_format == "json" and isinstance(error, TableTalkError):
            click.echo(json.dumps({"failure": error.to_dict()}, sort_keys=True))
            raise click.exceptions.Exit(EXIT_OPERATIONAL_FAILURE)
        _failure(error)
    if output_format == "json":
        click.echo(json.dumps(to_primitive(answer), indent=2, sort_keys=True))
        return
    console.print(
        f"[bold]Verification: {answer.status.value.replace('_', ' ').title()}[/bold]"
    )
    if answer.direct_answer:
        console.print(answer.direct_answer)
    elif answer.status.value == "insufficient_evidence":
        console.print("[muted]The query returned no evidence.[/muted]")
    if show_assumptions and answer.interpretation.assumptions:
        console.print("\n[bold]Assumptions[/bold]")
        for assumption in answer.interpretation.assumptions:
            console.print(f"- {assumption}")
    if show_sources and answer.sources:
        console.print("\n[bold]Sources[/bold]")
        for source in answer.sources:
            console.print(f"- {source.relation}: {', '.join(source.columns)}")
    if show_sql and answer.sql:
        _print_sql(answer.sql)
    _print_rows(answer.data)
    if answer.receipt:
        console.print(
            f"[muted]artifact={answer.receipt.artifact_digest} "
            f"model={answer.receipt.runtime.model} "
            f"database={answer.receipt.database_identity}[/muted]"
        )


def _config_path(project_folder: str) -> Path:
    return Path(project_folder).resolve() / "tabletalk.yaml"


def _read_config(project_folder: str) -> tuple[Path, dict[str, Any]]:
    path = _config_path(project_folder)
    if not path.is_file():
        _failure(
            FileNotFoundError(f"{path} was not found. Run 'tabletalk init' first."),
            exit_code=EXIT_VALIDATION_FAILURE,
        )
    try:
        value = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as error:
        _failure(error, exit_code=EXIT_VALIDATION_FAILURE)
    if not isinstance(value, dict):
        _failure(
            ValueError("tabletalk.yaml must be a mapping."),
            exit_code=EXIT_VALIDATION_FAILURE,
        )
    return path, value


def _dbt_env_reference(value: Any, default_name: str) -> str:
    if not isinstance(value, str):
        return f"${{{default_name}}}"
    match = re.fullmatch(
        r"\{\{\s*env_var\(['\"]([^'\"]+)['\"](?:,\s*[^)]+)?\)\s*\}\}",
        value,
    )
    if match:
        return f"${{{match.group(1)}}}"
    if value.startswith("${"):
        return value
    return f"${{{default_name}}}"


def _connection_from_dbt(project_dir: str, target: str) -> tuple[str, dict[str, Any]]:
    project_path = Path(project_dir).expanduser().resolve()
    project_file = project_path / "dbt_project.yml"
    if not project_file.is_file():
        raise ValueError(f"dbt_project.yml was not found in {project_path}.")
    project_config = yaml.safe_load(project_file.read_text()) or {}
    profile_name = project_config.get("profile")
    if not isinstance(profile_name, str) or not profile_name:
        raise ValueError("dbt_project.yml does not declare a profile.")
    profiles_root = Path(
        os.environ.get("DBT_PROFILES_DIR", str(Path.home() / ".dbt"))
    ).expanduser()
    profiles_file = profiles_root / "profiles.yml"
    profiles = yaml.safe_load(profiles_file.read_text()) or {}
    profile = profiles.get(profile_name)
    outputs = profile.get("outputs") if isinstance(profile, dict) else None
    output = outputs.get(target) if isinstance(outputs, dict) else None
    if not isinstance(output, dict):
        raise ValueError(
            f"dbt target '{target}' was not found for profile '{profile_name}'."
        )
    database_type = str(output.get("type") or "").lower()
    if database_type not in {"sqlite", "duckdb", "snowflake"}:
        raise ValueError(
            f"dbt target type '{database_type}' is unsupported. "
            "Supported: sqlite, duckdb, snowflake."
        )
    if database_type in {"sqlite", "duckdb"}:
        raw_path = output.get("path") or output.get("database_path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError(f"dbt {database_type} target requires path.")
        config = {
            "type": database_type,
            "path": raw_path,
            "read_only": True,
        }
    else:
        config = {
            "type": "snowflake",
            "account": _dbt_env_reference(
                output.get("account"), "SNOWFLAKE_ACCOUNT"
            ),
            "user": _dbt_env_reference(output.get("user"), "SNOWFLAKE_USER"),
            "password": _dbt_env_reference(
                output.get("password"), "SNOWFLAKE_PASSWORD"
            ),
            "database": str(output.get("database") or ""),
            "warehouse": str(output.get("warehouse") or ""),
            "role": str(output.get("role") or ""),
            "schema": str(output.get("schema") or "PUBLIC"),
            "read_only": True,
        }
    return target, config


def _interactive_connection(database_type: str | None) -> tuple[str, dict[str, Any]]:
    selected = database_type or click.prompt(
        "Select a database",
        type=click.Choice(["sqlite", "duckdb", "snowflake"]),
    )
    name = click.prompt("Connection name", default="local")
    if selected in {"sqlite", "duckdb"}:
        path = click.prompt("Database file", default=f"./data/app.{selected}")
        read_only = click.confirm("Open read-only?", default=True)
        return name, {"type": selected, "path": path, "read_only": read_only}
    fields = {
        "account": click.prompt("Account"),
        "user": click.prompt("User"),
        "database": click.prompt("Database"),
        "warehouse": click.prompt("Warehouse"),
        "role": click.prompt("Role", default="TABLETALK_READONLY"),
        "schema": click.prompt("Default schema", default="PUBLIC"),
    }
    password_env = click.prompt(
        "Password environment variable",
        default="SNOWFLAKE_PASSWORD",
    )
    return name, {
        "type": "snowflake",
        **fields,
        "password": f"${{{password_env}}}",
        "read_only": True,
    }


@cli.command()
@click.option(
    "--project-folder",
    type=click.Path(file_okay=False),
    default=".",
    show_default=True,
)
@click.option("--type", "database_type", type=click.Choice(["sqlite", "duckdb", "snowflake"]))
@click.option("--from-dbt", type=click.Path(file_okay=False))
@click.option("--target", default="dev", show_default=True)
@click.option("--no-test", is_flag=True, help="Save without testing connectivity.")
def connect(
    project_folder: str,
    database_type: str | None,
    from_dbt: str | None,
    target: str,
    no_test: bool,
) -> None:
    """Create and test a secret-safe database connection."""
    path, config = _read_config(project_folder)
    try:
        name, connection = (
            _connection_from_dbt(from_dbt, target)
            if from_dbt
            else _interactive_connection(database_type)
        )
        resolved = dict(connection)
        if "path" in resolved:
            raw_path = str(resolved.pop("path"))
            resolved["database_path"] = str(
                (path.parent / raw_path).resolve()
                if raw_path != ":memory:" and not Path(raw_path).is_absolute()
                else raw_path
            )
        if not no_test:
            provider = get_db_provider(resolved)
            visible = provider.get_compact_tables(
                str(connection.get("schema") or "main")
            )
            console.print("[success]Connection successful.[/success]")
            console.print(f"Type: {connection['type']}")
            console.print(f"Visible relations: {len(visible)}")
            console.print(
                "Read-only posture: configured; database permissions remain "
                "the final security boundary."
            )
    except Exception as error:
        _failure(error)
    connections = config.get("connections")
    if not isinstance(connections, dict):
        connections = {}
    connections[name] = connection
    config["connections"] = {
        key: connections[key] for key in sorted(connections)
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    console.print(f"[success]Saved connection '{name}' without resolved secrets.[/success]")
    console.print(f"[muted]Next: tabletalk discover --connection {name}[/muted]")


def _connection_config(
    project: Project,
    name: str | None,
) -> tuple[str, dict[str, Any]]:
    connections = project.config.get("connections")
    if not isinstance(connections, dict) or not connections:
        raise ValueError("No connections are declared in tabletalk.yaml.")
    selected = name or next(iter(sorted(connections)))
    return selected, project.connection_config(selected)


@cli.group()
def connections() -> None:
    """Inspect and test project database connections."""


@connections.command("list")
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
def connections_list(project_folder: str) -> None:
    project = _load_project(project_folder)
    values = project.config.get("connections")
    if not isinstance(values, dict):
        return
    for name in sorted(values):
        config = values[name]
        database_type = config.get("type") if isinstance(config, dict) else "invalid"
        console.print(f"{name}\t{database_type}")


@connections.command("test")
@click.argument("name")
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
def connections_test(name: str, project_folder: str) -> None:
    project = _load_project(project_folder)
    try:
        provider = get_db_provider(project.connection_config(name))
        provider.get_client()
    except Exception as error:
        _failure(error)
    console.print(f"[success]✓ {name}[/success]")


@connections.command("inspect")
@click.argument("name")
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
def connections_inspect(name: str, project_folder: str) -> None:
    project = _load_project(project_folder)
    try:
        config = project.connection_config(name)
        provider = get_db_provider(config)
        schema = str(config.get("schema") or "main")
        relations = provider.get_compact_tables(schema)
    except Exception as error:
        _failure(error)
    safe = {
        key: ("${REDACTED}" if key in {"password", "api_key", "token"} else value)
        for key, value in config.items()
    }
    click.echo(
        json.dumps(
            {
                "name": name,
                "configuration": safe,
                "visible_relation_count": len(relations),
                "read_only_posture": bool(config.get("read_only", True)),
            },
            indent=2,
            sort_keys=True,
        )
    )


@cli.command()
@click.option("--connection")
@click.option("--schema")
@click.option("--search")
@click.option("--relation")
@click.option("--write-agent")
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
def discover(
    connection: str | None,
    schema: str | None,
    search: str | None,
    relation: str | None,
    write_agent: str | None,
    project_folder: str,
) -> None:
    """Inspect visible relations and optionally write a scoped Agent skeleton."""
    project = _load_project(project_folder)
    try:
        connection_name, config = _connection_config(project, connection)
        provider = get_db_provider(config)
        selected_schema = schema or str(config.get("schema") or "main")
        relations = provider.get_compact_tables(selected_schema)
    except Exception as error:
        _failure(error)
    needle = (search or "").lower()
    selected = []
    for item in relations:
        full_name = str(item.get("t") or "")
        if "." not in full_name:
            full_name = f"{selected_schema}.{full_name}"
        fields = item.get("f") or []
        haystack = " ".join(
            [
                full_name,
                str(item.get("d") or ""),
                *(str(field.get("n") or "") for field in fields if isinstance(field, dict)),
            ]
        ).lower()
        if needle and needle not in haystack:
            continue
        if relation and full_name.lower() != relation.lower():
            continue
        selected.append((full_name, item))
    for full_name, item in selected:
        console.print(f"\n[bold]{full_name}[/bold] {item.get('d') or ''}")
        for field in item.get("f") or []:
            markers = []
            if field.get("pk"):
                markers.append("PK")
            if field.get("fk"):
                markers.append(f"FK→{field['fk']}")
            console.print(
                f"  {field.get('n')}  {field.get('t')} "
                f"[muted]{' '.join(markers)}[/muted]"
            )
    console.print(f"\n[muted]{len(selected)} visible relation(s)[/muted]")
    if write_agent:
        if not selected:
            _failure(
                ValueError("No selected relations are available for the Agent."),
                exit_code=EXIT_VALIDATION_FAILURE,
            )
        if re.fullmatch(r"[a-z][a-z0-9_-]*", write_agent) is None:
            _failure(
                ValueError("Agent name must be lowercase letters, numbers, '_' or '-'."),
                exit_code=EXIT_VALIDATION_FAILURE,
            )
        output = project.agents_directory / f"{write_agent}.yaml"
        if output.exists():
            _failure(
                FileExistsError(f"{output} already exists; no file was changed."),
                exit_code=EXIT_VALIDATION_FAILURE,
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "kind": "Agent",
            "name": write_agent,
            "description": f"Governed Agent over {len(selected)} discovered relation(s).",
            "connection": connection_name,
            "relations": {"include": [name for name, _ in selected]},
            "semantics": {
                "metrics": {},
                "time": {"timezone": "UTC", "week_start": "monday"},
                "rules": ["State exact date boundaries for relative periods."],
            },
            "policies": {
                "read_only": True,
                "require_evidence": True,
                "allow_ambiguous_execution": False,
                "max_rows": 500,
                "timeout_seconds": 30,
            },
            "evals": [],
        }
        output.write_text(yaml.safe_dump(payload, sort_keys=False))
        console.print(f"[success]Wrote {output}[/success]")


@cli.group()
def agents() -> None:
    """List and inspect source and applied Agents."""


@agents.command("list")
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
def agents_list(project_folder: str) -> None:
    project = _load_project(project_folder)
    state_path = project.root / ".tabletalk" / "state.json"
    applied: dict[str, Any] = {}
    if state_path.is_file():
        state = json.loads(state_path.read_text())
        if isinstance(state.get("agents"), dict):
            applied = state["agents"]
    table = Table(header_style="bold")
    table.add_column("Agent")
    table.add_column("Connection")
    table.add_column("Applied artifact")
    for definition in project.agents():
        entry = applied.get(definition.name)
        digest = entry.get("artifact_digest") if isinstance(entry, dict) else "—"
        table.add_row(definition.name, definition.connection, str(digest))
    console.print(table)


@agents.command("inspect")
@click.argument("name")
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
def agents_inspect(name: str, project_folder: str) -> None:
    project = _load_project(project_folder)
    try:
        definition = project.agent(name)
        candidate = project.compile(name)
        plan_result = project.plan(candidate)
    except TableTalkError as error:
        _failure(error, exit_code=EXIT_VALIDATION_FAILURE)
    click.echo(
        json.dumps(
            {
                "source": to_primitive(definition),
                "candidate_digest": candidate.digest,
                "applied_digest": plan_result.applied_digest,
                "changes": list(plan_result.changes),
            },
            indent=2,
            sort_keys=True,
        )
    )


@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=5000, type=click.IntRange(1, 65535), show_default=True)
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
def serve(host: str, port: int, project_folder: str) -> None:
    """Launch the trust-centered web application."""
    from tabletalk.app import app

    os.environ["TABLETALK_PROJECT_FOLDER"] = str(Path(project_folder).resolve())
    console.print(f"TableTalk web application: http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    cli()
