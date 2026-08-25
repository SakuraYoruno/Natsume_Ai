"""Pure Research-domain adapters for the shared PlanningSearch kernel.

This module derives query, primary-source, independent-source, cross-check,
conflict-resolution and stop candidates from public, frozen source metadata.
It has no import of the browser worker, filesystem, HTTP client, model runtime,
oracle, verifier, TaskGraph, or Host composition.  Selecting a path here does
not read a source or answer a user.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from host_brain.contracts.planning_search.contracts import CandidateScore, SearchCandidate, VirtualStateSnapshot
from host_brain.contracts.planning_search.research_domain_models import (
    RESEARCH_DOMAIN,
    RESEARCH_SHADOW_MODE,
    ResearchPlanningError,
    ResearchSourceDescriptor,
    ResearchTaskSnapshot,
)


def _thaw(value: Any) -> Any:
    return json.loads(
        json.dumps(_plain_json(value), ensure_ascii=False, sort_keys=True)
    )


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _facts(state: VirtualStateSnapshot) -> dict[str, Any]:
    return _thaw(state.facts)


def _constraints(state: VirtualStateSnapshot) -> dict[str, Any]:
    return _thaw(state.constraints)


def _source_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(sorted({str(item or "").strip().lower() for item in value if str(item or "").strip()}))


class ResearchSuccessorCatalog:
    """Dynamic Research successors derived only from public frozen metadata."""

    def __init__(self, *, snapshot: ResearchTaskSnapshot) -> None:
        if not isinstance(snapshot, ResearchTaskSnapshot):
            raise TypeError("snapshot must be ResearchTaskSnapshot")
        self._snapshot = snapshot
        self._sources = {item.source_id: item for item in snapshot.sources}

    def expand(
        self,
        *,
        state: Any,
        path: tuple[SearchCandidate, ...],
        depth: int,
        limit: int,
        context: Mapping[str, Any],
    ) -> Sequence[SearchCandidate]:
        del path, depth, context
        snapshot = _research_state(state, expected=self._snapshot)
        if int(limit) <= 0:
            return ()
        facts = _facts(snapshot)
        phase = str(facts.get("phase") or "root").strip().lower()
        read_ids = _source_ids(facts.get("read_source_ids"))
        primary_ids = _source_ids(facts.get("primary_source_ids"))
        independent_ids = _source_ids(facts.get("independent_source_ids"))
        cross_checked = bool(facts.get("cross_checked"))
        conflict_resolved = bool(facts.get("conflict_resolved"))
        if phase == "root":
            values = self._root_candidates()
        elif phase in {"queried", "primary_read", "independent_read"}:
            values = self._evidence_candidates(
                read_ids=read_ids,
                primary_ids=primary_ids,
                independent_ids=independent_ids,
                cross_checked=cross_checked,
                conflict_resolved=conflict_resolved,
            )
        elif phase == "cross_checked":
            values = self._after_cross_check_candidates(conflict_resolved=conflict_resolved)
        elif phase == "conflict_resolved":
            values = (self._stop_candidate(),)
        else:
            values = ()
        return tuple(values[: max(0, int(limit))])

    def transition(
        self,
        *,
        state: Any,
        candidate: SearchCandidate,
        path: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> VirtualStateSnapshot:
        del context
        current = _research_state(state, expected=self._snapshot)
        expected = {
            item.candidate_id: item
            for item in self.expand(
                state=current,
                path=path[:-1],
                depth=max(0, len(path) - 1),
                limit=128,
                context={},
            )
        }.get(str(candidate.candidate_id or "").strip())
        if expected is None or expected.to_dict() != candidate.to_dict():
            raise ResearchPlanningError("candidate is not a legal successor of the current Research state")

        facts = _facts(current)
        action = str(candidate.payload.get("research_action") or "").strip().lower()
        if action == "query":
            facts["phase"] = "queried"
            facts["query_scoped"] = True
        elif action == "quick_answer":
            source_id = str(candidate.payload.get("source_id") or "").strip().lower()
            source = self._sources.get(source_id)
            if source is None or source.authority_tier != "secondary":
                raise ResearchPlanningError("quick-answer source is not a registered secondary source")
            facts["read_source_ids"] = [source_id]
            facts["phase"] = "quick_answered"
        elif action in {"primary_source", "independent_source"}:
            source_id = str(candidate.payload.get("source_id") or "").strip().lower()
            source = self._sources.get(source_id)
            if source is None:
                raise ResearchPlanningError("Research source candidate is not registered")
            read_ids = set(_source_ids(facts.get("read_source_ids")))
            read_ids.add(source_id)
            facts["read_source_ids"] = sorted(read_ids)
            if action == "primary_source":
                primary_ids = set(_source_ids(facts.get("primary_source_ids")))
                primary_ids.add(source_id)
                facts["primary_source_ids"] = sorted(primary_ids)
                facts["phase"] = "primary_read"
            else:
                independent_ids = set(_source_ids(facts.get("independent_source_ids")))
                independent_ids.add(source_id)
                facts["independent_source_ids"] = sorted(independent_ids)
                facts["phase"] = "independent_read"
        elif action == "cross_check":
            facts["cross_checked"] = True
            facts["phase"] = "cross_checked"
        elif action == "resolve_conflict":
            if not bool(_constraints(current).get("requires_conflict_resolution")):
                raise ResearchPlanningError("conflict resolution is not required for this snapshot")
            facts["conflict_resolved"] = True
            facts["phase"] = "conflict_resolved"
        elif action == "stop":
            facts["phase"] = "stopped"
        else:
            raise ResearchPlanningError("unsupported Research virtual action")
        return current.replace(facts=facts)

    def _evidence_candidates(
        self,
        *,
        read_ids: tuple[str, ...],
        primary_ids: tuple[str, ...],
        independent_ids: tuple[str, ...],
        cross_checked: bool,
        conflict_resolved: bool,
    ) -> tuple[SearchCandidate, ...]:
        del conflict_resolved
        if not primary_ids:
            return self._source_candidates(authority_tier="primary", read_ids=read_ids)
        independent_available = any(
            item.authority_tier == "independent" for item in self._snapshot.sources
        )
        if independent_available and not independent_ids:
            return self._source_candidates(authority_tier="independent", read_ids=read_ids)
        if not cross_checked:
            return (self._cross_check_candidate(),)
        return self._after_cross_check_candidates(conflict_resolved=False)

    def _source_candidates(
        self,
        *,
        authority_tier: str,
        read_ids: tuple[str, ...],
    ) -> tuple[SearchCandidate, ...]:
        if len(read_ids) >= self._snapshot.max_sources_per_plan:
            return ()
        action = "primary_source" if authority_tier == "primary" else "independent_source"
        prefix = "primary" if authority_tier == "primary" else "independent"
        values: list[SearchCandidate] = []
        for source in self._snapshot.sources:
            if source.source_id in read_ids or source.authority_tier != authority_tier:
                continue
            values.append(self._source_candidate(source=source, action=action, prefix=prefix))
        return tuple(sorted(values, key=lambda item: (-item.prior_score, item.candidate_id)))

    def _after_cross_check_candidates(
        self,
        *,
        conflict_resolved: bool,
    ) -> tuple[SearchCandidate, ...]:
        if self._snapshot.requires_conflict_resolution and not conflict_resolved:
            return (self._conflict_candidate(),)
        return (self._stop_candidate(),)

    def _query_candidate(self) -> SearchCandidate:
        return _candidate(
            candidate_id="research.query.scope",
            action="query",
            prior_score=0.62,
            requires_lookahead=self._snapshot.virtual_transition_ready,
            payload={
                "objective_ref": self._snapshot.task_ref,
                "corpus_ref": self._snapshot.corpus_ref,
                "estimated_read_cost": 0.02,
            },
        )

    def _root_candidates(self) -> tuple[SearchCandidate, ...]:
        values: list[SearchCandidate] = [self._query_candidate()]
        secondary = [
            item
            for item in self._snapshot.sources
            if item.authority_tier == "secondary"
        ]
        if secondary:
            source = max(
                secondary,
                key=lambda item: (item.relevance_score, -item.estimated_read_cost, item.source_id),
            )
            values.append(self._quick_answer_candidate(source))
        return tuple(sorted(values, key=lambda item: (-item.prior_score, item.candidate_id)))

    def _quick_answer_candidate(self, source: ResearchSourceDescriptor) -> SearchCandidate:
        """A plausible fast baseline; L3 must decide whether it is sufficient."""

        prior = 0.34 + 0.65 * source.relevance_score - 0.15 * source.estimated_read_cost
        return _candidate(
            candidate_id=f"research.quick-answer.{source.source_id}",
            action="quick_answer",
            prior_score=prior,
            requires_lookahead=self._snapshot.virtual_transition_ready,
            payload={
                "source_id": source.source_id,
                "source_ref": source.source_ref,
                "source_digest": source.content_digest,
                "authority_tier": source.authority_tier,
                "relevance_score": source.relevance_score,
                "estimated_read_cost": source.estimated_read_cost,
            },
        )

    def _source_candidate(
        self,
        *,
        source: ResearchSourceDescriptor,
        action: str,
        prefix: str,
    ) -> SearchCandidate:
        authority_bonus = 0.14 if source.authority_tier == "primary" else 0.10
        prior = 0.28 + authority_bonus + 0.54 * source.relevance_score - 0.20 * source.estimated_read_cost
        return _candidate(
            candidate_id=f"research.{prefix}.{source.source_id}",
            action=action,
            prior_score=prior,
            requires_lookahead=self._snapshot.virtual_transition_ready,
            payload={
                "source_id": source.source_id,
                "source_ref": source.source_ref,
                "source_digest": source.content_digest,
                "authority_tier": source.authority_tier,
                "relevance_score": source.relevance_score,
                "estimated_read_cost": source.estimated_read_cost,
            },
        )

    def _cross_check_candidate(self) -> SearchCandidate:
        return _candidate(
            candidate_id="research.cross-check.evidence",
            action="cross_check",
            prior_score=0.73,
            requires_lookahead=self._snapshot.virtual_transition_ready,
            payload={"estimated_read_cost": 0.10},
        )

    def _conflict_candidate(self) -> SearchCandidate:
        return _candidate(
            candidate_id="research.resolve-conflict.scope",
            action="resolve_conflict",
            prior_score=0.70,
            requires_lookahead=self._snapshot.virtual_transition_ready,
            payload={"estimated_read_cost": 0.08},
        )

    def _stop_candidate(self) -> SearchCandidate:
        return _candidate(
            candidate_id="research.stop.synthesis",
            action="stop",
            prior_score=0.66,
            requires_lookahead=False,
            payload={"estimated_read_cost": 0.0},
        )


class ResearchCognitiveRanker:
    """L2 ranks only public authority, relevance and bounded read-cost hints."""

    def rank(
        self,
        *,
        state: Any,
        candidates: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> Sequence[CandidateScore]:
        del state, context
        values: list[CandidateScore] = []
        for candidate in candidates:
            payload = candidate.payload
            action = str(payload.get("research_action") or "")
            authority = str(payload.get("authority_tier") or "")
            authority_bonus = {"primary": 0.18, "independent": 0.12}.get(authority, 0.0)
            relevance = _unit(payload.get("relevance_score"))
            cost = _unit(payload.get("estimated_read_cost"))
            action_bonus = {
                "query": 0.05,
                "cross_check": 0.12,
                "resolve_conflict": 0.10,
                "stop": 0.02,
            }.get(action, 0.0)
            score = float(candidate.prior_score) + authority_bonus + 0.22 * relevance - 0.14 * cost + action_bonus
            values.append(
                CandidateScore(
                    candidate_id=candidate.candidate_id,
                    score=score,
                    reason_code="research_public_evidence_rank",
                )
            )
        return tuple(values)


class ResearchRewardModel:
    """L3 virtual reward over public metadata, never verifier or answer labels."""

    def __init__(self, *, snapshot: ResearchTaskSnapshot) -> None:
        self._snapshot = snapshot

    def evaluate(
        self,
        *,
        initial_state: Any,
        state: Any,
        path: tuple[SearchCandidate, ...],
        context: Mapping[str, Any],
    ) -> float:
        del initial_state, path, context
        current = _research_state(state, expected=self._snapshot)
        facts = _facts(current)
        read_ids = _source_ids(facts.get("read_source_ids"))
        primary_ids = _source_ids(facts.get("primary_source_ids"))
        independent_ids = _source_ids(facts.get("independent_source_ids"))
        phase = str(facts.get("phase") or "").lower()
        total_cost = sum(
            self._source_cost(source_id)
            for source_id in read_ids
        )
        value = 0.06 if bool(facts.get("query_scoped")) else 0.0
        value += 0.31 if primary_ids else 0.0
        value += 0.20 if independent_ids else 0.0
        value += 0.23 if bool(facts.get("cross_checked")) else 0.0
        if self._snapshot.requires_conflict_resolution:
            value += 0.12 if bool(facts.get("conflict_resolved")) else -0.18
        if phase == "stopped":
            value += 0.16
        elif phase == "quick_answered":
            value -= 0.12
        elif phase in {"cross_checked", "conflict_resolved"}:
            value += 0.05
        value -= min(0.18, 0.06 * total_cost)
        return max(-1.0, min(1.0, value))

    def _source_cost(self, source_id: str) -> float:
        for source in self._snapshot.sources:
            if source.source_id == source_id:
                return source.estimated_read_cost
        return 1.0


class ResearchMCTSCondition:
    """Run L3 only when the public source set needs cross-source reasoning."""

    def should_search(
        self,
        *,
        candidates: tuple[SearchCandidate, ...],
        scores: Mapping[str, float],
        context: Mapping[str, Any],
    ) -> bool:
        del scores, context
        return any(bool(item.payload.get("requires_lookahead")) for item in candidates)


@dataclass(frozen=True, slots=True)
class ResearchPlanningDomain:
    """One immutable, pure adapter bundle for a Research shadow request."""

    snapshot: ResearchTaskSnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, ResearchTaskSnapshot):
            raise TypeError("snapshot must be ResearchTaskSnapshot")

    def planning_ports(self):  # noqa: ANN201
        """Return the common kernel ports; no worker/model is attached here."""

        from host_brain.orchestration.goal_planning import PlanningDomainPorts  # noqa: PLC0415

        catalog = ResearchSuccessorCatalog(snapshot=self.snapshot)
        transition = catalog if self.snapshot.virtual_transition_ready else None
        reward = (
            ResearchRewardModel(snapshot=self.snapshot)
            if transition is not None
            else None
        )
        return PlanningDomainPorts(
            successors=catalog,
            ranker=ResearchCognitiveRanker(),
            transition=transition,
            reward=reward,
            mcts_condition=ResearchMCTSCondition(),
        )


def _research_state(value: Any, *, expected: ResearchTaskSnapshot) -> VirtualStateSnapshot:
    if not isinstance(value, VirtualStateSnapshot):
        raise ResearchPlanningError("Research state must be VirtualStateSnapshot")
    if value.domain != RESEARCH_DOMAIN:
        raise ResearchPlanningError("Research state has the wrong domain")
    facts = _facts(value)
    constraints = _constraints(value)
    if facts.get("snapshot_digest") != expected.snapshot_digest:
        raise ResearchPlanningError("Research state does not bind the frozen snapshot")
    if facts.get("sources") != [item.to_state_dict() for item in expected.sources]:
        raise ResearchPlanningError("Research state source metadata drifted")
    if constraints.get("mode") != RESEARCH_SHADOW_MODE:
        raise ResearchPlanningError("Research state is not shadow-only")
    if bool(constraints.get("requires_conflict_resolution")) != expected.requires_conflict_resolution:
        raise ResearchPlanningError("Research state conflict constraint drifted")
    if bool(constraints.get("virtual_transition_ready")) != expected.virtual_transition_ready:
        raise ResearchPlanningError("Research state virtual-transition constraint drifted")
    return value


def _candidate(
    *,
    candidate_id: str,
    action: str,
    prior_score: float,
    requires_lookahead: bool,
    payload: Mapping[str, Any],
) -> SearchCandidate:
    return SearchCandidate(
        candidate_id=candidate_id,
        prior_score=prior_score,
        labels=("research", action, "readonly_shadow"),
        payload={
            "research_action": action,
            "requires_lookahead": bool(requires_lookahead),
            "mode": RESEARCH_SHADOW_MODE,
            **dict(payload),
        },
    )


def _unit(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, number))


__all__ = [
    "ResearchCognitiveRanker",
    "ResearchMCTSCondition",
    "ResearchPlanningDomain",
    "ResearchRewardModel",
    "ResearchSuccessorCatalog",
]
