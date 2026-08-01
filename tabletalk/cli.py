"""The dbt-native TableTalk command-line workflow."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import click
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from tabletalk import __version__
from tabletalk.agents import Agent
from tabletalk.authoring import (
    SELECTOR_KINDS,
    node_details,
    parse_choices,
    selector_options,
)
from tabletalk.connections import available_targets, load_profile_target
from tabletalk.evals import (
    EvalCase,
    EvalRunner,
    EvalSuite,
    ResultExpectation,
    SuiteResult,
    load_eval_suite,
)
from tabletalk.manifest import Manifest, Node
from tabletalk.project import Project
from tabletalk.traces import Interpretation as TraceInterpretation
from tabletalk.traces import Trace

console = Console()
EXIT_OPERATIONAL_FAILURE = 1
EXIT_EVAL_FAILURE = 3
EXIT_VALIDATION_FAILURE = 4


def _fail(error: Exception, code: int = EXIT_OPERATIONAL_FAILURE) -> None:
    console.print(f"[bold red]{error}[/bold red]")
    raise click.exceptions.Exit(code)


def _project(path: str) -> Project:
    try:
        return Project.load(path)
    except Exception as exc:
        _fail(exc, EXIT_VALIDATION_FAILURE)
        raise AssertionError("unreachable")


def _find_dbt_project(start: Path) -> Path:
    for candidate in (start.resolve(), *start.resolve().parents):
        if (candidate / "dbt_project.yml").is_file():
            return candidate
    raise ValueError(f"No dbt_project.yml found at or above {start.resolve()}")


def _print_rows(rows: tuple[dict[str, Any], ...]) -> None:
    if not rows:
        console.print("[dim]No rows returned.[/dim]")
        return
    columns = list(rows[0])
    table = Table(show_header=True, header_style="bold magenta")
    for column in columns:
        table.add_column(column)
    for row in rows:
        table.add_row(*(str(row.get(column, "")) for column in columns))
    console.print(table)


def _yaml_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _yaml_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_yaml_value(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _print_trace(trace: Trace) -> None:
    verified = trace.correctness_verified
    status = "VERIFIED" if verified else "UNVERIFIED"
    style = "green" if verified else "yellow"
    console.print(
        Panel(
            trace.answer.text or "No answer",
            title=f"{status} RESULT",
            border_style=style,
        )
    )
    console.print("\n[bold]How this answer was formed[/bold]")
    console.print(f"Interpretation: {trace.interpretation.intent or 'unspecified'}")
    if trace.interpretation.start_date or trace.interpretation.end_date:
        console.print(
            "Date boundaries: "
            f"{trace.interpretation.start_date or 'open'} → "
            f"{trace.interpretation.end_date or 'open'}"
        )
    if trace.interpretation.assumptions:
        console.print("Assumptions: " + "; ".join(trace.interpretation.assumptions))
    console.print("dbt resources: " + ", ".join(trace.dbt_context.selected_nodes))
    console.print("Columns: " + (", ".join(trace.dbt_context.columns) or "none"))
    if trace.dbt_context.relevant_tests:
        console.print("Relevant dbt tests: " + ", ".join(trace.dbt_context.relevant_tests))
    if trace.dbt_context.test_health:
        console.print(
            "Recent dbt test health: "
            + ", ".join(
                f"{name}={status}" for name, status in trace.dbt_context.test_health.items()
            )
        )
    if trace.sql.executed:
        console.print(
            Panel(Syntax(trace.sql.executed, "sql", word_wrap=True), title="Executed SQL")
        )
    _print_rows(trace.result.rows)
    checks = Table(show_header=True)
    checks.add_column("Verification")
    checks.add_column("Status")
    checks.add_column("Detail")
    for check in trace.verification:
        checks.add_row(check.name, "PASS" if check.passed else "FAIL", check.message)
    console.print(checks)


def _prompt_selectors(manifest: Manifest) -> tuple[tuple[str, ...], bool, bool]:
    kind = click.prompt(
        "Start from",
        type=click.Choice(SELECTOR_KINDS),
        default="group" if manifest.summary.groups else "model",
    )
    options = selector_options(manifest, kind)
    if not options:
        raise ValueError(f"This manifest has no selectable {kind} values")
    table = Table(title=f"Available dbt {kind} scopes")
    table.add_column("#", justify="right")
    table.add_column("Value")
    table.add_column("Resources", justify="right")
    table.add_column("Manifest description")
    for index, option in enumerate(options, 1):
        table.add_row(
            str(index),
            option.selector.split(":", 1)[1],
            str(option.resource_count),
            option.label,
        )
    console.print(table)
    selectors = parse_choices(
        click.prompt("Choose one or more numbers or values (comma-separated)"), options
    )
    expansion = click.prompt(
        "Include lineage",
        type=click.Choice(("none", "upstream", "downstream", "both")),
        default="none",
    )
    return (
        selectors,
        expansion in {"upstream", "both"},
        expansion
        in {
            "downstream",
            "both",
        },
    )


def _column_summary(node: Node) -> str:
    return (
        "\n".join(
            f"{column.name}: {column.physical_type or column.data_type or 'type unknown'}"
            + (f" — {column.description}" if column.description else " — MISSING DESCRIPTION")
            for column in node.columns.values()
        )
        or "No documented columns"
    )


def _print_scope(nodes: tuple[Node, ...], catalog_available: bool) -> None:
    table = Table(title="Resolved dbt scope — exactly what the agent can query")
    table.add_column("Resource")
    table.add_column("Description")
    table.add_column("Columns and types")
    table.add_column("Tests / constraints")
    table.add_column("Lineage")
    for node in nodes:
        evidence = [test.name for test in node.tests]
        evidence.extend(str(item.get("type") or item) for item in node.constraints)
        table.add_row(
            node.unique_id,
            node.description or "MISSING DESCRIPTION",
            _column_summary(node),
            "\n".join(evidence) or "none declared",
            f"↑ {', '.join(node.parents) or 'none'}\n↓ {', '.join(node.children) or 'none'}",
        )
    console.print(table)
    source = "manifest + catalog physical types" if catalog_available else "manifest only"
    console.print(f"[dim]Metadata source: {source}[/dim]")


def _persist_eval_result(project: Project, result: SuiteResult) -> Path:
    result_dir = project.root / ".tabletalk" / "eval-results" / result.agent
    timestamp = datetime.now(timezone.utc).isoformat().replace(":", "-")
    target = result_dir / f"{timestamp}-{result.suite_digest[:12]}.json"
    result_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n")
    return target


@click.group()
@click.version_option(__version__)
@click.option("--verbose", is_flag=True)
def cli(verbose: bool) -> None:
    """Evaluate and observe natural-language agents over an existing dbt project."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.WARNING, stream=sys.stderr)


@cli.command()
@click.option("--project-dir", type=click.Path(file_okay=False, path_type=Path))
@click.option("--manifest")
@click.option("--target")
@click.option("--profiles-dir", type=click.Path(file_okay=False, path_type=Path))
@click.option("--llm-provider", default="ollama", show_default=True)
@click.option("--llm-model", default="gemma4:31b-cloud", show_default=True)
@click.option("--base-url", default="http://localhost:11434/v1", show_default=True)
@click.option("--no-input", is_flag=True, help="Use defaults without prompts.")
def init(
    project_dir: Path | None,
    manifest: str | None,
    target: str | None,
    profiles_dir: Path | None,
    llm_provider: str,
    llm_model: str,
    base_url: str,
    no_input: bool,
) -> None:
    """Initialize TableTalk inside an existing parsed dbt project."""
    try:
        dbt_root = _find_dbt_project(project_dir or Path.cwd())
        project_config = yaml.safe_load((dbt_root / "dbt_project.yml").read_text()) or {}
        manifest = manifest or str(
            Path(str(project_config.get("target-path") or "target")) / "manifest.json"
        )
        artifact = Path(manifest)
        if not artifact.is_absolute():
            artifact = dbt_root / artifact
        index = Manifest.load(artifact)
        targets = available_targets(dbt_root, profiles_dir)
        if not targets:
            raise ValueError("The dbt profile has no targets")
        if target is None:
            if no_input:
                profile_name = project_config.get("profile")
                profile_root = profiles_dir or Path(
                    os.environ.get("DBT_PROFILES_DIR") or Path.home() / ".dbt"
                )
                profiles_file = profile_root / "profiles.yml"
                profiles = yaml.safe_load(profiles_file.read_text()) or {}
                target = (profiles.get(profile_name) or {}).get("target") or targets[0]
            else:
                target = click.prompt("dbt target", type=click.Choice(targets), default=targets[0])
        resolved_target = load_profile_target(dbt_root, target, profiles_dir)
        config: dict[str, Any] = {
            "dbt": {"project_dir": ".", "manifest": manifest, "target": target},
            "llm": {"provider": llm_provider, "model": llm_model},
        }
        for key, filename in (("catalog", "catalog.json"), ("run_results", "run_results.json")):
            optional = artifact.with_name(filename)
            if optional.is_file():
                try:
                    stored_optional = optional.relative_to(dbt_root)
                except ValueError:
                    stored_optional = optional
                config["dbt"][key] = str(stored_optional)
        if profiles_dir:
            try:
                stored_profiles_dir = profiles_dir.resolve().relative_to(dbt_root)
            except ValueError:
                stored_profiles_dir = profiles_dir.resolve()
            config["dbt"]["profiles_dir"] = str(stored_profiles_dir)
        if llm_provider in {"ollama", "openai-compatible"}:
            config["llm"]["base_url"] = base_url
        if llm_provider == "openai":
            config["llm"]["api_key"] = "${OPENAI_API_KEY}"
        elif llm_provider == "openai-compatible":
            config["llm"]["api_key"] = "${LLM_API_KEY}"
        (dbt_root / "tabletalk.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
        (dbt_root / "agents").mkdir(exist_ok=True)
        (dbt_root / "evals").mkdir(exist_ok=True)
    except Exception as exc:
        _fail(exc, EXIT_VALIDATION_FAILURE)
    summary = index.summary
    console.print("[bold green]TableTalk initialized.[/bold green]")
    console.print(
        f"Manifest: {summary.manifest_version or 'unknown'} · "
        f"dbt {summary.dbt_version or 'unknown'}"
    )
    console.print(f"Models: {summary.model_count} · target: {target} ({resolved_target.adapter})")
    console.print(f"Profile: {resolved_target.profile}")
    console.print(
        "Catalog: "
        + ("loaded" if index.catalog_digest else "not found (optional: run dbt docs generate)")
    )
    console.print("Groups: " + (", ".join(summary.groups) or "none"))
    console.print("Tags: " + (", ".join(summary.tags) or "none"))


@cli.group()
def agent() -> None:
    """Create and inspect agents scoped by dbt selectors."""


@agent.command("create")
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
@click.option("--select", "selectors", multiple=True)
@click.option("--exclude", multiple=True)
@click.option("--name")
@click.option("--description")
@click.option("--instruction", multiple=True)
@click.option("--sample-question", multiple=True)
@click.option("--create-eval", is_flag=True)
@click.option("--include-parents", is_flag=True)
@click.option("--include-children", is_flag=True)
def agent_create(
    project_folder: str,
    selectors: tuple[str, ...],
    exclude: tuple[str, ...],
    name: str | None,
    description: str | None,
    instruction: tuple[str, ...],
    sample_question: tuple[str, ...],
    create_eval: bool,
    include_parents: bool,
    include_children: bool,
) -> None:
    project = _project(project_folder)
    summary = project.manifest.summary
    console.print("Groups: " + (", ".join(summary.groups) or "none"))
    console.print("Tags: " + (", ".join(summary.tags) or "none"))
    console.print("Packages: " + (", ".join(summary.packages) or "none"))
    console.print("Paths: " + (", ".join(summary.paths) or "none"))
    console.print(
        "Catalog: "
        + (
            "loaded — physical warehouse types available"
            if project.manifest.catalog_digest
            else "not found — run dbt docs generate for physical types"
        )
    )
    if not selectors:
        try:
            selectors, include_parents, include_children = _prompt_selectors(project.manifest)
        except Exception as exc:
            _fail(exc, EXIT_VALIDATION_FAILURE)
    try:
        nodes = project.manifest.select(
            selectors,
            exclude,
            include_parents=include_parents,
            include_children=include_children,
        )
    except Exception as exc:
        _fail(exc, EXIT_VALIDATION_FAILURE)
    _print_scope(nodes, project.manifest.catalog_digest is not None)
    missing = [node.unique_id for node in nodes if not node.description]
    missing.extend(
        f"{node.unique_id}.{column.name}"
        for node in nodes
        for column in node.columns.values()
        if not column.description
    )
    if missing:
        console.print("[yellow]Missing descriptions: " + ", ".join(missing) + "[/yellow]")
    aliases: dict[str, list[str]] = {}
    for node in nodes:
        aliases.setdefault(node.alias.lower(), []).append(node.unique_id)
    duplicates = {key: values for key, values in aliases.items() if len(values) > 1}
    if duplicates:
        console.print(f"[yellow]Ambiguous duplicate relation aliases: {duplicates}[/yellow]")
    name = name or click.prompt("Agent name")
    description = description or click.prompt("Description")
    if not instruction and not sys.stdin.isatty():
        instruction = ()
    elif not instruction:
        raw_instruction = click.prompt("Instructions (semicolon-separated)", default="")
        instruction = tuple(item.strip() for item in raw_instruction.split(";") if item.strip())
    if not sample_question and sys.stdin.isatty():
        raw_samples = click.prompt("Example questions (semicolon-separated)", default="")
        sample_question = tuple(item.strip() for item in raw_samples.split(";") if item.strip())
    definition = Agent(
        name=name,
        description=description,
        select=selectors,
        exclude=exclude,
        instructions=instruction,
        sample_questions=sample_question,
        include_parents=include_parents,
        include_children=include_children,
    )
    project.agents_directory.mkdir(parents=True, exist_ok=True)
    target = project.agents_directory / f"{name}.yaml"
    if target.exists() and not click.confirm(f"Overwrite {target}?", default=False):
        raise click.Abort()
    target.write_text(definition.dump())
    console.print(f"[green]Created {target}[/green]")
    if create_eval:
        ctx = click.get_current_context()
        ctx.invoke(eval_create, agent_name=name, project_folder=project_folder)
    elif sys.stdin.isatty() and click.confirm("Create the first eval now?", default=True):
        ctx = click.get_current_context()
        ctx.invoke(eval_create, agent_name=name, project_folder=project_folder)


@agent.command("list")
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
def agent_list(project_folder: str) -> None:
    project = _project(project_folder)
    for item in project.agents():
        resolved = item.resolve(project.manifest)
        console.print(f"{item.name}\t{len(resolved.nodes)} models\t{item.description}")


@agent.command("show")
@click.argument("name")
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
@click.option(
    "--format", "output_format", type=click.Choice(["terminal", "json"]), default="terminal"
)
def agent_show(name: str, project_folder: str, output_format: str) -> None:
    project = _project(project_folder)
    item = project.agent(name)
    resolved = item.resolve(project.manifest)
    payload = {
        "agent": yaml.safe_load(item.dump()),
        "manifest_digest": resolved.manifest_digest,
        "catalog_digest": project.manifest.catalog_digest,
        "resources": [node_details(node) for node in resolved.nodes],
        "missing_descriptions": list(resolved.missing_descriptions),
        "duplicate_aliases": resolved.duplicate_aliases,
    }
    if output_format == "json":
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        console.print(Panel(item.description, title=f"Agent: {item.name}"))
        console.print("Selectors: " + ", ".join(item.select))
        if item.exclude:
            console.print("Exclusions: " + ", ".join(item.exclude))
        _print_scope(resolved.nodes, project.manifest.catalog_digest is not None)
        if resolved.missing_descriptions:
            console.print(
                "[yellow]Metadata gaps: " + ", ".join(resolved.missing_descriptions) + "[/yellow]"
            )


@cli.group()
def eval() -> None:
    """Author and run execution-based correctness evaluations."""


@eval.command("create")
@click.argument("agent_name")
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
@click.option("--question")
@click.option("--reference-sql")
@click.option("--approve", is_flag=True, help="Approve generated SQL without prompting.")
@click.option("--name")
@click.option("--starter-cases", is_flag=True, help="Add paraphrase, ambiguity, and scope cases.")
def eval_create(
    agent_name: str,
    project_folder: str,
    question: str | None,
    reference_sql: str | None,
    approve: bool,
    name: str | None,
    starter_cases: bool,
) -> None:
    project = _project(project_folder)
    question = question or click.prompt("Representative business question")

    def approve_sql(interpretation: TraceInterpretation, generated: str, executed: str) -> None:
        console.print(f"Interpretation: {interpretation.intent}")
        if interpretation.assumptions:
            console.print("Assumptions: " + "; ".join(interpretation.assumptions))
        console.print(Panel(Syntax(generated, "sql", word_wrap=True), title="Generated SQL"))
        if executed != generated:
            console.print(Panel(Syntax(executed, "sql", word_wrap=True), title="SQL to execute"))
        if not approve and not click.confirm("Execute this read-only SQL?", default=True):
            raise click.Abort()

    try:
        runtime = project.runtime(agent_name)
        trace = runtime.answer(question, before_execute=approve_sql)
    except Exception as exc:
        _fail(exc)
    _print_trace(trace)
    if not approve and not click.confirm("Approve this result and answer trace?", default=True):
        raise click.Abort()
    if reference_sql is None:
        if approve or click.confirm(
            "Use the reviewed SQL as the changing-data reference?", default=True
        ):
            reference_sql = trace.sql.generated
        else:
            reference_sql = click.prompt(
                "Different reference SQL (blank to freeze the current rows)", default=""
            )
    required_models = list(trace.dbt_context.selected_nodes)
    required_columns = list(trace.dbt_context.columns)
    if not approve:
        raw_models = click.prompt("Required dbt node IDs", default=", ".join(required_models))
        raw_columns = click.prompt("Required columns", default=", ".join(required_columns))
        required_models = [item.strip() for item in raw_models.split(",") if item.strip()]
        required_columns = [item.strip() for item in raw_columns.split(",") if item.strip()]
    case_name = name or re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:60]
    result: dict[str, Any] = {"comparison": "unordered"}
    expect: dict[str, Any] = {
        "result": result,
        "models": {"required": required_models},
        "columns": {"required": required_columns},
    }
    if reference_sql:
        expect["reference_sql"] = reference_sql
    else:
        result["rows"] = _yaml_value(trace.result.rows)
    suite: dict[str, Any] = {
        "name": f"{agent_name}-regression",
        "agent": agent_name,
        "cases": [{"name": case_name, "question": question, "expect": expect}],
    }
    if starter_cases or (
        not approve and click.confirm("Add starter robustness cases?", default=True)
    ):
        suite["cases"].extend(
            [
                {
                    "name": f"{case_name}-paraphrase",
                    "question": f"Please answer this another way: {question}",
                    "expect": deepcopy(expect),
                },
                {
                    "name": f"{case_name}-ambiguity",
                    "question": "Revenue?",
                    "expect": {"outcome": "ambiguity"},
                },
                {
                    "name": f"{case_name}-out-of-scope",
                    "question": "Show data from a resource outside this agent's dbt scope.",
                    "expect": {"outcome": "rejection"},
                },
            ]
        )
    verification_suite = EvalSuite(
        str(suite["name"]),
        agent_name,
        (
            EvalCase(
                case_name,
                question,
                reference_sql=reference_sql or None,
                result=ResultExpectation(
                    comparison="unordered",
                    rows=trace.result.rows if not reference_sql else None,
                ),
                required_models=tuple(required_models),
                required_columns=tuple(required_columns),
            ),
        ),
    )
    initial_result = EvalRunner(verification_suite, runtime).evaluate_trace(
        verification_suite.cases[0], trace
    )
    if not initial_result.passed:
        console.print("[red]The proposed eval does not verify this result:[/red]")
        for check in initial_result.checks:
            if not check.passed:
                console.print(f"  ✗ {check.name}: {check.message}")
        if approve:
            _fail(ValueError("Refusing to save an eval that fails immediately"), EXIT_EVAL_FAILURE)
        if not click.confirm("Save this intentionally failing regression?", default=False):
            raise click.Abort()

    project.evals_directory.mkdir(parents=True, exist_ok=True)
    target = project.evals_directory / f"{agent_name}.yaml"
    if target.exists():
        existing = yaml.safe_load(target.read_text()) or {}
        existing_cases = existing.setdefault("cases", [])
        existing_names = {
            item["name"]
            for item in existing_cases
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        duplicates = existing_names & {item["name"] for item in suite["cases"]}
        if duplicates:
            _fail(
                ValueError("Eval case names already exist: " + ", ".join(sorted(duplicates))),
                EXIT_VALIDATION_FAILURE,
            )
        existing_cases.extend(suite["cases"])
        suite = existing
    target.write_text(yaml.safe_dump(suite, sort_keys=False, allow_unicode=True))
    saved_suite = load_eval_suite(target)
    saved_case = next(case for case in saved_suite.cases if case.name == case_name)
    saved_case_result = EvalRunner(saved_suite, runtime).evaluate_trace(saved_case, trace)
    saved_result = SuiteResult(
        saved_suite.name,
        saved_suite.agent,
        (saved_case_result,),
        saved_suite.digest,
    )
    result_path = _persist_eval_result(project, saved_result)
    label = "VERIFIED" if saved_case_result.passed else "FAILING REGRESSION"
    color = "green" if saved_case_result.passed else "yellow"
    console.print(f"[{color}]{label}: saved {target}[/{color}]")
    console.print(f"[dim]Verification record: {result_path}[/dim]")


@eval.command("run")
@click.argument("agent_name", required=False)
@click.option("--case", "case_name")
@click.option(
    "--trials",
    type=click.IntRange(1, 20),
    help="Override the number of independent trials declared by each suite.",
)
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
@click.option(
    "--format", "output_format", type=click.Choice(["terminal", "json"]), default="terminal"
)
def eval_run(
    agent_name: str | None,
    case_name: str | None,
    trials: int | None,
    project_folder: str,
    output_format: str,
) -> None:
    project = _project(project_folder)
    paths = sorted(
        (*project.evals_directory.glob("*.yaml"), *project.evals_directory.glob("*.yml"))
    )
    results = []
    try:
        for path in paths:
            suite = load_eval_suite(path)
            if agent_name and suite.agent != agent_name:
                continue
            if case_name and all(case.name != case_name for case in suite.cases):
                continue
            trial_count = trials or suite.trials
            runtime = project.runtime(suite.agent)
            for trial in range(1, trial_count + 1):
                raw_result = EvalRunner(suite, runtime).run(case_name)
                result = SuiteResult(
                    raw_result.name,
                    raw_result.agent,
                    raw_result.cases,
                    raw_result.suite_digest,
                    trial,
                    trial_count,
                )
                results.append(result)
                _persist_eval_result(project, result)
    except Exception as exc:
        _fail(exc)
    if not results:
        _fail(ValueError("No matching eval suites were found"), EXIT_VALIDATION_FAILURE)
    if output_format == "json":
        click.echo(json.dumps([result.to_dict() for result in results], indent=2, sort_keys=True))
    else:
        for result in results:
            suffix = f" — trial {result.trial}/{result.trials}" if result.trials > 1 else ""
            console.print(f"[bold]{result.name}{suffix}[/bold]")
            for case in result.cases:
                console.print(
                    f"{'[green]PASS[/green]' if case.passed else '[red]FAIL[/red]'} {case.name}"
                )
                for check in case.checks:
                    console.print(f"  {'✓' if check.passed else '✗'} {check.name} {check.message}")
        if any(result.trials > 1 for result in results):
            passed = sum(result.passed for result in results)
            rate = passed / len(results)
            console.print(f"[bold]Trial pass rate: {passed}/{len(results)} ({rate:.1%})[/bold]")
    if any(not result.passed for result in results):
        raise click.exceptions.Exit(EXIT_EVAL_FAILURE)


@cli.command()
@click.argument("agent_name")
@click.argument("question", nargs=-1, required=True)
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
@click.option(
    "--format", "output_format", type=click.Choice(["terminal", "json"]), default="terminal"
)
def ask(
    agent_name: str, question: tuple[str, ...], project_folder: str, output_format: str
) -> None:
    """Ask through the same inspected runtime used by eval cases."""
    project = _project(project_folder)
    question_text = " ".join(question)
    try:
        trace = project.answer(agent_name, question_text)
    except Exception as exc:
        _fail(exc)
    if output_format == "json":
        click.echo(json.dumps(trace.to_dict(), indent=2, sort_keys=True))
    else:
        _print_trace(trace)
    if not trace.passed:
        raise click.exceptions.Exit(EXIT_VALIDATION_FAILURE)


@cli.command()
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
@click.option("--connect/--no-connect", default=True)
def doctor(project_folder: str, connect: bool) -> None:
    """Validate artifacts, adapter connectivity, selectors, metadata, and eval coverage."""
    checks: list[tuple[str, bool, str]] = []
    warnings: list[tuple[str, str]] = []
    try:
        project = Project.load(project_folder)
        summary = project.manifest.summary
        checks.append(
            ("manifest", True, f"{summary.model_count} models; dbt {summary.dbt_version}")
        )
        physical_columns = sum(
            1
            for node in project.manifest.queryable_nodes
            for column in node.columns.values()
            if column.physical_type
        )
        checks.append(
            (
                "catalog",
                project.manifest.catalog_digest is not None,
                f"{physical_columns} physical column types"
                if project.manifest.catalog_digest
                else "missing; run 'dbt docs generate'",
            )
        )
        manifest_mtime = project.manifest.path.stat().st_mtime
        dbt_sources = [project.dbt_project_dir / "dbt_project.yml"]
        packages_file = project.dbt_project_dir / "packages.yml"
        if packages_file.is_file():
            dbt_sources.append(packages_file)
        for folder_name in (
            "models",
            "seeds",
            "snapshots",
            "macros",
            "analyses",
            "tests",
            "packages",
        ):
            folder = project.dbt_project_dir / folder_name
            if folder.is_dir():
                dbt_sources.extend(path for path in folder.rglob("*") if path.is_file())
        stale = [path for path in dbt_sources if path.stat().st_mtime > manifest_mtime]
        checks.append(
            (
                "manifest-freshness",
                not stale,
                "current"
                if not stale
                else "newer dbt files: " + ", ".join(str(path) for path in stale[:5]),
            )
        )
        target = project.target()
        checks.append(("adapter", True, f"{target.adapter} target {target.name}"))
        if connect:
            project.connection().ping()
            checks.append(("connectivity", True, target.identity))
        agent_names = set()
        for item in project.agents():
            resolved = item.resolve(project.manifest)
            agent_names.add(item.name)
            checks.append((f"agent:{item.name}", True, f"{len(resolved.nodes)} resources"))
            if resolved.missing_descriptions:
                warnings.append(
                    (
                        f"metadata:{item.name}",
                        f"{len(resolved.missing_descriptions)} missing descriptions; "
                        "run 'tabletalk agent show "
                        f"{item.name}' for details",
                    )
                )
        coverage_counts: dict[str, int] = {}
        for path in (
            *project.evals_directory.glob("*.yaml"),
            *project.evals_directory.glob("*.yml"),
        ):
            suite = load_eval_suite(path)
            count = sum(
                case.expected_outcome == "answer" and case.verifies_result for case in suite.cases
            )
            coverage_counts[suite.agent] = coverage_counts.get(suite.agent, 0) + count
        for checked_agent in sorted(agent_names):
            count = coverage_counts.get(checked_agent, 0)
            checks.append(
                (
                    f"eval-coverage:{checked_agent}",
                    count > 0,
                    f"{count} result-verifying case{'s' if count != 1 else ''}"
                    if count
                    else "no result-verifying eval case",
                )
            )
    except Exception as exc:
        checks.append(("project", False, str(exc)))
    for name, passed, detail in checks:
        console.print(f"{'[green]PASS[/green]' if passed else '[red]FAIL[/red]'} {name}: {detail}")
    for name, detail in warnings:
        console.print(f"[yellow]WARN[/yellow] {name}: {detail}")
    if any(not passed for _, passed, _ in checks):
        raise click.exceptions.Exit(EXIT_VALIDATION_FAILURE)


if __name__ == "__main__":
    cli()
