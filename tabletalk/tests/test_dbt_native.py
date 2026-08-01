from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from tabletalk.agents import Agent
from tabletalk.authoring import parse_choices, selector_options
from tabletalk.cli import cli
from tabletalk.connections import ReadOnlyConnection, Target
from tabletalk.evals import (
    EvalCase,
    EvalError,
    EvalRunner,
    EvalSuite,
    ResultExpectation,
    load_eval_suite,
)
from tabletalk.evals import _compare_result as compare_result
from tabletalk.interfaces import LLMProvider, validate_structured_value
from tabletalk.manifest import Manifest, ManifestError
from tabletalk.project import Project
from tabletalk.providers.duckdb_provider import DuckDBProvider
from tabletalk.providers.openai_provider import _json_object
from tabletalk.runtime import Runtime
from tabletalk.runtime import _claim_covered as claim_covered
from tabletalk.runtime import _text_value_present as text_value_present
from tabletalk.traces import Claim, Evidence
from tabletalk.validation import SQLValidationError, validate_sql

EXAMPLE = Path(__file__).parents[2] / "examples" / "dbt-analytics"


def test_cloud_json_fences_are_parsed_without_weakening_object_shape() -> None:
    assert _json_object('```json\n{"answer": 42}\n```', "cloud") == {"answer": 42}
    assert _json_object('Here is the result:\n```json\n{"answer": 42}\n```', "cloud") == {
        "answer": 42
    }
    with pytest.raises(ValueError, match="must be an object"):
        _json_object("```json\n[42]\n```", "cloud")


def test_cloud_output_is_validated_against_the_requested_schema() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["answer"],
        "properties": {"answer": {"type": "integer"}},
    }
    validate_structured_value({"answer": 42}, schema)
    with pytest.raises(ValueError, match="missing: answer"):
        validate_structured_value({}, schema)
    with pytest.raises(ValueError, match="unexpected fields: extra"):
        validate_structured_value({"answer": 42, "extra": True}, schema)
    with pytest.raises(ValueError, match=r"\$\.answer must have type integer"):
        validate_structured_value({"answer": "42"}, schema)


def test_claim_matching_tolerates_wording_but_not_different_claims() -> None:
    assert claim_covered(
        "The Dodgers have won the most games, with 69 wins.",
        "The Dodgers won the most games with 69 wins.",
    )
    assert claim_covered(
        "In 2026, the Dodgers won the most games with 69 wins.",
        "The Dodgers won the most games in 2026. They had 69 wins.",
    )
    assert claim_covered(
        "The Los Angeles Dodgers have won the most regular-season games in 2026, "
        "with a total of 69 wins.",
        "The Los Angeles Dodgers won the most games in 2026. They won 69 games.",
    )
    assert not claim_covered("The Dodgers won 69 games.", "The Yankees won 60 games.")
    assert text_value_present("Los Angeles Dodgers", "The Los Angeles Dodgers won 69 games.")
    assert not text_value_present("Boston Red Sox", "The Chicago White Sox won 69 games.")


def test_year_followed_by_punctuation_is_not_treated_as_a_result_value() -> None:
    text = "In 2026, the Los Angeles Dodgers won 69 games."
    claims = (
        Claim(
            text,
            (Evidence(0, "team_name"), Evidence(0, "wins")),
        ),
    )
    checks = Runtime._validate_claims(
        text,
        claims,
        ({"team_name": "Los Angeles Dodgers", "wins": 69},),
    )
    assert all(check.passed for check in checks)


def test_claim_evidence_accepts_display_rounding_and_percent_formatting() -> None:
    text = "His ERA is 3.18 and his whiff rate is 31.8%."
    checks = Runtime._validate_claims(
        text,
        (
            Claim(
                text,
                (Evidence(0, "era"), Evidence(0, "whiff_rate")),
            ),
        ),
        ({"era": 3.180871, "whiff_rate": 0.3177},),
    )
    assert all(check.passed for check in checks)

    already_scaled = Runtime._validate_claims(
        "The one-run rate was 27.06%.",
        (Claim("The one-run rate was 27.06%.", (Evidence(0, "one_run_pct"),)),),
        ({"one_run_pct": 27.0649},),
    )
    assert all(check.passed for check in already_scaled)

    threshold = Runtime._validate_claims(
        "Among hitters with at least 50 PA, the best xwOBA was 0.450.",
        (
            Claim(
                "Among hitters with at least 50 PA, the best xwOBA was 0.450.",
                (Evidence(0, "xwoba"),),
            ),
        ),
        ({"xwoba": 0.45},),
        question="Who was best with at least 50 PA?",
    )
    assert all(check.passed for check in threshold)


def test_claim_evidence_does_not_treat_iso_date_parts_as_numeric_metrics() -> None:
    text = "The feature view covers 2020-07-23 through 2026-07-31."
    checks = Runtime._validate_claims(
        text,
        (
            Claim(
                text,
                (Evidence(0, "start_date"), Evidence(0, "end_date")),
            ),
        ),
        ({"start_date": date(2020, 7, 23), "end_date": date(2026, 7, 31)},),
    )
    assert all(check.passed for check in checks)

    natural_date = "The game was on July 26, 2026."
    natural_checks = Runtime._validate_claims(
        natural_date,
        (Claim(natural_date, (Evidence(0, "game_date"),)),),
        ({"game_date": date(2026, 7, 26)},),
    )
    assert all(check.passed for check in natural_checks)


def test_eval_suite_declares_kind_and_repeat_trials(tmp_path: Path) -> None:
    path = tmp_path / "suite.yaml"
    path.write_text(
        "name: reliability\nagent: revenue\nkind: regression\ntrials: 3\n"
        "description: Repeats customer-facing regressions.\n"
        "cases:\n  - name: one\n    question: One?\n"
    )
    suite = load_eval_suite(path)
    assert suite.kind == "regression"
    assert suite.trials == 3
    assert suite.description == "Repeats customer-facing regressions."


class StubLLM(LLMProvider):
    def __init__(self, sql: str, value: str = "Recognized revenue was $184.25.") -> None:
        super().__init__()
        self.sql = sql
        self.value = value
        self.calls = 0

    def generate_response(self, prompt: str) -> str:
        raise NotImplementedError

    def generate_structured(
        self, messages: list[dict[str, str]], json_schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls += 1
        if self.calls % 2:
            return {
                "interpretation": {
                    "intent": "aggregate",
                    "metrics": ["recognized_revenue"],
                    "dimensions": [],
                    "start_date": "2026-07-01",
                    "end_date": "2026-08-01",
                    "assumptions": [],
                },
                "sql": self.sql,
                "rejection": None,
            }
        return {
            "text": self.value,
            "claims": [
                {
                    "text": self.value,
                    "evidence": [{"row": 0, "column": "recognized_revenue"}],
                }
            ],
        }


@pytest.fixture
def manifest() -> Manifest:
    return Manifest.load(EXAMPLE / "target" / "manifest.json")


@pytest.fixture
def runtime(manifest: Manifest) -> Runtime:
    provider = DuckDBProvider()
    provider.connection.execute("ATTACH ':memory:' AS analytics")
    provider.connection.execute(
        "create table analytics.main.fct_orders "
        "(order_id integer, order_date date, recognized_revenue decimal(18,2), customer_id varchar)"
    )
    provider.connection.execute(
        "insert into analytics.main.fct_orders values "
        "(1, '2026-07-03', 100.00, 'C001'), "
        "(2, '2026-07-17', 84.25, 'C002'), "
        "(3, '2026-08-02', 50.00, 'C001')"
    )
    target = Target("analytics", "dev", "duckdb", {"type": "duckdb"})
    connection = ReadOnlyConnection(target, provider)
    agent = Agent("revenue", "Revenue", ("group:finance",)).resolve(manifest)
    sql = (
        "select sum(recognized_revenue) as recognized_revenue "
        "from analytics.main.fct_orders "
        "where order_date >= '2026-07-01' and order_date < '2026-08-01'"
    )
    return Runtime(manifest, agent, connection, StubLLM(sql), model_identity="stub:model")


def test_manifest_normalizes_authoritative_metadata(manifest: Manifest) -> None:
    node = manifest.nodes["model.analytics.fct_orders"]
    assert node.group == "finance"
    assert node.tags == ("revenue",)
    assert node.owner == "Finance Analytics"
    assert node.access == "protected"
    assert node.materialized == "table"
    assert node.constraints[0]["type"] == "primary_key"
    assert node.columns["recognized_revenue"].data_type == "decimal(18,2)"
    assert node.columns["recognized_revenue"].physical_type == "DECIMAL(18,2)"
    assert manifest.catalog_digest is not None
    assert node.tests[0].name == "not_null"
    assert node.parents == ("model.analytics.stg_orders",)
    assert node.checksum and len(node.checksum) == 64
    assert manifest.summary.model_count == 4


def test_example_manifest_is_generated_by_dbt_from_example_project(tmp_path: Path) -> None:
    from dbt.cli.main import dbtRunner

    project = tmp_path / "dbt-analytics"
    shutil.copytree(
        EXAMPLE,
        project,
        ignore=shutil.ignore_patterns("target", "*.duckdb", "dbt_packages", "logs"),
    )
    dependencies = dbtRunner().invoke(
        ["deps", "--project-dir", str(project), "--profiles-dir", str(project)]
    )
    assert dependencies.success
    result = dbtRunner().invoke(
        [
            "parse",
            "--project-dir",
            str(project),
            "--profiles-dir",
            str(project),
            "--no-partial-parse",
        ]
    )
    assert result.success
    generated = Manifest.load(project / "target" / "manifest.json")
    assert generated.summary.model_count == manifest_count(EXAMPLE)


def manifest_count(project: Path) -> int:
    return Manifest.load(project / "target" / "manifest.json").summary.model_count


def test_generated_manifest_excludes_disabled_and_ephemeral_models(manifest: Manifest) -> None:
    queryable = {node.name for node in manifest.queryable_nodes}
    assert "disabled_orders" not in queryable
    assert "ephemeral_order_keys" not in queryable
    assert "duplicate_orders" in queryable


def test_duplicate_alias_requires_qualified_relation(manifest: Manifest) -> None:
    with pytest.raises(ManifestError, match="ambiguous"):
        manifest.resolve_relation(("fct_orders",))
    assert manifest.resolve_relation(("main", "fct_orders")).unique_id == (
        "model.analytics.fct_orders"
    )


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        ("group:finance", "model.analytics.fct_orders"),
        ("tag:revenue", "model.analytics.fct_orders"),
        ("model:fct_orders", "model.analytics.fct_orders"),
        ("source:raw.orders", "source.analytics.raw.orders"),
        ("path:models/marts", "model.analytics.fct_orders"),
        ("package:analytics", "model.analytics.fct_orders"),
        ("package:shared_models", "model.shared_models.shared_calendar"),
    ],
)
def test_small_selector_language(manifest: Manifest, selector: str, expected: str) -> None:
    assert expected in {node.unique_id for node in manifest.select((selector,))}


def test_guided_selector_options_are_derived_from_manifest(manifest: Manifest) -> None:
    options = selector_options(manifest, "group")
    assert parse_choices("1, finance", options) == ("group:finance",)
    with pytest.raises(ManifestError, match="Unknown choice"):
        parse_choices("invented", options)


def test_exclusion_and_explicit_graph_expansion(manifest: Manifest) -> None:
    with pytest.raises(ManifestError, match="empty scope"):
        manifest.select(("group:finance",), ("tag:revenue",))
    expanded = manifest.select(("group:finance",), include_parents=True)
    assert {node.unique_id for node in expanded} == {
        "model.analytics.fct_orders",
        "model.analytics.stg_orders",
    }


def test_source_lineage_requires_explicit_selection(manifest: Manifest) -> None:
    expanded = manifest.select(("model:stg_orders",), include_parents=True)
    assert "source.analytics.raw.orders" not in {node.unique_id for node in expanded}
    explicit = manifest.select(("source:raw.orders",))
    assert tuple(node.unique_id for node in explicit) == ("source.analytics.raw.orders",)


def test_sql_validation_derives_nodes_columns_and_limit(manifest: Manifest) -> None:
    scope = manifest.select(("group:finance",))
    validated = validate_sql(
        "select recognized_revenue, order_date from analytics.main.fct_orders",
        manifest,
        scope,
        dialect="duckdb",
        max_rows=25,
    )
    assert [node.unique_id for node in validated.nodes] == ["model.analytics.fct_orders"]
    assert validated.columns == ("order_date", "recognized_revenue")
    assert "LIMIT 25" in validated.executed


def test_sql_validation_supports_cte_derived_columns(manifest: Manifest) -> None:
    validated = validate_sql(
        "with revenue as (select sum(recognized_revenue) as total "
        "from analytics.main.fct_orders) select total from revenue",
        manifest,
        manifest.select(("group:finance",)),
        dialect="duckdb",
        max_rows=10,
    )
    assert validated.columns == ("recognized_revenue",)


def test_sql_validation_supports_qualified_cte_and_subquery_columns(
    manifest: Manifest,
) -> None:
    scope = manifest.select(("group:finance",))
    for sql in (
        "with totals as (select sum(recognized_revenue) as total "
        "from analytics.main.fct_orders) select t.total from totals t",
        "select t.total from (select sum(recognized_revenue) as total "
        "from analytics.main.fct_orders) t",
    ):
        validated = validate_sql(
            sql,
            manifest,
            scope,
            dialect="duckdb",
            max_rows=10,
        )
        assert validated.columns == ("recognized_revenue",)


def test_sql_validation_rejects_ambiguous_unqualified_join_column(manifest: Manifest) -> None:
    with pytest.raises(SQLValidationError, match="ambiguous"):
        validate_sql(
            "select order_id from analytics.main.fct_orders f "
            "join analytics.main.stg_orders s on f.order_id = s.order_id",
            manifest,
            manifest.select(("model:fct_orders", "model:stg_orders")),
            dialect="duckdb",
            max_rows=10,
        )


def test_sql_validation_rejects_external_read_functions(manifest: Manifest) -> None:
    with pytest.raises(SQLValidationError, match="forbidden"):
        validate_sql(
            "select pg_read_file('/etc/passwd') from analytics.main.fct_orders",
            manifest,
            manifest.select(("group:finance",)),
            dialect="duckdb",
            max_rows=10,
        )


@pytest.mark.parametrize(
    "sql",
    [
        "delete from analytics.main.fct_orders",
        "select * from analytics.main.stg_orders",
        "select unknown from analytics.main.fct_orders",
        "select * from analytics.main.fct_orders; select 1",
    ],
)
def test_sql_validation_rejects_writes_out_of_scope_unknown_columns_and_multiple_statements(
    manifest: Manifest, sql: str
) -> None:
    with pytest.raises(SQLValidationError):
        validate_sql(
            sql,
            manifest,
            manifest.select(("group:finance",)),
            dialect="duckdb",
            max_rows=100,
        )


def test_runtime_returns_complete_evidence_trace(runtime: Runtime) -> None:
    trace = runtime.answer("What was recognized revenue in July 2026?")
    assert trace.answer.text == "Recognized revenue was $184.25."
    assert float(trace.result.rows[0]["recognized_revenue"]) == 184.25
    assert trace.dbt_context.selected_nodes == ("model.analytics.fct_orders",)
    assert trace.dbt_context.columns == ("order_date", "recognized_revenue")
    assert "not_null" in trace.dbt_context.relevant_tests
    assert trace.sql.generated != trace.sql.executed
    assert trace.answer.claims[0].evidence[0].column == "recognized_revenue"
    assert trace.passed


def test_runtime_fails_claims_not_present_in_cited_evidence(runtime: Runtime) -> None:
    runtime.llm = StubLLM(
        runtime.llm.sql,  # type: ignore[attr-defined]
        value="Recognized revenue was $999.00.",
    )
    trace = runtime.answer("What was recognized revenue in July 2026?")
    check = next(item for item in trace.verification if item.name == "claims_supported")
    assert not check.passed
    assert "999.0" in check.message


def test_runtime_repairs_one_sql_validation_failure(runtime: Runtime) -> None:
    valid_sql = runtime.llm.sql  # type: ignore[attr-defined]

    class RepairLLM(StubLLM):
        def generate_structured(
            self, messages: list[dict[str, str]], json_schema: dict[str, Any]
        ) -> dict[str, Any]:
            self.calls += 1
            if self.calls <= 2:
                return {
                    "interpretation": {
                        "intent": "aggregate",
                        "metrics": ["recognized_revenue"],
                        "dimensions": [],
                        "start_date": "2026-07-01",
                        "end_date": "2026-08-01",
                        "assumptions": [],
                    },
                    "sql": "select missing from analytics.main.fct_orders"
                    if self.calls == 1
                    else valid_sql,
                    "rejection": None,
                }
            return {
                "text": "Recognized revenue was $184.25.",
                "claims": [
                    {
                        "text": "Recognized revenue was $184.25.",
                        "evidence": [{"row": 0, "column": "recognized_revenue"}],
                    }
                ],
            }

    runtime.llm = RepairLLM(valid_sql)
    trace = runtime.answer("What was recognized revenue in July 2026?")
    assert trace.passed
    assert runtime.llm.calls == 3  # type: ignore[attr-defined]


def test_eval_uses_runtime_and_reference_query_as_hard_gate(runtime: Runtime) -> None:
    case = EvalCase(
        "july",
        "What was recognized revenue in July 2026?",
        reference_sql=(
            "select sum(recognized_revenue) as recognized_revenue "
            "from {{ ref('fct_orders') }} where order_date >= '2026-07-01' "
            "and order_date < '2026-08-01'"
        ),
        result=ResultExpectation(comparison="scalar", tolerance=0.01),
        required_models=("model.analytics.fct_orders",),
        required_columns=("recognized_revenue", "order_date"),
    )
    result = EvalRunner(EvalSuite("revenue", "revenue", (case,)), runtime).run()
    assert result.passed
    assert result.cases[0].trace is not None
    assert result.cases[0].trace.eval_suite_digest == result.suite_digest


def test_reference_result_difference_is_a_regression(runtime: Runtime) -> None:
    case = EvalCase(
        "wrong",
        "What was recognized revenue in July 2026?",
        result=ResultExpectation(comparison="scalar", value=999, tolerance=0.01),
    )
    result = EvalRunner(EvalSuite("revenue", "revenue", (case,)), runtime).run()
    assert not result.passed
    assert not next(
        check for check in result.cases[0].checks if check.name == "result_match"
    ).passed


def test_empty_literal_rows_are_a_real_expectation(runtime: Runtime, tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text(
        "name: empty\nagent: revenue\ncases:\n"
        "  - name: empty\n    question: none\n"
        "    expect:\n      result:\n        rows: []\n"
    )
    suite = load_eval_suite(path)
    assert suite.cases[0].result.rows == ()
    trace = runtime.answer("What was recognized revenue in July 2026?")
    result = EvalRunner(suite, runtime).evaluate_trace(suite.cases[0], trace)
    assert not result.passed


@pytest.mark.parametrize(
    "expectation",
    [
        lambda: ResultExpectation(comparison="guess"),
        lambda: ResultExpectation(tolerance=-1),
        lambda: ResultExpectation(comparison="keyed"),
        lambda: ResultExpectation(row_count=-1),
    ],
)
def test_invalid_eval_expectations_fail_early(expectation: Any) -> None:
    with pytest.raises(EvalError):
        expectation()


def _project_with_runtime(tmp_path: Path, runtime: Runtime) -> Project:
    project = object.__new__(Project)
    project.root = tmp_path
    project.config = {"evals_dir": "evals"}
    project.runtime = lambda agent_name: runtime  # type: ignore[method-assign]
    return project


def test_live_answer_is_unverified_without_exact_eval_coverage(
    runtime: Runtime, tmp_path: Path
) -> None:
    project = _project_with_runtime(tmp_path, runtime)
    trace = project.answer("revenue", "A new question")
    assert not trace.correctness_verified
    assert not trace.passed
    assert next(
        check for check in trace.verification if check.name == "correctness_eval_coverage"
    ).message.startswith("No approved eval")
    assert list((tmp_path / ".tabletalk" / "runs").glob("*.json"))


def test_live_answer_is_verified_by_an_exact_approved_eval(
    runtime: Runtime, tmp_path: Path
) -> None:
    evals = tmp_path / "evals"
    evals.mkdir()
    (evals / "revenue.yaml").write_text(
        "name: revenue-regression\nagent: revenue\ncases:\n"
        "  - name: july\n    question: What was recognized revenue in July 2026?\n"
        "    expect:\n      result:\n        comparison: scalar\n        value: 184.25\n"
    )
    project = _project_with_runtime(tmp_path, runtime)
    trace = project.answer("revenue", "  WHAT was recognized revenue in July 2026?  ")
    assert trace.correctness_verified
    check = next(item for item in trace.verification if item.name.startswith("correctness_eval:"))
    assert check.passed


def test_structural_eval_cannot_claim_result_verification(runtime: Runtime, tmp_path: Path) -> None:
    evals = tmp_path / "evals"
    evals.mkdir()
    (evals / "revenue.yaml").write_text(
        "name: structural\nagent: revenue\ncases:\n"
        "  - name: july\n    question: What was recognized revenue in July 2026?\n"
        "    expect:\n      result:\n        row_count: 1\n"
    )
    trace = _project_with_runtime(tmp_path, runtime).answer(
        "revenue", "What was recognized revenue in July 2026?"
    )
    assert not trace.correctness_verified
    assert any(check.name == "correctness_eval_coverage" for check in trace.verification)


def test_broken_reference_marks_live_answer_unverified(runtime: Runtime, tmp_path: Path) -> None:
    evals = tmp_path / "evals"
    evals.mkdir()
    (evals / "revenue.yaml").write_text(
        "name: revenue-regression\nagent: revenue\ncases:\n"
        "  - name: july\n    question: What was recognized revenue in July 2026?\n"
        "    expect:\n      reference_sql: select missing from {{ ref('fct_orders') }}\n"
    )
    trace = _project_with_runtime(tmp_path, runtime).answer(
        "revenue", "What was recognized revenue in July 2026?"
    )
    assert not trace.correctness_verified
    check = next(item for item in trace.verification if item.name.startswith("correctness_eval:"))
    assert "reference_query" in check.message


def test_eval_create_appends_all_starter_cases_and_verifies(
    runtime: Runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evals = tmp_path / "evals"
    evals.mkdir()
    (evals / "revenue.yaml").write_text(
        "name: revenue-regression\nagent: revenue\ncases:\n"
        "  - name: existing\n    question: Existing question\n"
        "    expect:\n      result:\n        row_count: 1\n"
    )
    project = _project_with_runtime(tmp_path, runtime)
    project.manifest = runtime.manifest
    monkeypatch.setattr("tabletalk.cli._project", lambda path: project)
    result = CliRunner().invoke(
        cli,
        [
            "eval",
            "create",
            "revenue",
            "--question",
            "What was recognized revenue in July 2026?",
            "--approve",
            "--starter-cases",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "VERIFIED" in result.output
    assert len(load_eval_suite(evals / "revenue.yaml").cases) == 5
    assert list((tmp_path / ".tabletalk" / "eval-results" / "revenue").glob("*.json"))


@pytest.mark.parametrize(
    ("actual", "expected", "expectation", "passed"),
    [
        (({"id": 1}, {"id": 2}), ({"id": 1}, {"id": 2}), ResultExpectation("ordered"), True),
        (({"id": 2}, {"id": 1}), ({"id": 1}, {"id": 2}), ResultExpectation("ordered"), False),
        (({"id": 2}, {"id": 1}), ({"id": 1}, {"id": 2}), ResultExpectation("unordered"), True),
        (
            ({"id": 2, "v": 20}, {"id": 1, "v": 10}),
            ({"id": 1, "v": 10}, {"id": 2, "v": 20}),
            ResultExpectation("keyed", keys=("id",)),
            True,
        ),
        (({"value": 10.01},), (), ResultExpectation("scalar", 0.02, value=10), True),
        (
            ({"minimum": 1, "maximum": 3},),
            ({"min_value": 1, "max_value": 3},),
            ResultExpectation("ordered_values"),
            True,
        ),
    ],
)
def test_all_result_comparison_modes(
    actual: tuple[dict[str, Any], ...],
    expected: tuple[dict[str, Any], ...],
    expectation: ResultExpectation,
    passed: bool,
) -> None:
    assert compare_result(actual, expected, expectation).passed is passed


def test_trace_serialization_is_stable_and_json_safe(runtime: Runtime, tmp_path: Path) -> None:
    trace = runtime.answer("What was recognized revenue in July 2026?")
    path = trace.write(tmp_path, "trace.json")
    payload = json.loads(path.read_text())
    assert payload == trace.to_dict()
    assert payload["dbt_context"]["manifest_digest"] == runtime.manifest.digest


def test_result_comparison_can_allow_helpful_extra_evidence_columns() -> None:
    actual = ({"player_name": "A", "ops": 1.05, "team": "HOU"},)
    expected = ({"player_name": "A", "ops": 1.05},)
    expectation = ResultExpectation(
        comparison="ordered",
        allow_extra_columns=True,
    )
    assert compare_result(actual, expected, expectation).passed


def test_agent_can_deterministically_reject_known_missing_concepts(
    runtime: Runtime,
) -> None:
    runtime.agent = Agent(
        "revenue",
        "Revenue",
        ("group:finance",),
        reject_if_contains=("projected revenue",),
    ).resolve(runtime.manifest)
    with pytest.raises(ValueError, match="requires data this agent does not have"):
        runtime.answer("What is projected revenue?")
    assert runtime.llm.calls == 0  # type: ignore[attr-defined]


def test_cli_exposes_only_dbt_native_lifecycle() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    for command in ("init", "agent", "eval", "ask", "doctor"):
        assert command in result.output


def test_eval_case_filter_skips_other_suites_for_same_agent(
    runtime: Runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evals = tmp_path / "evals"
    evals.mkdir()
    (evals / "a.yaml").write_text(
        "name: first\nagent: revenue\ncases:\n  - name: other\n    question: Other?\n"
    )
    (evals / "b.yaml").write_text(
        "name: second\nagent: revenue\ncases:\n"
        "  - name: july\n    question: What was recognized revenue in July 2026?\n"
        "    expect:\n      result:\n        comparison: scalar\n        value: 184.25\n"
    )
    project = _project_with_runtime(tmp_path, runtime)
    monkeypatch.setattr("tabletalk.cli._project", lambda path: project)
    result = CliRunner().invoke(cli, ["eval", "run", "revenue", "--case", "july"])
    assert result.exit_code == 0, result.output
    assert "PASS july" in result.output
    for removed in (
        "compile",
        "plan",
        "apply",
        "discover",
        "connect",
        "connections",
        "serve",
    ):
        assert re_search_command(result.output, removed) is False


def test_project_load_discovers_nested_tabletalk_folder(tmp_path: Path) -> None:
    nested = tmp_path / "tabletalk"
    shutil.copytree(EXAMPLE, nested)
    project = Project.load(tmp_path)
    assert project.root == nested
    assert project.manifest.summary.model_count == manifest_count(EXAMPLE)


def test_end_to_end_dbt_init_agent_eval_and_live_ask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "analytics"
    shutil.copytree(
        EXAMPLE,
        project,
        ignore=shutil.ignore_patterns("target", "*.duckdb", "dbt_packages", "logs"),
    )
    monkeypatch.chdir(project)
    for dbt_command in ("deps", "seed", "build"):
        built = subprocess.run(
            [
                sys.executable,
                "-m",
                "dbt.cli.main",
                dbt_command,
                "--project-dir",
                str(project),
                "--profiles-dir",
                str(project),
            ],
            cwd=project,
            capture_output=True,
            text=True,
            check=False,
        )
        assert built.returncode == 0, built.stdout + built.stderr
    documented = subprocess.run(
        [
            sys.executable,
            "-m",
            "dbt.cli.main",
            "docs",
            "generate",
            "--project-dir",
            str(project),
            "--profiles-dir",
            str(project),
        ],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    assert documented.returncode == 0, documented.stdout + documented.stderr
    (project / "tabletalk.yaml").unlink()
    for directory in (project / "agents", project / "evals"):
        for path in directory.glob("*.yaml"):
            path.unlink()
    monkeypatch.setenv("DBT_PROFILES_DIR", str(project))
    runner = CliRunner()
    initialized = runner.invoke(
        cli,
        [
            "init",
            "--project-dir",
            str(project),
            "--no-input",
            "--llm-model",
            "stub",
        ],
    )
    assert initialized.exit_code == 0, initialized.output
    assert "Catalog: loaded" in initialized.output
    initialized_config = yaml.safe_load((project / "tabletalk.yaml").read_text())
    assert initialized_config["dbt"]["catalog"] == "target/catalog.json"
    assert initialized_config["dbt"]["run_results"] == "target/run_results.json"
    created = runner.invoke(
        cli,
        [
            "agent",
            "create",
            "--project-folder",
            str(project),
            "--select",
            "group:finance",
            "--name",
            "revenue",
            "--description",
            "Revenue answers",
        ],
    )
    assert created.exit_code == 0, created.output

    sql = (
        "select sum(recognized_revenue) as recognized_revenue "
        'from "analytics"."main"."fct_orders" '
        "where order_date >= '2026-07-01' and order_date < '2026-08-01'"
    )
    monkeypatch.setattr("tabletalk.project.get_llm_provider", lambda config: StubLLM(sql))
    reference = (
        "select sum(recognized_revenue) as recognized_revenue "
        "from {{ ref('fct_orders') }} where order_date >= '2026-07-01' "
        "and order_date < '2026-08-01'"
    )
    authored = runner.invoke(
        cli,
        [
            "eval",
            "create",
            "revenue",
            "--project-folder",
            str(project),
            "--question",
            "What was recognized revenue in July 2026?",
            "--reference-sql",
            reference,
            "--approve",
        ],
    )
    assert authored.exit_code == 0, authored.output
    evaluated = runner.invoke(cli, ["eval", "run", "revenue", "--project-folder", str(project)])
    assert evaluated.exit_code == 0, evaluated.output
    asked = runner.invoke(
        cli,
        [
            "ask",
            "revenue",
            "What was recognized revenue in July 2026?",
            "--project-folder",
            str(project),
        ],
    )
    assert asked.exit_code == 0, asked.output
    assert "How this answer was formed" in asked.output
    assert "model.analytics.fct_orders" in asked.output
    assert "recognized_revenue" in asked.output
    assert list((project / ".tabletalk" / "runs").glob("*.json"))
    assert list((project / ".tabletalk" / "eval-results" / "revenue").glob("*.json"))


def re_search_command(output: str, command: str) -> bool:
    return any(
        line.strip().split(maxsplit=1)[0] == command for line in output.splitlines() if line.strip()
    )
