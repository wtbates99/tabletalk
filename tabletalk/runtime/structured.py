"""Structured reliability runtime with evidence-linked answer construction."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

from tabletalk.domain import (
    AnswerReceipt,
    CalculationStep,
    ClaimEvidence,
    ErrorCode,
    EvidenceItem,
    Interpretation,
    PlanOperation,
    QueryAnswer,
    RepairAttempt,
    RuntimeIdentity,
    RuntimeStage,
    SemanticPlan,
    SourceReference,
    TableTalkError,
    VerificationCheck,
    VerificationStatus,
    canonical_json,
    model_request_error,
)
from tabletalk.interfaces import LLMProvider
from tabletalk.runtime.sql_validation import SQLScope, ValidatedSQL, validate_sql

_QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["interpretation", "plan", "sql", "ambiguity"],
    "properties": {
        "interpretation": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "intent",
                "metrics",
                "dimensions",
                "filters",
                "start_date",
                "end_date",
                "timezone",
                "assumptions",
            ],
            "properties": {
                "intent": {"type": "string"},
                "metrics": {"type": "array", "items": {"type": "string"}},
                "dimensions": {"type": "array", "items": {"type": "string"}},
                "filters": {"type": "array", "items": {"type": "string"}},
                "start_date": {"type": ["string", "null"]},
                "end_date": {"type": ["string", "null"]},
                "timezone": {"type": ["string", "null"]},
                "assumptions": {"type": "array", "items": {"type": "string"}},
            },
        },
        "plan": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["operation", "relation", "detail"],
                "properties": {
                    "operation": {"type": "string"},
                    "relation": {"type": "string"},
                    "detail": {"type": "string"},
                },
            },
        },
        "sql": {"type": ["string", "null"]},
        "ambiguity": {"type": ["string", "null"]},
    },
}

_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["direct_answer", "calculations", "claims"],
    "properties": {
        "direct_answer": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["claim", "evidence_ids", "calculation_ids"],
                "properties": {
                    "claim": {"type": "string"},
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "calculation_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
        "calculations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["calculation_id", "label", "operation", "inputs"],
                "properties": {
                    "calculation_id": {"type": "string"},
                    "label": {"type": "string"},
                    "operation": {
                        "type": "string",
                        "enum": ["difference", "percent_change", "ratio", "sum"],
                    },
                    "inputs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["evidence_id", "column"],
                            "properties": {
                                "evidence_id": {"type": "string"},
                                "column": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
}

_REPAIR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sql"],
    "properties": {"sql": {"type": "string"}},
}

_REPAIRABLE_DATABASE_ERROR_MARKERS = (
    "ambiguous column",
    "binder error",
    "compilation error",
    "does not exist",
    "group by",
    "invalid identifier",
    "no such column",
    "no such function",
    "no such table",
    "parser error",
    "syntax error",
    "unknown column",
)


def _strings(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TableTalkError(
            ErrorCode.MODEL_OUTPUT_MALFORMED,
            RuntimeStage.GENERATION,
            f"Structured output field '{field}' must be a string array.",
        )
    return tuple(value)


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TableTalkError(
            ErrorCode.MODEL_OUTPUT_MALFORMED,
            RuntimeStage.GENERATION,
            f"Structured output field '{field}' must be a non-empty string.",
        )
    return value


def _nullable_string(value: Any, field: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise TableTalkError(
            ErrorCode.MODEL_OUTPUT_MALFORMED,
            RuntimeStage.GENERATION,
            f"Structured output field '{field}' must be a string or null.",
        )
    return value


def _safe_database_error(error: Exception) -> str:
    """Retain useful SQL diagnostics while redacting credential-shaped values."""
    message = str(error).strip()[:1000] or type(error).__name__
    return re.sub(
        r"(?i)(api[_-]?key|authorization|credential|password|secret|token)"
        r"(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        message,
    )


def _policy_value(artifact: dict[str, Any], name: str, default: Any) -> Any:
    policies = artifact.get("agent", {}).get("policies", {})
    if isinstance(policies, dict):
        return policies.get(name, default)
    if isinstance(policies, list):
        for value in policies:
            if isinstance(value, list) and len(value) == 2 and value[0] == name:
                return value[1]
    return default


def _agent_mapping(artifact: dict[str, Any], name: str) -> dict[str, Any]:
    value = artifact.get("agent", {}).get(name, {})
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {
            str(item[0]): item[1]
            for item in value
            if isinstance(item, list) and len(item) == 2
        }
    return {}


def _parse_boundary(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise TableTalkError(
            ErrorCode.SEMANTIC_INVALID,
            RuntimeStage.INTERPRETATION,
            f"Interpretation {field} must be an ISO date.",
            details={"field": field},
        ) from error


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return None


def _calculate(operation: str, values: list[float]) -> tuple[str, float]:
    if operation == "difference" and len(values) == 2:
        return "input_1 - input_2", values[0] - values[1]
    if operation == "percent_change" and len(values) == 2:
        if values[1] == 0:
            raise ValueError("percent_change denominator cannot be zero")
        return "(input_1 - input_2) / input_2", (
            values[0] - values[1]
        ) / values[1]
    if operation == "ratio" and len(values) == 2:
        if values[1] == 0:
            raise ValueError("ratio denominator cannot be zero")
        return "input_1 / input_2", values[0] / values[1]
    if operation == "sum" and values:
        return "sum(inputs)", sum(values)
    raise ValueError(f"invalid inputs for {operation}")


def _claim_numbers_supported(
    claim: str,
    supported_values: tuple[float, ...],
    interpretation: Interpretation,
) -> bool:
    date_numbers: set[float] = set()
    for boundary in (interpretation.start_date, interpretation.end_date):
        if boundary:
            date_numbers.update(float(value) for value in boundary.split("-"))
    available = (*supported_values, *date_numbers)
    for match in re.finditer(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?%?", claim):
        raw = match.group(0)
        percent = raw.endswith("%")
        number = float(raw.rstrip("%").replace(",", ""))
        if not any(
            math.isclose(
                number,
                candidate * 100 if percent else candidate,
                rel_tol=1e-6,
                abs_tol=1e-6,
            )
            or math.isclose(
                number,
                candidate,
                rel_tol=1e-6,
                abs_tol=1e-6,
            )
            for candidate in available
        ):
            return False
    return True


class StructuredQueryRuntime:
    def __init__(
        self,
        *,
        model: LLMProvider,
        execute: Callable[[str], list[dict[str, Any]]],
        artifact: dict[str, Any],
        runtime_identity: RuntimeIdentity,
        database_type: str,
        database_identity: str,
        dialect: str | None = None,
        eval_receipt_digest: str | None = None,
        max_repair_attempts: int | None = None,
    ) -> None:
        self.model = model
        self.execute = execute
        self.artifact = artifact
        self.runtime_identity = runtime_identity
        self.database_type = database_type
        self.database_identity = database_identity
        self.dialect = dialect
        self.eval_receipt_digest = eval_receipt_digest
        self.scope = SQLScope.from_artifact(artifact)
        configured_repairs = (
            _policy_value(artifact, "max_repair_attempts", 1)
            if max_repair_attempts is None
            else max_repair_attempts
        )
        if (
            not isinstance(configured_repairs, int)
            or isinstance(configured_repairs, bool)
            or not 0 <= configured_repairs <= 2
        ):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "max_repair_attempts must be an integer between 0 and 2.",
            )
        self.max_repair_attempts = configured_repairs
        self.max_rows = _policy_value(artifact, "max_rows", 500)
        if (
            not isinstance(self.max_rows, int)
            or isinstance(self.max_rows, bool)
            or not 1 <= self.max_rows <= 10_000
        ):
            raise TableTalkError(
                ErrorCode.CONFIG_INVALID,
                RuntimeStage.CONFIGURATION,
                "max_rows must be an integer between 1 and 10000.",
            )

    def _validate_interpretation(
        self,
        interpretation: Interpretation,
        plan: SemanticPlan,
        sql: str | None,
    ) -> None:
        agent = self.artifact.get("agent", {})
        metrics = agent.get("metrics", []) if isinstance(agent, dict) else []
        known_metrics = {
            str(metric.get("name"))
            for metric in metrics
            if isinstance(metric, dict) and metric.get("name")
        }
        unknown_metrics = sorted(set(interpretation.metrics) - known_metrics)
        if unknown_metrics:
            raise TableTalkError(
                ErrorCode.SEMANTIC_INVALID,
                RuntimeStage.INTERPRETATION,
                "Interpretation selected metrics that are not declared by the applied Agent.",
                details={"unknown_metrics": unknown_metrics},
            )

        allowed_relations = {
            relation.lower(): relation for relation in self.scope.relations
        }
        allowed_short_names = {
            relation.rsplit(".", 1)[-1].lower(): relation
            for relation in self.scope.relations
        }
        unknown_relations = sorted(
            {
                operation.relation
                for operation in plan.operations
                if operation.relation.lower() not in allowed_relations
                and operation.relation.lower() not in allowed_short_names
            }
        )
        if unknown_relations:
            raise TableTalkError(
                ErrorCode.SEMANTIC_INVALID,
                RuntimeStage.PLANNING,
                "Semantic plan references relations outside the applied Agent scope.",
                details={"relations": unknown_relations},
            )

        if bool(interpretation.start_date) != bool(interpretation.end_date):
            raise TableTalkError(
                ErrorCode.SEMANTIC_INVALID,
                RuntimeStage.INTERPRETATION,
                "Interpretation must provide both start_date and end_date or neither.",
            )
        if interpretation.start_date and interpretation.end_date:
            start = _parse_boundary(interpretation.start_date, "start_date")
            end = _parse_boundary(interpretation.end_date, "end_date")
            if start >= end:
                raise TableTalkError(
                    ErrorCode.SEMANTIC_INVALID,
                    RuntimeStage.INTERPRETATION,
                    "Interpretation end_date must be after start_date.",
                )

        configured_timezone = _agent_mapping(
            self.artifact, "time_semantics"
        ).get("timezone")
        if (
            configured_timezone
            and interpretation.timezone != configured_timezone
        ):
            raise TableTalkError(
                ErrorCode.SEMANTIC_INVALID,
                RuntimeStage.INTERPRETATION,
                "Interpretation timezone differs from the applied Agent.",
                details={"expected_timezone": configured_timezone},
            )

        if interpretation.ambiguity and sql:
            raise TableTalkError(
                ErrorCode.MODEL_OUTPUT_MALFORMED,
                RuntimeStage.INTERPRETATION,
                "A materially ambiguous interpretation must not include executable SQL.",
            )

    def _model_call(
        self, messages: list[dict[str, str]], schema: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            return self.model.generate_structured(messages, schema)
        except Exception as error:
            raise model_request_error(
                error,
                provider=self.runtime_identity.provider,
                model=self.runtime_identity.model,
            ) from error

    def _interpret(
        self, question: str
    ) -> tuple[Interpretation, SemanticPlan, str | None]:
        payload = self._model_call(
            [
                {
                    "role": "system",
                    "content": (
                        "Interpret the question against the compiled agent, create a "
                        "semantic plan, and generate one read-only SQL query. If material "
                        "ambiguity remains, set ambiguity and sql to null. Return only the "
                        "required structured object."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Compiled agent:\n{canonical_json(self.artifact)}\n\n"
                        f"Question:\n{question}"
                    ),
                },
            ],
            _QUERY_SCHEMA,
        )
        raw_interpretation = payload.get("interpretation")
        raw_plan = payload.get("plan")
        if not isinstance(raw_interpretation, dict) or not isinstance(raw_plan, list):
            raise TableTalkError(
                ErrorCode.MODEL_OUTPUT_MALFORMED,
                RuntimeStage.INTERPRETATION,
                "Structured query output omitted interpretation or plan.",
            )
        ambiguity = payload.get("ambiguity")
        sql = payload.get("sql")
        if ambiguity is not None and not isinstance(ambiguity, str):
            raise TableTalkError(
                ErrorCode.MODEL_OUTPUT_MALFORMED,
                RuntimeStage.INTERPRETATION,
                "Structured ambiguity must be a string or null.",
            )
        if sql is not None and not isinstance(sql, str):
            raise TableTalkError(
                ErrorCode.MODEL_OUTPUT_MALFORMED,
                RuntimeStage.GENERATION,
                "Structured SQL must be a string or null.",
            )
        interpretation = Interpretation(
            question=question,
            intent=_string(raw_interpretation.get("intent"), "intent"),
            metrics=_strings(raw_interpretation.get("metrics"), "metrics"),
            dimensions=_strings(raw_interpretation.get("dimensions"), "dimensions"),
            filters=_strings(raw_interpretation.get("filters"), "filters"),
            start_date=_nullable_string(
                raw_interpretation.get("start_date"), "start_date"
            ),
            end_date=_nullable_string(raw_interpretation.get("end_date"), "end_date"),
            timezone=_nullable_string(raw_interpretation.get("timezone"), "timezone"),
            assumptions=_strings(raw_interpretation.get("assumptions"), "assumptions"),
            ambiguity=ambiguity,
        )
        operations = []
        for value in raw_plan:
            if not isinstance(value, dict):
                raise TableTalkError(
                    ErrorCode.MODEL_OUTPUT_MALFORMED,
                    RuntimeStage.PLANNING,
                    "Every semantic plan operation must be an object.",
                )
            operations.append(
                PlanOperation(
                    operation=_string(value.get("operation"), "operation"),
                    relation=_string(value.get("relation"), "relation"),
                    detail=_string(value.get("detail"), "detail"),
                )
            )
        plan = SemanticPlan(tuple(operations))
        self._validate_interpretation(interpretation, plan, sql)
        return interpretation, plan, sql

    def _repair_sql(
        self,
        *,
        question: str,
        interpretation: Interpretation,
        plan: SemanticPlan,
        failed_sql: str,
        error_code: str,
        error_message: str,
    ) -> str:
        payload = self._model_call(
            [
                {
                    "role": "system",
                    "content": (
                        "Repair exactly one read-only SQL statement. Preserve the supplied "
                        "interpretation, metric, dates, filters, comparison, plan, and "
                        "business rules without semantic changes. Return only the required "
                        "structured object."
                    ),
                },
                {
                    "role": "user",
                    "content": canonical_json(
                        {
                            "question": question,
                            "interpretation": interpretation,
                            "plan": plan,
                            "failed_sql": failed_sql,
                            "error_code": error_code,
                            "error_message": error_message,
                            "compiled_agent": self.artifact,
                        }
                    ),
                },
            ],
            _REPAIR_SCHEMA,
        )
        return _string(payload.get("sql"), "sql")

    @staticmethod
    def _repairable_database_error(error: Exception) -> bool:
        if isinstance(error, TableTalkError):
            return False
        message = str(error).lower()
        return any(marker in message for marker in _REPAIRABLE_DATABASE_ERROR_MARKERS)

    def _validate_execute_with_repair(
        self,
        *,
        question: str,
        interpretation: Interpretation,
        plan: SemanticPlan,
        sql: str,
    ) -> tuple[ValidatedSQL, list[dict[str, Any]], tuple[RepairAttempt, ...]]:
        current_sql = sql
        repairs: list[RepairAttempt] = []
        while True:
            try:
                validated = validate_sql(
                    current_sql,
                    dialect=self.dialect,
                    scope=self.scope,
                    max_rows=self.max_rows,
                )
            except TableTalkError as error:
                if (
                    error.code is not ErrorCode.SQL_INVALID
                    or len(repairs) >= self.max_repair_attempts
                ):
                    raise
                repaired_sql = self._repair_sql(
                    question=question,
                    interpretation=interpretation,
                    plan=plan,
                    failed_sql=current_sql,
                    error_code=error.code.value,
                    error_message=error.message,
                )
                repairs.append(
                    RepairAttempt(
                        attempt=len(repairs) + 1,
                        failed_sql=current_sql,
                        error_code=error.code.value,
                        error_message=error.message,
                        repaired_sql=repaired_sql,
                    )
                )
                current_sql = repaired_sql
                continue

            try:
                rows = self.execute(validated.sql)
            except Exception as error:
                if (
                    not self._repairable_database_error(error)
                    or len(repairs) >= self.max_repair_attempts
                ):
                    if isinstance(error, TableTalkError):
                        raise
                    raise TableTalkError(
                        ErrorCode.DATABASE_QUERY_FAILED,
                        RuntimeStage.EXECUTION,
                        "Database execution failed; no answer was generated.",
                        details={
                            "database_type": self.database_type,
                            "repair_attempts": len(repairs),
                        },
                    ) from error
                error_message = _safe_database_error(error)
                repaired_sql = self._repair_sql(
                    question=question,
                    interpretation=interpretation,
                    plan=plan,
                    failed_sql=validated.sql,
                    error_code=ErrorCode.DATABASE_QUERY_FAILED.value,
                    error_message=error_message,
                )
                repairs.append(
                    RepairAttempt(
                        attempt=len(repairs) + 1,
                        failed_sql=validated.sql,
                        error_code=ErrorCode.DATABASE_QUERY_FAILED.value,
                        error_message=error_message,
                        repaired_sql=repaired_sql,
                    )
                )
                current_sql = repaired_sql
                continue
            return validated, rows, tuple(repairs)

    def invoke(self, question: str) -> QueryAnswer:
        interpretation, plan, sql = self._interpret(question)
        if interpretation.ambiguity:
            return QueryAnswer(
                status=VerificationStatus.AMBIGUOUS,
                direct_answer=interpretation.ambiguity,
                interpretation=interpretation,
                plan=plan,
                sql=None,
                verification=(
                    VerificationCheck("model_request_succeeded", True),
                    VerificationCheck("structured_output_valid", True),
                    VerificationCheck(
                        "material_ambiguity_resolved",
                        False,
                        interpretation.ambiguity,
                    ),
                ),
            )
        if not sql:
            raise TableTalkError(
                ErrorCode.MODEL_OUTPUT_MALFORMED,
                RuntimeStage.GENERATION,
                "Unambiguous structured output requires SQL.",
            )
        validated, rows, repairs = self._validate_execute_with_repair(
            question=question,
            interpretation=interpretation,
            plan=plan,
            sql=sql,
        )

        sources = tuple(
            SourceReference(
                relation=relation,
                columns=validated.columns,
                row_indices=tuple(range(len(rows))),
            )
            for relation in validated.relations
        )
        evidence = tuple(
            EvidenceItem(
                evidence_id=f"row-{index}",
                source=SourceReference(
                    "query_result",
                    tuple(row.keys()),
                    (index,),
                ),
                values=(row,),
            )
            for index, row in enumerate(rows)
        )
        receipt = AnswerReceipt(
            artifact_digest=str(self.artifact.get("digest") or ""),
            eval_receipt_digest=self.eval_receipt_digest,
            runtime=self.runtime_identity,
            database_type=self.database_type,
            database_identity=self.database_identity,
        )
        if not evidence:
            return QueryAnswer(
                status=VerificationStatus.INSUFFICIENT_EVIDENCE,
                direct_answer=None,
                interpretation=interpretation,
                plan=plan,
                sql=validated.sql,
                sources=sources,
                evidence=(),
                claims=(),
                verification=(
                    VerificationCheck("model_request_succeeded", True),
                    VerificationCheck("structured_output_valid", True),
                    VerificationCheck("sql_parsed", True),
                    VerificationCheck("sql_read_only", True),
                    VerificationCheck("relations_in_scope", True),
                    VerificationCheck("approved_join_paths", True),
                    VerificationCheck("database_execution_succeeded", True),
                    VerificationCheck(
                        "claims_supported",
                        False,
                        "The query returned no evidence rows.",
                    ),
                ),
                data=tuple(rows),
                receipt=receipt,
                repairs=repairs,
            )
        answer_payload = self._model_call(
            [
                {
                    "role": "system",
                    "content": (
                        "Answer only from the executed evidence. Every material claim "
                        "must cite one or more supplied evidence_ids. Do not add general "
                        "knowledge or unsupported claims."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": question,
                            "interpretation": interpretation.__dict__,
                            "sql": validated.sql,
                            "evidence": [
                                {
                                    "evidence_id": item.evidence_id,
                                    "values": item.values,
                                }
                                for item in evidence
                            ],
                        },
                        default=str,
                        sort_keys=True,
                    ),
                },
            ],
            _ANSWER_SCHEMA,
        )
        direct_answer = answer_payload.get("direct_answer")
        raw_claims = answer_payload.get("claims")
        raw_calculations = answer_payload.get("calculations")
        if (
            not isinstance(direct_answer, str)
            or not isinstance(raw_claims, list)
            or not isinstance(raw_calculations, list)
        ):
            raise TableTalkError(
                ErrorCode.MODEL_OUTPUT_MALFORMED,
                RuntimeStage.VERIFICATION,
                "Structured answer omitted direct_answer, calculations, or claims.",
            )
        evidence_ids = {item.evidence_id for item in evidence}
        evidence_by_id = {item.evidence_id: item for item in evidence}
        calculations = []
        calculation_ids: set[str] = set()
        for raw_calculation in raw_calculations:
            if not isinstance(raw_calculation, dict):
                raise TableTalkError(
                    ErrorCode.MODEL_OUTPUT_MALFORMED,
                    RuntimeStage.VERIFICATION,
                    "Every calculation must be an object.",
                )
            calculation_id = _string(
                raw_calculation.get("calculation_id"),
                "calculation_id",
            )
            if calculation_id in calculation_ids:
                raise TableTalkError(
                    ErrorCode.MODEL_OUTPUT_MALFORMED,
                    RuntimeStage.VERIFICATION,
                    f"Calculation id '{calculation_id}' is duplicated.",
                )
            raw_inputs = raw_calculation.get("inputs")
            if not isinstance(raw_inputs, list):
                raise TableTalkError(
                    ErrorCode.MODEL_OUTPUT_MALFORMED,
                    RuntimeStage.VERIFICATION,
                    "Calculation inputs must be an array.",
                )
            inputs = []
            numeric_values = []
            for raw_input in raw_inputs:
                if not isinstance(raw_input, dict):
                    raise TableTalkError(
                        ErrorCode.MODEL_OUTPUT_MALFORMED,
                        RuntimeStage.VERIFICATION,
                        "Every calculation input must be an object.",
                    )
                evidence_id = _string(
                    raw_input.get("evidence_id"),
                    "evidence_id",
                )
                column = _string(raw_input.get("column"), "column")
                item = evidence_by_id.get(evidence_id)
                if item is None or not item.values or column not in item.values[0]:
                    raise TableTalkError(
                        ErrorCode.EVIDENCE_INSUFFICIENT,
                        RuntimeStage.VERIFICATION,
                        "Calculation references missing evidence.",
                        details={
                            "calculation_id": calculation_id,
                            "evidence_id": evidence_id,
                            "column": column,
                        },
                    )
                value = item.values[0][column]
                numeric_value = _numeric(value)
                if numeric_value is None:
                    raise TableTalkError(
                        ErrorCode.EVIDENCE_INSUFFICIENT,
                        RuntimeStage.VERIFICATION,
                        "Calculation input is not numeric.",
                        details={
                            "calculation_id": calculation_id,
                            "evidence_id": evidence_id,
                            "column": column,
                        },
                    )
                inputs.append((f"{evidence_id}.{column}", value))
                numeric_values.append(numeric_value)
            operation = _string(raw_calculation.get("operation"), "operation")
            try:
                formula, result = _calculate(operation, numeric_values)
            except ValueError as error:
                raise TableTalkError(
                    ErrorCode.EVIDENCE_INSUFFICIENT,
                    RuntimeStage.VERIFICATION,
                    "Calculation could not be reproduced from evidence.",
                    details={
                        "calculation_id": calculation_id,
                        "operation": operation,
                    },
                ) from error
            calculations.append(
                CalculationStep(
                    calculation_id=calculation_id,
                    label=_string(raw_calculation.get("label"), "label"),
                    formula=formula,
                    inputs=tuple(inputs),
                    result=result,
                )
            )
            calculation_ids.add(calculation_id)
        claims = []
        for raw_claim in raw_claims:
            if not isinstance(raw_claim, dict):
                raise TableTalkError(
                    ErrorCode.MODEL_OUTPUT_MALFORMED,
                    RuntimeStage.VERIFICATION,
                    "Every answer claim must be an object.",
                )
            claim_ids = _strings(raw_claim.get("evidence_ids"), "evidence_ids")
            claim_calculation_ids = _strings(
                raw_claim.get("calculation_ids"),
                "calculation_ids",
            )
            referenced_evidence = set(claim_ids)
            referenced_calculations = set(claim_calculation_ids)
            values = tuple(
                numeric
                for evidence_id in referenced_evidence
                for item in (evidence_by_id.get(evidence_id),)
                if item is not None
                for row in item.values
                for value in row.values()
                for numeric in (_numeric(value),)
                if numeric is not None
            ) + tuple(
                float(calculation.result)
                for calculation in calculations
                if calculation.calculation_id in referenced_calculations
                and _numeric(calculation.result) is not None
            )
            claim_text = _string(raw_claim.get("claim"), "claim")
            references_valid = (
                bool(referenced_evidence or referenced_calculations)
                and referenced_evidence.issubset(evidence_ids)
                and referenced_calculations.issubset(calculation_ids)
            )
            supported = references_valid and _claim_numbers_supported(
                claim_text,
                values,
                interpretation,
            )
            claims.append(
                ClaimEvidence(
                    claim=claim_text,
                    evidence_ids=claim_ids,
                    supported=supported,
                    calculation_ids=claim_calculation_ids,
                    reason=(
                        None
                        if supported
                        else "Claim cites missing evidence or unsupported numeric values."
                    ),
                )
            )
        all_claims_supported = bool(claims) and all(
            claim.supported for claim in claims
        )
        if all_claims_supported and repairs:
            status = VerificationStatus.VERIFIED_WITH_WARNINGS
        elif all_claims_supported:
            status = VerificationStatus.VERIFIED
        else:
            status = VerificationStatus.INSUFFICIENT_EVIDENCE
        checks = (
            VerificationCheck("model_request_succeeded", True),
            VerificationCheck("structured_output_valid", True),
            VerificationCheck("sql_parsed", True),
            VerificationCheck("sql_read_only", True),
            VerificationCheck("relations_in_scope", True),
            VerificationCheck("approved_join_paths", True),
            VerificationCheck("database_execution_succeeded", True),
            VerificationCheck(
                "query_repaired",
                not repairs,
                (
                    f"{len(repairs)} controlled SQL repair attempt(s) occurred."
                    if repairs
                    else None
                ),
            ),
            VerificationCheck(
                "claims_supported",
                all_claims_supported,
                None
                if all_claims_supported
                else "One or more claims lacked valid evidence references.",
            ),
        )
        return QueryAnswer(
            status=status,
            direct_answer=(
                " ".join(claim.claim for claim in claims)
                if status is VerificationStatus.VERIFIED
                else None
            ),
            interpretation=interpretation,
            plan=plan,
            sql=validated.sql,
            sources=sources,
            evidence=evidence,
            calculations=tuple(calculations),
            claims=tuple(claims),
            verification=checks,
            data=tuple(rows),
            receipt=receipt,
            repairs=repairs,
        )
