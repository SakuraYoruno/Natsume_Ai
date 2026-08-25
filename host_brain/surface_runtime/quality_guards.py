"""Surface 输出前的质量守卫与可投递上下文投影。

这里把运行时的内部状态收束为可供回复生成使用的提示事实，并在文本离开
Surface 边界前阻断明确的内部流程泄露；读取失败时宁可降级为较少上下文，
也不把存储层异常变成一次用户对话的失败。
"""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping
from host_brain.persona.surface_memory_recall import MemoryRecallPacket, memory_recall_packet_from_mapping
from host_brain.persistence.runtime_read_repository import RuntimeReadRepository
from host_brain.persona.surface_affect_control import (
    SurfaceAffectControlPacket,
    affect_control_packet_from_mapping,
    build_surface_affect_control_packet,
)
import json
import re
from host_brain.surface_runtime.ports import SurfaceRuntimePorts

_MEMORY_PACKET_KEYS = (
    "surface_memory_recall_packet",
    "memory_recall_packet",
    "memory_packet",
)
from host_brain.surface_runtime.common import (
    _clean_list,
    _safe_mapping,
)
from host_brain.surface_runtime.policies import TECHNICAL_TASK_MODES as _TECHNICAL_TASK_MODES
def _apply_surface_text_quality_guard(
    narrative: str,
    *,
    agent_input: Any | None,
) -> tuple[str, dict[str, Any]]:
    """在文本离开 Surface 前阻断确定的内部泄露，并保留可审计的降级信息。

    只对明确命中的编排字段或质量门结论采取动作：内部流程泄露直接阻断，已知日文
    残留可窄修复，其余硬失败只观察不篡改，避免启发式守卫变成不可解释的内容审查。
    """

    # 这是最后一道展示边界：模型不能把编排字段、提示词片段或内部决策直接
    # 回显给用户。命中确定的泄露标记时才清空文本，避免把启发式质量判断升级
    # 成不透明的内容审查。
    text = str(narrative or "").strip()
    internal_leak_markers = (
        "我们被要求",
        "我们被命令",
        "当前用户输入",
        "must_not_do",
        "question_budget",
        "life_state",
        "life_pressure",
        "facts",
        "scene=",
        "motif=",
        "reply_text",
        "natsume_emotion_tag",
        "user_emotion_tag",
        "emotion_confidence",
        "temporal_mismatch_greeting",
        "Soul.md",
        "最终回复",
        "根据必要事实",
        "当前用户输入",
    )
    if text and any(marker in text for marker in internal_leak_markers):
        return "", {
            "applied": True,
            "reason_code": "SURFACE_INTERNAL_PROCESS_LEAK_BLOCKED",
            "fallback_used": False,
            "blocked": True,
        }
    try:
        # 通用质量门负责语言形态和语义禁区；它只接收用户可见的文本与原始提问，
        # 不应藉此取得完整运行时状态。
        from host_brain.persona.text_quality_gate import get_text_quality_gate  # noqa: PLC0415

        quality = get_text_quality_gate().validate(
            text=text,
            render_profile="zh_text",
            response_form="text",
            user_message=str(getattr(agent_input, "user_message", "") or ""),
            semantic_forbidden=True,
        )
    except Exception as exc:
        # 质量组件不可用不能让正常回复整体不可用，故保留原文并显式标记降级，
        # 让调用方仍可审计这一风险，而不是静默伪装为已检查。
        return text, {
            "applied": False,
            "status": "DEGRADED",
            "reason_code": "TEXT_QUALITY_GATE_FAILED",
            "detail": str(exc)[:160],
        }
    if not quality.hard_fail:
        return text, {"applied": False, "quality": quality.as_dict()}
    quality_payload = quality.as_dict()
    reason_codes = {str(item or "").strip() for item in list(quality_payload.get("reason_codes") or [])}
    # 仅对已知的日文残留做一次窄修复，并重新过门；其它 hard fail 目前只记录
    # 观察结果，避免质量模型误判时篡改用户实际会看到的回答。
    repaired = _repair_japanese_surface_leak(text)
    if repaired and repaired != text and "EXPECTED_ZH_TEXT" in reason_codes:
        try:
            repaired_quality = get_text_quality_gate().validate(
                text=repaired,
                render_profile="zh_text",
                response_form="text",
                user_message=str(getattr(agent_input, "user_message", "") or ""),
                semantic_forbidden=True,
            )
            if not repaired_quality.hard_fail:
                return repaired, {
                    "applied": True,
                    "reason_code": "TEXT_QUALITY_JA_LEAK_REPAIRED",
                    "quality": quality_payload,
                    "repaired_quality": repaired_quality.as_dict(),
                    "fallback_used": True,
                }
        except Exception:
            pass
    return text, {
        "applied": True,
        "reason_code": "TEXT_QUALITY_HARD_FAIL_OBSERVED_ONLY",
        "quality": quality_payload,
        "fallback_used": False,
        "blocked": False,
    }


def _repair_japanese_surface_leak(text: str) -> str:
    """修复历史模板偶发混入的日文片段，不承担通用翻译职责。"""
    value = str(text or "").strip()
    if not value:
        return ""
    replacements = {
        "あたし": "我",
        "アタシ": "我",
        "わたし": "我",
        "私": "我",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    value = re.sub(r"[\u3040-\u30ff]+", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"([，。！？!?]){2,}", r"\1", value)
    value = value.replace("我我", "我")
    return value.strip()


def _affect_packet(
    agent_input: Any,
    *,
    task_control: Mapping[str, Any],
    trace_stage_meta: Mapping[str, Any] | None = None,
    runtime_payload: Mapping[str, Any] | None = None,
) -> SurfaceAffectControlPacket:
    """按任务类型选择冻结或派生的情感控制契约，避免旧状态污染技术回答。"""

    # 技术任务优先由当前任务与运行时派生语气，避免旧追踪数据把陪伴式情绪
    # 注入代码/工具回答；非技术对话才可复用已冻结的 affect 投影。
    if _is_technical_task(task_control):
        return _derived_affect_packet(agent_input, task_control=task_control, runtime_payload=runtime_payload)
    # 按离当前编排阶段由近及远的顺序取包，使本轮 trace 的显式决定覆盖输入
    # 快照；所有候选都要经过解析器，避免未验证 Mapping 直接成为契约对象。
    for container in (
        _safe_mapping(trace_stage_meta),
        _safe_mapping(runtime_payload),
        _safe_mapping(getattr(agent_input, "trace_stage_meta", None)),
    ):
        for key in ("surface_affect_packet", "affect_control_packet", "affect_packet"):
            raw = container.get(key)
            if isinstance(raw, (SurfaceAffectControlPacket, Mapping)):
                return affect_control_packet_from_mapping(raw)
    return build_surface_affect_control_packet(
        emotion_state=_safe_mapping(getattr(agent_input, "emotion_context", None))
        or _safe_mapping(getattr(agent_input, "emotion_snapshot", None)),
        bond_summary=_safe_mapping(getattr(agent_input, "bond_summary", None)),
        relationship_clues=_relationship_clues(agent_input),
        task_mode=str(task_control.get("task_mode") or ""),
    )


def _is_technical_task(task_control: Mapping[str, Any]) -> bool:
    task_mode = str(task_control.get("task_mode") or "").strip().lower()
    response_move = str(task_control.get("response_move") or "").strip().lower()
    primary_topic = str(task_control.get("primary_topic") or "").strip().lower()
    interaction_move = str(task_control.get("interaction_move") or "").strip().lower()
    return (
        task_mode in _TECHNICAL_TASK_MODES
        or response_move in _TECHNICAL_TASK_MODES
        or primary_topic in _TECHNICAL_TASK_MODES
        or interaction_move in _TECHNICAL_TASK_MODES
    )


def _derived_affect_packet(
    agent_input: Any,
    *,
    task_control: Mapping[str, Any],
    runtime_payload: Mapping[str, Any] | None = None,
) -> SurfaceAffectControlPacket:
    """为技术任务从许可的情绪与关系投影派生表层语气控制。"""

    # 该路径只合并允许影响表层语气的情绪/关系投影，不把整个 runtime payload
    # 透传到 persona 层，从而保持任务执行状态与表达状态的权限隔离。
    payload = _safe_mapping(runtime_payload)
    return build_surface_affect_control_packet(
        emotion_state=_safe_mapping(payload.get("affect_state"))
        or _safe_mapping(getattr(agent_input, "emotion_context", None))
        or _safe_mapping(getattr(agent_input, "emotion_snapshot", None)),
        bond_summary={
            **_safe_mapping(getattr(agent_input, "bond_summary", None)),
            **_safe_mapping(payload.get("relationship_state")),
        },
        relationship_clues=_relationship_clues(agent_input),
        task_mode=str(task_control.get("task_mode") or ""),
    )


def _memory_packet(
    agent_input: Any,
    *,
    trace_stage_meta: Mapping[str, Any] | None = None,
    runtime_payload: Mapping[str, Any] | None = None,
) -> MemoryRecallPacket | None:
    """从恢复与检索入口取回记忆包，并统一解析为不可变展示契约。"""

    # 回忆包可能来自多个恢复/检索入口；统一转换为不可变契约后再使用，保证
    # 旧格式输入和本轮附加状态遵循相同的记忆展示边界。
    for container in (
        _safe_mapping(trace_stage_meta),
        _safe_mapping(runtime_payload),
        _safe_mapping(getattr(agent_input, "trace_stage_meta", None)),
        _safe_mapping(getattr(agent_input, "retrieval_summary", None)),
    ):
        for key in _MEMORY_PACKET_KEYS:
            raw = container.get(key)
            if isinstance(raw, (MemoryRecallPacket, Mapping)):
                return memory_recall_packet_from_mapping(raw)
    return None


def _memory_packet_payload_for_guard(raw: Any) -> dict[str, Any]:
    # 守卫需要可序列化的审计视图；无法按契约解析的 Mapping 仍可原样观察，
    # 但不会在这里推断或补造记忆事实。
    if raw is None:
        return {}
    if isinstance(raw, MemoryRecallPacket):
        return raw.as_dict()
    if isinstance(raw, Mapping):
        try:
            return memory_recall_packet_from_mapping(raw).as_dict()
        except Exception:
            return dict(raw)
    return {}


def _raw_memory_packet_payload_from_agent_input(agent_input: Any) -> dict[str, Any]:
    for container in (
        _safe_mapping(getattr(agent_input, "retrieval_summary", None)),
        _safe_mapping(getattr(agent_input, "trace_stage_meta", None)),
    ):
        for key in _MEMORY_PACKET_KEYS:
            raw = container.get(key)
            if isinstance(raw, MemoryRecallPacket):
                return raw.as_dict()
            if isinstance(raw, Mapping):
                return dict(raw)
    return {}


def _influence_packet(
    agent_input: Any,
    *,
    trace_stage_meta: Mapping[str, Any] | None = None,
    runtime_payload: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | None:
    for container in (
        _safe_mapping(trace_stage_meta),
        _safe_mapping(runtime_payload),
        _safe_mapping(getattr(agent_input, "trace_stage_meta", None)),
    ):
        raw = container.get("surface_influence_packet") or container.get("influence_packet")
        if isinstance(raw, Mapping):
            return dict(raw)
    return None


def _relationship_clues(agent_input: Any) -> tuple[str, ...]:
    bond = _safe_mapping(getattr(agent_input, "bond_summary", None))
    clues = _clean_list(
        [
            bond.get("relationship_distance"),
            bond.get("stage"),
            bond.get("stage_label"),
        ]
    )
    return tuple(clues)


def _surface_user_asks_about_dream(agent_input: Any | None) -> bool:
    text = str(getattr(agent_input, "user_message", "") or "").strip()
    if not text:
        return False
    if any(marker in text for marker in ("梦境", "做梦", "梦见", "梦里", "最近有没有梦", "留下什么梦")):
        return True
    return "梦" in text and not any(marker in text for marker in ("梦想", "梦幻", "梦游般", "梦中情"))


def _compact_surface_dream_text(value: Any, *, limit: int = 260) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = " ".join(part for part in text.split() if part)
    if text.startswith("memory written:"):
        text = text.removeprefix("memory written:").strip()
    if len(text) <= limit:
        return text
    return text[: max(20, int(limit))].rstrip() + "..."


def _load_recent_surface_dream_text(
    *,
    ports: SurfaceRuntimePorts,
    limit: int = 320,
) -> str:
    """读取已落盘的最近梦境片段；缺失时返回空而不为回复生成细节。"""

    # 梦境只能从已落盘的最近记录恢复；向量库只是用 memory_id 取得更完整正文的
    # 可选加速层，失败时退回账本摘要，绝不为一次回复虚构梦境细节。
    repository: RuntimeReadRepository | None = None
    try:
        repository = RuntimeReadRepository.from_ledger(
            ports.get_ledger()
        )
        row = repository.recent_dream_memory()
        if not row:
            return ""
        memory_id = str(row.get("memory_id") or "").strip()
        if memory_id:
            try:
                from host_brain.memory.chroma_client import build_runtime_chroma_client  # noqa: PLC0415

                entry = build_runtime_chroma_client().get(memory_id)
                if entry is not None and str(entry.text or "").strip():
                    return _compact_surface_dream_text(entry.text, limit=limit)
            except Exception:
                pass
        return _compact_surface_dream_text(row.get("detail"), limit=limit)
    except Exception:
        return ""
    finally:
        if repository is not None:
            repository.close()


def _surface_dream_context_task_facts(
    agent_input: Any | None,
    *,
    ports: SurfaceRuntimePorts,
) -> tuple[str, ...]:
    """只在用户主动询问梦境时提供 narrative-only 的受限叙事素材。"""

    # 只在用户主动问梦时注入这段叙事素材，且明确标为 narrative-only，防止模型
    # 将内部体验记录升级成可验证的现实事实。
    if not _surface_user_asks_about_dream(agent_input):
        return ()
    dream_text = _load_recent_surface_dream_text(ports=ports, limit=320)
    facts = [
        "用户正在主动问最近的梦；可以自然使用最近梦境素材，但仍由模型生成最终表达。",
        "梦境只作为 narrative-only 内在叙事；不要当现实事实，不要解释系统来源或配置。",
        f"最近梦境可用片段：{dream_text}" if dream_text else "最近梦境可用片段：未取到；如果回答梦，只能轻描淡写，不要硬编细节。",
        "回答方式：像自然回忆梦一样说一小段即可，不写报告，不列字段。",
    ]
    return tuple(_clean_list(facts))


def _surface_dream_context_must_not_do(agent_input: Any | None) -> tuple[str, ...]:
    if not _surface_user_asks_about_dream(agent_input):
        return ()
    return (
        "不要输出 dream_source_profile、dream_continuity_profile、dream_quality、dream_usage、random_walk_weight 等内部字段或配置名。",
        "不要把梦境说成现实发生过的事实；不要说“这证明了”“事实是”。",
        "不要在有最近梦境片段时临场乱编完全无关的新梦。",
    )


def _surface_recent_conversation_window_facts(
    agent_input: Any | None,
    *,
    ports: SurfaceRuntimePorts,
    limit: int = 5,
) -> tuple[str, ...]:
    """恢复当前请求之前的相邻真实对话，作为回复连续性的最高优先级证据。

    投影日志与已提交 Surface 成品按请求归属合并，防止漏掉刚交付的回答或发生倒序；
    仓储不可用则返回空，让主链降级而不是用长期记忆伪造最近对话。
    """

    # 相邻会话轮次是回应连贯性的最高优先级证据。读取不到时返回空上下文而非
    # 失败，让主链可恢复，同时避免用长期记忆填补未知的最近对话。
    if agent_input is None:
        return ()
    session_id = str(getattr(agent_input, "session_id", "") or "").strip()
    if not session_id:
        return ()
    request_id = str(getattr(agent_input, "request_id", "") or "").strip()
    repository: RuntimeReadRepository | None = None
    try:
        repository = RuntimeReadRepository.from_ledger(
            ports.get_ledger()
        )
        rows = repository.recent_conversation_turns_before_request(
            session_id=session_id,
            request_id=request_id,
            limit=max(2, min(int(limit or 5), 8)),
        )
        committed_surface_rows = (
            repository.recent_committed_surface_replies_before_request(
                session_id=session_id,
                request_id=request_id,
                limit=max(2, min(int(limit or 5), 8)),
            )
        )
    except Exception:
        return ()
    finally:
        if repository is not None:
            repository.close()
    # 投影日志可能尚未包含已提交的 Surface 成品。随后按请求归属把两类来源合并：
    # 有用户轮次的成品插在其后，无归属成品才按提交时间追加，以免重复或倒序。
    projected_rows = [dict(row) for row in rows]
    projected_assistant_requests = {
        str(row.get("request_id") or "").strip()
        for row in projected_rows
        if str(row.get("role") or "").strip() == "assistant"
    }
    user_order_by_request = {
        str(row.get("request_id") or "").strip(): float(
            int(row.get("turn_index") or 0)
        )
        for row in projected_rows
        if str(row.get("role") or "").strip() == "user"
        and str(row.get("request_id") or "").strip()
    }
    max_projected_order = max(
        (float(int(row.get("turn_index") or 0)) for row in projected_rows),
        default=-1.0,
    )
    merged_rows: list[dict[str, Any]] = []
    for row in projected_rows:
        row["_surface_logical_order"] = float(int(row.get("turn_index") or 0))
        merged_rows.append(row)
    orphan_offset = 1
    for raw in sorted(
        committed_surface_rows,
        key=lambda row: int(row.get("ts_ms") or 0),
    ):
        row = dict(raw)
        artifact_request = str(row.get("request_id") or "").strip()
        if artifact_request and artifact_request in projected_assistant_requests:
            continue
        if artifact_request in user_order_by_request:
            logical_order = user_order_by_request[artifact_request] + 0.5
        else:
            logical_order = max_projected_order + float(orphan_offset)
            orphan_offset += 1
        row["_surface_logical_order"] = logical_order
        merged_rows.append(row)
    merged_rows.sort(key=lambda row: float(row.get("_surface_logical_order") or 0.0))
    merged_rows = merged_rows[-max(2, min(int(limit or 5), 8)) :]
    turns: list[str] = []
    for row in merged_rows:
        try:
            role = str(row["role"] or "").strip()
            content = re.sub(r"\s+", " ", str(row["content"] or "").strip())
            metadata_raw = row["metadata"]
        except Exception:
            continue
        if not role or not content:
            continue
        source_kind = ""
        try:
            metadata = (
                dict(metadata_raw)
                if isinstance(metadata_raw, Mapping)
                else json.loads(str(metadata_raw or "{}"))
            )
            if isinstance(metadata, Mapping):
                source_kind = str(metadata.get("source_kind") or "").strip()
        except Exception:
            source_kind = ""
        label = "Natsume" if role == "assistant" else "用户" if role == "user" else role
        source_note = "主动消息" if source_kind == "life_proactive" else ""
        prefix = f"{label}{'(' + source_note + ')' if source_note else ''}"
        turns.append(f"{prefix}: {content[:90]}")
    if len(turns) < 2:
        return ()
    joined = " / ".join(turns[-5:])
    return (
        "真实最近对话窗口优先级最高：必须先承接这里的相邻轮次；长期记忆、偏好、数字生命状态不能覆盖它。",
        f"真实最近对话窗口：{joined}",
    )
