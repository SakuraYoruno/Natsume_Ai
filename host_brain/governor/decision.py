"""Governor intent decision entrypoint with intent+gate+audit contracts."""

from __future__ import annotations

from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import new_request_id, new_turn_id, now_ms
from host_brain.core.metabolism import (
    build_governance_snapshot,
    governance_snapshot_ref,
    hard_state_ref,
    normalize_hard_state,
)
from host_brain.governor.policy_cache import GovernorPolicyCache
from host_brain.governor.rules import evaluate_governor_gates
from host_brain.governor.route_token import CommittedRouteDecision, RouteToken, compile_route_decision
from host_brain.governor.schemas import (
    DECISION_APPROVE,
    EXPIRED_INTENT,
    INTENT_PARSE_FAILED,
    REPLAY_DETECTED,
    SEQ_INVALID,
    lenient_parse_intent,
    strict_validate_intent,
    validate_governor_output,
)
from host_brain.governor.tier_policy import ApprovalTier, GovernorTierPolicy
from host_brain.governor.tokens import TokenService


class IntentDecisionEngine:
    """Governor engine for intent validation and R1-R7 gate evaluation."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        token_service: TokenService | None = None,
        expiry_window_ms: int = 5 * 60 * 1000,
        # M42：分层治理注入点（可选，向后兼容）
        registry: Any | None = None,
        policy_cache: GovernorPolicyCache | None = None,
    ) -> None:
        self.ledger = ledger or SQLiteLedger()
        self.token_service = token_service
        self.expiry_window_ms = expiry_window_ms
        # M42：分层治理组件（可选注入，未注入时保持现有行为）
        self._registry = registry
        self._policy_cache = policy_cache
        self._tier_policy = GovernorTierPolicy() if registry is not None else None

    def evaluate_intent(
        self,
        raw_intent: str | dict[str, Any],
        *,
        request_id: str | None = None,
        thread_id: str | None = None,
        turn_id: str | None = None,
        current_ms: int | None = None,
        proposer_node_id: str = "core_reasoner_primary",
        model_snapshot: str = "unknown_model",
        prompt_profile_id: str = "default_profile",
        tool_visibility_hash: str = "",
        governance_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        req_id = request_id or new_request_id()
        now = current_ms if current_ms is not None else now_ms()
        turn = turn_id or new_turn_id()
        snapshot_refs = _derive_snapshot_refs(governance_snapshot)

        parse = lenient_parse_intent(raw_intent)
        normalized = parse.normalized_json or {}
        session_id = normalized.get("session_id")
        seq = normalized.get("seq")
        intent_id = normalized.get("id")
        nonce = normalized.get("nonce")

        # M34-E：状态字段三分法注释（ModelPlane.md §10）
        # proposed_*：LLM 提案，只能由 LLM 节点写入
        # observed_*：观测值，只能由 Host 观测层写入
        # committed_*：Host 已提交决策，只能由 Host Governor 写入
        proposal_row = {
            "request_id": req_id,
            "thread_id": thread_id,
            "turn_id": turn,
            "intent_id": intent_id,
            "session_id": session_id,
            "seq": seq,
            "ts_ms": normalized.get("ts_ms"),
            "nonce": nonce,
            "parse_stage": parse.parse_stage,
            "raw_text": raw_intent if isinstance(raw_intent, str) else None,
            "normalized_json": parse.normalized_json,
            "proposer_node_id": proposer_node_id,
            "model_snapshot": model_snapshot,
            "prompt_profile_id": prompt_profile_id,
            "tool_visibility_hash": tool_visibility_hash,
            # proposed_intent_draft_json：LLM 提案字段（proposed_* 类）
            "proposed_intent_draft_json": parse.normalized_json,
            "lint_result": parse.lint_result,
        }

        if parse.reason_code == INTENT_PARSE_FAILED:
            self.ledger.log_intent(proposal_row)
            return self._reject(
                req_id,
                intent_id,
                INTENT_PARSE_FAILED,
                stage="S_INTENT_PARSE",
                turn_id=turn,
                thread_id=thread_id,
                rule_trace=[{"gate_id": "INTENT_PARSE", "hit": True, "reason_hint": INTENT_PARSE_FAILED}],
                ts_ms=now,
                snapshot_refs=snapshot_refs,
            )

        # M42：若注入了 registry，则将所有已注册 Skill 名称作为动态 action 白名单
        extra_action_allowlist: set[str] | None = None
        if self._registry is not None:
            extra_action_allowlist = set(self._registry.list_skill_names())

        envelope, reason_code, lint = strict_validate_intent(normalized, extra_action_allowlist)
        proposal_row["lint_result"] = lint
        self.ledger.log_intent(proposal_row)
        if reason_code is not None:
            return self._reject(
                req_id,
                intent_id,
                reason_code,
                stage="S_INTENT_VALIDATE",
                turn_id=turn,
                thread_id=thread_id,
                rule_trace=[{"gate_id": "INTENT_VALIDATE", "hit": True, "reason_hint": reason_code}],
                ts_ms=now,
                snapshot_refs=snapshot_refs,
            )

        assert envelope is not None

        if self.ledger.has_replay(
            intent_id=envelope.id,
            session_id=envelope.session_id,
            nonce=envelope.nonce,
        ):
            return self._reject(
                req_id,
                envelope.id,
                REPLAY_DETECTED,
                stage="S_INTENT_REPLAY",
                turn_id=turn,
                thread_id=thread_id,
                rule_trace=[{"gate_id": "INTENT_REPLAY", "hit": True, "reason_hint": REPLAY_DETECTED}],
                ts_ms=now,
                snapshot_refs=snapshot_refs,
            )

        if self.ledger.is_seq_invalid(session_id=envelope.session_id, seq=envelope.seq):
            return self._reject(
                req_id,
                envelope.id,
                SEQ_INVALID,
                stage="S_INTENT_SEQ",
                turn_id=turn,
                thread_id=thread_id,
                rule_trace=[{"gate_id": "INTENT_SEQ", "hit": True, "reason_hint": SEQ_INVALID}],
                ts_ms=now,
                snapshot_refs=snapshot_refs,
            )

        if _is_expired(ts_ms=envelope.ts_ms, now_ms_value=now, ttl_ms=self.expiry_window_ms):
            return self._reject(
                req_id,
                envelope.id,
                EXPIRED_INTENT,
                stage="S_INTENT_EXPIRED",
                turn_id=turn,
                thread_id=thread_id,
                rule_trace=[{"gate_id": "INTENT_EXPIRY", "hit": True, "reason_hint": EXPIRED_INTENT}],
                ts_ms=now,
                snapshot_refs=snapshot_refs,
            )

        gates_input = _build_gate_input(governance_snapshot, target_scope=envelope.target)
        snapshot_refs = {
            "governance_snapshot": governance_snapshot_ref(gates_input),
            "hard_state": hard_state_ref(_hard_state_from_snapshot(governance_snapshot)),
        }
        gate_eval = evaluate_governor_gates(gates_input)
        if gate_eval["decision"] == "REJECT":
            return self._reject(
                req_id,
                envelope.id,
                gate_eval["reason_code"],
                stage="S_GOV_GATES",
                turn_id=turn,
                thread_id=thread_id,
                rule_trace=gate_eval["rule_trace"],
                ts_ms=now,
                snapshot_refs=snapshot_refs,
            )

        # M27-H：情感状态门控（OPT-32）：critical 状态下拒绝 TOOL_CALL/SKILL_EXEC 类意图
        mood_state = _extract_mood_state(governance_snapshot)
        if mood_state == "critical" and envelope.action in ("TOOL_CALL", "SKILL_EXEC"):
            return self._reject(
                req_id,
                envelope.id,
                "EMOTION_GATE_BLOCKED",
                stage="S_GOV_EMOTION_GATE",
                turn_id=turn,
                thread_id=thread_id,
                rule_trace=[{
                    "gate_id": "R8_EMOTION_GATE",
                    "hit": True,
                    "reason_hint": "EMOTION_GATE_BLOCKED",
                    "threshold_summary": {"blocked_mood_states": ["critical"]},
                    "snapshot_summary": {"mood_state": mood_state, "action": envelope.action},
                }],
                ts_ms=now,
                snapshot_refs=snapshot_refs,
            )

        self.ledger.mark_intent_seen(
            intent_id=envelope.id,
            session_id=envelope.session_id,
            nonce=envelope.nonce,
        )
        self.ledger.update_seq(session_id=envelope.session_id, seq=envelope.seq)

        approval_token_ref = f"approval::{envelope.id}"
        if self.token_service is not None:
            approval_token_ref = self.token_service.issue_approval_token(
                intent_id=envelope.id,
                skill_name=envelope.action,
                session_id=envelope.session_id,
                seq=envelope.seq,
                now_ts_ms=now,
            )

        row = {
            "request_id": req_id,
            "thread_id": thread_id,
            "turn_id": turn,
            "intent_id": envelope.id,
            "decision": DECISION_APPROVE,
            "reason_code": None,
            "rule_trace": gate_eval["rule_trace"],
            "tokens": {"approval_token_ref": approval_token_ref},
            "snapshot_refs": snapshot_refs,
            "stage": "S_INTENT_OK",
            "ts_ms": now,
        }
        # M42：分层治理路由（若注入了 registry，则按 risk_level 选择审批通道）
        approval_tier = "full"  # 默认全量审批（向后兼容）
        if self._registry is not None and self._tier_policy is not None:
            skill_name = envelope.action
            meta = self._registry.get(skill_name)
            if meta is not None:
                tier_enum = self._tier_policy.get_approval_tier(meta)
                approval_tier = tier_enum.value

                # CACHE 通道：查询 Policy Cache
                if tier_enum == ApprovalTier.CACHE and self._policy_cache is not None:
                    trust_level = int(gates_input.get("trust_level", 50))
                    cached = self._policy_cache.get(skill_name, trust_level)
                    if cached is True:
                        # 缓存命中，直接放行
                        committed = compile_route_decision(
                            approved=True,
                            reason_code="APPROVE_CACHED",
                            committed_at_ms=now,
                            proposer_node_id=proposer_node_id,
                            tier="cache",
                        )
                        row["committed_route_token_ref"] = committed.token.value
                        row["committed_route_token"] = {
                            "token": committed.token.value,
                            "reason_code": committed.reason_code,
                            "committed_at_ms": committed.committed_at_ms,
                            "proposer_node_id": committed.proposer_node_id,
                            "approval_tier": approval_tier,
                        }
                        return self._finalize_governor_row(row)
                    elif cached is None:
                        # 缓存未命中，规则引擎审批后写入缓存
                        self._policy_cache.put(skill_name, trust_level, True)
                        approval_tier = "rule"  # 首次审批走 RULE 通道

                # AIRLOCK 通道：强制返回 GOV_AIRLOCK_REVIEW
                elif tier_enum == ApprovalTier.AIRLOCK:
                    committed = compile_route_decision(
                        approved=True,
                        reason_code="AIRLOCK_REQUIRED",
                        committed_at_ms=now,
                        proposer_node_id=proposer_node_id,
                        tier="airlock",
                    )
                    row["committed_route_token_ref"] = committed.token.value
                    row["committed_route_token"] = {
                        "token": committed.token.value,
                        "reason_code": committed.reason_code,
                        "committed_at_ms": committed.committed_at_ms,
                        "proposer_node_id": committed.proposer_node_id,
                        "approval_tier": approval_tier,
                    }
                    return self._finalize_governor_row(row)

        # M34-D：编译 committed_route_token（按 approval_tier 选择令牌）
        committed = compile_route_decision(
            approved=True,
            reason_code="APPROVE",
            committed_at_ms=now,
            proposer_node_id=proposer_node_id,
            tier=approval_tier,
        )
        row["committed_route_token_ref"] = committed.token.value
        row["committed_route_token"] = {
            "token": committed.token.value,
            "reason_code": committed.reason_code,
            "committed_at_ms": committed.committed_at_ms,
            "proposer_node_id": committed.proposer_node_id,
            "approval_tier": approval_tier,
        }
        return self._finalize_governor_row(row)

    def _reject(
        self,
        request_id: str,
        intent_id: str | None,
        reason_code: str,
        *,
        stage: str,
        thread_id: str | None,
        turn_id: str,
        rule_trace: list[dict[str, Any]],
        ts_ms: int,
        snapshot_refs: dict[str, Any] | None = None,
        proposer_node_id: str = "governor",
    ) -> dict[str, Any]:
        # M34-D：编译 committed_route_token（REJECT 路径）
        committed = compile_route_decision(
            approved=False,
            reason_code=reason_code,
            committed_at_ms=ts_ms,
            proposer_node_id=proposer_node_id,
        )
        row = {
            "request_id": request_id,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "intent_id": intent_id,
            "decision": "REJECT",
            "reason_code": reason_code,
            "rule_trace": rule_trace,
            "snapshot_refs": snapshot_refs or {"governance_snapshot": "runtime"},
            "stage": stage,
            "ts_ms": ts_ms,
            "committed_route_token_ref": committed.token.value,
            "committed_route_token": {
                "token": committed.token.value,
                "reason_code": committed.reason_code,
                "committed_at_ms": committed.committed_at_ms,
                "proposer_node_id": committed.proposer_node_id,
            },
        }
        return self._finalize_governor_row(row)

    def _finalize_governor_row(self, row: dict[str, Any]) -> dict[str, Any]:
        validation = validate_governor_output(row)
        if not validation.valid:
            row = {
                "request_id": row.get("request_id"),
                "thread_id": row.get("thread_id"),
                "turn_id": row.get("turn_id"),
                "intent_id": row.get("intent_id"),
                "decision": "REJECT",
                "reason_code": validation.reason_code,
                "rule_trace": [
                    {
                        "gate_id": "GOVERNOR_OUTPUT_CONTRACT",
                        "hit": True,
                        "reason_hint": validation.reason_code,
                        "violations": validation.violations,
                        "threshold_summary": {"contract": "strict"},
                        "snapshot_summary": {"source": "governor_output"},
                    }
                ],
                "snapshot_refs": row.get("snapshot_refs") or {"governance_snapshot": "runtime"},
                "stage": "S_GOV_OUTPUT_INVALID",
                "ts_ms": now_ms(),
            }
        self.ledger.log_governor(row)
        return row


def _is_expired(*, ts_ms: int, now_ms_value: int, ttl_ms: int) -> bool:
    if ts_ms > now_ms_value + 60_000:
        return True
    return now_ms_value - ts_ms > ttl_ms


def _hard_state_from_snapshot(governance_snapshot: dict[str, Any] | None) -> dict:
    if isinstance(governance_snapshot, dict):
        raw_state = governance_snapshot.get("hard_state")
        if isinstance(raw_state, dict):
            return normalize_hard_state(raw_state)
        return normalize_hard_state(governance_snapshot)
    return normalize_hard_state({})


def _build_gate_input(
    governance_snapshot: dict[str, Any] | None,
    *,
    target_scope: str,
) -> dict[str, Any]:
    source = dict(governance_snapshot or {})
    state = _hard_state_from_snapshot(governance_snapshot)
    gates_input = build_governance_snapshot(state, overrides=source)
    if "scope" not in source:
        gates_input["scope"] = str(target_scope).strip().lower() or "host"
    return gates_input


def _derive_snapshot_refs(governance_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(governance_snapshot, dict):
        return {"governance_snapshot": "runtime"}
    state = _hard_state_from_snapshot(governance_snapshot)
    gates_input = _build_gate_input(governance_snapshot, target_scope="host")
    return {
        "governance_snapshot": governance_snapshot_ref(gates_input),
        "hard_state": hard_state_ref(state),
    }


def _extract_mood_state(governance_snapshot: dict[str, Any] | None) -> str:
    """从 governance_snapshot 中提取 mood_state（M27-H OPT-32）。"""
    if not isinstance(governance_snapshot, dict):
        return "steady"
    # 优先从 hard_state_summary 读取
    hard_state_summary = governance_snapshot.get("hard_state_summary")
    if isinstance(hard_state_summary, dict):
        mood = str(hard_state_summary.get("mood_state", "steady")).strip().lower()
        if mood:
            return mood
    # 其次从顶层读取
    mood = str(governance_snapshot.get("mood_state", "steady")).strip().lower()
    return mood if mood else "steady"
