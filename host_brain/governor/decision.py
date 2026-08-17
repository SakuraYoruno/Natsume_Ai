"""Governor 意图裁决入口：串联意图、门控、令牌与审计契约。

该层将不可信请求解析为受限意图，在写入可追溯提案后进行防重放、序号、时效和治理门
检查；只有 Host 决定会被编译为 committed 路由令牌，模型或入口不能直接放行执行边。
"""

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
from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.governor.capability_policy import CapabilityGovernorPolicyAdapter
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
    """执行意图验证与 R1–R7 门控的 Governor 状态机。

    账本负责把提案和最终裁决固定为审计证据，令牌服务负责短时的一次性执行授权；二者均
    可注入，便于宿主选择耐久实现。决策引擎本身不调用工具，也不信任请求携带的风险等级。
    """

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        token_service: TokenService | None = None,
        expiry_window_ms: int = 5 * 60 * 1000,
        # M42：分层治理注入点（可选，向后兼容）
        registry: Any | None = None,
        policy_cache: GovernorPolicyCache | None = None,
        capability_scorecard_provider: Any | None = None,
        capability_maturity_enabled: bool | None = None,
    ) -> None:
        self.ledger = ledger if ledger is not None else SQLiteLedger(":memory:")
        self.token_service = token_service
        self.expiry_window_ms = expiry_window_ms
        # M42：分层治理组件（可选注入，未注入时保持现有行为）
        self._registry = registry
        self._policy_cache = policy_cache
        self._tier_policy = (
            GovernorTierPolicy(
                CapabilityGovernorPolicyAdapter(
                    enabled=capability_maturity_enabled,
                    scorecard_provider=capability_scorecard_provider,
                )
            )
            if registry is not None
            else None
        )

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
        registry_override: Any | None = None,
    ) -> dict[str, Any]:
        """解析并裁决一次意图，返回仅由 Governor 提交的可审计输出。

        所有拒绝路径同样会生成 committed 令牌，令图运行时能用单一验证接口安全消费。可选
        ``registry_override`` 仅替换本次能力元数据查询，不替换账本、令牌或治理策略的权威。
        """
        # HTTP/应用适配器可提供不具备执行能力的本地能力目录。相同的 Governor、账本、
        # 令牌服务和成熟度策略仍拥有裁决权；只替换此调用的元数据查询，既避免把
        # Tool/Provider 目录混入 Skill 执行注册表，也不复制出第二套 Governor 状态机。
        registry = registry_override if registry_override is not None else self._registry
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
            "ts_ms": _normalize_intent_log_ts_ms(normalized.get("ts_ms"), now_ms_value=now),
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
        if registry is not None:
            extra_action_allowlist = set(registry.list_skill_names())

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

        # P2-A 快速通道：narrative_only + policy_cache 命中时跳过完整 gate 评估
        _p2a_cache_miss = False  # 标记首次未命中，gate 通过后再写入缓存
        _p2a_trust: int = 0
        if (
            self._policy_cache is not None
            and str(envelope.action or "").strip() == "narrative_only"
            and registry is None  # registry 注入时由分层治理处理，避免重复
        ):
            _p2a_trust = int(gates_input.get("trust_level", 50))
            _cached = self._policy_cache.get("narrative_only", _p2a_trust)
            if _cached is True:
                return self._approve_fast(
                    req_id=req_id,
                    envelope=envelope,
                    now=now,
                    turn=turn,
                    thread_id=thread_id,
                    snapshot_refs=snapshot_refs,
                    proposer_node_id=proposer_node_id,
                    governance_snapshot=governance_snapshot,
                )
            elif _cached is None:
                # 首次未命中：本次走正常 gate 评估路径，gate 通过后再写入缓存
                # 注意：不能在 gate 评估前写入 True，否则 gate 失败时缓存会错误放行后续请求
                _p2a_cache_miss = True

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

        # P2-A：所有 gate 评估通过后，将首次未命中的 narrative_only 写入缓存
        # 必须在此处（gate 全部通过后）写入，防止 gate 失败时缓存错误放行后续请求
        if _p2a_cache_miss and self._policy_cache is not None:
            self._policy_cache.put("narrative_only", _p2a_trust, True)

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
        approved_action_binding = build_governed_action_binding(
            intent_envelope=envelope,
            approval_token_ref=approval_token_ref,
            risk_level=_resolve_intent_risk_level(registry, envelope.action),
            issued_at_ms=now,
            expires_at_ms=now + _approval_binding_ttl_ms(self),
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
            "approved_action_binding": approved_action_binding,
            "snapshot_refs": snapshot_refs,
            "stage": "S_INTENT_OK",
            "ts_ms": now,
        }
        # M42：分层治理路由（若注入了 registry，则按 risk_level 选择审批通道）
        approval_tier = "full"  # 默认全量审批（向后兼容）
        if registry is not None and self._tier_policy is not None:
            skill_name = envelope.action
            meta = registry.get(skill_name)
            if meta is not None:
                tier_enum = self._tier_policy.get_approval_tier(meta)
                approval_tier = tier_enum.value

                # CACHE 通道：查询 Policy Cache
                if tier_enum == ApprovalTier.CACHE and self._policy_cache is not None:
                    trust_level = int(gates_input.get("trust_level", 50))
                    cached = self._policy_cache.get(skill_name, trust_level)
                    if cached is True:
                        # 缓存命中，直接放行
                        # 修复：CACHE 命中时仍需写入 replay_guard 和 session_seq_guard，
                        # 防止相同 intent_id/nonce 被重放（防重放约束不可绕过）
                        self.ledger.mark_intent_seen(
                            intent_id=envelope.id,
                            session_id=envelope.session_id,
                            nonce=envelope.nonce,
                        )
                        self.ledger.update_seq(session_id=envelope.session_id, seq=envelope.seq)
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
                    # 审核尚未完成时绝不暴露可执行批准令牌。此引用被刻意排除在
                    # TokenService 之外，使遗留 broker 也会在验证时失败关闭。
                    airlock_token_ref = f"airlock-review::{envelope.id}"
                    row["tokens"] = {"approval_token_ref": airlock_token_ref}
                    row["approved_action_binding"] = build_governed_action_binding(
                        intent_envelope=envelope,
                        approval_token_ref=airlock_token_ref,
                        risk_level=_resolve_intent_risk_level(registry, envelope.action),
                        issued_at_ms=now,
                        expires_at_ms=now + _approval_binding_ttl_ms(self),
                    )
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
        """把任一拒绝归一为带失败路由令牌的账本记录。"""
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

    def _approve_fast(
        self,
        *,
        req_id: str,
        envelope: Any,
        now: int,
        turn: str,
        thread_id: str | None,
        snapshot_refs: dict[str, Any],
        proposer_node_id: str = "governor",
        governance_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """P2-A 快速审批通道：policy cache 命中时跳过完整 gate 评估，直接返回 APPROVE。

        仅用于 narrative_only 且 policy_cache 已预热命中的场景，
        可节省约 20-30ms 的 gate 评估开销。
        注意：M27-H 情感门控不可绕过，即使 policy cache 命中也必须检查。
        """
        # M27-H：情感状态门控不可绕过（OPT-32）：critical 状态下拒绝 TOOL_CALL/SKILL_EXEC
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
        approval_token_ref = f"approval::{envelope.id}"
        if self.token_service is not None:
            approval_token_ref = self.token_service.issue_approval_token(
                intent_id=envelope.id,
                skill_name=envelope.action,
                session_id=envelope.session_id,
                seq=envelope.seq,
                now_ts_ms=now,
            )
        approved_action_binding = build_governed_action_binding(
            intent_envelope=envelope,
            approval_token_ref=approval_token_ref,
            risk_level=_resolve_intent_risk_level(self._registry, envelope.action),
            issued_at_ms=now,
            expires_at_ms=now + _approval_binding_ttl_ms(self),
        )
        self.ledger.mark_intent_seen(
            intent_id=envelope.id,
            session_id=envelope.session_id,
            nonce=envelope.nonce,
        )
        self.ledger.update_seq(session_id=envelope.session_id, seq=envelope.seq)
        committed = compile_route_decision(
            approved=True,
            reason_code="APPROVE_CACHED",
            committed_at_ms=now,
            proposer_node_id=proposer_node_id,
            tier="cache",
        )
        row = {
            "request_id": req_id,
            "thread_id": thread_id,
            "turn_id": turn,
            "intent_id": envelope.id,
            "decision": DECISION_APPROVE,
            "reason_code": None,
            "rule_trace": [{"gate_id": "POLICY_CACHE_HIT", "hit": False, "reason_hint": "APPROVE_CACHED"}],
            "tokens": {"approval_token_ref": approval_token_ref},
            "approved_action_binding": approved_action_binding,
            "snapshot_refs": snapshot_refs,
            "stage": "S_INTENT_OK",
            "ts_ms": now,
            "committed_route_token_ref": committed.token.value,
            "committed_route_token": {
                "token": committed.token.value,
                "reason_code": committed.reason_code,
                "committed_at_ms": committed.committed_at_ms,
                "proposer_node_id": committed.proposer_node_id,
                "approval_tier": "cache",
            },
        }
        return self._finalize_governor_row(row)

    def _finalize_governor_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """严格验证最终输出后再入账；输出契约漂移一律改写为拒绝。"""
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


def _resolve_intent_risk_level(registry: Any, action: str) -> int:
    """从权威注册表读取静态风险；未知动作始终维持最高风险。"""
    action_name = str(action or "").strip()
    # 内建治理动作不是可执行 Skill，因此不属于 SkillRegistry。其风险由 Governor 在此
    # 固化，而不是交由入口调用方声明。
    builtin_risk = {
        # ``work_translate`` 是服务端拥有的语义动作而非 SkillRegistry 条目。其受治理
        # 目标是冻结的只读翻译委托：可消耗 Task 模型预算，却不能调用工具、修改工作区，
        # 也不能在图接受前产生副作用。把它留给未知动作回退会将普通翻译判为风险 5，
        # 并在意图准入阶段永久阻断。
        "work_translate": 2,
        "operator_edge_command_push": 4,
        "operator_sys_mode_transition": 4,
        # 风险 5 是不可覆盖的意图阻断。已签名终端修复虽为高风险但设计为可审核；
        # 最终 Acceptance Effect 仍属风险 5，且需要精确 Action Preview。
        "operator_task_terminal_reconciliation": 4,
        "operator_task_operation_reconciliation": 4,
        "operator_task_effect_reconciliation": 4,
        # 取消仍是高风险、可审核控制。审核决定本身是已认证的人类控制面动作；若把它
        # 也设为高风险会递归要求另一轮审核。其最终变更仍被风险 4 的 ActionPreview 绑定。
        "operator_task_graph_cancellation": 4,
        "operator_task_review_control": 2,
        "operator_scheduler_state_control": 4,
        "operator_host_mode_policy_control": 4,
        "operator_long_term_goal_control": 4,
    }.get(action_name)
    if builtin_risk is not None:
        return builtin_risk
    if registry is None:
        return 5
    try:
        meta = registry.get(action_name)
        return max(1, min(5, int(getattr(meta, "risk_level", 5)))) if meta is not None else 5
    except (AttributeError, TypeError, ValueError):
        return 5


def _approval_binding_ttl_ms(engine: IntentDecisionEngine) -> int:
    """从令牌服务继承绑定有效期；配置异常时退回受限的意图时效窗口。"""
    if engine.token_service is not None:
        try:
            return max(1, int(engine.token_service.approval_ttl_ms))
        except (AttributeError, TypeError, ValueError):
            pass
    return max(1, min(int(engine.expiry_window_ms), 120_000))


def _is_expired(*, ts_ms: int, now_ms_value: int, ttl_ms: int) -> bool:
    """判定过期或明显未来伪造的意图时间戳，限制重放与时钟投毒窗口。"""
    if ts_ms > now_ms_value + 60_000:
        return True
    return now_ms_value - ts_ms > ttl_ms


def _normalize_intent_log_ts_ms(raw_ts_ms: Any, *, now_ms_value: int, max_future_skew_ms: int = 60_000) -> int:
    """为审计日志保留可信时间：坏值或未来过远值改记当前 Host 时间。"""
    try:
        candidate = int(raw_ts_ms)
    except (TypeError, ValueError):
        return now_ms_value
    if candidate <= 0:
        return now_ms_value
    if candidate > now_ms_value + max_future_skew_ms:
        return now_ms_value
    return candidate


def _hard_state_from_snapshot(governance_snapshot: dict[str, Any] | None) -> dict:
    """从可选快照提取并规范化硬状态，避免门控直接相信松散外部字典。"""
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
    """以硬状态为基线合成门控快照，仅在缺省时补入当前意图的目标范围。"""
    source = dict(governance_snapshot or {})
    state = _hard_state_from_snapshot(governance_snapshot)
    gates_input = build_governance_snapshot(state, overrides=source)
    if "scope" not in source:
        gates_input["scope"] = str(target_scope).strip().lower() or "host"
    return gates_input


def _derive_snapshot_refs(governance_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """生成不可变快照引用，令审计记录能指向裁决时观察到的治理状态。"""
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
