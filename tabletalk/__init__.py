"""TableTalk: dbt-native development, evaluation, and observability for trusted data agents."""

from tabletalk.agents import Agent, ResolvedAgent
from tabletalk.evals import EvalCase, EvalSuite, SuiteResult
from tabletalk.manifest import Manifest, Node
from tabletalk.project import Project
from tabletalk.traces import Trace

__version__ = "0.7.0"

__all__ = [
    "Agent",
    "EvalCase",
    "EvalSuite",
    "Manifest",
    "Node",
    "Project",
    "ResolvedAgent",
    "SuiteResult",
    "Trace",
    "__version__",
]
