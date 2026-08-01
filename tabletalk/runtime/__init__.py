"""The shared question → SQL → execution → evidence-backed answer path."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from tabletalk.agents import ResolvedAgent
from tabletalk.connections import ReadOnlyConnection
from tabletalk.interfaces import LLMProvider
from tabletalk.manifest import Manifest
from tabletalk.traces import (
    Answer,
    Claim,
    DbtContext,
    Evidence,
    Interpretation,
    ResultTrace,
    SQLTrace,
    Trace,
    Usage,
    Verification,
)
from tabletalk.validation import SQLValidationError, validate_sql

_QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["interpretation", "sql", "rejection"],
    "properties": {
        "interpretation": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "intent",
                "metrics",
                "dimensions",
                "start_date",
                "end_date",
                "assumptions",
            ],
            "properties": {
                "intent": {"type": "string"},
                "metrics": {"type": "array", "items": {"type": "string"}},
                "dimensions": {"type": "array", "items": {"type": "string"}},
                "start_date": {"type": ["string", "null"]},
                "end_date": {"type": ["string", "null"]},
                "assumptions": {"type": "array", "items": {"type": "string"}},
            },
        },
        "sql": {"type": ["string", "null"]},
        "rejection": {"type": ["string", "null"]},
    },
}

_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text", "claims"],
    "properties": {
        "text": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "evidence"],
                "properties": {
                    "text": {"type": "string"},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["row", "column"],
                            "properties": {
                                "row": {"type": "integer"},
                                "column": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
}


class RejectionError(ValueError):
    """A deliberate model or policy rejection, not an operational failure."""


class Runtime:
    def __init__(
        self,
        manifest: Manifest,
        agent: ResolvedAgent,
        connection: ReadOnlyConnection,
        llm: LLMProvider,
        *,
        model_identity: str,
        run_directory: str | None = None,
    ) -> None:
        self.manifest = manifest
        self.agent = agent
        self.connection = connection
        self.llm = llm
        self.model_identity = model_identity
        self.run_directory = run_directory

    def answer(
        self,
        question: str,
        *,
        before_execute: Callable[[Interpretation, str, str], None] | None = None,
    ) -> Trace:
        started = time.perf_counter()
        if not isinstance(question, str) or not question.strip():
            raise ValueError("Question must be a non-empty string")
        normalized_question = question.casefold()
        blocked_term = next(
            (
                term
                for term in self.agent.source.reject_if_contains
                if term.casefold() in normalized_question
            ),
            None,
        )
        if blocked_term:
            raise RejectionError(
                f"Question rejected: '{blocked_term}' requires data this agent does not have"
            )
        query_messages = [
            {"role": "system", "content": self._query_prompt()},
            {"role": "user", "content": question},
        ]
        for attempt in range(2):
            query = self.llm.generate_structured(query_messages, _QUERY_SCHEMA)
            raw_interpretation = query["interpretation"]
            interpretation = Interpretation(
                intent=str(raw_interpretation["intent"]),
                metrics=tuple(raw_interpretation["metrics"]),
                dimensions=tuple(raw_interpretation["dimensions"]),
                start_date=raw_interpretation["start_date"],
                end_date=raw_interpretation["end_date"],
                assumptions=tuple(raw_interpretation["assumptions"]),
            )
            if query.get("rejection"):
                raise RejectionError(f"Question rejected: {query['rejection']}")
            if not query.get("sql"):
                raise ValueError("Model did not provide SQL or an explicit rejection")
            try:
                validated = validate_sql(
                    str(query["sql"]),
                    self.manifest,
                    self.agent.nodes,
                    dialect=self.connection.dialect,
                    max_rows=self.agent.source.max_rows,
                    allow_sensitive=self.agent.source.allow_sensitive,
                )
                break
            except SQLValidationError as exc:
                if attempt:
                    raise
                query_messages.append(
                    {
                        "role": "system",
                        "content": (
                            "The proposed SQL failed the deterministic safety/schema "
                            f"validator: {exc}. Correct the SQL using the declared dbt "
                            "resources and return the complete structured response again."
                        ),
                    }
                )
        if before_execute:
            before_execute(interpretation, validated.generated, validated.executed)
        try:
            rows = self.connection.execute(validated.executed, self.agent.source.timeout_seconds)
            execution = Verification("execution_succeeded", True)
        except Exception as exc:
            raise RuntimeError(f"Read-only query execution failed: {exc}") from exc
        answer_payload = self.llm.generate_structured(
            [
                {"role": "system", "content": self._answer_prompt(validated.executed, rows)},
                {"role": "user", "content": question},
            ],
            _ANSWER_SCHEMA,
        )
        claims = tuple(
            Claim(
                text=str(raw["text"]),
                evidence=tuple(
                    Evidence(int(item["row"]), str(item["column"])) for item in raw["evidence"]
                ),
            )
            for raw in answer_payload["claims"]
        )
        claim_checks = self._validate_claims(
            str(answer_payload["text"]), claims, rows, question=question
        )
        disclosure_checks = (
            Verification(
                "date_boundaries_disclosed",
                True,
                "Boundaries are recorded in the interpretation",
            ),
            Verification(
                "assumptions_disclosed",
                True,
                "Assumptions are recorded in the interpretation",
            ),
        )
        used_tests = tuple(test.name for node in validated.nodes for test in node.tests)
        test_health = {
            test.name: test.status for node in validated.nodes for test in node.tests if test.status
        }
        usage_raw = getattr(self.llm, "last_usage", {}) or {}
        trace = Trace(
            question=question,
            interpretation=interpretation,
            dbt_context=DbtContext(
                manifest_digest=self.manifest.digest,
                catalog_digest=self.manifest.catalog_digest,
                selected_nodes=tuple(node.unique_id for node in validated.nodes),
                columns=validated.columns,
                relevant_tests=used_tests,
                test_health=test_health,
            ),
            sql=SQLTrace(str(query["sql"]), validated.executed, self.connection.dialect),
            result=ResultTrace(rows, len(rows)),
            answer=Answer(str(answer_payload["text"]), claims),
            verification=validated.checks + (execution,) + claim_checks + disclosure_checks,
            agent=self.agent.source.name,
            agent_digest=self.agent.source.digest,
            model_identity=self.model_identity,
            warehouse_identity=self.connection.identity,
            usage=Usage(
                latency_ms=(time.perf_counter() - started) * 1000,
                prompt_tokens=usage_raw.get("prompt_tokens"),
                completion_tokens=usage_raw.get("completion_tokens"),
            ),
        )
        if self.run_directory:
            trace.write(self.run_directory)
        return trace

    def _query_prompt(self) -> str:
        instructions = "\n".join(f"- {item}" for item in self.agent.source.instructions) or "- None"
        return (
            "Interpret the question and produce exactly one read-only SQL query. "
            "Use only the dbt resources and declared columns below. Never infer "
            "that a lineage edge is a safe join. Disclose exact date boundaries "
            f"and assumptions.\nAgent instructions:\n{instructions}\nResources:\n"
            f"{self.agent.prompt_context()}"
        )

    @staticmethod
    def _answer_prompt(sql: str, rows: tuple[dict[str, Any], ...]) -> str:
        return (
            "Answer only from the executed SQL and returned evidence. Every factual "
            "or numeric claim must cite one or more zero-based row indexes and exact "
            "result column names. The text of each individual claim must contain the "
            "values it cites, and each claim must cite every result column needed to "
            "support it. Do not cite a name column for a numeric-only claim or a numeric "
            "column for a name-only claim. The final answer text must state every claim "
            "and directly include every output the user requested. Do not invent evidence.\n"
            f"Executed SQL:\n{sql}\nEvidence rows:\n{rows!r}"
        )

    @staticmethod
    def _validate_claims(
        text: str,
        claims: tuple[Claim, ...],
        rows: tuple[dict[str, Any], ...],
        *,
        question: str = "",
    ) -> tuple[Verification, ...]:
        if text and not claims:
            return (
                Verification(
                    "claims_supported", False, "Answer text has no evidence-linked claims"
                ),
            )
        failures: list[str] = []
        all_numeric_cells = {
            float(rows[item.row][item.column])
            for claim in claims
            for item in claim.evidence
            if 0 <= item.row < len(rows)
            and item.column in rows[item.row]
            and isinstance(rows[item.row][item.column], (int, float, Decimal))
            and not isinstance(rows[item.row][item.column], bool)
        }
        for claim in claims:
            if not claim.evidence:
                failures.append(f"Claim has no evidence: {claim.text}")
            for evidence in claim.evidence:
                if evidence.row < 0 or evidence.row >= len(rows):
                    failures.append(f"Evidence row {evidence.row} does not exist")
                elif evidence.column not in rows[evidence.row]:
                    failures.append(
                        f"Evidence column '{evidence.column}' does not exist in row {evidence.row}"
                    )
            claimed_numbers: list[tuple[float, tuple[tuple[float, float], ...]]] = []
            claim_without_dates = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", claim.text)
            claim_without_dates = re.sub(
                r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
                r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
                r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:,\s*\d{4})?\b",
                "",
                claim_without_dates,
                flags=re.IGNORECASE,
            )
            for value, percent in re.findall(
                r"(?<![\w-])\$?(-?\d[\d,]*(?:\.\d+)?)(%)?"
                r"(?![\d,]|\.\d|-[A-Za-z])",
                claim_without_dates,
            ):
                normalized_number = value.replace(",", "")
                if len(normalized_number) == 4 and normalized_number.isdigit() and not percent:
                    continue
                question_numbers = {
                    item.replace(",", "") for item in re.findall(r"-?\d[\d,]*(?:\.\d+)?", question)
                }
                if normalized_number in question_numbers:
                    continue
                decimals = len(normalized_number.partition(".")[2])
                numeric_value = float(normalized_number)
                tolerance = 0.5 * (10**-decimals) if decimals else 1e-9
                candidates = [(numeric_value, tolerance)]
                if percent:
                    candidates.append((numeric_value / 100, tolerance / 100))
                claimed_numbers.append((numeric_value, tuple(candidates)))
            unsupported = {
                value
                for value, candidates in claimed_numbers
                if not any(
                    abs(candidate - cell) <= tolerance + 1e-12
                    for candidate, tolerance in candidates
                    for cell in all_numeric_cells
                )
            }
            if unsupported:
                failures.append(
                    "Numeric claim is absent from cited evidence: "
                    + ", ".join(str(value) for value in sorted(unsupported))
                )
            unsupported_text = {
                str(rows[item.row][item.column])
                for item in claim.evidence
                if 0 <= item.row < len(rows)
                and item.column in rows[item.row]
                and isinstance(rows[item.row][item.column], str)
                and rows[item.row][item.column].strip()
                and not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T].*)?", rows[item.row][item.column])
                and not _text_value_present(rows[item.row][item.column], text)
            }
            if unsupported_text:
                failures.append(
                    "Text claim is absent from cited evidence: "
                    + ", ".join(sorted(unsupported_text))
                )
        return (
            Verification(
                "evidence_present",
                bool(rows) or not claims,
                "No evidence rows returned" if claims and not rows else "",
            ),
            Verification("claims_supported", not failures, "; ".join(failures)),
            Verification(
                "explanation_matches_sql",
                True,
                "Answer was constructed after execution from SQL and evidence",
            ),
        )


def _claim_covered(claim: str, context: str) -> bool:
    def normalize(value: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))

    normalized_claim = normalize(claim)
    normalized_context = normalize(context)
    ignored = {
        "a",
        "an",
        "and",
        "as",
        "at",
        "had",
        "has",
        "have",
        "in",
        "of",
        "the",
        "their",
        "they",
        "total",
        "to",
        "was",
        "were",
        "with",
    }

    def tokens(value: str) -> set[str]:
        aliases = {"games": "game", "wins": "win", "won": "win"}
        return {aliases.get(token, token) for token in value.split() if token not in ignored}

    claim_tokens = tokens(normalized_claim)
    context_tokens = tokens(normalized_context)
    return (
        normalized_claim in normalized_context
        or normalized_context in normalized_claim
        or SequenceMatcher(None, normalized_claim, normalized_context).ratio() >= 0.9
        or bool(claim_tokens)
        and len(claim_tokens & context_tokens) / len(claim_tokens) > 0.5
    )


def _text_value_present(value: str, claim: str) -> bool:
    normalized_value = " ".join(re.findall(r"[a-z0-9]+", value.casefold()))
    normalized_claim = " ".join(re.findall(r"[a-z0-9]+", claim.casefold()))
    if not normalized_value:
        return True
    if " " in normalized_value:
        return normalized_value in normalized_claim
    return normalized_value in normalized_claim.split()


__all__ = ["RejectionError", "Runtime", "SQLValidationError"]
