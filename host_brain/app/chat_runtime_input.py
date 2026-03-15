from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any, Mapping

from host_brain.app.emotion_context import (
    build_chat_emotion_snapshot,
    normalize_chat_emotion_context,
)


CHAT_RUNTIME_INPUT_VERSION = "chat_runtime_input.v2"


def normalize_recall_slot_text(
    recall_slot_text: Mapping[str, Any] | None,
) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for slot_name, raw_value in dict(recall_slot_text or {}).items():
        text = str(raw_value or "").strip()
        if not text:
            continue
        normalized[str(slot_name).strip()] = text
    return normalized


def _resolve_render_target_lang(render_target_lang: str | None) -> str:
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


@dataclass(slots=True)
class ChatRuntimeInput:
    request_id: str
    session_id: str
    thread_id: str | None
    user_message: str
    render_target_lang: str
    retrieval_summary: dict[str, Any] = field(default_factory=dict)
    retrieval_evidence_items: list[dict[str, Any]] = field(default_factory=list)
    emotion_state: dict[str, Any] = field(default_factory=dict)
    emotion_snapshot: dict[str, Any] = field(default_factory=dict)
    recall_slot_text: dict[str, str] = field(default_factory=dict)
    scene_type: str | None = None
    scene_type_source: str | None = None
    committed_route_token: str | None = None
    committed_route_token_detail: dict[str, Any] = field(default_factory=dict)
    skill_manifest: list[dict[str, Any]] = field(default_factory=list)

    def to_runtime_payload(
        self,
        *,
        prompt_context: Mapping[str, Any],
        prompt_context_validation: Mapping[str, Any],
        prompt_context_observability: Mapping[str, Any],
    ) -> dict[str, Any]:
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

        return {
            "version": CHAT_RUNTIME_INPUT_VERSION,
            "request": {
                "request_id": self.request_id,
                "session_id": self.session_id,
                "thread_id": self.thread_id,
                "user_message": self.user_message,
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
    session_id: str,
    thread_id: str | None,
    user_message: str,
    retrieval_summary: Mapping[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    emotion_state: Mapping[str, Any] | None = None,
    emotion_snapshot: Mapping[str, Any] | None = None,
    render_target_lang: str | None = None,
    recall_slot_text: Mapping[str, Any] | None = None,
    scene_type: str | None = None,
    scene_type_source: str | None = None,
    committed_route_token: str | None = None,
    committed_route_token_detail: Mapping[str, Any] | None = None,
    skill_manifest: list[dict[str, Any]] | None = None,
) -> ChatRuntimeInput:
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
        session_id=str(session_id or "").strip(),
        thread_id=(str(thread_id).strip() if thread_id else None),
        user_message=str(user_message or ""),
        render_target_lang=_resolve_render_target_lang(render_target_lang),
        retrieval_summary=_copy_mapping(retrieval_summary),
        retrieval_evidence_items=_copy_rows(retrieval_evidence_items),
        emotion_state=normalized_emotion_state,
        emotion_snapshot=normalized_emotion_snapshot,
        recall_slot_text=normalize_recall_slot_text(recall_slot_text),
        scene_type=(str(scene_type).strip().upper() if scene_type else None),
        scene_type_source=(str(scene_type_source).strip() if scene_type_source else None),
        committed_route_token=(str(committed_route_token).strip() if committed_route_token else None),
        committed_route_token_detail=_copy_mapping(committed_route_token_detail),
        skill_manifest=_copy_skill_manifest(skill_manifest),
    )


def _extract_section(
    runtime_payload: Mapping[str, Any],
    *,
    section: str,
) -> dict[str, Any]:
    raw = runtime_payload.get(section)
    if isinstance(raw, Mapping):
        return dict(raw)
    return {}


def get_chat_runtime_request(runtime_payload: Mapping[str, Any]) -> dict[str, Any]:
    return _extract_section(runtime_payload, section="request")


def get_chat_runtime_render(runtime_payload: Mapping[str, Any]) -> dict[str, Any]:
    return _extract_section(runtime_payload, section="render")


def get_chat_runtime_retrieval(runtime_payload: Mapping[str, Any]) -> dict[str, Any]:
    retrieval = _extract_section(runtime_payload, section="retrieval")
    if retrieval:
        evidence_items = retrieval.get("evidence_items")
        retrieval["evidence_items"] = _copy_rows(evidence_items if isinstance(evidence_items, list) else [])
        return retrieval
    legacy = runtime_payload.get("retrieval")
    if isinstance(legacy, Mapping):
        copied = dict(legacy)
        evidence_items = copied.get("evidence_items")
        copied["evidence_items"] = _copy_rows(evidence_items if isinstance(evidence_items, list) else [])
        return copied
    return {}


def get_chat_runtime_emotion_state(runtime_payload: Mapping[str, Any]) -> dict[str, Any]:
    emotion_section = _extract_section(runtime_payload, section="emotion")
    state_raw = emotion_section.get("state")
    if isinstance(state_raw, Mapping):
        return normalize_chat_emotion_context(state_raw)
    legacy = runtime_payload.get("emotion_context")
    if isinstance(legacy, Mapping):
        return normalize_chat_emotion_context(legacy)
    return {}


def get_chat_runtime_emotion_snapshot(runtime_payload: Mapping[str, Any]) -> dict[str, Any]:
    emotion_section = _extract_section(runtime_payload, section="emotion")
    snapshot_raw = emotion_section.get("snapshot")
    if isinstance(snapshot_raw, Mapping):
        return dict(snapshot_raw)
    legacy = runtime_payload.get("emotion_snapshot")
    if isinstance(legacy, Mapping):
        return dict(legacy)
    return {}


def get_chat_runtime_prompt_context_section(runtime_payload: Mapping[str, Any]) -> dict[str, Any]:
    return _extract_section(runtime_payload, section="prompt_context")


def get_chat_runtime_prompt_context(runtime_payload: Mapping[str, Any]) -> dict[str, Any]:
    prompt_context_section = get_chat_runtime_prompt_context_section(runtime_payload)
    payload_raw = prompt_context_section.get("payload")
    if isinstance(payload_raw, Mapping):
        return dict(payload_raw)
    return {}


def resolve_prompt_context_style_row(
    prompt_context: Mapping[str, Any],
    *,
    node_id: str,
) -> dict[str, Any]:
    style_pack_raw = prompt_context.get("style_pack")
    style_pack = dict(style_pack_raw) if isinstance(style_pack_raw, Mapping) else {}
    style_rows = (
        list(style_pack.get("style_canon") or [])
        if isinstance(style_pack.get("style_canon"), list)
        else []
    )

    normalized_node_id = str(node_id or "").strip()
    for row in style_rows:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("node_id") or "").strip() == normalized_node_id:
            return dict(row)

    for row in style_rows:
        if isinstance(row, Mapping):
            return dict(row)
    return {}


def get_chat_runtime_prompt_context_style_row(
    runtime_payload: Mapping[str, Any],
    *,
    node_id: str,
) -> dict[str, Any]:
    return resolve_prompt_context_style_row(
        get_chat_runtime_prompt_context(runtime_payload),
        node_id=node_id,
    )


def get_chat_runtime_prompt_context_validation(runtime_payload: Mapping[str, Any]) -> dict[str, Any]:
    prompt_context_section = get_chat_runtime_prompt_context_section(runtime_payload)
    validation_raw = prompt_context_section.get("validation")
    if isinstance(validation_raw, Mapping):
        return dict(validation_raw)
    return {}


def get_chat_runtime_prompt_context_observability(runtime_payload: Mapping[str, Any]) -> dict[str, Any]:
    prompt_context_section = get_chat_runtime_prompt_context_section(runtime_payload)
    observability_raw = prompt_context_section.get("observability")
    if isinstance(observability_raw, Mapping):
        return dict(observability_raw)
    return {}


def get_chat_runtime_graph(runtime_payload: Mapping[str, Any]) -> dict[str, Any]:
    graph_section = _extract_section(runtime_payload, section="graph")
    if graph_section:
        skill_manifest = graph_section.get("skill_manifest")
        graph_section["skill_manifest"] = _copy_skill_manifest(
            skill_manifest if isinstance(skill_manifest, list) else []
        )
        return graph_section
    graph_section = {}
    if runtime_payload.get("scene_type") is not None:
        graph_section["scene_type"] = runtime_payload.get("scene_type")
    if runtime_payload.get("scene_type_source") is not None:
        graph_section["scene_type_source"] = runtime_payload.get("scene_type_source")
    if runtime_payload.get("committed_route_token") is not None:
        graph_section["committed_route_token"] = runtime_payload.get("committed_route_token")
    committed_detail = runtime_payload.get("committed_route_token_detail")
    if isinstance(committed_detail, Mapping):
        graph_section["committed_route_token_detail"] = dict(committed_detail)
    skill_manifest = runtime_payload.get("skill_manifest")
    if isinstance(skill_manifest, list):
        graph_section["skill_manifest"] = _copy_skill_manifest(skill_manifest)
    return graph_section


def get_chat_runtime_user_message(runtime_payload: Mapping[str, Any]) -> str:
    request = get_chat_runtime_request(runtime_payload)
    return str(request.get("user_message") or runtime_payload.get("user_message") or "")


def get_chat_runtime_render_target_lang(runtime_payload: Mapping[str, Any]) -> str:
    render = get_chat_runtime_render(runtime_payload)
    return _resolve_render_target_lang(
        str(render.get("target_lang") or runtime_payload.get("render_target_lang") or "").strip().lower()
    )


def get_chat_runtime_skill_manifest(runtime_payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    graph_section = get_chat_runtime_graph(runtime_payload)
    skill_manifest = graph_section.get("skill_manifest")
    if isinstance(skill_manifest, list):
        return _copy_skill_manifest(skill_manifest)
    return []
