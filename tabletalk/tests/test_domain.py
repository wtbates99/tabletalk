from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tabletalk.migrations import migrate_project
from tabletalk.proposals import (
    ApprovalAction,
    ApprovalRecord,
    Confidence,
    FieldEvidence,
    ProposalError,
    ProposalRecord,
    ProposedFile,
    ProvenanceRecord,
)
from tabletalk.schemas import SEMANTIC_SCHEMA, SchemaVersionError, content_digest
from tabletalk.semantics import (
    ApprovalRef,
    EvidenceKind,
    EvidenceRef,
    SemanticError,
    SemanticKnowledge,
    SemanticStatement,
    SemanticStatus,
)


def evidence(kind: EvidenceKind = EvidenceKind.DBT_ARTIFACT) -> EvidenceRef:
    return EvidenceRef(kind, "target/manifest.json", content_digest(b"manifest"), "/nodes/one")


def test_semantic_status_controls_answer_support() -> None:
    trusted = SemanticStatement(
        "order_status",
        SemanticStatus.TRUSTED,
        "The order status comes from dbt metadata.",
        evidence=(evidence(),),
    )
    inferred = SemanticStatement(
        "late_order",
        SemanticStatus.INFERRED,
        "A late order arrives after its promised date.",
        approval=ApprovalRef("apr_store", "store_untrusted"),
        evidence=(evidence(EvidenceKind.LLM_INFERENCE),),
    )
    assert trusted.can_support_answer is True
    assert inferred.can_support_answer is False
    with pytest.raises(SemanticError, match="cannot be trusted"):
        SemanticStatement(
            "late_order",
            SemanticStatus.TRUSTED,
            "A late order arrives after its promised date.",
            evidence=(evidence(EvidenceKind.LLM_INFERENCE),),
        )


def test_inferred_statement_requires_user_evidence_to_become_confirmed() -> None:
    inferred = SemanticStatement(
        "recognized_revenue",
        SemanticStatus.INFERRED,
        "Recognized revenue excludes cancelled orders.",
        evidence=(evidence(EvidenceKind.LLM_INFERENCE),),
    )
    with pytest.raises(SemanticError, match="user-answer evidence"):
        inferred.confirm("apr_one", evidence(EvidenceKind.APPROVED_SQL))
    answer = EvidenceRef(
        EvidenceKind.USER_ANSWER,
        "prp_one/question/recognized_revenue",
        content_digest(b"answer-one"),
        "/answer",
    )
    confirmed = inferred.confirm("apr_one", answer)
    assert confirmed.status is SemanticStatus.CONFIRMED
    assert confirmed.approval == ApprovalRef("apr_one", "confirm")
    assert confirmed.can_support_answer is True


def test_semantic_resource_is_versioned_and_stably_ordered(tmp_path: Path) -> None:
    knowledge = SemanticKnowledge(
        (
            SemanticStatement(
                "late_order",
                SemanticStatus.INFERRED,
                "A late order arrives after its promised date.",
                approval=ApprovalRef("apr_one", "store_untrusted"),
            ),
            SemanticStatement(
                "recognized_revenue",
                SemanticStatus.CONFIRMED,
                "Recognized revenue excludes cancelled orders.",
                expression="sum(recognized_revenue)",
                filters=("order_status != 'cancelled'",),
                approval=ApprovalRef("apr_one"),
            ),
        )
    )
    first = knowledge.dump()
    second = knowledge.dump()
    assert first == second
    assert first.startswith(f"schema_version: {SEMANTIC_SCHEMA}\n")
    assert first.index("late_order:") < first.index("recognized_revenue:")
    path = tmp_path / "knowledge.yaml"
    path.write_text(first)
    loaded = SemanticKnowledge.load(path)
    assert loaded.get("recognized_revenue").can_support_answer is True


def test_semantic_resource_keeps_source_evidence(tmp_path: Path) -> None:
    source = evidence(EvidenceKind.USER_ANSWER)
    knowledge = SemanticKnowledge(
        (
            SemanticStatement(
                "recognized_revenue",
                SemanticStatus.CONFIRMED,
                "Recognized revenue excludes cancelled orders.",
                approval=ApprovalRef("apr_one"),
                evidence=(source,),
            ),
        )
    )
    path = tmp_path / "knowledge.yaml"
    path.write_text(knowledge.dump())
    assert SemanticKnowledge.load(path).statements[0].evidence == (source,)


def test_stale_semantic_resource_has_a_migration_message(tmp_path: Path) -> None:
    path = tmp_path / "knowledge.yaml"
    path.write_text("schema_version: tabletalk.semantic/v0\nstatements: {}\n")
    with pytest.raises(SchemaVersionError, match=r"tabletalk migrate .*knowledge.yaml"):
        SemanticKnowledge.load(path)


def test_project_migration_adds_schema_versions_deterministically(tmp_path: Path) -> None:
    (tmp_path / "agents").mkdir()
    (tmp_path / "evals").mkdir()
    (tmp_path / "tabletalk.yaml").write_text("dbt: {}\nllm: {}\n")
    (tmp_path / "agents" / "revenue.yaml").write_text(
        "name: revenue\ndescription: Revenue.\nselect: [group:finance]\n"
    )
    (tmp_path / "evals" / "revenue.yaml").write_text(
        "name: revenue\nagent: revenue\ncases:\n- name: one\n  question: One?\n"
    )
    changed = migrate_project(tmp_path)
    assert len(changed) == 3
    assert migrate_project(tmp_path) == ()
    assert yaml.safe_load((tmp_path / "tabletalk.yaml").read_text())["schema_version"] == (
        "tabletalk.project/v1"
    )
    assert (
        yaml.safe_load((tmp_path / "agents" / "revenue.yaml").read_text())["schema_version"]
        == "tabletalk.agent/v1"
    )
    assert (
        yaml.safe_load((tmp_path / "evals" / "revenue.yaml").read_text())["schema_version"]
        == "tabletalk.eval/v1"
    )


def proposed_file() -> ProposedFile:
    content = "schema_version: tabletalk.agent/v1\nname: revenue\nselect:\n  - group:finance\n"
    source = evidence()
    return ProposedFile(
        "agents/revenue.yaml",
        content,
        tuple(
            FieldEvidence(path, Confidence.HIGH, (source,))
            for path in ("/schema_version", "/name", "/select/0")
        ),
    )


def test_proposal_requires_provenance_for_every_generated_field() -> None:
    content = "schema_version: tabletalk.agent/v1\nname: revenue\n"
    with pytest.raises(ProposalError, match=r"missing /name"):
        ProposedFile(
            "agents/revenue.yaml",
            content,
            (FieldEvidence("/schema_version", Confidence.HIGH, (evidence(),)),),
        )


def test_proposal_is_content_addressed_and_deterministic(tmp_path: Path) -> None:
    first = ProposalRecord((proposed_file(),), content_digest(b"manifest"))
    second = ProposalRecord((proposed_file(),), content_digest(b"manifest"))
    assert first.id == second.id
    assert first.digest == second.digest
    path = first.write(tmp_path)
    assert path.read_text() == first.dump()
    assert ProposalRecord.load(path) == first

    payload = json.loads(path.read_text())
    payload["files"][0]["content"] = payload["files"][0]["content"].replace(
        "name: revenue", "name: changed"
    )
    path.write_text(json.dumps(payload))
    with pytest.raises(ProposalError, match="content-digest mismatch"):
        ProposalRecord.load(path)


def test_approval_binds_selected_exact_file_bytes() -> None:
    proposal = ProposalRecord((proposed_file(),), content_digest(b"manifest"))
    approval = ApprovalRecord.from_proposal(
        proposal,
        recorded_at="2026-08-10T18:42:11Z",
        actor="local-user",
    )
    assert approval.action is ApprovalAction.APPROVE_ALL
    assert approval.files[0].sha256 == proposed_file().digest
    provenance = ProvenanceRecord(approval, proposal.files)
    payload = provenance.to_dict()
    assert payload["approval"]["proposal_digest"] == proposal.digest
    assert set(payload["files"]["agents/revenue.yaml"]["fields"]) == {
        "/schema_version",
        "/name",
        "/select/0",
    }


def test_approval_rejects_unknown_file_selection() -> None:
    proposal = ProposalRecord((proposed_file(),), content_digest(b"manifest"))
    with pytest.raises(ProposalError, match="unknown files"):
        ApprovalRecord.from_proposal(
            proposal,
            recorded_at="2026-08-10T18:42:11Z",
            actor="local-user",
            selected_paths=("evals/missing.yaml",),
        )
