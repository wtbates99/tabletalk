from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from tabletalk.agents import Agent
from tabletalk.cli import cli
from tabletalk.evals import load_eval_suite
from tabletalk.imports import ImportKind, preview_import
from tabletalk.interfaces import LLMProvider
from tabletalk.manifest import Manifest
from tabletalk.project import Project
from tabletalk.proposal_service import (
    approve_proposal,
    bind_existing_content,
    generate_agent_proposals,
)
from tabletalk.proposals import (
    Confidence,
    FieldEvidence,
    ProposalError,
    ProposalRecord,
    ProposedFile,
)
from tabletalk.schemas import content_digest
from tabletalk.semantic_analysis import analyze_semantics
from tabletalk.semantic_inference import infer_semantic_answers
from tabletalk.semantics import (
    EvidenceKind,
    EvidenceRef,
    SemanticKnowledge,
    SemanticStatus,
)

EXAMPLE = Path(__file__).parents[2] / "examples" / "dbt-analytics"


class SemanticLLM(LLMProvider):
    def __init__(self, gap_id: str) -> None:
        super().__init__()
        self.gap_id = gap_id

    def generate_response(self, prompt: str) -> str:
        raise NotImplementedError

    def generate_structured(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, object],
    ) -> dict[str, object]:
        return {
            "answers": [
                {
                    "gap_id": self.gap_id,
                    "value": "order_date",
                    "definition": "Revenue probably uses the order date.",
                }
            ]
        }


def manifest() -> Manifest:
    return Manifest.load(EXAMPLE / "target" / "manifest.json")


def local_project(root: Path) -> Project:
    project = object.__new__(Project)
    project.root = root
    project.config = {}
    project.manifest = manifest()
    return project


def test_agent_proposals_are_small_deterministic_and_fully_attributed() -> None:
    first = generate_agent_proposals(manifest())
    second = generate_agent_proposals(manifest())
    assert first == second
    assert 1 <= len(first) <= 3
    assert len({item.id for item in first}) == len(first)
    for proposal in first:
        assert len(proposal.files) == 2
        proposed_file = next(item for item in proposal.files if item.path.startswith("agents/"))
        payload = yaml.safe_load(proposed_file.content)
        assert payload["schema_version"] == "tabletalk.agent/v1"
        assert payload["supported_questions"]
        assert payload["unsupported_questions"]
        assert {field.path for field in proposed_file.fields} == proposed_file.leaf_pointers
        assert all(field.evidence for field in proposed_file.fields)
        eval_file = next(item for item in proposal.files if item.path.startswith("evals/"))
        assert {field.path for field in eval_file.fields} == eval_file.leaf_pointers


def test_explicit_query_import_adds_an_attributed_reference_eval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "finance.sql"
    source.write_text(
        "select sum(recognized_revenue) as recognized_revenue "
        "from {{ ref('fct_orders') }} "
        "where order_date >= '2026-07-01' and order_date < '2026-08-01'\n"
    )
    current_manifest = manifest()
    monkeypatch.chdir(tmp_path)
    imported = preview_import(
        Path("finance.sql"),
        ImportKind.SQL,
        current_manifest,
        dialect="duckdb",
    )
    assert imported[0].source_path == Path("finance.sql")
    assert imported[0].evidence().reference == "finance.sql"
    proposal = generate_agent_proposals(current_manifest, imported)[0]
    assert len(proposal.files) == 4
    reference = next(item for item in proposal.files if item.path.startswith("references/"))
    assert reference.content == source.read_text()
    assert reference.fields[0].evidence[0].kind is EvidenceKind.SQL_IMPORT
    eval_file = next(item for item in proposal.files if item.path.startswith("evals/"))
    payload = yaml.safe_load(eval_file.content)
    imported_case = next(
        item for item in payload["cases"] if item["name"].startswith("approved-query-")
    )
    assert imported_case["question"] == (
        "What is the total recognized revenue in July 2026?"
    )
    assert imported_case["expect"]["reference"] == reference.path
    assert imported_case["expect"]["result"]["comparison"] == "scalar"
    semantic_file = next(
        item for item in proposal.files if item.path.startswith("semantics/")
    )
    semantic_path = tmp_path / semantic_file.path
    semantic_path.parent.mkdir(parents=True)
    semantic_path.write_text(semantic_file.content)
    statement = SemanticKnowledge.load(semantic_path).get("recognized_revenue")
    assert statement.status is SemanticStatus.CONFIRMED
    assert statement.time_column == "order_date"
    assert statement.filters
    assert statement.evidence[0].kind is EvidenceKind.SQL_IMPORT


def test_targeted_answer_proposes_confirmed_semantic_knowledge(tmp_path: Path) -> None:
    source_manifest = manifest()
    payload = deepcopy(source_manifest.payload)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload))
    current_manifest = Manifest.load(manifest_path)
    gap_id = "time_basis:model.analytics.fct_orders.booked_revenue"
    proposal = generate_agent_proposals(
        current_manifest,
        semantic_answers={gap_id: "booked_date"},
    )[0]
    semantic_file = next(
        item for item in proposal.files if item.path.startswith("semantics/")
    )
    semantic_path = tmp_path / semantic_file.path
    semantic_path.parent.mkdir(parents=True)
    semantic_path.write_text(semantic_file.content)
    knowledge = SemanticKnowledge.load(semantic_path)
    statement = knowledge.get("booked_revenue")
    assert statement.status is SemanticStatus.CONFIRMED
    assert statement.time_column == "booked_date"
    assert all(
        evidence.kind is EvidenceKind.USER_ANSWER
        for field in semantic_file.fields
        for evidence in field.evidence
    )
    agent_file = next(item for item in proposal.files if item.path.startswith("agents/"))
    assert yaml.safe_load(agent_file.content)["knowledge"] == [semantic_file.path]
    eval_file = next(item for item in proposal.files if item.path.startswith("evals/"))
    cases = yaml.safe_load(eval_file.content)["cases"]
    assert not any(
        item["name"] == "clarify-booked-revenue-time-field" for item in cases
    )
    monthly = next(
        item for item in cases if item["name"] == "group-booked-revenue-by-month"
    )
    assert monthly["expect"]["columns"]["required"] == [
        "booked_revenue",
        "booked_date",
    ]
    not_null = next(item for item in cases if item["name"] == "not-null-order-id")
    assert not_null["expect"]["result"]["comparison"] == "scalar"


def test_optional_model_hypothesis_stays_inferred_and_untrusted(tmp_path: Path) -> None:
    source_manifest = manifest()
    payload = deepcopy(source_manifest.payload)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload))
    current_manifest = Manifest.load(manifest_path)
    gap_id = "time_basis:model.analytics.fct_orders.booked_revenue"
    inferred = infer_semantic_answers(
        analyze_semantics(current_manifest),
        SemanticLLM(gap_id),
        model_identity="ollama:gemma4:31b-cloud",
    )
    proposal = generate_agent_proposals(
        current_manifest,
        inferred_answers=inferred,
    )[0]
    semantic_file = next(
        item for item in proposal.files if item.path.startswith("semantics/")
    )
    semantic_path = tmp_path / semantic_file.path
    semantic_path.parent.mkdir(parents=True)
    semantic_path.write_text(semantic_file.content)
    statement = SemanticKnowledge.load(semantic_path).get("booked_revenue")
    assert statement.status is SemanticStatus.INFERRED
    assert statement.can_support_answer is False
    assert statement.approval is not None
    assert statement.approval.action == "store_untrusted"
    assert any(field.confidence.value == "low" for field in semantic_file.fields)
    assert all(
        evidence.kind is EvidenceKind.LLM_INFERENCE
        for field in semantic_file.fields
        if field.path.startswith("/statements/")
        for evidence in field.evidence
    )


def test_approval_writes_exact_bytes_and_records_provenance(tmp_path: Path) -> None:
    current_manifest = manifest()
    proposal = generate_agent_proposals(current_manifest)[0]
    result = approve_proposal(
        tmp_path,
        proposal,
        manifest_digest="sha256:" + current_manifest.digest,
        expected_digest=proposal.digest,
        actor="test-user",
        recorded_at="2026-08-10T18:42:11Z",
    )
    proposed_file = proposal.files[0]
    target = tmp_path / proposed_file.path
    assert target.read_bytes() == proposed_file.content.encode()
    assert Agent.load(target).name == yaml.safe_load(proposed_file.content)["name"]
    approval = json.loads(result.approval_path.read_text())
    provenance = json.loads(result.provenance_path.read_text())
    assert approval["proposal_digest"] == proposal.digest
    assert provenance["files"][proposed_file.path]["sha256"] == proposed_file.digest
    suite = load_eval_suite(tmp_path / f"evals/{Agent.load(target).name}.yaml")
    assert {case.kind for case in suite.cases} >= {"capability", "refusal"}
    assert all(
        case.protects and case.reason and case.source and case.failure
        for case in suite.cases
    )


def test_approval_requires_exact_digest_and_current_manifest(tmp_path: Path) -> None:
    current_manifest = manifest()
    proposal = generate_agent_proposals(current_manifest)[0]
    arguments = {
        "manifest_digest": "sha256:" + current_manifest.digest,
        "expected_digest": proposal.digest,
        "actor": "test-user",
    }
    with pytest.raises(ProposalError, match="approval digest"):
        approve_proposal(tmp_path, proposal, **{**arguments, "expected_digest": "sha256:bad"})
    with pytest.raises(ProposalError, match="manifest changed"):
        approve_proposal(
            tmp_path,
            proposal,
            **{**arguments, "manifest_digest": content_digest(b"changed")},
        )


def test_approval_blocks_missing_file_dependencies(tmp_path: Path) -> None:
    source = EvidenceRef(
        EvidenceKind.USER_ANSWER,
        "answer",
        content_digest(b"answer"),
        "/answer",
    )
    agent_content = (
        "schema_version: tabletalk.agent/v1\n"
        "name: revenue\n"
        "description: Revenue.\n"
        "select: [group:finance]\n"
        "knowledge: [semantics/revenue.yaml]\n"
    )
    semantic_content = "schema_version: tabletalk.semantic/v1\nstatements: {}\n"
    agent = ProposedFile(
        "agents/revenue.yaml",
        agent_content,
        tuple(
            FieldEvidence(path, Confidence.HIGH, (source,))
            for path in sorted(
                {
                    "/schema_version",
                    "/name",
                    "/description",
                    "/select/0",
                    "/knowledge/0",
                }
            )
        ),
    )
    semantic = ProposedFile(
        "semantics/revenue.yaml",
        semantic_content,
        (
            FieldEvidence("/schema_version", Confidence.HIGH, (source,)),
            FieldEvidence("/statements", Confidence.HIGH, (source,)),
        ),
    )
    proposal = ProposalRecord((agent, semantic), content_digest(b"manifest"))
    with pytest.raises(ProposalError, match="omitted required files"):
        approve_proposal(
            tmp_path,
            proposal,
            manifest_digest=proposal.manifest_digest,
            expected_digest=proposal.digest,
            actor="test-user",
            selected_paths=("agents/revenue.yaml",),
        )


def test_approval_refuses_to_overwrite_different_content(tmp_path: Path) -> None:
    current_manifest = manifest()
    proposal = generate_agent_proposals(current_manifest)[0]
    target = tmp_path / proposal.files[0].path
    target.parent.mkdir(parents=True)
    target.write_text("user content\n")
    with pytest.raises(ProposalError, match="overwrite different content"):
        approve_proposal(
            tmp_path,
            proposal,
            manifest_digest="sha256:" + current_manifest.digest,
            expected_digest=proposal.digest,
            actor="test-user",
        )


def test_approval_writes_a_reviewed_edit_only_from_its_exact_base(tmp_path: Path) -> None:
    current_manifest = manifest()
    proposal = generate_agent_proposals(current_manifest)[0]
    target = tmp_path / proposal.files[0].path
    target.parent.mkdir(parents=True)
    target.write_text("schema_version: tabletalk.agent/v1\nname: old\n")
    bound = bind_existing_content(proposal, tmp_path)
    approve_proposal(
        tmp_path,
        bound,
        manifest_digest="sha256:" + current_manifest.digest,
        expected_digest=bound.digest,
        actor="test-user",
        selected_paths=(bound.files[0].path,),
    )
    assert target.read_text() == bound.files[0].content

    target.write_text("user changed this after proposal\n")
    with pytest.raises(ProposalError, match="base content changed"):
        approve_proposal(
            tmp_path,
            bound,
            manifest_digest="sha256:" + current_manifest.digest,
            expected_digest=bound.digest,
            actor="test-user",
            selected_paths=(bound.files[0].path,),
        )


def test_cli_proposes_before_it_writes_an_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = local_project(tmp_path)
    monkeypatch.setattr("tabletalk.cli._project", lambda path: project)
    runner = CliRunner()
    proposed = runner.invoke(cli, ["propose", "--project-folder", str(tmp_path)])
    assert proposed.exit_code == 0, proposed.output
    assert "No agent or eval resource changed" in proposed.output
    assert not (tmp_path / "agents").exists()
    proposal_path = next(project.proposals_directory.glob("prp_*.json"))
    record = ProposalRecord.load(proposal_path)

    approved = runner.invoke(
        cli,
        [
            "approve",
            record.id,
            "--project-folder",
            str(tmp_path),
            "--yes",
            "--digest",
            record.digest,
            "--actor",
            "test-user",
            "--file",
            record.files[0].path,
        ],
    )
    assert approved.exit_code == 0, approved.output
    assert (tmp_path / record.files[0].path).read_text() == record.files[0].content


def test_automated_cli_approval_requires_a_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = local_project(tmp_path)
    record = generate_agent_proposals(project.manifest)[0]
    record.write(project.proposals_directory)
    monkeypatch.setattr("tabletalk.cli._project", lambda path: project)
    result = CliRunner().invoke(
        cli,
        ["approve", record.id, "--project-folder", str(tmp_path), "--yes"],
    )
    assert result.exit_code == 4
    assert "Automated approval requires --digest" in result.output
    assert not (tmp_path / "agents").exists()


def test_cli_runs_each_approved_eval_immediately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = local_project(tmp_path)
    record = generate_agent_proposals(project.manifest)[0]
    record.write(project.proposals_directory)
    observed: list[tuple[Path, ...]] = []
    monkeypatch.setattr("tabletalk.cli._project", lambda path: project)
    monkeypatch.setattr(
        "tabletalk.cli._run_approved_evals",
        lambda loaded_project, paths: observed.append(paths) is None,
    )
    result = CliRunner().invoke(
        cli,
        [
            "approve",
            record.id,
            "--project-folder",
            str(tmp_path),
            "--yes",
            "--digest",
            record.digest,
            "--actor",
            "test-user",
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(observed) == 1
    assert {path.parent.name for path in observed[0]} == {"agents", "evals"}
