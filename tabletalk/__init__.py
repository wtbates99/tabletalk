"""Define, evaluate, apply, and run trusted data agents as code."""

from tabletalk.agents import AgentDefinition
from tabletalk.compiler import CompiledAgent, CompiledArtifact
from tabletalk.domain import (
    EvidenceItem,
    Interpretation,
    QueryAnswer,
    SemanticPlan,
    VerificationCheck,
    VerificationStatus,
)
from tabletalk.evals import EvalSuite, SuiteResult
from tabletalk.project import AppliedAgent, Project, ProjectPlan

__version__ = "0.4.0"
Agent = AgentDefinition
Plan = ProjectPlan
Answer = QueryAnswer
Evidence = EvidenceItem
EvalReport = SuiteResult

__all__ = [
    "Agent",
    "AgentDefinition",
    "Answer",
    "AppliedAgent",
    "CompiledAgent",
    "CompiledArtifact",
    "EvalReport",
    "EvalSuite",
    "Evidence",
    "EvidenceItem",
    "Interpretation",
    "Plan",
    "Project",
    "ProjectPlan",
    "QueryAnswer",
    "SemanticPlan",
    "SuiteResult",
    "VerificationCheck",
    "VerificationStatus",
    "__version__",
]
