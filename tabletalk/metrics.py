"""Deterministic metrics, uncertainty, and paired comparisons for eval trials."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

from tabletalk.evals import SuiteResult
from tabletalk.schemas import BASELINE_SCHEMA, content_digest, require_schema_version


@dataclass(frozen=True)
class TrialObservation:
    suite: str
    suite_digest: str
    case: str
    trial: int
    passed: bool
    inference_fingerprint: str | None
    latency_ms: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    estimated_cost: float | None

    def __post_init__(self) -> None:
        if not self.suite.strip() or not self.case.strip() or not self.suite_digest.strip():
            raise ValueError("A trial observation requires suite, case, and suite digest values")
        if self.trial < 1:
            raise ValueError("The trial number must be positive")
        if self.latency_ms is not None:
            if not math.isfinite(self.latency_ms) or self.latency_ms < 0:
                raise ValueError("Trial latency must be a finite non-negative number")
            object.__setattr__(self, "latency_ms", float(self.latency_ms))
        if self.estimated_cost is not None:
            if not math.isfinite(self.estimated_cost) or self.estimated_cost < 0:
                raise ValueError("Trial cost must be a finite non-negative number")
            object.__setattr__(self, "estimated_cost", float(self.estimated_cost))
        for name in ("prompt_tokens", "completion_tokens"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"Trial {name} must be a non-negative integer")

    @property
    def pair_key(self) -> tuple[str, str, int]:
        return (self.suite, self.case, self.trial)

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "suite_digest": self.suite_digest,
            "case": self.case,
            "trial": self.trial,
            "passed": self.passed,
            "inference_fingerprint": self.inference_fingerprint,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "estimated_cost": self.estimated_cost,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TrialObservation:
        passed = payload.get("passed")
        if not isinstance(passed, bool):
            raise ValueError("Trial passed must be true or false")
        return cls(
            suite=str(payload.get("suite") or ""),
            suite_digest=str(payload.get("suite_digest") or ""),
            case=str(payload.get("case") or ""),
            trial=int(payload.get("trial") or 1),
            passed=passed,
            inference_fingerprint=(
                str(payload["inference_fingerprint"])
                if payload.get("inference_fingerprint")
                else None
            ),
            latency_ms=(
                float(payload["latency_ms"])
                if payload.get("latency_ms") is not None
                else None
            ),
            prompt_tokens=payload.get("prompt_tokens"),
            completion_tokens=payload.get("completion_tokens"),
            estimated_cost=(
                float(payload["estimated_cost"])
                if payload.get("estimated_cost") is not None
                else None
            ),
        )


def observations(results: tuple[SuiteResult, ...]) -> tuple[TrialObservation, ...]:
    values = []
    for result in results:
        for case in result.cases:
            usage = case.trace.usage if case.trace else case.usage
            values.append(
                TrialObservation(
                    result.name,
                    result.suite_digest,
                    case.name,
                    result.trial,
                    case.passed,
                    (
                        case.trace.inference_fingerprint
                        if case.trace
                        else case.inference_fingerprint
                    ),
                    usage.latency_ms if usage else None,
                    usage.prompt_tokens if usage else None,
                    usage.completion_tokens if usage else None,
                    usage.estimated_cost if usage else None,
                )
            )
    return tuple(sorted(values, key=lambda item: item.pair_key))


def wilson_interval(
    successes: int,
    total: int,
    z: float = 1.959963984540054,
) -> tuple[float, float]:
    if total < 1:
        return (0.0, 1.0)
    rate = successes / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    spread = (
        z
        * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
        / denominator
    )
    return (max(0.0, center - spread), min(1.0, center + spread))


def _percentile(values: tuple[float, ...], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


@dataclass(frozen=True)
class MetricSummary:
    trials: int
    passed: int
    pass_rate: float
    pass_rate_interval: tuple[float, float]
    latency_mean_ms: float | None
    latency_p95_ms: float | None
    prompt_tokens: int
    completion_tokens: int
    estimated_cost: float | None
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trials": self.trials,
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "pass_rate_interval": list(self.pass_rate_interval),
            "latency_mean_ms": self.latency_mean_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "estimated_cost": self.estimated_cost,
            "warnings": list(self.warnings),
        }


def summarize(values: tuple[TrialObservation, ...]) -> MetricSummary:
    total = len(values)
    passed = sum(item.passed for item in values)
    latencies = tuple(item.latency_ms for item in values if item.latency_ms is not None)
    costs = tuple(item.estimated_cost for item in values if item.estimated_cost is not None)
    warnings = []
    if total < 20:
        warnings.append(
            f"The sample has {total} observations. Use at least 20 for a stable comparison."
        )
    return MetricSummary(
        trials=total,
        passed=passed,
        pass_rate=passed / total if total else 0,
        pass_rate_interval=wilson_interval(passed, total),
        latency_mean_ms=mean(latencies) if latencies else None,
        latency_p95_ms=_percentile(latencies, 0.95),
        prompt_tokens=sum(item.prompt_tokens or 0 for item in values),
        completion_tokens=sum(item.completion_tokens or 0 for item in values),
        estimated_cost=sum(costs) if costs else None,
        warnings=tuple(warnings),
    )


def _mcnemar_exact(baseline_only: int, candidate_only: int) -> float:
    discordant = baseline_only + candidate_only
    if discordant == 0:
        return 1.0
    smaller = min(baseline_only, candidate_only)
    probability = sum(math.comb(discordant, value) for value in range(smaller + 1))
    return min(1.0, float(2 * probability / (2**discordant)))


def _paired_interval(
    differences: tuple[float, ...],
    *,
    bounds: tuple[float, float] | None = None,
) -> tuple[float, float]:
    if not differences:
        return (0.0, 0.0)
    effect = mean(differences)
    if len(differences) == 1:
        return (effect, effect)
    variance = sum((value - effect) ** 2 for value in differences) / (len(differences) - 1)
    margin = 1.959963984540054 * math.sqrt(variance / len(differences))
    interval = (effect - margin, effect + margin)
    if bounds:
        return (max(bounds[0], interval[0]), min(bounds[1], interval[1]))
    return interval


def _signed_ranks(differences: tuple[float, ...]) -> tuple[tuple[float, int], ...]:
    nonzero = [(abs(value), 1 if value > 0 else -1) for value in differences if value]
    ordered = sorted(enumerate(nonzero), key=lambda item: item[1][0])
    ranks = [0.0] * len(nonzero)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1][0] == ordered[index][1][0]:
            end += 1
        average_rank = (index + 1 + end) / 2
        for position in range(index, end):
            ranks[ordered[position][0]] = average_rank
        index = end
    return tuple((ranks[index], sign) for index, (_, sign) in enumerate(nonzero))


def _wilcoxon(differences: tuple[float, ...]) -> tuple[float, float]:
    ranks = _signed_ranks(differences)
    if not ranks:
        return (0.0, 1.0)
    positive = sum(rank for rank, sign in ranks if sign > 0)
    total_rank = sum(rank for rank, _ in ranks)
    statistic = min(positive, total_rank - positive)
    count = len(ranks)
    if count <= 16:
        extreme = 0
        combinations = 2**count
        for mask in range(combinations):
            selected = sum(rank for index, (rank, _) in enumerate(ranks) if mask & (1 << index))
            if min(selected, total_rank - selected) <= statistic + 1e-12:
                extreme += 1
        return (statistic, min(1.0, extreme / combinations))
    expected = total_rank / 2
    variance = sum(rank * rank for rank, _ in ranks) / 4
    z = (abs(positive - expected) - 0.5) / math.sqrt(variance)
    return (statistic, math.erfc(max(0.0, z) / math.sqrt(2)))


@dataclass(frozen=True)
class PairedComparison:
    pairs: int
    baseline_pass_rate: float
    candidate_pass_rate: float
    pass_rate_effect: float
    pass_rate_effect_interval: tuple[float, float]
    mcnemar_p_value: float
    latency_effect_ms: float | None
    latency_effect_interval_ms: tuple[float, float] | None
    wilcoxon_statistic: float | None
    wilcoxon_p_value: float | None
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pairs": self.pairs,
            "baseline_pass_rate": self.baseline_pass_rate,
            "candidate_pass_rate": self.candidate_pass_rate,
            "pass_rate_effect": self.pass_rate_effect,
            "pass_rate_effect_interval": list(self.pass_rate_effect_interval),
            "mcnemar_p_value": self.mcnemar_p_value,
            "latency_effect_ms": self.latency_effect_ms,
            "latency_effect_interval_ms": (
                list(self.latency_effect_interval_ms)
                if self.latency_effect_interval_ms
                else None
            ),
            "wilcoxon_statistic": self.wilcoxon_statistic,
            "wilcoxon_p_value": self.wilcoxon_p_value,
            "warnings": list(self.warnings),
        }


def compare_paired(
    baseline: tuple[TrialObservation, ...],
    candidate: tuple[TrialObservation, ...],
) -> PairedComparison:
    baseline_by_key = {item.pair_key: item for item in baseline}
    candidate_by_key = {item.pair_key: item for item in candidate}
    if len(baseline_by_key) != len(baseline):
        raise ValueError("The baseline has duplicate paired observation keys")
    if len(candidate_by_key) != len(candidate):
        raise ValueError("The candidate has duplicate paired observation keys")
    keys = sorted(set(baseline_by_key) & set(candidate_by_key))
    if not keys:
        raise ValueError("The baseline and candidate have no paired observations")
    pairs = tuple((baseline_by_key[key], candidate_by_key[key]) for key in keys)
    baseline_only = sum(left.passed and not right.passed for left, right in pairs)
    candidate_only = sum(not left.passed and right.passed for left, right in pairs)
    pass_differences = tuple(float(right.passed) - float(left.passed) for left, right in pairs)
    latency_differences = tuple(
        right.latency_ms - left.latency_ms
        for left, right in pairs
        if left.latency_ms is not None and right.latency_ms is not None
    )
    statistic, wilcoxon_p = _wilcoxon(latency_differences)
    warnings = []
    if len(pairs) < 20:
        warnings.append(
            f"The paired sample has {len(pairs)} observations. "
            "Use at least 20 for a stable comparison."
        )
    if any(left.suite_digest != right.suite_digest for left, right in pairs):
        warnings.append("The eval definition changed between the baseline and candidate.")
    return PairedComparison(
        pairs=len(pairs),
        baseline_pass_rate=sum(left.passed for left, _ in pairs) / len(pairs),
        candidate_pass_rate=sum(right.passed for _, right in pairs) / len(pairs),
        pass_rate_effect=mean(pass_differences),
        pass_rate_effect_interval=_paired_interval(
            pass_differences,
            bounds=(-1.0, 1.0),
        ),
        mcnemar_p_value=_mcnemar_exact(baseline_only, candidate_only),
        latency_effect_ms=median(latency_differences) if latency_differences else None,
        latency_effect_interval_ms=(
            _paired_interval(latency_differences) if latency_differences else None
        ),
        wilcoxon_statistic=statistic if latency_differences else None,
        wilcoxon_p_value=wilcoxon_p if latency_differences else None,
        warnings=tuple(warnings),
    )


@dataclass(frozen=True)
class BaselineRecord:
    name: str
    observations: tuple[TrialObservation, ...]
    actor: str
    approved_at: str
    source_digest: str
    known_failures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.actor.strip():
            raise ValueError("A baseline requires a name and an actor")
        if not self.observations:
            raise ValueError("A baseline requires at least one trial observation")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", self.source_digest) is None:
            raise ValueError("A baseline requires a source report digest")
        keys = [item.pair_key for item in self.observations]
        if len(keys) != len(set(keys)):
            raise ValueError("A baseline cannot contain duplicate trial observations")
        object.__setattr__(
            self,
            "observations",
            tuple(sorted(self.observations, key=lambda item: item.pair_key)),
        )
        try:
            datetime.fromisoformat(self.approved_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Baseline approved_at must use ISO 8601") from exc

    def content_payload(self) -> dict[str, Any]:
        return {
            "schema_version": BASELINE_SCHEMA,
            "name": self.name,
            "actor": self.actor,
            "approved_at": self.approved_at,
            "source_digest": self.source_digest,
            "known_failures": list(self.known_failures),
            "observations": [item.to_dict() for item in self.observations],
        }

    @property
    def digest(self) -> str:
        return content_digest(self.content_payload())

    def to_dict(self) -> dict[str, Any]:
        return {"digest": self.digest, **self.content_payload()}

    def dump(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        rendered = self.dump()
        if target.exists() and target.read_text() != rendered:
            raise ValueError(f"Baseline path has different content: {target}")
        target.write_text(rendered)
        return target

    @classmethod
    def load(cls, path: str | Path) -> BaselineRecord:
        source = Path(path)
        try:
            payload = json.loads(source.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not load baseline {source}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("A baseline must be a JSON object")
        require_schema_version(
            payload,
            BASELINE_SCHEMA,
            resource=str(source),
            editable=False,
        )
        values = payload.get("observations")
        if not isinstance(values, list):
            raise ValueError("Baseline observations must be a list")
        baseline = cls(
            str(payload.get("name") or ""),
            tuple(TrialObservation.from_dict(item) for item in values),
            str(payload.get("actor") or ""),
            str(payload.get("approved_at") or ""),
            str(payload.get("source_digest") or ""),
            tuple(str(item) for item in payload.get("known_failures") or ()),
        )
        if payload.get("digest") != baseline.digest:
            raise ValueError("Baseline content digest does not match")
        return baseline
