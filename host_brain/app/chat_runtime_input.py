"""把聊天请求的检索、情绪与图路由上下文冻结为运行时载荷。"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any, Mapping

from host_brain.app.emotion_context import build_chat_emotion_snapshot, normalize_chat_emotion_context


CHAT_RUNTIME_INPUT_VERSION = "chat_runtime_input.v2"


def _resolve_render_target_lang(render_target_lang: str | None) -> str:
    """解析输出语言，部署级环境覆盖优先于单请求偏好以保持同一节点一致性。"""
    override = str(os.environ.get("HOST_RENDER_TARGET_LANG") or "").strip().lower()
    if override in {"zh", "ja"}:
        return override
    resolved = str(render_target_lang or "").strip().lower()
    if resolved in {"zh", "ja"}:
        return resolved
    return "ja"


def _copy_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    for row in list(rows or []):
        if isinstance(row, Mapping):
            copied.append(dict(row))
    return copied


def _copy_mapping(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    return dict(raw)


def _copy_skill_manifest(skill_manifest: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    for row in list(skill_manifest or []):
        if isinstance(row, Mapping):
            copied.append(dict(row))
    return copied


def _copy_scene_awareness(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}

    scene_tags: list[str] = []
    raw_tags = raw.get("scene_tags") or []
    if isinstance(raw_tags, list):
        for item in raw_tags:
            normalized = str(item or "").strip().lower().replace("-", "_").replace(" ", "_")
            if normalized and normalized not in scene_tags:
                scene_tags.append(normalized)

    current_activity = str(raw.get("current_activity") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if current_activity and current_activity not in scene_tags:
        scene_tags.insert(0, current_activity)

    cleaned: dict[str, Any] = {}
    if scene_tags:
        cleaned["scene_tags"] = scene_tags
    if current_activity:
        cleaned["current_activity"] = current_activity
    if isinstance(raw.get("companion_suitable"), bool):
        cleaned["companion_suitable"] = bool(raw.get("companion_suitable"))
    if isinstance(raw.get("has_screenshot"), bool):
        cleaned["has_screenshot"] = bool(raw.get("has_screenshot"))
    for key in (
        "observed_at_ms",
        "expires_at_ms",
        "source_observed_at_ms",
        "source_expires_at_ms",
        "confidence",
        "epistemic_type",
        "conflict_state",
        "source_ref",
    ):
        if raw.get(key) is not None:
            cleaned[key] = raw.get(key)
    source_refs = raw.get("source_refs")
    if isinstance(source_refs, (list, tuple)):
        cleaned["source_refs"] = [str(item).strip() for item in source_refs if str(item).strip()]
    return cleaned


@dataclass(slots=True)
class ChatRuntimeInput:
    """一次聊天运行所需的输入快照。

    其中检索、附件、情绪和图路由信息均为调用时复制的数据，后续组件不能通过修改
    原始请求对象回写此快照；它也不包含任何执行授权或模型最终结论。
    """
    request_id: str
    person_id: str
    workspace_id: str
    session_id: str
    thread_id: str | None
    user_message: str
    render_target_lang: str
    retrieval_summary: dict[str, Any] = field(default_factory=dict)
    retrieval_evidence_items: list[dict[str, Any]] = field(default_factory=list)
    emotion_state: dict[str, Any] = field(default_factory=dict)
    emotion_snapshot: dict[str, Any] = field(default_factory=dict)
    bond_summary: dict[str, Any] = field(default_factory=dict)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    multimodal_observations: dict[str, Any] = field(default_factory=dict)
    attachment_summary: str = ""
    scene_type: str | None = None
    scene_type_source: str | None = None
    committed_route_token: str | None = None
    committed_route_token_detail: dict[str, Any] = field(default_factory=dict)
    skill_manifest: list[dict[str, Any]] = field(default_factory=list)
    scene_awareness: dict[str, Any] = field(default_factory=dict)

    def to_runtime_payload(
        self,
        *,
        prompt_context: Mapping[str, Any],
        prompt_context_validation: Mapping[str, Any],
        prompt_context_observability: Mapping[str, Any],
    ) -> dict[str, Any]:
        """导出按职责分区的 v2 载荷，并复制嵌套可变集合以隔离下游修改。"""
        graph_section: dict[str, Any] = {}
        if self.scene_type:
            graph_section["scene_type"] = str(self.scene_type).strip().upper()
        if self.scene_type_source:
            graph_section["scene_type_source"] = str(self.scene_type_source).strip()
        if self.committed_route_token:
            graph_section["committed_route_token"] = str(self.committed_route_token).strip()
        if self.committed_route_token_detail:
            graph_section["committed_route_token_detail"] = dict(self.committed_route_token_detail)
        if self.skill_manifest:
            graph_section["skill_manifest"] = _copy_skill_manifest(self.skill_manifest)
        if self.scene_awareness:
            graph_section["scene_awareness"] = _copy_scene_awareness(self.scene_awareness)

        return {
            "version": CHAT_RUNTIME_INPUT_VERSION,
            "request": {
                "request_id": self.request_id,
                "person_id": self.person_id,
                "workspace_id": self.workspace_id,
                "session_id": self.session_id,
                "thread_id": self.thread_id,
                "user_message": self.user_message,
                "attachments": _copy_rows(self.attachments),
                "multimodal_observations": dict(self.multimodal_observations),
                "attachment_summary": self.attachment_summary,
            },
            "render": {
                "target_lang": self.render_target_lang,
            },
            "retrieval": {
                **dict(self.retrieval_summary),
                "evidence_items": _copy_rows(self.retrieval_evidence_items),
            },
            "emotion": {
                "state": dict(self.emotion_state),
                "snapshot": dict(self.emotion_snapshot),
            },
            "prompt_context": {
                "payload": dict(prompt_context),
                "validation": dict(prompt_context_validation),
                "observability": dict(prompt_context_observability),
            },
            "graph": graph_section,
        }


def build_chat_runtime_input(
    *,
    request_id: str,
    person_id: str = "",
    workspace_id: str = "",
    session_id: str,
    thread_id: str | None,
    user_message: str,
    retrieval_summary: Mapping[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    emotion_state: Mapping[str, Any] | None = None,
    emotion_snapshot: Mapping[str, Any] | None = None,
    render_target_lang: str | None = None,
    bond_summary: Mapping[str, Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    multimodal_observations: Mapping[str, Any] | None = None,
    attachment_summary: str | None = None,
    scene_type: str | None = None,
    scene_type_source: str | None = None,
    committed_route_token: str | None = None,
    committed_route_token_detail: Mapping[str, Any] | None = None,
    skill_manifest: list[dict[str, Any]] | None = None,
    scene_awareness: Mapping[str, Any] | None = None,
) -> ChatRuntimeInput:
    """规范化外部输入并创建快照；缺失情绪快照时才从已规范化状态派生默认值。"""
    normalized_emotion_state = normalize_chat_emotion_context(emotion_state)
    normalized_emotion_snapshot = (
        dict(emotion_snapshot)
        if isinstance(emotion_snapshot, Mapping)
        else build_chat_emotion_snapshot(
            emotion_context=normalized_emotion_state,
            source="chat_runtime_input",
        )
    )
    return ChatRuntimeInput(
        request_id=str(request_id or "").strip(),
        person_id=str(person_id or "").strip(),
        workspace_id=str(workspace_id or "").strip(),
        session_id=str(session_id or "").strip(),
        thread_id=(str(thread_id).strip() if thread_id else None),
        user_message=str(user_message or ""),
        render_target_lang=_resolve_render_target_lang(render_target_lang),
        retrieval_summary=_copy_mapping(retrieval_summary),
        retrieval_evidence_items=_copy_rows(retrieval_evidence_items),
        emotion_state=normalized_emotion_state,
        emotion_snapshot=normalized_emotion_snapshot,
        bond_summary=dict(bond_summary) if isinstance(bond_summary, Mapping) else {},
        attachments=_copy_rows(attachments),
        multimodal_observations=_copy_mapping(multimodal_observations),
        attachment_summary=str(attachment_summary or "").strip(),
        scene_type=(str(scene_type).strip().upper() if scene_type else None),
        scene_type_source=(str(scene_type_source).strip() if scene_type_source else None),
        committed_route_token=(str(committed_route_token).strip() if committed_route_token else None),
        committed_route_token_detail=_copy_mapping(committed_route_token_detail),
        skill_manifest=_copy_skill_manifest(skill_manifest),
        scene_awareness=_copy_scene_awareness(scene_awareness),
    )


def _extract_section(
    runtime_payload: Mapping[str, Any],
    *,
    section: str,
) -> dict[str, Any]:
    """安全复制一个顶层分区；错误形状退化为空，避免兼容读取泄露调用方引用。"""
    raw = runtime_payload.get(section)
    if isinstance(raw, Mapping):
        return dict(raw)
    return {}


def get_chat_runtime_prompt_context(runtime_payload: Mapping[str, Any]) -> dict[str, Any]:
    prompt_context_section = _extract_section(runtime_payload, section="prompt_context")
    payload_raw = prompt_context_section.get("payload")
    if isinstance(payload_raw, Mapping):
        return dict(payload_raw)
    return {}


def get_chat_runtime_render_target_lang(runtime_payload: Mapping[str, Any]) -> str:
    render = _extract_section(runtime_payload, section="render")
    return _resolve_render_target_lang(
        str(render.get("target_lang") or runtime_payload.get("render_target_lang") or "").strip().lower()
    )
