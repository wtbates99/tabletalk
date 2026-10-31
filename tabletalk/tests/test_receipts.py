"""Answer receipts: trust levels, dbt context, the published spec, and replay."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner

from tabletalk.cli import cli
from tabletalk.manifest import Manifest
from tabletalk.receipts import (
    RECEIPTS_DIRECTORY,
    Receipt,
    build_receipt,
    load_receipt,
    receipt_json_schema,
    run_id_for,
    write_receipt,
)
from tabletalk.tests.test_dbt_native import make_runtime
from tabletalk.traces import Trace, Verification

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "dbt-analytics"
SPEC = ROOT / "docs" / "spec"
QUESTION = "What was recognized revenue in July 2026?"


@pytest.fixture
def manifest() -> Manifest:
    return Manifest.load(EXAMPLE / "target" / "manifest.json")


@pytest.fixture
def trace(manifest: Manifest) -> Trace:
    return make_runtime(manifest).answer(QUESTION)


def test_hard_checks_without_eval_coverage_are_grounded(trace: Trace, manifest: Manifest) -> None:
    receipt = build_receipt(trace, manifest)

    assert receipt.trust == "grounded"
    assert receipt.trust_reasons[-1] == "No approved eval case covers this question."
    assert receipt.claims[0].evidence[0].column == "recognized_revenue"


def test_a_passing_approved_eval_makes_the_answer_verified(
    trace: Trace, manifest: Manifest
) -> None:
    covered = replace(
        trace,
        verification=(
            *trace.verification,
            Verification("correctness_eval:revenue-capability/july", True, "Approved eval matched"),
        ),
    )

    receipt = build_receipt(covered, manifest)

    assert receipt.trust == "verified"
    assert receipt.trust_reasons == [
        "Approved eval revenue-capability/july matched this question and passed."
    ]


def test_any_failed_check_makes_the_answer_unverified(trace: Trace, manifest: Manifest) -> None:
    failed = replace(
        trace,
        verification=(*trace.verification, Verification("claims_supported", False, "no cell")),
    )

    receipt = build_receipt(failed, manifest)

    assert receipt.trust == "unverified"
    assert receipt.trust_reasons == ["claims_supported failed: no cell"]


def test_resources_carry_descriptions_tests_lineage_and_exposures(
    trace: Trace, manifest: Manifest
) -> None:
    [resource] = build_receipt(trace, manifest).resources

    assert resource.unique_id == "model.analytics.fct_orders"
    assert {column.name for column in resource.columns} == {"order_date", "recognized_revenue"}
    assert resource.upstream == ["model.analytics.stg_orders", "source.analytics.raw.orders"]
    assert {test.name for test in resource.tests} >= {"unique", "not_null"}  # Grain tests too.
    [exposure] = resource.exposures
    assert (exposure.name, exposure.type) == ("Weekly Revenue", "dashboard")
    assert exposure.url == "https://app.powerbi.com/groups/finance/reports/weekly-revenue"


def test_source_freshness_comes_from_sources_json(tmp_path: Path, trace: Trace) -> None:
    target = tmp_path / "target"
    target.mkdir()
    shutil.copy(EXAMPLE / "target" / "manifest.json", target / "manifest.json")
    (target / "sources.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "unique_id": "source.analytics.raw.orders",
                        "status": "warn",
                        "max_loaded_at": "2026-10-30T06:00:00Z",
                        "snapshotted_at": "2026-10-31T06:00:00Z",
                    }
                ]
            }
        )
    )

    [resource] = build_receipt(trace, Manifest.load(target / "manifest.json")).resources

    [freshness] = resource.freshness
    assert (freshness.source, freshness.status) == ("source.analytics.raw.orders", "warn")
    assert freshness.max_loaded_at == "2026-10-30T06:00:00Z"


def test_identity_digests_use_one_form(trace: Trace, manifest: Manifest) -> None:
    receipt = build_receipt(trace, manifest)
    digests = [
        receipt.identity.agent_digest,
        receipt.identity.manifest_digest,
        receipt.identity.catalog_digest,
        receipt.replay.inference_fingerprint,
    ]

    assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", str(item)) for item in digests)


def test_run_id_is_stable_and_receipts_round_trip(
    tmp_path: Path, trace: Trace, manifest: Manifest
) -> None:
    receipt = build_receipt(trace, manifest, recorded=True)

    assert receipt.run_id == run_id_for(trace) == build_receipt(trace, manifest).run_id
    assert receipt.replay.command == f"tabletalk receipt replay {receipt.run_id}"
    write_receipt(tmp_path, receipt)
    assert load_receipt(tmp_path, receipt.run_id[:10]) == receipt


def test_published_schema_matches_the_model() -> None:
    published = json.loads((SPEC / "receipt-v1.schema.json").read_text())

    assert published == receipt_json_schema()


def test_published_example_is_a_valid_receipt() -> None:
    example = Receipt.model_validate_json(
        (SPEC / "examples" / "receipt-v1.verified.json").read_text()
    )

    assert example.trust == "verified"
    assert example.replay.recorded


def test_cli_shows_stored_receipts_and_prints_the_schema(
    tmp_path: Path, trace: Trace, manifest: Manifest
) -> None:
    receipt = build_receipt(trace, manifest)
    write_receipt(tmp_path / RECEIPTS_DIRECTORY, receipt)
    runner = CliRunner()
    folder = ["--project-folder", str(tmp_path)]

    shown = runner.invoke(cli, ["receipt", "show", receipt.run_id[:8], *folder])
    as_json = runner.invoke(cli, ["receipt", "show", receipt.run_id, "--format", "json", *folder])
    missing = runner.invoke(cli, ["receipt", "show", "run_ffff", *folder])
    schema = runner.invoke(cli, ["receipt", "schema"])

    assert shown.exit_code == 0, shown.output
    assert "GROUNDED" in shown.output and receipt.run_id in shown.output
    assert Receipt.model_validate_json(as_json.output) == receipt
    assert missing.exit_code == 4
    assert json.loads(schema.output) == receipt_json_schema()
