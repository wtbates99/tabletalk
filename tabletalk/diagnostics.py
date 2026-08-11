"""Stable deterministic failure categories and corrective actions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from tabletalk.evals import CaseResult, EvalCase, SuiteResult


class FailureCode(str, Enum):
    UNSUPPORTED_QUESTION = "unsupported_question"
    AMBIGUITY_NOT_DETECTED = "ambiguity_not_detected"
    INSUFFICIENT_CLARIFICATION = "insufficient_clarification"
    WRONG_MODEL_SELECTION = "wrong_model_selection"
    WRONG_COLUMN_SELECTION = "wrong_column_selection"
    INCORRECT_JOIN = "incorrect_join"
    INCORRECT_FILTER = "incorrect_filter"
    INCORRECT_TIME_BOUNDARY = "incorrect_time_boundary"
    INCORRECT_AGGREGATION_OR_GRAIN = "incorrect_aggregation_or_grain"
    UNSAFE_OR_OUT_OF_SCOPE_ACCESS = "unsafe_or_out_of_scope_access"
    SQL_VALIDATION_FAILURE = "sql_validation_failure"
    EXECUTION_FAILURE = "execution_failure"
    RESULT_MISMATCH = "result_mismatch"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    MISSING_REQUESTED_OUTPUT = "missing_requested_output"
    LATENCY_BUDGET_VIOLATION = "latency_budget_violation"
    TOKEN_BUDGET_VIOLATION = "token_budget_violation"
    COST_BUDGET_VIOLATION = "cost_budget_violation"
    FLAKY_OR_INCONSISTENT_OUTCOME = "flaky_or_inconsistent_outcome"
    INVALID_OR_AMBIGUOUS_GROUND_TRUTH = "invalid_or_ambiguous_ground_truth"


class FailureOwner(str, Enum):
    DBT_METADATA = "dbt_metadata"
    SEMANTIC_KNOWLEDGE = "semantic_knowledge"
    AGENT_DEFINITION = "agent_definition"
    EVAL_DEFINITION = "eval_definition"
    RUNTIME_CODE = "runtime_code"
    WAREHOUSE = "warehouse"


@dataclass(frozen=True)
class FailureDiagnosis:
    suite: str
    case: str
    trial: int
    code: FailureCode
    stage: str
    evidence: tuple[str, ...]
    corrective_action: str
    owner: FailureOwner
    baseline_value: Any = None
    candidate_value: Any = None

    def to_dict(self) -> dict[str, Any]:
        value = {
            "suite": self.suite,
            "case": self.case,
            "trial": self.trial,
            "code": self.code.value,
            "stage": self.stage,
            "evidence": list(self.evidence),
            "corrective_action": self.corrective_action,
            "owner": self.owner.value,
        }
        if self.baseline_value is not None:
            value["baseline_value"] = self.baseline_value
        if self.candidate_value is not None:
            value["candidate_value"] = self.candidate_value
        return value


_CHECK_MAP: dict[str, tuple[FailureCode, str, str, FailureOwner]] = {
    "required_models": (
        FailureCode.WRONG_MODEL_SELECTION,
        "selection",
        "Update the agent scope or the model-selection prompt.",
        FailureOwner.AGENT_DEFINITION,
    ),
    "forbidden_models": (
        FailureCode.UNSAFE_OR_OUT_OF_SCOPE_ACCESS,
        "selection",
        "Remove the resource from the agent scope.",
        FailureOwner.AGENT_DEFINITION,
    ),
    "required_columns": (
        FailureCode.WRONG_COLUMN_SELECTION,
        "selection",
        "Add the missing column meaning to dbt or semantic knowledge.",
        FailureOwner.DBT_METADATA,
    ),
    "forbidden_columns": (
        FailureCode.UNSAFE_OR_OUT_OF_SCOPE_ACCESS,
        "validation",
        "Mark the column as sensitive or exclude it from the agent.",
        FailureOwner.DBT_METADATA,
    ),
    "join_conditions": (
        FailureCode.INCORRECT_JOIN,
        "validation",
        "Add explicit relationship evidence in dbt metadata.",
        FailureOwner.DBT_METADATA,
    ),
    "result_match": (
        FailureCode.RESULT_MISMATCH,
        "metric",
        "Inspect the SQL, semantic definition, and eval ground truth.",
        FailureOwner.SEMANTIC_KNOWLEDGE,
    ),
    "supported_claims": (
        FailureCode.UNSUPPORTED_CLAIM,
        "answer",
        "Change the answer prompt or remove the unsupported claim.",
        FailureOwner.RUNTIME_CODE,
    ),
    "claims_supported": (
        FailureCode.UNSUPPORTED_CLAIM,
        "answer",
        "Change the answer prompt or evidence links.",
        FailureOwner.RUNTIME_CODE,
    ),
    "shape": (
        FailureCode.MISSING_REQUESTED_OUTPUT,
        "metric",
        "Add the requested output to the query or correct the eval expectation.",
        FailureOwner.EVAL_DEFINITION,
    ),
    "date_boundaries_disclosed": (
        FailureCode.INCORRECT_TIME_BOUNDARY,
        "answer",
        "Record the exact time boundary in semantic knowledge.",
        FailureOwner.SEMANTIC_KNOWLEDGE,
    ),
    "reference_query": (
        FailureCode.INVALID_OR_AMBIGUOUS_GROUND_TRUTH,
        "ground_truth",
        "Correct and approve the reference query.",
        FailureOwner.EVAL_DEFINITION,
    ),
    "budget_latency_ms": (
        FailureCode.LATENCY_BUDGET_VIOLATION,
        "budget",
        "Reduce runtime latency or change the approved latency budget.",
        FailureOwner.RUNTIME_CODE,
    ),
    "budget_prompt_tokens": (
        FailureCode.TOKEN_BUDGET_VIOLATION,
        "budget",
        "Reduce prompt tokens or change the approved token budget.",
        FailureOwner.RUNTIME_CODE,
    ),
    "budget_completion_tokens": (
        FailureCode.TOKEN_BUDGET_VIOLATION,
        "budget",
        "Reduce completion tokens or change the approved token budget.",
        FailureOwner.RUNTIME_CODE,
    ),
    "budget_total_tokens": (
        FailureCode.TOKEN_BUDGET_VIOLATION,
        "budget",
        "Reduce total tokens or change the approved token budget.",
        FailureOwner.RUNTIME_CODE,
    ),
    "budget_cost": (
        FailureCode.COST_BUDGET_VIOLATION,
        "budget",
        "Reduce model cost or change the approved cost budget.",
        FailureOwner.AGENT_DEFINITION,
    ),
}


def _expected_outcome_diagnosis(case: EvalCase) -> tuple[FailureCode, str, str, FailureOwner]:
    if case.expected_outcome == "ambiguity":
        return (
            FailureCode.AMBIGUITY_NOT_DETECTED,
            "interpretation",
            "Add the missing alternatives to semantic knowledge.",
            FailureOwner.SEMANTIC_KNOWLEDGE,
        )
    if case.expected_outcome == "rejection":
        return (
            FailureCode.UNSAFE_OR_OUT_OF_SCOPE_ACCESS,
            "policy",
            "Add an explicit rejection rule to the agent definition.",
            FailureOwner.AGENT_DEFINITION,
        )
    return (
        FailureCode.UNSUPPORTED_QUESTION,
        "interpretation",
        "Add trusted semantic knowledge or mark the question as unsupported.",
        FailureOwner.SEMANTIC_KNOWLEDGE,
    )


def diagnose_case(
    suite: str,
    case: EvalCase,
    result: CaseResult,
    trial: int,
) -> tuple[FailureDiagnosis, ...]:
    diagnoses: list[FailureDiagnosis] = []
    if result.error and not result.passed and case.expected_outcome == "answer":
        if "rejected" in result.error.casefold():
            code = FailureCode.UNSUPPORTED_QUESTION
            stage = "interpretation"
            owner = FailureOwner.SEMANTIC_KNOWLEDGE
            action = "Add trusted semantic knowledge or keep the question unsupported."
        elif "sql" in result.error.casefold() or "validation" in result.error.casefold():
            code = FailureCode.SQL_VALIDATION_FAILURE
            stage = "validation"
            owner = FailureOwner.RUNTIME_CODE
            action = "Correct the generated SQL or the SQL validation rule."
        else:
            code = FailureCode.EXECUTION_FAILURE
            stage = "execution"
            owner = FailureOwner.WAREHOUSE
            action = "Inspect the warehouse error and the read-only connection."
        diagnoses.append(
            FailureDiagnosis(
                suite,
                case.name,
                trial,
                code,
                stage,
                (result.error,),
                action,
                owner,
            )
        )
    for check in result.checks:
        if check.passed:
            continue
        mapping = (
            _expected_outcome_diagnosis(case)
            if check.name == "expected_outcome"
            else _CHECK_MAP.get(check.name)
        )
        if (
            check.name == "expected_outcome"
            and case.expected_outcome == "ambiguity"
            and result.error
        ):
            mapping = (
                FailureCode.INSUFFICIENT_CLARIFICATION,
                "interpretation",
                "Ask a concrete question that names the available choices.",
                FailureOwner.AGENT_DEFINITION,
            )
        if (
            check.name == "expected_outcome"
            and case.expected_outcome == "answer"
            and result.error
        ):
            continue
        if mapping is None:
            mapping = (
                FailureCode.EXECUTION_FAILURE,
                "runtime",
                "Inspect the failed hard check and correct the runtime.",
                FailureOwner.RUNTIME_CODE,
            )
        code, stage, action, owner = mapping
        diagnoses.append(
            FailureDiagnosis(
                suite,
                case.name,
                trial,
                code,
                stage,
                (f"{check.name}: {check.message or 'failed'}",),
                action,
                owner,
            )
        )
    return tuple(diagnoses)


def diagnose_results(
    suites: dict[str, tuple[EvalCase, ...]],
    results: tuple[SuiteResult, ...],
) -> tuple[FailureDiagnosis, ...]:
    diagnoses: list[FailureDiagnosis] = []
    outcomes: dict[tuple[str, str], set[bool]] = {}
    for result in results:
        cases = {case.name: case for case in suites.get(result.name, ())}
        for case_result in result.cases:
            outcomes.setdefault((result.name, case_result.name), set()).add(case_result.passed)
            eval_case = cases.get(case_result.name)
            if eval_case:
                diagnoses.extend(
                    diagnose_case(result.name, eval_case, case_result, result.trial)
                )
    for (suite, case_name), values in sorted(outcomes.items()):
        if len(values) < 2:
            continue
        diagnoses.append(
            FailureDiagnosis(
                suite,
                case_name,
                0,
                FailureCode.FLAKY_OR_INCONSISTENT_OUTCOME,
                "aggregation",
                ("The case has passing and failing trials.",),
                "Increase the trial count and inspect the inference artifacts.",
                FailureOwner.RUNTIME_CODE,
            )
        )
    return tuple(diagnoses)
