"""Reproducible, execution-based evaluations for TableTalk agents."""

from tabletalk.evals.loader import EvalConfigError, load_eval_suite
from tabletalk.evals.models import (
    CaseResult,
    EvalCase,
    EvalSuite,
    ExecutionTrace,
    MetricResult,
    SuiteResult,
)
from tabletalk.evals.receipts import (
    create_eval_receipt,
    matching_eval_receipt,
    write_eval_receipt,
)
from tabletalk.evals.runner import EvalRunner

__all__ = [
    "CaseResult",
    "EvalCase",
    "EvalConfigError",
    "EvalRunner",
    "create_eval_receipt",
    "matching_eval_receipt",
    "write_eval_receipt",
    "EvalSuite",
    "ExecutionTrace",
    "MetricResult",
    "SuiteResult",
    "load_eval_suite",
]
