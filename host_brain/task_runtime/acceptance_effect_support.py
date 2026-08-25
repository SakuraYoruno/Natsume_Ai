"""Acceptance Effect 的共享校验与规范 intent 构造器。

图接受阶段在这里把 worker 终态转换为内容寻址的 effect 描述：每项绑定图、
节点、尝试和 payload 摘要。它同时从 Runtime Context 导出最小 Preview 谱系，
并删除 worker 绝不应携带的授权字段，使后续 dispatcher 可在独立租约中安全
重开效果，而不是信任执行器传回的能力对象。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import re
from typing import Any

from host_brain.persistence.task_repository import (
    AcceptanceEffectCommitScope,
    AcceptanceEffectIntentSpec,
)
from host_brain.runtime.action_preview_authority import (
    ActionPreviewAuthorityError,
    context_pack_digest_from_work_order,
)
from host_brain.runtime.action_preview_resolver import action_preview_scene_identity
from host_brain.task_runtime.worker_authority_guard import forbidden_worker_authority_path
from natsume_contracts.task import HostCommand


ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID = "ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID"
_EFFECT_TYPE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")


class AcceptanceEffectCompileError(RuntimeError):
    """终态无法编译为可接受 Effect intent 时的稳定、无密钥拒绝。"""

    def __init__(self, detail: str) -> None:
        self.reason_code = ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID
        self.detail = str(detail or "acceptance effect descriptor is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


def build_action_preview_context(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    worker_commit: Mapping[str, Any],
) -> dict[str, Any] | None:
    """从冻结 Runtime Context 构造 Effect 重开所需的最小节点谱系。

    无 Runtime Context 的历史图返回 ``None`` 以维持其既有路径；新图必须
    同时提供场景身份、Context 摘要和 node grant 摘要，防止 Acceptance 后
    的效果借用错误节点或在场景漂移后获得授权。
    """
    pack = getattr(work_order, "input_pack", None)
    if not isinstance(pack, Mapping) or pack.get("runtime_context_schema") != "natsume.runtime-context.request.v1":
        return None
    audit = worker_commit.get("audit_refs")
    audit_map = dict(audit) if isinstance(audit, Mapping) else {}
    node_grant_ref = str(audit_map.get("node_grant_ref") or "").strip()
    node_grant_digest = str(audit_map.get("node_grant_digest") or "").strip().lower()
    if not node_grant_ref or not node_grant_digest.startswith("sha256:"):
        raise AcceptanceEffectCompileError(
            "runtime-context effect has no exact node grant preview binding"
        )
    try:
        context_digest = context_pack_digest_from_work_order(work_order)
    except ActionPreviewAuthorityError as exc:
        raise AcceptanceEffectCompileError(exc.detail) from exc
    scene = pack.get("scene_identity_v2")
    if not isinstance(scene, Mapping):
        raise AcceptanceEffectCompileError("runtime-context effect has no exact Scene identity")
    return {
        "schema_version": "Natsume.acceptance_effect.action_preview_context.v1",
        "context_pack_digest": context_digest,
        "scene_identity": action_preview_scene_identity(
            _json_mapping(scene, field_name="scene_identity_v2")
        ),
        "task_graph_id": command.graph_id,
        "graph_revision": command.graph_revision,
        "task_node_id": node_id,
        "parent_governor_grant_ref": command.governor_grant_ref,
        "node_grant_ref": node_grant_ref,
        "node_grant_digest": node_grant_digest,
    }


def _trusted_foreground_command(
    *,
    descriptors: Sequence[Mapping[str, Any]],
    command: HostCommand,
    companion_subject_id: str,
    companion_workspace_id: str,
    surface_message: str,
    narrative: str,
    render_lang: str,
    request_body: Mapping[str, Any],
    emotion_context: Mapping[str, Any],
    bond_summary: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> dict[str, Any]:
    """构造只在图已接受后执行的规范 Life 前台完成命令。

    worker 可提供受限 projection 补充，但请求/会话/主体、回复、工作区和
    投递策略由 Host 冻结材料覆盖。实际 transport delivery 被拆为另一条
    effect，避免 Life 记录成功被误当作消息已经送达。
    """
    if descriptors:
        foreground = deepcopy(dict(descriptors[0]["payload"]))
        if str(foreground.get("command_type") or "") != "foreground_completed":
            raise AcceptanceEffectCompileError("worker foreground command_type drifted")
        payload = _json_mapping(foreground.get("payload"), field_name="foreground.payload")
    else:
        payload = {}
        foreground = {"payload": payload}

    completed_at_ms = int(foreground.get("created_at_ms") or command.created_at_ms)
    projection_payload = {
        "request_id": command.request_id,
        "session_id": command.session_id,
        "person_id": companion_subject_id,
        "user_message": surface_message,
        "assistant_reply": narrative,
        "narrative": narrative,
        "request_ts_ms": command.created_at_ms,
        "ts_ms": command.created_at_ms,
        "render_target_lang": render_lang,
        "emotion_context": dict(emotion_context),
        "bond_summary": dict(bond_summary),
        "reply_channel": None,
        "reply_target": None,
        "retrieval_summary": dict(pack.get("retrieval_summary") or {}),
        "retrieval_evidence_items": list(pack.get("retrieval_evidence_items") or []),
        "pii_ingress_stats": dict(pack.get("pii_ingress_stats") or {}),
        "motive_state": dict(pack.get("motive_state") or {}),
        "attachments": list(request_body.get("attachments") or []),
        "multimodal_observations": dict(
            request_body.get("multimodal_observations") or {}
        ),
        "attachment_summary": str(request_body.get("attachment_summary") or ""),
        "trace_stage_meta": {
            "governed_task_acceptance": True,
            "surface_main_chain_used": bool(descriptors),
            "reply_source": "governed_task_runtime",
            "life_projection_mode": True,
        },
        "authority_principal_id": command.principal_id,
    }
    existing_projection = next(
        (
            dict(item)
            for item in list(payload.get("projection_intents") or [])
            if isinstance(item, Mapping) and item.get("target") == "surface.post_process"
        ),
        None,
    )
    if existing_projection is not None:
        rich = _json_mapping(existing_projection.get("payload"), field_name="surface.post_process")
        rich.update(projection_payload)
        projection_payload = rich
    canonical_workspace_id = (
        str(companion_workspace_id or "").strip()
        or str(payload.get("workspace_id") or "").strip()
    )
    canonical_payload = {
        **payload,
        "request_id": command.request_id,
        "session_id": command.session_id,
        "person_id": companion_subject_id,
        # Migration 0146 binds every Voice/Avatar protocol row back to this
        # exact immutable Life event. The workspace must live on the command
        # payload itself, not only inside the plan or individual intents.
        "workspace_id": canonical_workspace_id,
        "user_message": surface_message,
        "assistant_reply": narrative,
        "completed_at_ms": completed_at_ms,
        "state_patch": {
            "clock": {"last_user_seen_ms": completed_at_ms, "idle_minutes": 0},
            "continuity": {"last_interaction_digest": surface_message[:160]},
        },
        "emotion_context": dict(emotion_context),
        "bond_summary": dict(bond_summary),
        "projection_intents": [
            {"target": "surface.post_process", "payload": projection_payload}
        ],
        # Transport delivery is a separate acceptance effect with independently
        # rebuilt authority and idempotency.
        "deliveries": [],
    }
    return {
        "command_type": "foreground_completed",
        "person_id": companion_subject_id,
        "command_id": f"life::foreground_completed::{command.request_id}",
        "correlation_id": command.request_id,
        "payload": canonical_payload,
        "created_at_ms": completed_at_ms,
        "metadata": {
            "source": "governed_task_acceptance",
            "reply_sendable": True,
            "reply_persistable": True,
            "delivery_authorized": False,
        },
    }


def _frozen_companion_scope(
    *,
    command: HostCommand,
    pack: Mapping[str, Any],
) -> tuple[str, str]:
    """Resolve the immutable relationship subject for companion projections.

    Runtime Context v1 deliberately separates an authenticated transport
    principal from the human whom Natsume is accompanying.  Gateway requests
    therefore keep their service principal in HostCommand and Preview
    authority, while conversation, Life, Memory and delivery projections use
    the frozen subject.  Pre-context accepted graphs retain their historical
    principal-scoped projection during the rollback window only.
    """

    schema = pack.get("runtime_context_schema")
    if schema is None:
        return str(command.principal_id or "").strip(), ""
    if schema != "natsume.runtime-context.request.v1":
        raise AcceptanceEffectCompileError(
            "runtime-context companion subject schema is invalid"
        )
    subject_id = str(pack.get("subject_id") or "").strip()
    workspace_id = str(pack.get("workspace_id") or "").strip()
    if not subject_id or not workspace_id:
        raise AcceptanceEffectCompileError(
            "runtime-context companion projection has no canonical subject or workspace"
        )
    return subject_id, workspace_id


def build_acceptance_effect_intent_spec(
    *,
    effect_type: str,
    payload: Mapping[str, Any],
    command: HostCommand,
    node_id: str,
    attempt_id: str,
) -> AcceptanceEffectIntentSpec:
    """把公开 payload 规范化为绑定节点尝试的内容寻址 Effect intent。

    同一图节点以相同 payload 重放会得到相同 effect_id/idempotency_key；
    任何类型、尝试或内容变化都会生成不同身份，交给持久队列显式处理而非
    隐式覆盖既有效果。
    """
    if not _EFFECT_TYPE.fullmatch(effect_type):
        raise AcceptanceEffectCompileError(f"invalid effect_type: {effect_type}")
    canonical = _canonical_json(payload)
    payload_hex = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    binding_hex = hashlib.sha256(
        _canonical_json(
            {
                "effect_type": effect_type,
                "graph_id": command.graph_id,
                "graph_revision": command.graph_revision,
                "node_id": node_id,
                "attempt_id": attempt_id,
                "payload_sha256": f"sha256:{payload_hex}",
            }
        ).encode("utf-8")
    ).hexdigest()
    return AcceptanceEffectIntentSpec(
        effect_id=f"acceptance-effect://sha256/{binding_hex}",
        graph_id=command.graph_id,
        graph_revision=command.graph_revision,
        node_id=node_id,
        attempt_id=attempt_id,
        commit_scope=AcceptanceEffectCommitScope.GRAPH,
        effect_type=effect_type,
        payload_ref=f"acceptance-payload://sha256/{payload_hex}",
        payload=json.loads(canonical),
        payload_sha256=f"sha256:{payload_hex}",
        idempotency_key=f"acceptance-idempotency://sha256/{binding_hex}",
    )


def _reject_worker_authority(value: Any, *, path: str = "payload") -> None:
    """拒绝 worker 将 grant/token/调度能力塞入跨 Acceptance payload。"""
    forbidden = forbidden_worker_authority_path(value, path=path)
    if forbidden is not None:
        raise AcceptanceEffectCompileError(f"worker effect contains forbidden authority field: {forbidden}")


def _json_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    """复制并验证 Mapping 为有限规范 JSON，断开调用方的可变别名。"""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise AcceptanceEffectCompileError(f"{field_name} must be a mapping")
    try:
        return json.loads(_canonical_json(value))
    except (TypeError, ValueError) as exc:
        raise AcceptanceEffectCompileError(f"{field_name} must be canonical JSON") from exc


def _canonical_json(value: Any) -> str:
    """生成稳定 JSON 字节序列，作为 payload 与 effect 身份的散列输入。"""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _required(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AcceptanceEffectCompileError(f"{field_name} is required")
    return text


__all__ = [
    "ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID",
    "AcceptanceEffectCompileError",
    "build_acceptance_effect_intent_spec",
    "build_action_preview_context",
]
