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
from tabletalk.evals.runner import EvalRunner

__all__ = [
    "CaseResult",
    "EvalCase",
    "EvalConfigError",
    "EvalRunner",
    "EvalSuite",
    "ExecutionTrace",
    "MetricResult",
    "SuiteResult",
    "load_eval_suite",
]
