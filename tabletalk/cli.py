"""The dbt-native TableTalk command-line workflow."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from difflib import unified_diff
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
    suite_result_from_dict,
)
from tabletalk.executors import LocalExecutor
from tabletalk.imports import ImportKind, QueryImportPreview, preview_import
from tabletalk.inference import CachePolicy, ReplayMissError
from tabletalk.manifest import Manifest, Node
from tabletalk.metrics import BaselineRecord, observations, summarize
from tabletalk.migrations import migrate_project
from tabletalk.plan import case_footprints, compute_plan, render_plan, render_plan_markdown
from tabletalk.project import Project
from tabletalk.proposal_service import (
    approve_proposal,
    bind_existing_content,
    generate_agent_proposals,
    proposal_from_user_files,
    reject_proposal,
    revise_proposal,
)
from tabletalk.proposals import ProposalRecord
from tabletalk.receipts import (
    RECEIPTS_DIRECTORY,
    ReceiptError,
    load_receipt,
    receipt_json_schema,
    render_receipt,
)
from tabletalk.reports import EvaluationReport
from tabletalk.schemas import (
    EVAL_SCHEMA,
    PROJECT_SCHEMA,
    REPORT_SCHEMA,
    content_digest,
    require_schema_version,
)
from tabletalk.semantic_analysis import analyze_semantics
from tabletalk.semantic_inference import infer_semantic_answers
from tabletalk.state import (
    LOCK_FILENAME,
    LockError,
    load_lock,
    refresh_agents,
    snapshot,
    write_lock,
)
from tabletalk.traces import Interpretation as TraceInterpretation
from tabletalk.traces import Trace

console = Console()
EXIT_OPERATIONAL_FAILURE = 1
EXIT_EVAL_FAILURE = 3
EXIT_VALIDATION_FAILURE = 4
EXIT_DRIFT = 5
EXIT_REPLAY_MISS = 6


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
        console.print("Assumptions: " + ". ".join(trace.interpretation.assumptions))
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
    rendered = json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n"
    result_digest = content_digest(rendered).removeprefix("sha256:")[:12]
    target = result_dir / (f"{result.suite_digest[:12]}-trial-{result.trial}-{result_digest}.json")
    result_dir.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_text() != rendered:
        raise ValueError(f"Eval result path has different content: {target}")
    target.write_text(rendered)
    return target


def _proposal_diff(project: Project, proposal: ProposalRecord) -> str:
    lines: list[str] = []
    for item in proposal.files:
        target = project.root / item.path
        before = target.read_text().splitlines(keepends=True) if target.is_file() else []
        after = item.content.splitlines(keepends=True)
        lines.extend(
            unified_diff(
                before,
                after,
                fromfile=item.path if before else "/dev/null",
                tofile=item.path,
            )
        )
    return "".join(lines)


def _load_proposal(project: Project, proposal_id: str | None) -> ProposalRecord:
    if proposal_id:
        source = Path(proposal_id)
        if not source.is_file():
            source = project.proposal_path(proposal_id.removesuffix(".json"))
        return ProposalRecord.load(source)
    available = tuple(sorted(project.proposals_directory.glob("prp_*.json")))
    if not available:
        raise ValueError("No proposals exist. Run 'tabletalk propose' first.")
    if len(available) == 1:
        return ProposalRecord.load(available[0])
    if not sys.stdin.isatty():
        raise ValueError("More than one proposal exists. Specify a proposal ID.")
    choices = {ProposalRecord.load(path).id: path for path in available}
    selected = click.prompt("Proposal ID", type=click.Choice(tuple(choices)))
    return ProposalRecord.load(choices[selected])


def _print_proposal(project: Project, proposal: ProposalRecord) -> None:
    console.print(f"[bold]{proposal.id}[/bold]  {proposal.digest}")
    for item in proposal.files:
        console.print(Panel(Syntax(item.content, "yaml"), title=item.path))
        confidence = {field.confidence.value for field in item.fields}
        references = {evidence.reference for field in item.fields for evidence in field.evidence}
        console.print(
            f"Evidence: {len(item.fields)} fields, "
            f"confidence {', '.join(sorted(confidence))}, "
            f"sources {', '.join(sorted(references))}"
        )
    diff = _proposal_diff(project, proposal)
    console.print(Panel(Syntax(diff or "No file change.\n", "diff"), title="Exact change"))


def _run_approved_evals(project: Project, paths: tuple[Path, ...]) -> bool:
    eval_paths = tuple(path for path in paths if path.parent == project.evals_directory)
    passed = True
    for path in eval_paths:
        try:
            suite = load_eval_suite(path)
            runtime = project.runtime(suite.agent, cache_policy=CachePolicy.NORMAL)
            for trial in range(1, suite.trials + 1):
                raw = EvalRunner(suite, runtime).run(trial=trial)
                result = SuiteResult(
                    raw.name,
                    raw.agent,
                    raw.cases,
                    raw.suite_digest,
                    trial,
                    suite.trials,
                )
                result_path = _persist_eval_result(project, result)
                label = "PASS" if result.passed else "FAIL"
                color = "green" if result.passed else "red"
                console.print(
                    f"[{color}]{label}[/{color}] {suite.name} "
                    f"trial {trial}/{suite.trials}: {result_path}"
                )
                passed = result.passed and passed
                if not result.passed and suite.kind == "regression":
                    cases = {case.name: case for case in suite.cases}
                    unaccepted = [
                        item.name
                        for item in result.cases
                        if not item.passed and not cases[item.name].known_failure
                    ]
                    if unaccepted:
                        console.print(
                            "[red]No regression baseline was created. "
                            "These cases failed without known_failure: "
                            + ", ".join(unaccepted)
                            + "[/red]"
                        )
        except Exception as exc:
            console.print(f"[red]Immediate eval validation failed for {path}: {exc}[/red]")
            passed = False
    return passed


@click.group()
@click.version_option(__version__)
@click.option("--verbose", is_flag=True)
def cli(verbose: bool) -> None:
    """Develop, evaluate, and observe trusted data agents over a dbt project."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.WARNING, stream=sys.stderr)


@cli.command()
@click.argument("path", default=".", type=click.Path(path_type=Path))
def migrate(path: Path) -> None:
    """Add current schema identifiers to editable project resources."""
    try:
        changed = migrate_project(path)
    except Exception as exc:
        _fail(exc, EXIT_VALIDATION_FAILURE)
    if not changed:
        console.print("All editable TableTalk resources already use the current schema.")
        return
    for resource in changed:
        console.print(f"Migrated {resource}")


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
            "schema_version": PROJECT_SCHEMA,
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
        for directory in (
            "agents",
            "evals",
            "semantics",
            "references",
            "provenance",
            "baselines",
        ):
            (dbt_root / directory).mkdir(exist_ok=True)
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


@cli.command()
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
@click.option(
    "--sql-import",
    "sql_imports",
    multiple=True,
    type=click.Path(dir_okay=False, exists=True, path_type=Path),
)
@click.option(
    "--bi-import",
    "bi_imports",
    multiple=True,
    type=click.Path(dir_okay=False, exists=True, path_type=Path),
)
@click.option(
    "--history-import",
    "history_imports",
    multiple=True,
    type=click.Path(dir_okay=False, exists=True, path_type=Path),
)
@click.option(
    "--answer",
    "semantic_answers",
    multiple=True,
    help="Answer one semantic gap with GAP_ID=VALUE.",
)
@click.option(
    "--infer",
    "use_inference",
    is_flag=True,
    help="Ask the configured model for untrusted semantic hypotheses.",
)
@click.option(
    "--format", "output_format", type=click.Choice(["terminal", "json"]), default="terminal"
)
def propose(
    project_folder: str,
    sql_imports: tuple[Path, ...],
    bi_imports: tuple[Path, ...],
    history_imports: tuple[Path, ...],
    semantic_answers: tuple[str, ...],
    use_inference: bool,
    output_format: str,
) -> None:
    """Analyze dbt metadata and create review-only agent proposals."""
    project = _project(project_folder)
    try:
        answers: dict[str, str] = {}
        for raw_answer in semantic_answers:
            gap_id, separator, value = raw_answer.partition("=")
            if not separator or not gap_id.strip() or not value.strip():
                raise ValueError("Each --answer value must use GAP_ID=VALUE")
            if gap_id in answers:
                raise ValueError(f"Semantic gap '{gap_id}' has more than one answer")
            answers[gap_id] = value.strip()
        imports: list[QueryImportPreview] = []
        configured_imports = (
            *((path, ImportKind.SQL) for path in sql_imports),
            *((path, ImportKind.BI_QUERY) for path in bi_imports),
            *((path, ImportKind.QUERY_HISTORY) for path in history_imports),
        )
        if configured_imports:
            dialect = project.connection().dialect
            for path, kind in configured_imports:
                imports.extend(preview_import(path, kind, project.manifest, dialect=dialect))
        analysis = analyze_semantics(project.manifest)
        hypotheses = {}
        if use_inference:
            llm_config = project.config.get("llm") or {}
            if not isinstance(llm_config, dict):
                raise ValueError("tabletalk.yaml requires an llm mapping")
            model_identity = (
                f"{llm_config.get('provider') or 'unknown'}:{llm_config.get('model') or 'unknown'}"
            )
            hypotheses = infer_semantic_answers(
                analysis,
                project.llm_provider(),
                model_identity=model_identity,
            )
        proposals = tuple(
            bind_existing_content(record, project.root)
            for record in generate_agent_proposals(
                project.manifest,
                tuple(imports),
                answers,
                hypotheses,
            )
        )
        paths = tuple(item.write(project.proposals_directory) for item in proposals)
    except Exception as exc:
        _fail(exc, EXIT_VALIDATION_FAILURE)
    if not proposals:
        _fail(ValueError("The dbt manifest has no queryable agent scope"))
    if output_format == "json":
        click.echo(
            json.dumps(
                {
                    "analysis": analysis.to_dict(),
                    "imports": [item.to_dict() for item in imports],
                    "proposals": [item.to_dict() for item in proposals],
                    "proposal_paths": [str(path) for path in paths],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    quality = analysis.quality
    console.print(
        f"Semantic quality: {quality.documented_resources}/{quality.resources} resources. "
        f"Columns documented: {quality.documented_columns}/{quality.columns}."
    )
    for question in analysis.high_value_questions():
        console.print(f"[yellow]Question {question.id}:[/yellow] {question.question}")
        if question.options:
            console.print("Options: " + ", ".join(question.options))
    for item in imports:
        console.print(
            f"Imported {item.kind.value}: {item.source_path} {item.locator}. "
            f"Found {item.literal_count} literal value(s). No query ran."
        )
        console.print(Panel(Syntax(item.normalized_sql, "sql"), title="Imported query"))
    if use_inference:
        console.print(
            f"Model hypotheses: {len(hypotheses)}. All hypotheses remain inferred and untrusted."
        )
    for proposal in proposals:
        _print_proposal(project, proposal)
    console.print(
        f"Created {len(proposals)} review-only proposal record(s). "
        "No agent or eval resource changed."
    )


@cli.group()
def proposal() -> None:
    """Inspect, edit, or reject immutable proposal records."""


@proposal.command("show")
@click.argument("proposal_id", required=False)
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
@click.option(
    "--format", "output_format", type=click.Choice(["terminal", "json"]), default="terminal"
)
def proposal_show(
    proposal_id: str | None,
    project_folder: str,
    output_format: str,
) -> None:
    project = _project(project_folder)
    try:
        record = _load_proposal(project, proposal_id)
    except Exception as exc:
        _fail(exc, EXIT_VALIDATION_FAILURE)
    if output_format == "json":
        click.echo(record.dump(), nl=False)
    else:
        _print_proposal(project, record)


@proposal.command("edit")
@click.argument("proposal_id")
@click.argument("resource_path")
@click.option(
    "--from",
    "edited_path",
    required=True,
    type=click.Path(dir_okay=False, exists=True, path_type=Path),
)
@click.option("--editor", default=lambda: os.environ.get("USER") or "local-user")
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
def proposal_edit(
    proposal_id: str,
    resource_path: str,
    edited_path: Path,
    editor: str,
    project_folder: str,
) -> None:
    """Create a new proposal from an edited resource file."""
    project = _project(project_folder)
    try:
        original = _load_proposal(project, proposal_id)
        revised = revise_proposal(
            original,
            resource_path,
            edited_path.read_text(),
            editor=editor,
        )
        path = revised.write(project.proposals_directory)
    except Exception as exc:
        _fail(exc, EXIT_VALIDATION_FAILURE)
    _print_proposal(project, revised)
    console.print(f"Created revised proposal {revised.id}: {path}")


@proposal.command("reject")
@click.argument("proposal_id", required=False)
@click.option("--reason", required=True)
@click.option("--actor", default=lambda: os.environ.get("USER") or "local-user")
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
def proposal_reject(
    proposal_id: str | None,
    reason: str,
    actor: str,
    project_folder: str,
) -> None:
    """Record a proposal rejection without changing project resources."""
    project = _project(project_folder)
    try:
        record = _load_proposal(project, proposal_id)
        path = reject_proposal(
            project.proposals_directory,
            record,
            actor=actor,
            reason=reason,
        )
    except Exception as exc:
        _fail(exc, EXIT_VALIDATION_FAILURE)
    console.print(f"Rejected {record.id}: {path}")


def _lock_approved_agents(project: Project, written: tuple[Path, ...]) -> None:
    """Record the approved agents' current scope in the lock file."""
    agents_dir = project.agents_directory.resolve()
    approved = tuple(
        Agent.load(path)
        for path in written
        if path.suffix in {".yaml", ".yml"} and path.resolve().parent == agents_dir
    )
    if not approved:
        return
    path = project.root / LOCK_FILENAME
    try:
        write_lock(path, refresh_agents(load_lock(path), approved, project.manifest))
    except (LockError, ValueError) as exc:
        _fail(exc, EXIT_VALIDATION_FAILURE)
    names = ", ".join(agent.name for agent in approved)
    console.print(f"Locked {names} in {LOCK_FILENAME}")


@cli.command()
@click.argument("proposal_id", required=False)
@click.option("--file", "selected_paths", multiple=True)
@click.option("--digest")
@click.option("--actor", default=lambda: os.environ.get("USER") or "local-user")
@click.option("--yes", is_flag=True, help="Approve without an interactive confirmation.")
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
def approve(
    proposal_id: str | None,
    selected_paths: tuple[str, ...],
    digest: str | None,
    actor: str,
    yes: bool,
    project_folder: str,
) -> None:
    """Approve exact proposal bytes and write the selected resources."""
    project = _project(project_folder)
    try:
        record = _load_proposal(project, proposal_id)
        _print_proposal(project, record)
        if yes and not digest:
            raise ValueError("Automated approval requires --digest")
        if digest and digest != record.digest:
            raise ValueError("The supplied digest does not match the proposal digest")
        if not yes and not click.confirm(
            f"Approve exact proposal digest {record.digest}?",
            default=False,
        ):
            raise click.Abort()
        result = approve_proposal(
            project.root,
            record,
            manifest_digest="sha256:" + project.manifest.digest,
            expected_digest=digest or record.digest,
            actor=actor,
            selected_paths=selected_paths or None,
        )
    except click.Abort:
        raise
    except Exception as exc:
        _fail(exc, EXIT_VALIDATION_FAILURE)
    console.print(f"[green]Approved {result.approval.id}.[/green]")
    for path in result.written_files:
        console.print(f"Wrote {path.relative_to(project.root)}")
    console.print(f"Provenance: {result.provenance_path.relative_to(project.root)}")
    _lock_approved_agents(project, result.written_files)
    if not _run_approved_evals(project, result.written_files):
        raise click.exceptions.Exit(EXIT_EVAL_FAILURE)


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
    target = project.agents_directory / f"{name}.yaml"
    if target.exists():
        _fail(
            ValueError(f"Agent '{name}' already exists. Use 'tabletalk propose' for a change."),
            EXIT_VALIDATION_FAILURE,
        )
    record = proposal_from_user_files(
        project.manifest,
        ((str(target.relative_to(project.root)), definition.dump()),),
        reference="tabletalk agent create",
    )
    proposal_path = record.write(project.proposals_directory)
    _print_proposal(project, record)
    console.print(f"Created review-only proposal {record.id}: {proposal_path}")
    if create_eval:
        console.print("Approve the agent proposal first. Then use 'tabletalk eval create'.")


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


@cli.group("eval")
def eval_group() -> None:
    """Author and run execution-based correctness evaluations."""


@eval_group.command("create")
@click.argument("agent_name")
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
@click.option("--question")
@click.option("--reference-sql")
@click.option(
    "--execute",
    "--approve",
    "execute_without_prompt",
    is_flag=True,
    help="Execute validated read-only SQL without an interactive prompt.",
)
@click.option("--name")
@click.option("--starter-cases", is_flag=True, help="Add paraphrase, ambiguity, and scope cases.")
def eval_create(
    agent_name: str,
    project_folder: str,
    question: str | None,
    reference_sql: str | None,
    execute_without_prompt: bool,
    name: str | None,
    starter_cases: bool,
) -> None:
    project = _project(project_folder)
    question = question or click.prompt("Representative business question")

    def approve_sql(interpretation: TraceInterpretation, generated: str, executed: str) -> None:
        console.print(f"Interpretation: {interpretation.intent}")
        if interpretation.assumptions:
            console.print("Assumptions: " + ". ".join(interpretation.assumptions))
        console.print(Panel(Syntax(generated, "sql", word_wrap=True), title="Generated SQL"))
        if executed != generated:
            console.print(Panel(Syntax(executed, "sql", word_wrap=True), title="SQL to execute"))
        if not execute_without_prompt and not click.confirm(
            "Execute this read-only SQL?", default=True
        ):
            raise click.Abort()

    try:
        runtime = project.runtime(agent_name)
        trace = runtime.answer(question, before_execute=approve_sql)
    except Exception as exc:
        _fail(exc)
    _print_trace(trace)
    if not execute_without_prompt and not click.confirm(
        "Use this result in an eval proposal?", default=True
    ):
        raise click.Abort()
    if reference_sql is None:
        if execute_without_prompt or click.confirm(
            "Use the reviewed SQL as the changing-data reference?", default=True
        ):
            reference_sql = trace.sql.generated
        else:
            reference_sql = click.prompt(
                "Different reference SQL (blank to freeze the current rows)", default=""
            )
    required_models = list(trace.dbt_context.selected_nodes)
    required_columns = list(trace.dbt_context.columns)
    if not execute_without_prompt:
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
        "schema_version": EVAL_SCHEMA,
        "name": f"{agent_name}-regression",
        "agent": agent_name,
        "cases": [{"name": case_name, "question": question, "expect": expect}],
    }
    if starter_cases or (
        not execute_without_prompt and click.confirm("Add starter robustness cases?", default=True)
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
        if execute_without_prompt:
            _fail(ValueError("Refusing to save an eval that fails immediately"), EXIT_EVAL_FAILURE)
        if not click.confirm("Save this intentionally failing regression?", default=False):
            raise click.Abort()

    target = project.evals_directory / f"{agent_name}.yaml"
    base_contents: dict[str, str] = {}
    if target.exists():
        original_content = target.read_text()
        base_contents[str(target.relative_to(project.root))] = original_content
        existing = yaml.safe_load(original_content) or {}
        existing.setdefault("schema_version", EVAL_SCHEMA)
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
    content = yaml.safe_dump(suite, sort_keys=False, allow_unicode=True)
    record = proposal_from_user_files(
        project.manifest,
        ((str(target.relative_to(project.root)), content),),
        reference="tabletalk eval create",
        base_contents=base_contents,
    )
    proposal_path = record.write(project.proposals_directory)
    _print_proposal(project, record)
    console.print(f"Created review-only eval proposal {record.id}: {proposal_path}")
    console.print("Approve the proposal to write and run the eval.")


@eval_group.command("run")
@click.argument("agent_name", required=False)
@click.option("--case", "case_name")
@click.option(
    "--trials",
    type=click.IntRange(1, 20),
    help="Override the number of independent trials declared by each suite.",
)
@click.option(
    "--cache-policy",
    type=click.Choice([item.value for item in CachePolicy]),
    default=CachePolicy.NORMAL.value,
    show_default=True,
)
@click.option("--workers", type=click.IntRange(1, 32), default=1, show_default=True)
@click.option("--baseline", help="Compare with an approved baseline name or JSON path.")
@click.option(
    "--markdown-out",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Also write the Markdown report to this path (for CI comments).",
)
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
@click.option(
    "--format", "output_format", type=click.Choice(["terminal", "json"]), default="terminal"
)
def eval_run(
    agent_name: str | None,
    case_name: str | None,
    trials: int | None,
    cache_policy: str,
    workers: int,
    baseline: str | None,
    markdown_out: Path | None,
    project_folder: str,
    output_format: str,
) -> None:
    """Run eval trials and write JSON, Markdown, and JUnit reports."""
    project = _project(project_folder)
    paths = sorted(
        (*project.evals_directory.glob("*.yaml"), *project.evals_directory.glob("*.yml"))
    )
    results: list[SuiteResult] = []
    suites: dict[str, tuple[EvalCase, ...]] = {}
    approved_baseline: BaselineRecord | None = None
    try:
        if baseline:
            baseline_path = Path(baseline)
            if not baseline_path.is_file():
                baseline_path = project.root / "baselines" / f"{baseline}.json"
            approved_baseline = BaselineRecord.load(baseline_path)
        tasks: list[tuple[Path, int, int]] = []
        for path in paths:
            suite = load_eval_suite(path)
            if agent_name and suite.agent != agent_name:
                continue
            if case_name and all(case.name != case_name for case in suite.cases):
                continue
            suites[suite.name] = suite.cases
            trial_count = trials or suite.trials
            for trial in range(1, trial_count + 1):
                tasks.append((path, trial, trial_count))

        def run_trial(task: tuple[Path, int, int]) -> SuiteResult:
            path, trial, trial_count = task
            suite = load_eval_suite(path)
            runtime = project.runtime(
                suite.agent,
                cache_policy=CachePolicy(cache_policy),
            )
            raw_result = EvalRunner(suite, runtime).run(case_name, trial=trial)
            result = SuiteResult(
                raw_result.name,
                raw_result.agent,
                raw_result.cases,
                raw_result.suite_digest,
                trial,
                trial_count,
            )
            _persist_eval_result(project, result)
            return result

        results.extend(LocalExecutor(workers).map(run_trial, tasks))
    except ReplayMissError as exc:
        _fail(exc, EXIT_REPLAY_MISS)
    except Exception as exc:
        _fail(exc)
    if not results:
        _fail(ValueError("No matching eval suites were found"), EXIT_VALIDATION_FAILURE)
    try:
        report = EvaluationReport.create(tuple(results), suites, approved_baseline)
        report_paths = report.write(project.root / ".tabletalk" / "reports")
        if markdown_out is not None:
            markdown_out.write_text(report.markdown_text(), encoding="utf-8")
    except Exception as exc:
        _fail(exc, EXIT_VALIDATION_FAILURE)
    if output_format == "json":
        click.echo(report.json_text(), nl=False)
    else:
        summary = report.summary
        color = "green" if report.passed else "red"
        interval = summary.pass_rate_interval
        console.print(
            f"[{color}]{'PASS' if report.passed else 'FAIL'}[/{color}] "
            f"{summary.passed}/{summary.trials} eval trials passed "
            f"({summary.pass_rate:.1%}). "
            f"The 95% CI is {interval[0]:.1%} to {interval[1]:.1%}."
        )
        cost_text = (
            "not recorded" if summary.estimated_cost is None else f"${summary.estimated_cost:.6f}"
        )
        console.print(
            f"Latency mean: {summary.latency_mean_ms or 0:.0f} ms. "
            f"Tokens: {summary.prompt_tokens + summary.completion_tokens}. "
            f"Cost: {cost_text}"
        )
        for result in results:
            for case in result.cases:
                label = "PASS" if case.passed else "FAIL"
                console.print(f"{label} {case.name} (trial {result.trial}/{result.trials})")
        if report.comparison:
            comparison = report.comparison
            console.print(
                f"Baseline {report.baseline_name}: pass-rate effect "
                f"{comparison.pass_rate_effect:+.1%}. "
                f"McNemar p={comparison.mcnemar_p_value:.4g}."
            )
        for diagnosis in report.diagnoses[:10]:
            console.print(
                f"[red]{diagnosis.code.value}[/red] "
                f"{diagnosis.suite}/{diagnosis.case}. "
                f"Owner: {diagnosis.owner.value}. {diagnosis.corrective_action}"
            )
        console.print(f"Report: {report_paths.markdown}")
        console.print(f"JSON: {report_paths.json}")
        console.print(f"JUnit: {report_paths.junit}")
    if any(not result.passed for result in results):
        raise click.exceptions.Exit(EXIT_EVAL_FAILURE)


@cli.group()
def baseline() -> None:
    """Approve and inspect paired-comparison baselines."""


@baseline.command("approve")
@click.argument("name")
@click.option(
    "--from",
    "report_path",
    required=True,
    type=click.Path(dir_okay=False, exists=True, path_type=Path),
)
@click.option("--digest")
@click.option("--known-failure", multiple=True, help="Use the suite/case form.")
@click.option("--actor", default=lambda: os.environ.get("USER") or "local-user")
@click.option("--yes", is_flag=True, help="Approve without an interactive confirmation.")
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
def baseline_approve(
    name: str,
    report_path: Path,
    digest: str | None,
    known_failure: tuple[str, ...],
    actor: str,
    yes: bool,
    project_folder: str,
) -> None:
    """Approve exact eval results as a paired baseline."""
    project = _project(project_folder)
    try:
        if re.fullmatch(r"[a-z][a-z0-9_-]*", name) is None:
            raise ValueError("Baseline name must use lowercase letters, numbers, '_' or '-'")
        raw = report_path.read_bytes()
        source_digest = content_digest(raw)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("The report must be a JSON object")
        require_schema_version(
            payload,
            REPORT_SCHEMA,
            resource=str(report_path),
            editable=False,
        )
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise ValueError("The report results must be a list")
        results = tuple(
            suite_result_from_dict(item) for item in raw_results if isinstance(item, dict)
        )
        failing = {
            f"{result.name}/{case.name}"
            for result in results
            for case in result.cases
            if not case.passed
        }
        declared = set(known_failure)
        unknown = declared - failing
        if unknown:
            raise ValueError("Known-failure entries did not fail: " + ", ".join(sorted(unknown)))
        unapproved = failing - declared
        if unapproved:
            raise ValueError(
                "The report has failing cases. Mark each case with --known-failure: "
                + ", ".join(sorted(unapproved))
            )
        if yes and not digest:
            raise ValueError("Automated baseline approval requires --digest")
        if digest and digest != source_digest:
            raise ValueError("The supplied digest does not match the exact report bytes")
        console.print(f"Report digest: {source_digest}")
        console.print(f"Observations: {len(observations(results))}. Failures: {len(failing)}.")
        if not yes and not click.confirm(
            "Approve these exact results as a baseline?",
            default=False,
        ):
            raise click.Abort()
        record = BaselineRecord(
            name,
            observations(results),
            actor,
            datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            source_digest,
            tuple(sorted(declared)),
        )
        path = record.write(project.root / "baselines" / f"{name}.json")
    except click.Abort:
        raise
    except Exception as exc:
        _fail(exc, EXIT_VALIDATION_FAILURE)
    console.print(f"[green]Approved baseline {name}.[/green] {path}")


@baseline.command("show")
@click.argument("name")
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
@click.option(
    "--format", "output_format", type=click.Choice(["terminal", "json"]), default="terminal"
)
def baseline_show(name: str, project_folder: str, output_format: str) -> None:
    """Show an approved baseline and its exact source digest."""
    project = _project(project_folder)
    try:
        record = BaselineRecord.load(project.root / "baselines" / f"{name}.json")
    except Exception as exc:
        _fail(exc, EXIT_VALIDATION_FAILURE)
    if output_format == "json":
        click.echo(record.dump(), nl=False)
    else:
        summary = summarize(record.observations)
        console.print(f"Baseline: {record.name}")
        console.print(f"Source report: {record.source_digest}")
        console.print(f"Pass rate: {summary.passed}/{summary.trials} ({summary.pass_rate:.1%}).")


@cli.command()
@click.argument("agent_name")
@click.argument("question", nargs=-1, required=True)
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
@click.option(
    "--format", "output_format", type=click.Choice(["terminal", "json"]), default="terminal"
)
@click.option(
    "--cache-policy",
    type=click.Choice([item.value for item in CachePolicy]),
    help="Override inference.cache_policy; 'replay' answers from recorded inference only.",
)
def ask(
    agent_name: str,
    question: tuple[str, ...],
    project_folder: str,
    output_format: str,
    cache_policy: str | None,
) -> None:
    """Ask an agent and print the answer receipt (tabletalk.receipt/v1)."""
    project = _project(project_folder)
    question_text = " ".join(question)
    policy = CachePolicy(cache_policy) if cache_policy else None
    try:
        trace = project.answer(agent_name, question_text, cache_policy=policy)
        receipt = project.receipt(trace)
    except ReplayMissError as exc:
        _fail(exc, EXIT_REPLAY_MISS)
    except Exception as exc:
        _fail(exc)
    if output_format == "json":
        click.echo(receipt.model_dump_json(indent=2))
    else:
        console.print(render_receipt(receipt))
    if not trace.passed:
        raise click.exceptions.Exit(EXIT_VALIDATION_FAILURE)


@cli.command()
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
@click.option(
    "--cache-policy",
    type=click.Choice([item.value for item in CachePolicy]),
    help="Override inference.cache_policy for every answer this server gives.",
)
def mcp(project_folder: str, cache_policy: str | None) -> None:
    """Serve locked agents to MCP clients over stdio (needs tabletalk[mcp])."""
    try:
        from tabletalk.mcp_server import build_server
    except ImportError:
        _fail(ImportError("Install MCP support with: pip install 'tabletalk[mcp]'"))
    from tabletalk.agent_service import AgentService

    project = _project(project_folder)
    try:
        service = AgentService(
            project, cache_policy=CachePolicy(cache_policy) if cache_policy else None
        )
    except Exception as exc:
        _fail(exc, EXIT_VALIDATION_FAILURE)
    # stdout carries the protocol; status goes to stderr.
    served = [agent.name for agent in service.agents() if agent.status == "served"]
    click.echo(f"tabletalk mcp: serving {', '.join(served) or 'no agents'}", err=True)
    build_server(service).run("stdio")


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
            ("manifest", True, f"{summary.model_count} models, dbt {summary.dbt_version}")
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
                else "missing. Run 'dbt docs generate'.",
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
                        f"{len(resolved.missing_descriptions)} missing descriptions. "
                        "Run 'tabletalk agent show "
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


@cli.command()
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
def lock(project_folder: str) -> None:
    """Record exactly what each agent is approved to see in tabletalk.lock.yaml."""
    project = _project(project_folder)
    try:
        current = snapshot(project.agents(), project.manifest)
    except Exception as exc:
        _fail(exc, EXIT_VALIDATION_FAILURE)
    path = project.root / LOCK_FILENAME
    write_lock(path, current)
    resources = sum(len(agent.resources) for agent in current.agents.values())
    console.print(
        f"[green]Locked[/green] {len(current.agents)} agent"
        f"{'' if len(current.agents) == 1 else 's'} over {resources} dbt resource"
        f"{'' if resources == 1 else 's'} → {path.name}"
    )


@cli.command()
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["terminal", "json", "markdown"]),
    default="terminal",
)
@click.option(
    "--check", is_flag=True, help=f"Exit {EXIT_DRIFT} when any agent differs from the lock (CI)."
)
def plan(project_folder: str, output_format: str, check: bool) -> None:
    """Show how the current dbt manifest changes each agent and which evals it puts at risk."""
    project = _project(project_folder)
    path = project.root / LOCK_FILENAME
    try:
        locked = load_lock(path)
        current = snapshot(project.agents(), project.manifest)
        suites = [
            load_eval_suite(item)
            for item in sorted(
                (*project.evals_directory.glob("*.yaml"), *project.evals_directory.glob("*.yml"))
            )
        ]
    except (LockError, ValueError) as exc:
        _fail(exc, EXIT_VALIDATION_FAILURE)
    if locked is None:
        if output_format == "markdown":
            click.echo(render_plan_markdown(None), nl=False)
            raise click.exceptions.Exit(EXIT_DRIFT if check else 0)
        console.print(
            f"No {LOCK_FILENAME} yet. Run [bold]tabletalk lock[/bold] to record what "
            "each agent is approved to see."
        )
        raise click.exceptions.Exit(EXIT_DRIFT if check else 0)
    result = compute_plan(locked, current, case_footprints(suites, project.manifest, project.root))
    if output_format == "json":
        click.echo(result.model_dump_json(indent=2))
    elif output_format == "markdown":
        click.echo(render_plan_markdown(result), nl=False)
    else:
        console.print(render_plan(result), soft_wrap=True)
    if check and result.has_changes:
        raise click.exceptions.Exit(EXIT_DRIFT)


@cli.group()
def receipt() -> None:
    """Inspect, export, and replay answer receipts."""


@receipt.command("show")
@click.argument("run_id")
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
@click.option(
    "--format", "output_format", type=click.Choice(["terminal", "json"]), default="terminal"
)
def receipt_show(run_id: str, project_folder: str, output_format: str) -> None:
    """Show a stored receipt by run ID or unique prefix."""
    try:
        found = load_receipt(Path(project_folder) / RECEIPTS_DIRECTORY, run_id)
    except ReceiptError as exc:
        _fail(exc, EXIT_VALIDATION_FAILURE)
    if output_format == "json":
        click.echo(found.model_dump_json(indent=2))
    else:
        console.print(render_receipt(found))


@receipt.command("schema")
def receipt_schema() -> None:
    """Print the JSON Schema of tabletalk.receipt/v1."""
    click.echo(json.dumps(receipt_json_schema(), indent=2))


@receipt.command("replay")
@click.argument("run_id")
@click.option("--project-folder", default=".", type=click.Path(file_okay=False))
def receipt_replay(run_id: str, project_folder: str) -> None:
    """Re-answer from the recorded inference, with no model call, and compare."""
    project = _project(project_folder)
    try:
        found = load_receipt(project.root / RECEIPTS_DIRECTORY, run_id)
        runtime = project.runtime(found.identity.agent, cache_policy=CachePolicy.REPLAY)
        replayed = runtime.answer(found.question)
    except ReplayMissError as exc:
        _fail(exc, EXIT_REPLAY_MISS)
    except Exception as exc:
        _fail(exc, EXIT_VALIDATION_FAILURE)
    differences = []
    if replayed.sql.executed != found.query.executed:
        differences.append("executed SQL differs")
    rows = replayed.to_dict()["result"]["rows"]
    if rows != found.result.rows:
        differences.append(
            f"result rows differ ({len(rows)} now, {found.result.row_count} recorded)"
        )
    if differences:
        console.print(f"[red]Not reproduced[/red] {found.run_id}: " + "; ".join(differences))
        raise click.exceptions.Exit(EXIT_VALIDATION_FAILURE)
    console.print(
        f"[green]Reproduced[/green] {found.run_id} with no model call: same SQL and "
        f"{found.result.row_count} identical row{'' if found.result.row_count == 1 else 's'}."
    )


if __name__ == "__main__":
    cli()
