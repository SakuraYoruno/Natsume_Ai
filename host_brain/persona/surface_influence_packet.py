from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from host_brain.persona.organ_influence_contract import (
    OrganInfluencePacket,
    build_organ_influence_packet,
    organ_influence_packet_from_mapping,
)


@dataclass(frozen=True)
class TaskInfluence:
    task_mode: str = ""
    response_move: str = ""
    information_budget: str = ""
    question_budget: int | None = None
    facts: tuple[str, ...] = ()
    style_anchor: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_mode": self.task_mode,
            "response_move": self.response_move,
            "information_budget": self.information_budget,
            "question_budget": self.question_budget,
            "facts": list(self.facts),
            "style_anchor": list(self.style_anchor),
        }


@dataclass(frozen=True)
class AffectInfluence:
    emotional_tone: str = ""
    expression_intensity: float = 0.0
    warmth_bias: float = 0.0
    withdraw_bias: float = 0.0
    irritation_bias: float = 0.0
    must_not_do: tuple[str, ...] = ()
    style_anchor: tuple[str, ...] = ()
    # --- Inner-state leakage fields ---
    inner_weather: str = ""
    body_state_hint: str = ""
    care_signal: float = 0.0
    continuity_signal: float = 0.0
    tension_hint: str = ""
    liveliness: float = 0.5
    active_loops: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "emotional_tone": self.emotional_tone,
            "expression_intensity": round(float(self.expression_intensity or 0.0), 4),
            "warmth_bias": round(float(self.warmth_bias or 0.0), 4),
            "withdraw_bias": round(float(self.withdraw_bias or 0.0), 4),
            "irritation_bias": round(float(self.irritation_bias or 0.0), 4),
            "must_not_do": list(self.must_not_do),
            "style_anchor": list(self.style_anchor),
            "inner_weather": self.inner_weather,
            "body_state_hint": self.body_state_hint,
            "care_signal": round(float(self.care_signal or 0.0), 4),
            "continuity_signal": round(float(self.continuity_signal or 0.0), 4),
            "tension_hint": self.tension_hint,
            "liveliness": round(float(self.liveliness or 0.5), 4),
            "active_loops": int(self.active_loops or 0),
        }


@dataclass(frozen=True)
class RelationshipInfluence:
    relationship_distance: str = ""
    stage_label: str = ""
    relationship_phase: str = ""
    relationship_substage: str = ""
    nickname_policy: str = ""
    tease_permission: str = ""
    intimacy_ceiling: float = 0.0
    must_not_do: tuple[str, ...] = ()
    style_anchor: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "relationship_distance": self.relationship_distance,
            "stage_label": self.stage_label,
            "relationship_phase": self.relationship_phase,
            "relationship_substage": self.relationship_substage,
            "nickname_policy": self.nickname_policy,
            "tease_permission": self.tease_permission,
            "intimacy_ceiling": round(float(self.intimacy_ceiling or 0.0), 4),
            "must_not_do": list(self.must_not_do),
            "style_anchor": list(self.style_anchor),
        }


@dataclass(frozen=True)
class MemoryInfluence:
    facts: tuple[str, ...] = ()
    must_not_do: tuple[str, ...] = ()
    style_anchor: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "facts": list(self.facts),
            "must_not_do": list(self.must_not_do),
            "style_anchor": list(self.style_anchor),
        }


@dataclass(frozen=True)
class ContinuityInfluence:
    recent_turns_injected: int = 0
    summary_present: bool = False
    should_be_brief: bool = False
    allow_followup: bool = True
    must_not_do: tuple[str, ...] = ()
    style_anchor: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "recent_turns_injected": int(self.recent_turns_injected or 0),
            "summary_present": bool(self.summary_present),
            "should_be_brief": bool(self.should_be_brief),
            "allow_followup": bool(self.allow_followup),
            "must_not_do": list(self.must_not_do),
            "style_anchor": list(self.style_anchor),
        }


@dataclass(frozen=True)
class SafetyInfluence:
    must_not_do: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"must_not_do": list(self.must_not_do)}


@dataclass(frozen=True)
class DeliveryInfluence:
    response_form: str = "text"
    vts_expression_id: str = ""
    output_channel: str = "chat_text"
    style_anchor: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "response_form": self.response_form,
            "vts_expression_id": self.vts_expression_id,
            "output_channel": self.output_channel,
            "style_anchor": list(self.style_anchor),
        }


@dataclass(frozen=True)
class SurfaceInfluencePacket:
    """器官族状态进入 Surface 前的唯一压缩影响包。

    各分区只能影响任务呈现、语气、关系限度、记忆使用和投递形态；原始器官输出
    留在 organ_trace 之外的上游，禁止作为自然语言建议或完整内部状态注入模型。
    """
    task: TaskInfluence = field(default_factory=TaskInfluence)
    affect: AffectInfluence = field(default_factory=AffectInfluence)
    relationship: RelationshipInfluence = field(default_factory=RelationshipInfluence)
    memory: MemoryInfluence = field(default_factory=MemoryInfluence)
    continuity: ContinuityInfluence = field(default_factory=ContinuityInfluence)
    safety: SafetyInfluence = field(default_factory=SafetyInfluence)
    delivery: DeliveryInfluence = field(default_factory=DeliveryInfluence)
    source: str = "organ_influence_compressed"
    organ_trace: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task": self.task.as_dict(),
            "affect": self.affect.as_dict(),
            "relationship": self.relationship.as_dict(),
            "memory": self.memory.as_dict(),
            "continuity": self.continuity.as_dict(),
            "safety": self.safety.as_dict(),
            "delivery": self.delivery.as_dict(),
            "source": self.source,
            "organ_trace": dict(self.organ_trace),
        }


def build_surface_influence_packet(
    *,
    reply_plan: Mapping[str, Any] | None = None,
    affect_packet: Mapping[str, Any] | None = None,
    affect_state: Mapping[str, Any] | None = None,
    relationship_state: Mapping[str, Any] | None = None,
    drive_state: Mapping[str, Any] | None = None,
    continuity: Mapping[str, Any] | None = None,
    memory_packet: Mapping[str, Any] | None = None,
    delivery: Mapping[str, Any] | None = None,
    organ_context: Mapping[str, Any] | None = None,
    organ_influence_packet: OrganInfluencePacket | Mapping[str, Any] | None = None,
) -> SurfaceInfluencePacket:
    """将面向提示词的器官影响压缩成小型类型包。

    这是器官族状态通往 Surface 生成器的唯一桥梁，刻意不保留原始历史、原始
    记忆行或模块生成的自然语言建议，避免内部子系统获得直接操控回复的权限。
    """

    # 上游既可给结构化 organ packet，也可给兼容上下文；先统一成契约包，再按每个
    # 字段的 authority 规则合并，不能让任一来源整体覆盖另一个来源。
    organ_packet = _coerce_organ_influence_packet(
        organ_context=organ_context,
        organ_influence_packet=organ_influence_packet,
    )
    plan = _safe_mapping(reply_plan)
    organ_task = _safe_mapping(organ_packet.task_delta)
    affect = _safe_mapping(affect_packet)
    organ_affect = _safe_mapping(organ_packet.emotion_delta)
    affect_block = _max_numeric_mapping(_safe_mapping(affect_state), organ_affect)
    # 数值驱动取最大强度以保留安全/压力信号，关系、连续性和投递字段则由明确的
    # 后序状态覆盖；这些合并只生成表达投影，不会写回生命周期状态。
    relationship = {**_safe_mapping(organ_packet.relationship_delta), **_safe_mapping(relationship_state)}
    drive = _max_numeric_mapping(_safe_mapping(drive_state), _safe_mapping(organ_packet.drive_delta))
    continuity_block = {**_safe_mapping(organ_packet.continuity_delta), **_safe_mapping(continuity)}
    memory = _safe_mapping(memory_packet)
    delivery_payload = {**_safe_mapping(organ_packet.delivery_delta), **_safe_mapping(delivery)}

    relationship_distance = _clean_text(
        affect.get("relationship_distance")
        or relationship.get("relationship_distance")
        or relationship.get("relationship_phase")
    )
    emotional_tone = _clean_text(affect.get("emotional_tone") or organ_affect.get("emotional_tone"))
    task_mode = _first_nonempty(
        plan.get("task_mode"),
        plan.get("primary_topic"),
        plan.get("response_form_reason"),
        plan.get("response_goal"),
    )
    response_move = _first_nonempty(
        plan.get("response_move"),
        plan.get("interaction_move"),
        plan.get("response_goal"),
        plan.get("tactic_variant"),
    )

    # 连续性只影响篇幅与追问许可；withdraw 偏置可请求更短表达，但不允许把它
    # 解释成用户拒绝交流或直接取消当前任务。
    should_be_brief = (
        bool(plan.get("should_be_brief"))
        or bool(continuity_block.get("should_be_brief"))
        or _coerce_float(affect_block.get("withdraw_bias")) >= 0.45
    )
    allow_followup = bool(plan.get("allow_followup", continuity_block.get("allow_followup", True))) and bool(
        continuity_block.get("allow_followup", True)
    )
    recent_turns = _coerce_int(continuity_block.get("turns_injected"), default=0)
    summary_present = bool(continuity_block.get("summary_present"))

    # 每个分区先产出自己的有界投影，最终包不会暴露器官名/数值；安全规则最后
    # 独立合并，确保语气或关系锚点不能抵消对内部过程泄露的禁令。
    task_facts = _clean_list(
        [
            "本轮只回应当前用户输入，不展开器官状态。",
            "当前是技术求助场景，先给可执行步骤。" if _looks_technical(task_mode, response_move) else "",
        ],
        limit=3,
    )
    task_anchors = _clean_list(
        [
            "任务面：先完成本轮目标，再体现角色感。",
            "技术场景保持步骤准确。" if _looks_technical(task_mode, response_move) else "",
            *_coerce_sequence(organ_task.get("style_anchor")),
        ],
        limit=4,
    )

    affect_anchors = _clean_list(
        [
            *_coerce_sequence(affect.get("style_anchor")),
            *_coerce_sequence(organ_affect.get("style_anchor")),
            _affect_bias_anchor(affect_block),
        ],
        limit=4,
    )
    relationship_anchors = _clean_list(
        [
            _relationship_anchor(relationship_distance, relationship),
            _drive_anchor(drive),
        ],
        limit=4,
    )
    continuity_anchors = _clean_list(
        [
            *_coerce_sequence(continuity_block.get("style_anchor")),
            "连续性面：只自然续上，不复述历史。" if recent_turns or summary_present else "",
            "当前适合短一点。" if should_be_brief else "",
        ],
        limit=5,
    )

    safety_rules = _clean_list(
        [
            "不要输出器官名、器官状态、内部驱动值或控制包字段。",
            "不要把情绪、关系、记忆来源当作元信息解释给用户。",
            "不要让器官状态覆盖用户当前事实和任务目标。",
            *_coerce_sequence(organ_packet.surface_constraints),
        ],
        limit=8,
    )

    # 分区对象保持不可变，供 packet compiler 分别处理事实、风格和禁令；不能把
    # 它们扁平化为一段模块建议，以免模型误把软偏好当成硬性任务指令。
    return SurfaceInfluencePacket(
        task=TaskInfluence(
            task_mode=task_mode,
            response_move=response_move,
            information_budget=_information_budget(
                plan.get("information_budget"),
                organ_task.get("information_budget"),
            ),
            question_budget=_min_optional_int(
                plan.get("question_budget") or plan.get("max_questions"),
                organ_task.get("question_budget"),
            ),
            facts=tuple(task_facts),
            style_anchor=tuple(task_anchors),
        ),
        affect=AffectInfluence(
            emotional_tone=emotional_tone,
            expression_intensity=_clamp01(affect.get("expression_intensity")),
            warmth_bias=_clamp01(affect_block.get("warmth_bias")),
            withdraw_bias=_clamp01(affect_block.get("withdraw_bias")),
            irritation_bias=_clamp01(affect_block.get("irritation_bias")),
            must_not_do=tuple(_clean_list(_coerce_sequence(affect.get("must_not_do")), limit=4)),
            style_anchor=tuple(affect_anchors),
        ),
        relationship=RelationshipInfluence(
            relationship_distance=relationship_distance,
            stage_label=_clean_text(relationship.get("stage_label")),
            relationship_phase=_clean_text(relationship.get("relationship_phase")),
            relationship_substage=_clean_text(relationship.get("relationship_substage")),
            nickname_policy=_nickname_policy(relationship_distance, relationship),
            tease_permission=_tease_permission(relationship),
            intimacy_ceiling=_relationship_intimacy_ceiling(relationship_distance, relationship),
            must_not_do=tuple(_relationship_must_not_do(relationship_distance, relationship)),
            style_anchor=tuple(relationship_anchors),
        ),
        memory=MemoryInfluence(
            facts=tuple(_clean_list(_coerce_sequence(memory.get("facts")), limit=4)),
            must_not_do=tuple(_clean_list(_coerce_sequence(memory.get("must_not_do")), limit=3)),
            style_anchor=tuple(_clean_list(_coerce_sequence(memory.get("style_anchor")), limit=3)),
        ),
        continuity=ContinuityInfluence(
            recent_turns_injected=recent_turns,
            summary_present=summary_present,
            should_be_brief=should_be_brief,
            allow_followup=allow_followup,
            must_not_do=tuple(
                _clean_list(
                    [
                        "不要复述历史摘要或逐字回放旧对话。" if recent_turns or summary_present else "",
                        "少追问，先回应当前句。" if not allow_followup else "",
                    ],
                    limit=3,
                )
            ),
            style_anchor=tuple(continuity_anchors),
        ),
        safety=SafetyInfluence(must_not_do=tuple(safety_rules)),
        delivery=DeliveryInfluence(
            response_form=_clean_text(plan.get("response_form") or delivery_payload.get("response_form")) or "text",
            vts_expression_id=_clean_text(delivery_payload.get("vts_expression_id")),
            output_channel=_clean_text(delivery_payload.get("output_channel")) or "chat_text",
            style_anchor=tuple(
                _clean_list(
                    [
                        *_coerce_sequence(delivery_payload.get("style_anchor")),
                        "输出面：最终只发一条中文私聊文本。",
                        "文字回复不要描述表情触发或动作执行。",
                    ],
                    limit=2,
                )
            ),
        ),
        organ_trace=dict(organ_packet.organ_trace),
    )


def surface_influence_packet_from_mapping(
    raw: SurfaceInfluencePacket | Mapping[str, Any] | None,
) -> SurfaceInfluencePacket:
    # 映射反序列化逐分区限幅/截断，空值有安全默认包；非 Mapping 明确拒绝，
    # 避免不受控对象穿过适配层进入 Surface 提示词。
    if raw is None:
        return SurfaceInfluencePacket()
    if isinstance(raw, SurfaceInfluencePacket):
        return raw
    if not isinstance(raw, Mapping):
        raise ValueError("surface influence packet must be a mapping")
    payload = dict(raw)
    return SurfaceInfluencePacket(
        task=_task_from_mapping(payload.get("task")),
        affect=_affect_from_mapping(payload.get("affect")),
        relationship=_relationship_from_mapping(payload.get("relationship")),
        memory=_memory_from_mapping(payload.get("memory")),
        continuity=_continuity_from_mapping(payload.get("continuity")),
        safety=_safety_from_mapping(payload.get("safety")),
        delivery=_delivery_from_mapping(payload.get("delivery")),
        source=_clean_text(payload.get("source")) or "organ_influence_compressed",
        organ_trace=dict(payload.get("organ_trace") or {}) if isinstance(payload.get("organ_trace"), Mapping) else {},
    )


def surface_influence_facts(packet: SurfaceInfluencePacket) -> tuple[str, ...]:
    # 只有任务和记忆分区可贡献事实；affect/relationship/continuity 只能提供
    # 表达约束，不能借“影响”身份新增世界事实。
    return tuple(
        _clean_list(
            [
                *packet.task.facts,
                *packet.memory.facts,
            ],
            limit=6,
        )
    )


def surface_influence_style_anchor(packet: SurfaceInfluencePacket) -> tuple[str, ...]:
    return tuple(
        _clean_list(
            [
                *packet.task.style_anchor,
                *packet.delivery.style_anchor,
                *packet.affect.style_anchor,
                *packet.relationship.style_anchor,
                *packet.memory.style_anchor,
                *packet.continuity.style_anchor,
            ],
            limit=10,
        )
    )


def surface_influence_must_not_do(packet: SurfaceInfluencePacket) -> tuple[str, ...]:
    # 禁令来自所有可能越界的分区并稳定去重；不从 task/delivery 反推禁令，避免
    # 表现目标意外压过明确的安全或关系边界。
    return tuple(
        _clean_list(
            [
                *packet.affect.must_not_do,
                *packet.relationship.must_not_do,
                *packet.memory.must_not_do,
                *packet.continuity.must_not_do,
                *packet.safety.must_not_do,
            ],
            limit=12,
        )
    )


def surface_influence_trace(packet: SurfaceInfluencePacket) -> dict[str, Any]:
    return {
        "source": packet.source,
        "task_mode": packet.task.task_mode,
        "response_move": packet.task.response_move,
        "emotional_tone": packet.affect.emotional_tone,
        "relationship_distance": packet.relationship.relationship_distance,
        "memory_fact_count": len(packet.memory.facts),
        "continuity_turns": packet.continuity.recent_turns_injected,
        "organ_trace": dict(packet.organ_trace),
    }


def _coerce_organ_influence_packet(
    *,
    organ_context: Mapping[str, Any] | None,
    organ_influence_packet: OrganInfluencePacket | Mapping[str, Any] | None,
) -> OrganInfluencePacket:
    if organ_influence_packet is not None:
        return organ_influence_packet_from_mapping(organ_influence_packet)
    return build_organ_influence_packet(organ_context)


def _task_from_mapping(raw: Any) -> TaskInfluence:
    payload = _safe_mapping(raw)
    return TaskInfluence(
        task_mode=_clean_text(payload.get("task_mode")),
        response_move=_clean_text(payload.get("response_move")),
        information_budget=_clean_text(payload.get("information_budget")),
        question_budget=_optional_int(payload.get("question_budget")),
        facts=tuple(_clean_list(_coerce_sequence(payload.get("facts")), limit=6)),
        style_anchor=tuple(_clean_list(_coerce_sequence(payload.get("style_anchor")), limit=6)),
    )


def _affect_from_mapping(raw: Any) -> AffectInfluence:
    payload = _safe_mapping(raw)
    return AffectInfluence(
        emotional_tone=_clean_text(payload.get("emotional_tone")),
        expression_intensity=_clamp01(payload.get("expression_intensity")),
        warmth_bias=_clamp01(payload.get("warmth_bias")),
        withdraw_bias=_clamp01(payload.get("withdraw_bias")),
        irritation_bias=_clamp01(payload.get("irritation_bias")),
        must_not_do=tuple(_clean_list(_coerce_sequence(payload.get("must_not_do")), limit=6)),
        style_anchor=tuple(_clean_list(_coerce_sequence(payload.get("style_anchor")), limit=6)),
        inner_weather=_clean_text(payload.get("inner_weather")),
        body_state_hint=_clean_text(payload.get("body_state_hint")),
        care_signal=_clamp01(payload.get("care_signal")),
        continuity_signal=_clamp01(payload.get("continuity_signal")),
        tension_hint=_clean_text(payload.get("tension_hint")),
        liveliness=_clamp01(payload.get("liveliness")) if payload.get("liveliness") is not None else 0.5,
        active_loops=_coerce_int(payload.get("active_loops"), default=0),
    )


def _relationship_from_mapping(raw: Any) -> RelationshipInfluence:
    payload = _safe_mapping(raw)
    return RelationshipInfluence(
        relationship_distance=_clean_text(payload.get("relationship_distance")),
        stage_label=_clean_text(payload.get("stage_label")),
        relationship_phase=_clean_text(payload.get("relationship_phase")),
        relationship_substage=_clean_text(payload.get("relationship_substage")),
        nickname_policy=_clean_text(payload.get("nickname_policy")),
        tease_permission=_clean_text(payload.get("tease_permission")),
        intimacy_ceiling=_clamp01(payload.get("intimacy_ceiling")),
        must_not_do=tuple(_clean_list(_coerce_sequence(payload.get("must_not_do")), limit=6)),
        style_anchor=tuple(_clean_list(_coerce_sequence(payload.get("style_anchor")), limit=6)),
    )


def _memory_from_mapping(raw: Any) -> MemoryInfluence:
    payload = _safe_mapping(raw)
    return MemoryInfluence(
        facts=tuple(_clean_list(_coerce_sequence(payload.get("facts")), limit=6)),
        must_not_do=tuple(_clean_list(_coerce_sequence(payload.get("must_not_do")), limit=6)),
        style_anchor=tuple(_clean_list(_coerce_sequence(payload.get("style_anchor")), limit=6)),
    )


def _continuity_from_mapping(raw: Any) -> ContinuityInfluence:
    payload = _safe_mapping(raw)
    return ContinuityInfluence(
        recent_turns_injected=_coerce_int(payload.get("recent_turns_injected"), default=0),
        summary_present=bool(payload.get("summary_present")),
        should_be_brief=bool(payload.get("should_be_brief")),
        allow_followup=bool(payload.get("allow_followup", True)),
        must_not_do=tuple(_clean_list(_coerce_sequence(payload.get("must_not_do")), limit=6)),
        style_anchor=tuple(_clean_list(_coerce_sequence(payload.get("style_anchor")), limit=6)),
    )


def _safety_from_mapping(raw: Any) -> SafetyInfluence:
    payload = _safe_mapping(raw)
    return SafetyInfluence(must_not_do=tuple(_clean_list(_coerce_sequence(payload.get("must_not_do")), limit=12)))


def _delivery_from_mapping(raw: Any) -> DeliveryInfluence:
    payload = _safe_mapping(raw)
    return DeliveryInfluence(
        response_form=_clean_text(payload.get("response_form")) or "text",
        vts_expression_id=_clean_text(payload.get("vts_expression_id")),
        output_channel=_clean_text(payload.get("output_channel")) or "chat_text",
        style_anchor=tuple(_clean_list(_coerce_sequence(payload.get("style_anchor")), limit=6)),
    )


def _relationship_anchor(distance: str, relationship: Mapping[str, Any]) -> str:
    phase = _clean_text(relationship.get("relationship_phase"))
    substage = _clean_text(relationship.get("relationship_substage"))
    if phase == "lover" or str(distance).lower() in {"close", "intimate"}:
        return "关系面：可以更亲近，但亲密要短、具体、不油。"
    if substage in {"protective_sibling", "playful_sibling"}:
        return "关系面：熟悉的兄妹感，可以轻微拌嘴和护短。"
    return "关系面：熟人私聊，不主动升级亲密。"


def _drive_anchor(drive: Mapping[str, Any]) -> str:
    care = _coerce_float(drive.get("care_drive"))
    repair = _coerce_float(drive.get("repair_drive"))
    play = _coerce_float(drive.get("playfulness_drive"))
    if repair >= 0.35:
        return "驱动面：优先修复和退让，不抢话。"
    if care >= 0.25:
        return "驱动面：可以主动关心，但先回应当前事实。"
    if play >= 0.30:
        return "驱动面：允许轻微玩笑，但不能压过用户意图。"
    return ""


def _affect_bias_anchor(affect: Mapping[str, Any]) -> str:
    withdraw = _coerce_float(affect.get("withdraw_bias"))
    irritation = _coerce_float(affect.get("irritation_bias"))
    warmth = _coerce_float(affect.get("warmth_bias"))
    if withdraw >= 0.45:
        return "情绪面：当前偏收着，少问、别强行热情。"
    if irritation >= 0.30:
        return "情绪面：可以有一点不满，但必须收住。"
    if warmth >= 0.50:
        return "情绪面：可以温一点，但不要解释情绪状态。"
    return ""


def _relationship_must_not_do(distance: str, relationship: Mapping[str, Any]) -> list[str]:
    phase = _clean_text(relationship.get("relationship_phase"))
    rules = ["不要直说关系阶段或好感度。"]
    if phase != "lover" and str(distance).lower() not in {"close", "intimate"}:
        rules.append("不要把兄妹熟悉感直接写成恋人承诺。")
    return _clean_list(rules, limit=4)


def _nickname_policy(distance: str, relationship: Mapping[str, Any]) -> str:
    phase = _clean_text(relationship.get("relationship_phase"))
    if phase == "lover" or str(distance).lower() in {"close", "intimate"}:
        return "nickname_allowed_sparse"
    return "nickname_allowed_but_sparse"


def _tease_permission(relationship: Mapping[str, Any]) -> str:
    playfulness = _coerce_float(relationship.get("playfulness"))
    conflict = _coerce_float(relationship.get("conflict_rift"))
    if conflict >= 0.35:
        return "tease_suppressed"
    if playfulness >= 0.45:
        return "light_tease_allowed"
    return "minimal_tease"


def _relationship_intimacy_ceiling(distance: str, relationship: Mapping[str, Any]) -> float:
    phase = _clean_text(relationship.get("relationship_phase"))
    if phase == "lover" or str(distance).lower() in {"close", "intimate"}:
        return 0.74
    return 0.46


def _looks_technical(*values: Any) -> bool:
    haystack = " ".join(_clean_text(value).lower() for value in values)
    return any(token in haystack for token in ("technical", "debug", "triage", "help", "task", "next_step"))


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _safe_mapping(raw: Any) -> dict[str, Any]:
    return dict(raw or {}) if isinstance(raw, Mapping) else {}


def _max_numeric_mapping(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base or {})
    for key, value in dict(overlay or {}).items():
        if value in (None, ""):
            continue
        if key in result and _looks_numeric(result.get(key)) and _looks_numeric(value):
            result[key] = max(_coerce_float(result.get(key)), _coerce_float(value))
        else:
            result.setdefault(key, value)
    return result


def _looks_numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _information_budget(*values: Any) -> str:
    ranks = {
        "very_short": 0,
        "short": 1,
        "normal": 2,
        "medium": 2,
        "long": 3,
    }
    selected = ""
    selected_rank = 99
    for value in values:
        text = _clean_text(value)
        if not text:
            continue
        rank = ranks.get(text, 2)
        if rank < selected_rank:
            selected = text
            selected_rank = rank
    return selected


def _min_optional_int(*values: Any) -> int | None:
    numbers = [
        _optional_int(value)
        for value in values
        if value not in (None, "")
    ]
    numbers = [value for value in numbers if value is not None]
    if not numbers:
        return None
    return max(0, min(numbers))


def _clean_text(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()[:180]


def _clean_list(
    values: Sequence[Any] | None,
    *,
    limit: int | None = None,
    max_chars: int = 180,
) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = _clean_text(value)
        if max_chars > 0:
            text = text[:max_chars].strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
        if limit is not None and len(cleaned) >= limit:
            break
    return cleaned


def _coerce_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        text = _clean_text(value)
        return [text] if text else []
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _coerce_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return max(0, _coerce_int(value, default=0))


def _clamp01(value: Any) -> float:
    return max(0.0, min(1.0, _coerce_float(value)))


__all__ = [
    "AffectInfluence",
    "ContinuityInfluence",
    "DeliveryInfluence",
    "MemoryInfluence",
    "RelationshipInfluence",
    "SafetyInfluence",
    "SurfaceInfluencePacket",
    "TaskInfluence",
    "build_surface_influence_packet",
    "surface_influence_facts",
    "surface_influence_must_not_do",
    "surface_influence_packet_from_mapping",
    "surface_influence_style_anchor",
    "surface_influence_trace",
]
