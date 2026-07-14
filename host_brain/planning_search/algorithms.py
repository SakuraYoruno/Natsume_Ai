from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from .contracts import (
    RankedCandidate,
    SearchBudget,
    SearchCandidate,
    SearchCounters,
    SearchTraceEvent,
)
from .ports import CandidateExpansionPort, RewardModelPort, VirtualTransitionPort


ClockMs = Callable[[], float]


class BudgetLedger:
    """Mutable request-local accounting shared across all search layers."""

    def __init__(self, budget: SearchBudget, clock_ms: ClockMs) -> None:
        self.budget = budget
        self._clock_ms = clock_ms
        self.candidates = 0
        self.expansions = 0
        self.transitions = 0
        self.rollouts = 0
        self.stop_reason = ""

    def deadline_reached(self) -> bool:
        deadline = self.budget.deadline_ms
        if deadline is not None and self._clock_ms() >= deadline:
            self.stop_reason = self.stop_reason or "deadline_reached"
            return True
        return False

    def reserve_expansion(self) -> bool:
        if self.deadline_reached():
            return False
        if self.expansions >= self.budget.max_expansions:
            self.stop_reason = self.stop_reason or "expansion_budget_exhausted"
            return False
        self.expansions += 1
        return True

    def reserve_transition(self) -> bool:
        if self.deadline_reached():
            return False
        if self.transitions >= self.budget.max_transitions:
            self.stop_reason = self.stop_reason or "transition_budget_exhausted"
            return False
        self.transitions += 1
        return True

    def reserve_rollout(self) -> bool:
        if self.deadline_reached():
            return False
        if self.rollouts >= self.budget.max_rollouts:
            self.stop_reason = self.stop_reason or "rollout_budget_exhausted"
            return False
        self.rollouts += 1
        return True

    def take_candidates(self, candidates: Sequence[SearchCandidate]) -> tuple[SearchCandidate, ...]:
        remaining = max(0, self.budget.max_candidates - self.candidates)
        accepted = tuple(candidates[:remaining])
        self.candidates += len(accepted)
        if len(accepted) < len(candidates):
            self.stop_reason = self.stop_reason or "candidate_budget_exhausted"
        return accepted

    def counters(self) -> SearchCounters:
        return SearchCounters(
            candidates=self.candidates,
            expansions=self.expansions,
            transitions=self.transitions,
            rollouts=self.rollouts,
        )


class TraceRecorder:
    def __init__(self) -> None:
        self._events: list[SearchTraceEvent] = []

    def emit(self, layer: str, event: str, **details: Any) -> None:
        self._events.append(
            SearchTraceEvent(
                sequence=len(self._events) + 1,
                layer=layer,
                event=event,
                details=details,
            )
        )

    def snapshot(self) -> tuple[SearchTraceEvent, ...]:
        return tuple(self._events)


def stable_candidates(values: Iterable[Any]) -> tuple[tuple[SearchCandidate, ...], tuple[str, ...]]:
    """Validate, deduplicate, and order candidates without provider-order ties."""

    valid: list[SearchCandidate] = []
    rejected: list[str] = []
    for value in values:
        if not isinstance(value, SearchCandidate):
            rejected.append("invalid_candidate_type")
            continue
        if not value.simulation_only:
            rejected.append(f"non_simulation_candidate:{value.candidate_id}")
            continue
        valid.append(value)
    valid.sort(key=lambda item: (-item.prior_score, item.candidate_id))
    unique: dict[str, SearchCandidate] = {}
    for candidate in valid:
        if candidate.candidate_id in unique:
            rejected.append(f"duplicate_candidate:{candidate.candidate_id}")
            continue
        unique[candidate.candidate_id] = candidate
    return tuple(unique.values()), tuple(sorted(rejected))


def rank_candidates(
    candidates: Sequence[SearchCandidate],
    cognitive_scores: Mapping[str, float],
) -> tuple[RankedCandidate, ...]:
    ranked = [
        RankedCandidate(
            candidate=candidate,
            score=float(cognitive_scores.get(candidate.candidate_id, candidate.prior_score)),
            source="l2_cognitive" if candidate.candidate_id in cognitive_scores else "l1_prior",
            path=(candidate,),
        )
        for candidate in candidates
    ]
    ranked.sort(key=lambda item: (-item.score, item.candidate.candidate_id))
    return tuple(ranked)


@dataclass(slots=True)
class _Node:
    candidate: SearchCandidate | None
    state: Any
    parent: "_Node | None"
    depth: int
    prior: float
    children: list["_Node"] = field(default_factory=list)
    expanded: bool = False
    visits: int = 0
    total_reward: float = 0.0
    state_ready: bool = False
    terminal: bool = False
    best_reward: float = -math.inf
    best_path: tuple[SearchCandidate, ...] = ()

    @property
    def mean_reward(self) -> float:
        return self.total_reward / self.visits if self.visits else 0.0

    def tree_path(self) -> tuple[SearchCandidate, ...]:
        values: list[SearchCandidate] = []
        node: _Node | None = self
        while node is not None:
            if node.candidate is not None:
                values.append(node.candidate)
            node = node.parent
        values.reverse()
        return tuple(values)


class DeterministicMCTS:
    """Bounded MCTS over pure virtual transition and reward ports."""

    def __init__(
        self,
        *,
        expander: CandidateExpansionPort,
        transition: VirtualTransitionPort,
        reward_model: RewardModelPort,
        ledger: BudgetLedger,
        trace: TraceRecorder,
    ) -> None:
        self._expander = expander
        self._transition = transition
        self._reward = reward_model
        self._ledger = ledger
        self._trace = trace

    def search(
        self,
        *,
        initial_state: Any,
        ranked: Sequence[RankedCandidate],
        context: Mapping[str, Any],
    ) -> tuple[RankedCandidate, ...]:
        root = _Node(
            candidate=None,
            state=initial_state,
            parent=None,
            depth=0,
            prior=0.0,
            expanded=True,
            state_ready=True,
        )
        for item in ranked:
            root.children.append(
                _Node(
                    candidate=item.candidate,
                    state=None,
                    parent=root,
                    depth=1,
                    prior=item.score,
                )
            )

        while self._ledger.rollouts < self._ledger.budget.max_rollouts:
            if not self._ledger.reserve_rollout():
                break
            leaf = self._select(root)
            if leaf is root:
                break
            if not self._ensure_state(leaf, context):
                reward = -1.0
                rollout_path = leaf.tree_path()
            else:
                leaf = self._expand_once(leaf, context)
                if not self._ensure_state(leaf, context):
                    reward = -1.0
                    rollout_path = leaf.tree_path()
                else:
                    reward, rollout_path = self._rollout(leaf, initial_state, context)
            self._backpropagate(leaf, reward, rollout_path)
            root_id = rollout_path[0].candidate_id if rollout_path else ""
            self._trace.emit(
                "l3",
                "rollout_backpropagated",
                rollout=self._ledger.rollouts,
                root_candidate_id=root_id,
                path=[item.candidate_id for item in rollout_path],
                reward=_rounded(reward),
            )
            if self._ledger.deadline_reached():
                break

        initial_scores = {item.candidate.candidate_id: item.score for item in ranked}
        output: list[RankedCandidate] = []
        for child in root.children:
            candidate = child.candidate
            if candidate is None:
                continue
            initial = initial_scores[candidate.candidate_id]
            score = (0.7 * child.mean_reward + 0.3 * initial) if child.visits else initial
            output.append(
                RankedCandidate(
                    candidate=candidate,
                    score=score,
                    source="l3_mcts",
                    visits=child.visits,
                    mean_reward=child.mean_reward,
                    path=child.best_path or (candidate,),
                )
            )
        output.sort(
            key=lambda item: (
                -item.score,
                -item.visits,
                item.candidate.candidate_id,
                tuple(candidate.candidate_id for candidate in item.path),
            )
        )
        return tuple(output)

    def _select(self, root: _Node) -> _Node:
        current = root
        while current.children:
            unvisited = [child for child in current.children if child.visits == 0 and not child.terminal]
            if unvisited:
                return sorted(unvisited, key=lambda item: (-item.prior, item.candidate.candidate_id))[0]
            eligible = [child for child in current.children if not child.terminal]
            if not eligible:
                return current
            parent_visits = max(1, current.visits)
            current = max(
                eligible,
                key=lambda item: (
                    self._ucb(item, parent_visits),
                    item.prior,
                    _reverse_lex_key(item.candidate.candidate_id),
                ),
            )
            if current.depth >= self._ledger.budget.max_depth or not current.expanded:
                return current
        return current

    def _ucb(self, node: _Node, parent_visits: int) -> float:
        if node.visits == 0:
            return math.inf
        exploration = self._ledger.budget.exploration * math.sqrt(
            math.log(max(1, parent_visits)) / node.visits
        )
        return node.mean_reward + exploration + 0.15 * node.prior

    def _ensure_state(self, node: _Node, context: Mapping[str, Any]) -> bool:
        if node.state_ready:
            return True
        if node.candidate is None or node.parent is None:
            return False
        if not self._ensure_state(node.parent, context):
            node.terminal = True
            return False
        if not self._ledger.reserve_transition():
            node.terminal = True
            return False
        try:
            node.state = self._transition.transition(
                state=node.parent.state,
                candidate=node.candidate,
                path=node.tree_path(),
                context=context,
            )
        except Exception as exc:
            node.terminal = True
            self._trace.emit(
                "l3",
                "transition_failed",
                candidate_id=node.candidate.candidate_id,
                error_type=type(exc).__name__,
            )
            return False
        node.state_ready = True
        return True

    def _expand_once(self, node: _Node, context: Mapping[str, Any]) -> _Node:
        if node.depth >= self._ledger.budget.max_depth or node.terminal:
            node.expanded = True
            return node
        if not node.expanded:
            if not self._ledger.reserve_expansion():
                node.terminal = True
                return node
            try:
                raw = self._expander.expand(
                    state=node.state,
                    path=node.tree_path(),
                    depth=node.depth,
                    limit=self._ledger.budget.branching_factor,
                    context=context,
                )
            except Exception as exc:
                node.terminal = True
                self._trace.emit(
                    "l3",
                    "expansion_failed",
                    depth=node.depth,
                    error_type=type(exc).__name__,
                )
                return node
            candidates, rejected = stable_candidates(raw)
            seen = {item.candidate_id for item in node.tree_path()}
            candidates = tuple(item for item in candidates if item.candidate_id not in seen)
            candidates = self._ledger.take_candidates(candidates[: self._ledger.budget.branching_factor])
            for candidate in candidates:
                node.children.append(
                    _Node(
                        candidate=candidate,
                        state=None,
                        parent=node,
                        depth=node.depth + 1,
                        prior=candidate.prior_score,
                    )
                )
            node.expanded = True
            self._trace.emit(
                "l3",
                "node_expanded",
                depth=node.depth,
                parent_id=node.candidate.candidate_id if node.candidate else "root",
                candidate_ids=[item.candidate_id for item in candidates],
                rejected=list(rejected),
            )
        unvisited = [child for child in node.children if child.visits == 0 and not child.terminal]
        if not unvisited:
            return node
        return sorted(unvisited, key=lambda item: (-item.prior, item.candidate.candidate_id))[0]

    def _rollout(
        self,
        node: _Node,
        initial_state: Any,
        context: Mapping[str, Any],
    ) -> tuple[float, tuple[SearchCandidate, ...]]:
        state = node.state
        path = node.tree_path()
        depth = node.depth
        while depth < self._ledger.budget.max_depth:
            if not self._ledger.reserve_expansion():
                break
            try:
                raw = self._expander.expand(
                    state=state,
                    path=path,
                    depth=depth,
                    limit=self._ledger.budget.branching_factor,
                    context=context,
                )
            except Exception as exc:
                self._trace.emit(
                    "l3",
                    "rollout_expansion_failed",
                    depth=depth,
                    error_type=type(exc).__name__,
                )
                break
            candidates, _rejected = stable_candidates(raw)
            seen = {item.candidate_id for item in path}
            candidates = tuple(item for item in candidates if item.candidate_id not in seen)
            candidates = self._ledger.take_candidates(candidates[: self._ledger.budget.branching_factor])
            if not candidates:
                break
            candidate = candidates[0]
            if not self._ledger.reserve_transition():
                break
            next_path = path + (candidate,)
            try:
                state = self._transition.transition(
                    state=state,
                    candidate=candidate,
                    path=next_path,
                    context=context,
                )
            except Exception as exc:
                self._trace.emit(
                    "l3",
                    "rollout_transition_failed",
                    candidate_id=candidate.candidate_id,
                    error_type=type(exc).__name__,
                )
                break
            path = next_path
            depth += 1
        try:
            reward = float(
                self._reward.evaluate(
                    initial_state=initial_state,
                    state=state,
                    path=path,
                    context=context,
                )
            )
            if not math.isfinite(reward):
                raise ValueError("reward is not finite")
        except Exception as exc:
            self._trace.emit("l3", "reward_failed", error_type=type(exc).__name__)
            reward = -1.0
        return reward, path

    @staticmethod
    def _backpropagate(node: _Node, reward: float, path: tuple[SearchCandidate, ...]) -> None:
        current: _Node | None = node
        path_key = tuple(item.candidate_id for item in path)
        while current is not None:
            current.visits += 1
            current.total_reward += reward
            current_key = tuple(item.candidate_id for item in current.best_path)
            if reward > current.best_reward or (reward == current.best_reward and path_key < current_key):
                current.best_reward = reward
                current.best_path = path
            current = current.parent


def _reverse_lex_key(value: str) -> tuple[int, ...]:
    return tuple(-ord(char) for char in value)


def _rounded(value: float) -> float:
    return round(float(value), 10)
