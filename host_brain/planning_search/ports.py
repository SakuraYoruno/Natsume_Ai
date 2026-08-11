from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from .contracts import CandidateScore, SearchCandidate


class SuccessorCatalogPort(Protocol):
    """Dynamically produce legal data-only successors from virtual state.

    This is the public L1 name.  A catalog must derive successors from the
    frozen virtual state and selected virtual path; it must not execute a
    candidate, access a real tool, or simply expose a caller-supplied plan.
    """

    def expand(
        self,
        *,
        state: Any,
        path: tuple[SearchCandidate, ...],
        depth: int,
        limit: int,
        context: Mapping[str, Any],
    ) -> Sequence[SearchCandidate]: ...


class CandidateExpansionPort(SuccessorCatalogPort, Protocol):
    """Compatibility name for :class:`SuccessorCatalogPort`.

    Existing Pentest and benchmark adapters remain structurally compatible
    while new goal-driven domains depend on the less ambiguous public name.
    """


class CognitiveRankingPort(Protocol):
    """L2 scoring boundary; implementations may be heuristic or model-backed."""

    def rank(
        self,
        *,
        state: Any,
        candidates: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> Sequence[CandidateScore]: ...


class VirtualTransitionPort(Protocol):
    """Pure simulation boundary.

    Implementations must return a new virtual state and must never execute the
    candidate against a real system. PlanningSearch intentionally exposes no
    executor or effect port.
    """

    def transition(
        self,
        *,
        state: Any,
        candidate: SearchCandidate,
        path: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> Any: ...


class RewardModelPort(Protocol):
    def evaluate(
        self,
        *,
        initial_state: Any,
        state: Any,
        path: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> float: ...


class MCTSConditionPort(Protocol):
    def should_search(
        self,
        *,
        candidates: tuple[SearchCandidate, ...],
        scores: Mapping[str, float],
        context: Mapping[str, Any],
    ) -> bool: ...
