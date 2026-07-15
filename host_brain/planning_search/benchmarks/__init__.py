"""Deterministic Code and Research PlanningSearch shadow benchmarks."""

from .adapters import CodeShadowBenchmarkAdapter, ResearchShadowBenchmarkAdapter
from .contracts import EVIDENCE_LEVEL, FrozenBenchmarkSuite, FrozenScenario, ReplayOutcome


def build_artifact(*args, **kwargs):
    from .runner import build_artifact as _build_artifact

    return _build_artifact(*args, **kwargs)


def run_suite(*args, **kwargs):
    from .runner import run_suite as _run_suite

    return _run_suite(*args, **kwargs)

__all__ = [
    "CodeShadowBenchmarkAdapter",
    "EVIDENCE_LEVEL",
    "FrozenBenchmarkSuite",
    "FrozenScenario",
    "ReplayOutcome",
    "ResearchShadowBenchmarkAdapter",
    "build_artifact",
    "run_suite",
]
