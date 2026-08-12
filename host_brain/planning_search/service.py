from __future__ import annotations

import time
from typing import Any, Callable, Mapping

from .algorithms import (
    BudgetLedger,
    DeterministicMCTS,
    TraceRecorder,
    rank_candidates,
    stable_candidates,
)
from .contracts import (
    CandidateScore,
    MCTSMode,
    RankedCandidate,
    SearchCandidate,
    SearchRequest,
    SearchResult,
    SearchStatus,
    VirtualStateSnapshot,
)
from .ports import (
    CandidateExpansionPort,
    CognitiveRankingPort,
    MCTSConditionPort,
    RewardModelPort,
    VirtualTransitionPort,
)


class PlanningSearchService:
    """Domain-neutral L1/L2/L3 planning search.

    The service only manipulates JSON-safe plan descriptors and virtual state.
    It has no execution dependency and no API through which a selected plan can
    be applied to a real system.
    """

    def __init__(
        self,
        *,
        expander: CandidateExpansionPort,
        cognitive_ranker: CognitiveRankingPort | None = None,
        transition: VirtualTransitionPort | None = None,
        reward_model: RewardModelPort | None = None,
        mcts_condition: MCTSConditionPort | None = None,
        clock_ms: Callable[[], float] | None = None,
    ) -> None:
        self._expander = expander
        self._cognitive_ranker = cognitive_ranker
        self._transition = transition
        self._reward_model = reward_model
        self._mcts_condition = mcts_condition
        self._clock_ms = clock_ms or _monotonic_ms

    def search(self, request: SearchRequest) -> SearchResult:
        # 每次搜索新建预算账本和只追加轨迹；它们描述这一次虚拟推演的资源消耗，
        # 不与真实任务预算或外部执行状态共享，因此结果天然不具备执行授权。
        ledger = BudgetLedger(request.budget, self._clock_ms)
        trace = TraceRecorder()
        state_schema, state_digest = _virtual_state_identity(request.state_snapshot)
        started_details: dict[str, Any] = {
            "request_id": request.request_id,
            "budget": request.budget.to_dict(),
            "mcts_mode": request.mcts_mode.value,
        }
        if state_schema:
            started_details["virtual_state_schema"] = state_schema
            started_details["virtual_state_digest"] = state_digest
        trace.emit("service", "search_started", **started_details)

        if ledger.deadline_reached():
            # 截止前尚未展开任何候选时直接返回显式终态，避免为了得到“一个答案”
            # 而在超时后继续调用扩展器或把空结果误标成失败。
            trace.emit("service", "search_stopped", reason="deadline_reached")
            return self._result(
                request=request,
                status=SearchStatus.DEADLINE_REACHED,
                stop_reason="deadline_reached",
                ranked=(),
                trace=trace,
                ledger=ledger,
                mcts_used=False,
                virtual_state_schema=state_schema,
                virtual_state_digest=state_digest,
                state_snapshot=request.state_snapshot,
            )

        ranked, failure = self._l1_l2(request, ledger, trace)
        if not ranked:
            reason = failure or ledger.stop_reason or "no_candidates"
            if reason == "deadline_reached":
                status = SearchStatus.DEADLINE_REACHED
            elif reason.endswith("budget_exhausted"):
                status = SearchStatus.BUDGET_EXHAUSTED
            elif failure:
                status = SearchStatus.FAILED
            else:
                status = SearchStatus.EMPTY
            trace.emit("service", "search_stopped", reason=reason)
            return self._result(
                request=request,
                status=status,
                stop_reason=reason,
                ranked=(),
                trace=trace,
                ledger=ledger,
                mcts_used=False,
                virtual_state_schema=state_schema,
                virtual_state_digest=state_digest,
                state_snapshot=request.state_snapshot,
            )

        mcts_used = False
        should_run, mcts_reason = self._should_run_mcts(request, ranked, ledger, trace)
        if should_run:
            # L3 仅消费已排序候选及虚拟 transition/reward 端口；即使选择了路径，
            # SearchResult 仍明确标记 side_effects_executed=False。
            mcts = DeterministicMCTS(
                expander=self._expander,
                transition=self._transition,  # type: ignore[arg-type]
                reward_model=self._reward_model,  # type: ignore[arg-type]
                ledger=ledger,
                trace=trace,
            )
            searched = mcts.search(
                initial_state=request.initial_state,
                ranked=ranked,
                context=request.context,
            )
            if searched:
                ranked = searched
                mcts_used = True
        else:
            trace.emit("l3", "mcts_skipped", reason=mcts_reason)

        selected = ranked[0]
        # rank_candidates 已提供稳定的并列规则；服务层只选首项，不引入时间、
        # 随机数或容器顺序作为第二套隐式决策逻辑。
        stop_reason = ledger.stop_reason or "selected"
        trace.emit(
            "service",
            "search_completed",
            selected_candidate_id=selected.candidate.candidate_id,
            selected_path=[item.candidate_id for item in selected.path],
            stop_reason=stop_reason,
            mcts_used=mcts_used,
            counters=ledger.counters().to_dict(),
        )
        return self._result(
            request=request,
            status=SearchStatus.SELECTED,
            stop_reason=stop_reason,
            ranked=ranked,
            trace=trace,
            ledger=ledger,
            mcts_used=mcts_used,
            virtual_state_schema=state_schema,
            virtual_state_digest=state_digest,
            state_snapshot=request.state_snapshot,
        )

    def _l1_l2(
        self,
        request: SearchRequest,
        ledger: BudgetLedger,
        trace: TraceRecorder,
    ) -> tuple[tuple[RankedCandidate, ...], str]:
        if not ledger.reserve_expansion():
            return (), ledger.stop_reason
        try:
            raw = self._expander.expand(
                state=request.initial_state,
                path=(),
                depth=0,
                limit=request.budget.max_candidates,
                context=request.context,
            )
        except Exception as exc:
            trace.emit("l1", "expansion_failed", error_type=type(exc).__name__)
            return (), "l1_expansion_failed"

        candidates, rejected = stable_candidates(raw or ())
        candidates = ledger.take_candidates(candidates)
        trace.emit(
            "l1",
            "candidates_expanded",
            candidate_ids=[item.candidate_id for item in candidates],
            rejected=list(rejected),
        )
        if not candidates:
            return (), ledger.stop_reason

        cognitive_scores: dict[str, float] = {}
        if self._cognitive_ranker is not None and not ledger.deadline_reached():
            try:
                raw_scores = self._cognitive_ranker.rank(
                    state=request.initial_state,
                    candidates=candidates,
                    context=request.context,
                )
                candidate_ids = {candidate.candidate_id for candidate in candidates}
                ignored = 0
                for value in raw_scores or ():
                    if not isinstance(value, CandidateScore) or value.candidate_id not in candidate_ids:
                        ignored += 1
                        continue
                    previous = cognitive_scores.get(value.candidate_id)
                    if previous is None or value.score > previous:
                        cognitive_scores[value.candidate_id] = value.score
                trace.emit(
                    "l2",
                    "candidates_ranked",
                    scores={key: _rounded(value) for key, value in sorted(cognitive_scores.items())},
                    ignored=ignored,
                )
        except Exception as exc:
            # 认知排序是可选增强。端口异常时保留 L1 候选及可审计失败轨迹，
            # 不把模型/插件瞬时故障升级为“没有可规划方案”。
            trace.emit("l2", "ranking_failed", error_type=type(exc).__name__, fallback="l1_prior")
        elif self._cognitive_ranker is None:
            trace.emit("l2", "ranking_skipped", reason="ranker_not_configured")
        else:
            trace.emit("l2", "ranking_skipped", reason="deadline_reached")

        ranked = rank_candidates(candidates, cognitive_scores)
        trace.emit(
            "l2",
            "ranking_finalized",
            order=[item.candidate.candidate_id for item in ranked],
            scores=[_rounded(item.score) for item in ranked],
        )
        return ranked, ""

    def _should_run_mcts(
        self,
        request: SearchRequest,
        ranked: tuple[RankedCandidate, ...],
        ledger: BudgetLedger,
        trace: TraceRecorder,
    ) -> tuple[bool, str]:
        if request.mcts_mode is MCTSMode.NEVER:
            return False, "disabled_by_request"
        if self._transition is None or self._reward_model is None:
            # 缺少任一模拟端口就不能凭启发式分数伪造前瞻结果；保持 L1/L2 排名，
            # 并把未运行原因写入 trace 供调用方决定是否重试或降级展示。
            return False, "simulation_ports_not_configured"
        if request.budget.max_rollouts <= 0 or request.budget.max_depth <= 0:
            return False, "mcts_budget_disabled"
        if ledger.deadline_reached():
            return False, "deadline_reached"
        if request.mcts_mode is MCTSMode.ALWAYS:
            trace.emit("l3", "mcts_condition", decision=True, reason="required_by_request")
            return True, "required_by_request"

        candidates = tuple(item.candidate for item in ranked)
        scores = {item.candidate.candidate_id: item.score for item in ranked}
        if self._mcts_condition is not None:
            try:
                decision = bool(
                    self._mcts_condition.should_search(
                        candidates=candidates,
                        scores=scores,
                        context=request.context,
                    )
                )
                trace.emit("l3", "mcts_condition", decision=decision, reason="condition_port")
                return decision, "condition_port_false" if not decision else "condition_port"
            except Exception as exc:
                trace.emit("l3", "mcts_condition_failed", error_type=type(exc).__name__)
                return False, "condition_port_failed"

        decision = self._default_mcts_condition(ranked, request.budget.mcts_score_gap)
        trace.emit("l3", "mcts_condition", decision=decision, reason="score_ambiguity")
        return decision, "score_gap_clear" if not decision else "score_ambiguity"

    @staticmethod
    def _default_mcts_condition(ranked: tuple[RankedCandidate, ...], score_gap: float) -> bool:
        if any(
            bool(item.candidate.payload.get("requires_lookahead"))
            or "requires_lookahead" in item.candidate.labels
            for item in ranked
        ):
            return True
        if len(ranked) < 2:
            return False
        return abs(ranked[0].score - ranked[1].score) <= score_gap

    @staticmethod
    def _result(
        *,
        request: SearchRequest,
        status: SearchStatus,
        stop_reason: str,
        ranked: tuple[RankedCandidate, ...],
        trace: TraceRecorder,
        ledger: BudgetLedger,
        mcts_used: bool,
        virtual_state_schema: str,
        virtual_state_digest: str,
        state_snapshot: VirtualStateSnapshot | None,
    ) -> SearchResult:
        selected = ranked[0] if ranked else None
        return SearchResult(
            request_id=request.request_id,
            status=status,
            stop_reason=stop_reason,
            selected_candidate=selected.candidate if selected else None,
            selected_path=selected.path if selected else (),
            ranked=ranked,
            trace=trace.snapshot(),
            counters=ledger.counters(),
            mcts_used=mcts_used,
            side_effects_executed=False,
            virtual_state_schema=virtual_state_schema,
            virtual_state_digest=virtual_state_digest,
            state_snapshot=state_snapshot,
        )


def _monotonic_ms() -> float:
    return time.monotonic() * 1000.0


def _rounded(value: float) -> float:
    return round(float(value), 10)


def _virtual_state_identity(state: VirtualStateSnapshot | None) -> tuple[str, str]:
    """Extract only canonical state provenance; generic legacy state is allowed."""

    if isinstance(state, VirtualStateSnapshot):
        return state.schema_version, state.state_digest
    return "", ""
