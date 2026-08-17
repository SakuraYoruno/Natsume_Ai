"""ScenePolicy 与 ActionArbiter 决定的干运行预览。

预览可记录脱敏 RuntimeEvent 供复核，但永不调用执行器或产生外部副作用；真正执行仍需
持久化的预览收据与独立的执行权威。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.governor.action_arbiter import ActionArbiter
from host_brain.runtime.action_preview_authority import DurableActionPreviewAuthority


ACTION_PREVIEW_SCHEMA_VERSION = "Natsume.action.preview.v1"
ACTION_ARBITER_EVENT_TYPE = "ACTION_ARBITER_DECIDED"


def _refs(value: Any) -> tuple[str, ...]:
    """将可选证据输入规整为去重引用，不让无效载荷污染预览事件。"""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return ()
    return tuple(
        dict.fromkeys(str(item).strip() for item in value if str(item).strip())
    )


def build_action_arbiter_runtime_event(
    *,
    preview_id: str,
    arbiter_decision: Mapping[str, Any],
    evidence_refs: Sequence[str] = (),
    ts_ms: int,
) -> Any:
    """构造脱敏 RuntimeEvent；不持久化动作正文/载荷，仅保存决策与引用。

    事件的 ``source_ref`` 同时指向此预览，便于后续耐久收据将执行权威追溯回同一次
    干运行；它本身不代表已执行，``side_effects_executed`` 始终为 ``False``。
    """
    from host_brain.integrations.runtime_events import RuntimeEvent  # noqa: PLC0415

    decision = dict(arbiter_decision or {})
    source_ref = f"action-preview://{preview_id}"
    payload = {
        "preview_id": preview_id,
        "decision_id": decision.get("decision_id"),
        "decision": decision.get("decision"),
        "reason_codes": list(decision.get("reason_codes") or []),
        "action_kind": decision.get("effective_action_kind"),
        "scope": decision.get("effective_scope"),
        "risk_level": decision.get("effective_risk_level"),
        "governor_decision": decision.get("governor_decision"),
        "committed_route_token": decision.get("committed_route_token"),
        "authority_binding_ref": decision.get("authority_binding_ref"),
        "scene_decision_ref": decision.get("scene_decision_ref"),
        "explicit_user_goal": bool(decision.get("explicit_user_goal")),
        "user_goal_preserved": bool(decision.get("user_goal_preserved")),
        "side_effects_executed": False,
    }
    return RuntimeEvent.create(
        event_type=ACTION_ARBITER_EVENT_TYPE,
        source_kind="SYSTEM",
        source_ref=source_ref,
        aggregate_type="ACTION_PREVIEW",
        aggregate_id=preview_id,
        aggregate_version=1,
        payload_ref=source_ref,
        payload=payload,
        evidence_refs=_refs(evidence_refs),
        redaction_level="INTERNAL_REDACTED",
        ts_ms=int(ts_ms),
    )


def preview_action(
    *,
    governor_decision: Mapping[str, Any],
    proposed_action: Mapping[str, Any],
    intent_envelope: Any = None,
    scene_decision: Any = None,
    user_intent: Mapping[str, Any] | None = None,
    evidence_refs: Sequence[str] = (),
    now_ms: int,
    arbiter: ActionArbiter | None = None,
    runtime_event_store: Any = None,
    record_runtime_event: bool = False,
) -> dict[str, Any]:
    """预览最终决定而不调用执行器或外部副作用；``would_execute`` 仅为可行性判断。"""
    resolved_arbiter = arbiter or ActionArbiter()
    decision = resolved_arbiter.decide(
        governor_decision=governor_decision,
        proposed_action=proposed_action,
        intent_envelope=intent_envelope,
        scene_decision=scene_decision,
        user_intent=user_intent,
        now_ms=int(now_ms),
    )
    seed = json.dumps(
        {
            "decision_id": decision["decision_id"],
            "evidence_refs": list(_refs(evidence_refs)),
            "ts_ms": int(now_ms),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    preview_id = f"action_preview_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:24]}"
    runtime_event = build_action_arbiter_runtime_event(
        preview_id=preview_id,
        arbiter_decision=decision,
        evidence_refs=evidence_refs,
        ts_ms=int(now_ms),
    )
    recorded_event: dict[str, Any] | None = None
    if record_runtime_event:
        if runtime_event_store is None or not callable(getattr(runtime_event_store, "append_event", None)):
            raise ValueError("record_runtime_event requires RuntimeEventStore.append_event")
        recorded_event = dict(runtime_event_store.append_event(runtime_event) or {})

    return {
        "schema_version": ACTION_PREVIEW_SCHEMA_VERSION,
        "preview_id": preview_id,
        "status": "DRY_RUN",
        "arbiter_decision": decision,
        "would_execute": (
            decision.get("decision") != "BLOCK"
            and decision.get("execution_state") == "EXECUTABLE"
            and bool(decision.get("authority_binding_valid"))
        ),
        "execution_performed": False,
        "external_side_effects": False,
        "evidence_refs": list(_refs(evidence_refs)),
        "runtime_event": {
            "event_id": runtime_event.event_id,
            "event_type": runtime_event.event_type,
            "source_ref": runtime_event.source_ref,
            "payload_ref": runtime_event.payload_ref,
            "dedupe_key": runtime_event.dedupe_key,
            "payload": dict(runtime_event.payload),
            "recorded": recorded_event is not None,
            "cursor": recorded_event.get("cursor") if recorded_event else None,
        },
    }


class ActionPreviewService:
    """注入持久化 Guardian 权威的应用局部预览门面。

    ``preview`` 只能生成并记录干运行证据；签发收据和要求执行必须另经
    ``DurableActionPreviewAuthority``，因此调用方无法把预览结果直接当作执行许可。
    """

    def __init__(
        self,
        *,
        runtime_event_store: Any,
        guardian: Any,
        authority_resolver: Any,
        clock: Any = None,
    ) -> None:
        if not callable(getattr(guardian, "evaluate_capability", None)):
            raise TypeError("ActionPreviewService requires app-local RuntimeGuardian")
        if not callable(getattr(authority_resolver, "resolve", None)):
            raise TypeError(
                "ActionPreviewService requires a canonical authority resolver"
            )
        self._clock = clock or (lambda: int(time.time() * 1000))
        if not callable(self._clock):
            raise TypeError("ActionPreviewService clock must be callable")
        self._event_store = runtime_event_store
        self._guardian = guardian
        self._authority_resolver = authority_resolver
        self._arbiter = ActionArbiter(degradation_capability_port=guardian)
        self._authority: DurableActionPreviewAuthority | None = None

    def preview(self, **kwargs: Any) -> dict[str, Any]:
        """生成并持久化干运行事件，供人工或后续效果边界复核。"""
        kwargs.setdefault("arbiter", self._arbiter)
        kwargs.setdefault("runtime_event_store", self._event_store)
        kwargs.setdefault("record_runtime_event", True)
        return preview_action(**kwargs)

    def issue_receipt(self, **kwargs: Any) -> dict[str, Any]:
        """委托耐久权威签发与预览绑定的执行收据。"""
        return self._durable_authority().issue(**kwargs)

    def require_execution(self, **kwargs: Any) -> Any:
        """在实际效果边界重新验证收据，而非信任调用方缓存的预览结果。"""
        return self._durable_authority().require_execution(**kwargs)

    def reopen_receipt(self, *, event_id: str) -> dict[str, Any]:
        """返回经验证的历史收据但不附带执行权威，供恢复/审计安全读取。"""

        return self._durable_authority().reopen_receipt(event_id=event_id)

    def now_ms(self) -> int:
        """返回与签发/重开过期检查相同的时钟，避免两套时间源产生边界漂移。"""

        return int(self._clock())

    def _durable_authority(self) -> DurableActionPreviewAuthority:
        authority = self._authority
        if authority is None:
            # 延迟到首个效果边界再验证仓储，使应用组合能先发布共享门面；仓储缺失/无效仍会
            # 在签发收据或运行执行器前闭合失败。
            authority = DurableActionPreviewAuthority(
                runtime_event_store=self._event_store,
                guardian=self._guardian,
                authority_resolver=self._authority_resolver,
                clock=self._clock,
            )
            self._authority = authority
        return authority


__all__ = [
    "ACTION_ARBITER_EVENT_TYPE",
    "ACTION_PREVIEW_SCHEMA_VERSION",
    "ActionPreviewService",
    "build_action_arbiter_runtime_event",
    "preview_action",
]
