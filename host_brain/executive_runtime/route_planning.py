"""Executive runtime responsibility extracted from the HTTP application adapter."""

from ._shared import *  # noqa: F403
from .ports import ExecutiveRuntimeApplicationPorts

def _hard_rule_route(
    *,
    intent_draft: dict[str, Any],
    sanitized_message: str,
    skill_registry: Any,
    surface_task_control: Any | None = None,
    allow_plan_detection: bool = True,
) -> _RouteSelection:
    """两层路由判断。

    第一层：RULE_ROUTE_MAP 规则映射（精确匹配，无歧义词）→ 命中则直接路由
    第二层：intent_draft 提示 + 硬规则检测 → 未命中则兜底 chat
    """
    route_hint = _normalize_text(intent_draft.get("route"), lower=True)
    target_hint = _normalize_text(intent_draft.get("target_id"))
    if _surface_control_requests_memory_recall(surface_task_control, intent_draft) or _message_requests_memory_recall(sanitized_message):
        return _RouteSelection("chat", "", "hard_rule:surface_memory_recall")

    if route_hint in {"recent_turn", "task_progress", "chat"}:
        return _RouteSelection(route_hint, target_hint, f"intent_draft:{route_hint}")

    relational_reason = build_relational_chat_reason_code(sanitized_message)
    if relational_reason:
        return _RouteSelection("chat", "", relational_reason)

    if route_hint == "plan":
        return _RouteSelection("plan", target_hint or "skill_dag", "intent_draft:plan")
    if route_hint == "skill" and target_hint:
        return _RouteSelection("skill", target_hint, "intent_draft:skill")
    if route_hint == "delegate" and target_hint:
        return _RouteSelection("delegate", target_hint, "intent_draft:delegate")
    if _looks_like_simple_technical_advice(sanitized_message, intent_draft):
        return _RouteSelection("chat", "", "hard_rule:surface_technical_advice")
    if _looks_like_simple_task_split_advice(sanitized_message, intent_draft):
        return _RouteSelection("chat", "", "hard_rule:surface_task_split")

    if _looks_like_task_progress_query(sanitized_message):
        return _RouteSelection("task_progress", "", "hard_rule:task_progress")
    if _looks_like_recent_turn_query(sanitized_message):
        return _RouteSelection("recent_turn", "", "hard_rule:recent_turn")

    skill_target = _extract_skill_target(intent_draft, skill_registry)
    if skill_target:
        return _RouteSelection("skill", skill_target, "intent_draft:skill_target")

    delegate_target = _extract_delegate_target(intent_draft)
    if delegate_target:
        return _RouteSelection("delegate", delegate_target, "intent_draft:delegate_target")

    # ── 第一层：RULE_ROUTE_MAP 规则映射（精确匹配，无歧义词）──
    # 命中则直接路由，不进入 LLM 判断，也不进入 plan 路径
    try:
        from host_brain.agents.planning_agent import RULE_ROUTE_MAP  # noqa: PLC0415
        msg_lower = _normalize_text(sanitized_message, lower=True)
        for keyword, (rule_route_type, rule_target_id) in RULE_ROUTE_MAP.items():
            if keyword.lower() in msg_lower:
                # 验证 target_id 是否在 registry 中（skill 路由需要验证）
                if rule_route_type == "skill":
                    try:
                        if skill_registry.get(rule_target_id) is not None:
                            return _RouteSelection(rule_route_type, rule_target_id, f"rule_map:{keyword}")
                        else:
                            # skill 不在 registry 中（可能尚未初始化或已下线）
                            # 降级到 delegate 路由而非 chat，保留意图语义
                            _logger.debug(
                                "_hard_rule_route: skill '%s' 不在 registry，降级到 delegate 路由 (keyword=%s)",
                                rule_target_id, keyword,
                            )
                            return _RouteSelection("delegate", "intent_agent", f"rule_map:{keyword}:skill_fallback")
                    except Exception:
                        # registry 访问异常时同样降级到 delegate，不丢失意图
                        return _RouteSelection("delegate", "intent_agent", f"rule_map:{keyword}:registry_error")
                else:
                    return _RouteSelection(rule_route_type, rule_target_id, f"rule_map:{keyword}")
    except ImportError:
        pass

    if allow_plan_detection and _looks_multi_step_request(sanitized_message, intent_draft):
        return _RouteSelection("plan", "skill_dag", "hard_rule:multi_step")

    return _RouteSelection("chat", "", "default:chat")


def _looks_like_simple_technical_advice(user_message: str, intent_draft: dict[str, Any] | None = None) -> bool:
    text = _normalize_text(user_message, lower=True)
    if not text:
        return False
    if any(token in text for token in _SIMPLE_TECH_ACTION_BLOCKERS):
        return False
    if not any(token in text for token in _SIMPLE_TECH_ADVICE_TERMS):
        return False
    if not any(token in text for token in ("怎么", "如何", "怎么办", "该", "排查", "查", "先看", "看什么", "?", "？")):
        return False
    draft = dict(intent_draft or {}) if isinstance(intent_draft, dict) else {}
    target_id = _normalize_text(draft.get("target_id") or draft.get("skill_id"), lower=True)
    if target_id and target_id not in {"", "agent_worker", "intent_agent"}:
        return False
    return True


def _looks_like_simple_task_split_advice(user_message: str, intent_draft: dict[str, Any] | None = None) -> bool:
    text = _normalize_text(user_message, lower=True)
    if not text or not _SIMPLE_TASK_SPLIT_RE.search(text):
        return False
    if any(token in text for token in _SIMPLE_TECH_ACTION_BLOCKERS):
        return False
    draft = dict(intent_draft or {}) if isinstance(intent_draft, dict) else {}
    target_id = _normalize_text(draft.get("target_id") or draft.get("skill_id"), lower=True)
    if target_id and target_id not in {"", "intent_agent", "agent_worker"}:
        return False
    return True


def _should_force_complex_plan(
    *,
    ports: ExecutiveRuntimeApplicationPorts,
    route: _RouteSelection,
    user_message: str,
    intent_draft: dict[str, Any],
    llm_route_used: bool,
) -> bool:
    """第二层判断：是否强制进入 plan 路径（LangGraph 父子图）。

    改进：
    - skill/delegate 路由也需要通过 gap 检测保护，避免已能处理的请求被过度拆解
    - needs_planning() 评分指标已收紧（见 planning_agent.py），误触发率降低
    - RULE_ROUTE_MAP 命中的请求不会到达此函数（已在 _hard_rule_route 中直接路由）
    """
    if route.route_type in {"plan", "recent_turn", "task_progress"}:
        return False
    if bool(intent_draft.get("disable_auto_plan")):
        return False
    if bool(intent_draft.get("force_plan")):
        return True

    normalized_message = _normalize_text(user_message)
    min_chars = get_orchestration_settings().executive_auto_complex_plan_min_chars
    if len(normalized_message) < min_chars:
        return False

    # 所有路由类型（包括 skill/delegate）都需要通过 gap 检测
    # 避免已有 skill/agent 能处理的请求被强制拆解为多步 DAG
    gap = detect_capability_gap(
        user_message=normalized_message,
        route_type=route.route_type,
        target_id=route.target_id,
        reason_code=route.reason_code,
        llm_route_used=llm_route_used,
    )
    if not gap.detected:
        return False

    try:
        return bool(ports.get_planning_agent().needs_planning(normalized_message))
    except Exception:
        return False


def _maybe_build_multi_step_plan(
    *,
    request_id: str,
    sanitized_message: str,
    intent_draft: dict[str, Any],
    skill_registry: Any,
    parallel_window: int | None = None,
) -> SkillDagPlan | None:
    """构建多步骤 DAG 计划。

    仅使用 intent_draft 中的显式步骤（由 LangGraph 父子图规划产生），
    不再依赖旧的 _build_planning_agent_plan_steps / _build_default_plan_steps 兜底。
    """
    explicit_steps = intent_draft.get("planned_steps")
    if not isinstance(explicit_steps, list):
        explicit_steps = intent_draft.get("steps")
    steps = [dict(item or {}) for item in explicit_steps] if isinstance(explicit_steps, list) else []
    if len(steps) <= 1:
        return None
    return build_skill_dag_plan(
        plan_id=f"plan::{request_id}",
        steps=steps,
        parallel_window=parallel_window,
    )


def _build_http_plan_node_grants(
    *,
    ports: ExecutiveRuntimeApplicationPorts,
    plan: SkillDagPlan,
    request_id: str,
    session_id: str,
    person_id: str,
    intent_draft: dict[str, Any],
    governor_result: dict[str, Any],
    skill_registry: Any,
) -> dict[str, dict[str, Any]]:
    """Derive exact, durable child grants from one approved HTTP plan.

    The parent ``exec_intent`` token is only authority to derive the immutable
    approved DAG. It is never reused to execute a child capability.
    """

    from host_brain.governor.action_arbiter import build_governed_action_binding  # noqa: PLC0415
    from host_brain.governor.route_token import compile_route_decision  # noqa: PLC0415
    from host_brain.orchestration import DurableGovernorGrantResolver  # noqa: PLC0415
    from host_brain.orchestration.governor_grant import (  # noqa: PLC0415
        durable_governor_binding,
        governor_grant_ref,
    )

    ledger = ports.get_ledger()
    parent_ref = governor_grant_ref(request_id, governor_result)
    parent_decision = DurableGovernorGrantResolver(ledger).require(
        request_id=request_id,
        expected_ref=parent_ref,
    )
    parent_binding = durable_governor_binding(parent_decision)
    now_value = int(time.time() * 1000)
    try:
        parent_seq = int(parent_binding.get("seq") or 0)
        parent_expiry = int(parent_binding.get("expires_at_ms") or 0)
    except (TypeError, ValueError) as exc:
        raise PermissionError("PLAN_PARENT_GRANT_INVALID") from exc
    parent_intent_id = _normalize_text(parent_binding.get("intent_id"))
    parent_scope = _normalize_text(parent_binding.get("scope"), lower=True)
    parent_principal = _normalize_text(parent_binding.get("principal_id"))
    if (
        _normalize_text(parent_binding.get("action_kind")) != "exec_intent"
        or parent_intent_id != _normalize_text(intent_draft.get("id") or intent_draft.get("intent_id"))
        or _normalize_text(parent_binding.get("session_id")) != session_id
        or parent_seq != int(intent_draft.get("seq") or 0)
        or parent_scope != (_normalize_text(intent_draft.get("target"), lower=True) or "host")
        or parent_principal != person_id
        or parent_expiry <= now_value
    ):
        raise PermissionError("PLAN_PARENT_GRANT_BINDING_MISMATCH")

    # Rebuild the DAG from the immutable proposal stored before Governor
    # approval. A caller cannot append or substitute a node after approval.
    trace = ledger.trace_request(request_id)
    approved_draft: dict[str, Any] | None = None
    for raw_row in list(trace.get("intent_log") or []):
        if not isinstance(raw_row, Mapping):
            continue
        if _normalize_text(raw_row.get("intent_id")) != parent_intent_id:
            continue
        raw_draft = raw_row.get("normalized_json")
        if isinstance(raw_draft, str):
            try:
                raw_draft = json.loads(raw_draft)
            except json.JSONDecodeError:
                raw_draft = None
        if isinstance(raw_draft, Mapping):
            approved_draft = dict(raw_draft)
    if approved_draft is None:
        raise PermissionError("PLAN_APPROVED_PROPOSAL_MISSING")
    approved_steps = approved_draft.get("planned_steps")
    if not isinstance(approved_steps, list):
        approved_steps = approved_draft.get("steps")
    if not isinstance(approved_steps, list):
        raise PermissionError("PLAN_APPROVED_NODES_MISSING")
    approved_plan = build_skill_dag_plan(
        plan_id=plan.plan_id,
        steps=[dict(item or {}) for item in approved_steps],
        parallel_window=plan.parallel_window,
    )
    if [node.as_dict() for node in approved_plan.nodes] != [node.as_dict() for node in plan.nodes]:
        raise PermissionError("PLAN_APPROVED_DAG_MISMATCH")

    token_service = ports.get_token_service()
    grants: dict[str, dict[str, Any]] = {}
    for node in plan.nodes:
        if node.route != "skill":
            continue
        child_seq = parent_seq * 10_000 + int(node.step_index)
        child_intent_id = f"{parent_intent_id}::node::{node.node_id}"
        child_args = _merge_plan_node_args(
            base_intent_draft=approved_draft,
            node=node,
            task_text=_normalize_text(node.task_description or node.description),
        )
        child_intent = {
            "schema_version": "Natsume.intent.v1",
            "id": child_intent_id,
            "ts_ms": now_value,
            "session_id": session_id,
            "seq": child_seq,
            "person_id": person_id,
            "principal_id": parent_principal,
            "type": "query",
            "action": node.target_id,
            "route": "skill",
            "target_id": node.target_id,
            "target": parent_scope,
            "args": child_args,
            "nonce": f"nonce::{request_id}::{node.node_id}",
            "source": "executive_plan_node_grant",
        }
        token_ref = token_service.issue_approval_token(
            intent_id=child_intent_id,
            skill_name=node.target_id,
            session_id=session_id,
            seq=child_seq,
            now_ts_ms=now_value,
        )
        skill_meta = skill_registry.require(node.target_id)
        child_expiry = min(parent_expiry, now_value + 60_000)
        if child_expiry <= now_value:
            raise PermissionError("PLAN_NODE_GRANT_EXPIRED")
        binding = build_governed_action_binding(
            intent_envelope=child_intent,
            approval_token_ref=token_ref,
            risk_level=max(int(node.risk_level), int(skill_meta.risk_level)),
            issued_at_ms=now_value,
            expires_at_ms=child_expiry,
        )
        committed = compile_route_decision(
            approved=True,
            reason_code="PLAN_NODE_DERIVED_APPROVE",
            committed_at_ms=now_value,
            proposer_node_id="executive_plan_node_grant",
            tier="full",
        )
        child_decision = {
            "request_id": request_id,
            "intent_id": child_intent_id,
            "decision": "APPROVE",
            "reason_code": "PLAN_NODE_DERIVED_APPROVE",
            "rule_trace": [{
                "gate_id": "PLAN_PARENT_GRANT",
                "hit": False,
                "reason_hint": "EXACT_APPROVED_NODE",
            }],
            "tokens": {"approval_token_ref": token_ref},
            "approved_action_binding": binding,
            "snapshot_refs": {
                "parent_governor_grant_ref": parent_ref,
                "plan_id": plan.plan_id,
                "node_id": node.node_id,
            },
            "stage": "S_PLAN_NODE_GRANT",
            "ts_ms": now_value,
            "committed_route_token_ref": committed.token.value,
            "committed_route_token": {
                "token": committed.token.value,
                "reason_code": committed.reason_code,
                "committed_at_ms": committed.committed_at_ms,
                "proposer_node_id": committed.proposer_node_id,
            },
        }
        ledger.log_governor(child_decision)
        grants[node.node_id] = {
            **child_decision,
            "_node_intent_draft": child_intent,
        }
    return grants
