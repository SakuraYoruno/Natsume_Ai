"""Read-only relationship guidance for the live Surface reply compiler.

The relationship journal stores evidence and state, not canned dialogue.  This
module turns the small, trusted turn hand-off into bounded writing constraints:
it can ask the model to respect a boundary or leave room for repair, but it can
never invent intimacy, expose scores, or reinterpret silence as rejection.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


_REPAIR_OUTCOMES = frozenset({"MISUNDERSTOOD", "REPAIR_UNRESOLVED"})


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True, slots=True)
class RelationshipReplyGuidance:
    """Safe language constraints derived from one canonical relation head."""

    facts: tuple[str, ...] = ()
    must_include: tuple[str, ...] = ()
    must_not_do: tuple[str, ...] = ()
    style_anchor: tuple[str, ...] = ()
    repair_required: bool = False
    proactive_permission: str = "UNKNOWN"
    aftereffect_outcome: str = ""

    @property
    def available(self) -> bool:
        return bool(
            self.facts
            or self.must_include
            or self.must_not_do
            or self.style_anchor
        )

    def trace(self) -> dict[str, Any]:
        """Small observability projection; never contains user/reply bodies."""

        return {
            "schema_version": "relationship_reply_guidance.v1",
            "repair_required": self.repair_required,
            "proactive_permission": self.proactive_permission,
            "aftereffect_outcome": self.aftereffect_outcome,
            "fact_count": len(self.facts),
            "must_include_count": len(self.must_include),
            "must_not_do_count": len(self.must_not_do),
        }


def build_relationship_reply_guidance(
    emotion_context: Mapping[str, Any] | None,
) -> RelationshipReplyGuidance:
    """Return no guidance unless the hand-off came from AffectFusionService.

    External ``emotion_context`` remains untrusted request input.  The internal
    marker is introduced only after the input runtime has normalised a durable
    Affective Life result, preventing callers from spoofing relationship flags
    to steer the model.
    """

    context = _mapping(emotion_context)
    if not bool(context.get("_affective_life_internal")):
        return RelationshipReplyGuidance()
    affective_life = _mapping(context.get("affective_life"))
    relationship_life = _mapping(affective_life.get("relationship_life"))
    if relationship_life.get("schema_version") != "relationship_turn_context.v1":
        return RelationshipReplyGuidance()

    state = _mapping(relationship_life.get("state"))
    aftereffect = _mapping(relationship_life.get("aftereffect"))
    repair_required = bool(state.get("repair_required"))
    proactive_permission = _text(state.get("proactive_permission")).upper() or "UNKNOWN"
    outcome = _text(aftereffect.get("outcome")).upper()

    facts: list[str] = []
    must_include: list[str] = []
    must_not_do: list[str] = [
        "不要把亲近说成占有、排他或回应义务；不要使用威胁、冷处理、负罪施压或依赖诱导。",
        "不要暴露关系分数、阶段、账本或内部判断；让分寸自然体现在措辞里。",
    ]
    style_anchor: list[str] = [
        "关系状态只收紧表达边界，不替换用户当前目标，也不凭互动次数升级亲密。",
    ]

    if proactive_permission == "DENIED":
        facts.append("关系事实：用户已明确要求空间；本轮尊重停顿，不把沉默或不回复解释成拒绝关系。")
        must_include.append("自然确认会尊重对方的空间，不追问，不要求立刻回应。")
        must_not_do.extend(
            [
                "不要连续追问、催回复，或把担心、失落写成用户必须安抚你的责任。",
                "不要把主动联系、亲密表达或继续讨论当前关系当作默认权限。",
            ]
        )
        style_anchor.append("留白比补满更重要；短一些、稳一些，给对方保留退出空间。")

    if outcome == "BOUNDARY":
        facts.append("关系事实：上一轮触及了用户边界；先承认可能没有接住，再按用户给出的距离后退。")
        must_include.append("如需回应边界，用第一人称承担自己的分寸，不替自己辩解。")
        must_not_do.append("不要把边界包装成撒娇、玩笑、吃醋或更强烈的情感索取。")
        style_anchor.append("克制地道歉或安静陪着都可以，但修复不能由七海单方面宣布完成。")
    elif outcome in _REPAIR_OUTCOMES or repair_required:
        facts.append("关系事实：这里仍有未解决的误解或裂痕；先听清、承认可能没接住，再把节奏交还给用户。")
        must_include.append("不要假装已经和好；给用户决定是否继续、何时继续的空间。")
        must_not_do.append("不要要求原谅、用解释压过感受，或把一次道歉当作自动修复。")
        style_anchor.append("先稳定关系安全感，再回答或处理眼前任务；技术任务也不丢掉基本尊重。")
    elif outcome == "REPAIR_ACCEPTED":
        facts.append("关系事实：用户确认愿意继续；可以自然回暖，但不要把一次接受夸张成永久承诺。")
        style_anchor.append("回暖要轻，不抢着定义关系，不把感谢变成情感债。")
    elif outcome == "HELPFUL":
        style_anchor.append("前一轮被接住了；保持自然延续，不把帮助兑换成更高的亲密或主动权限。")

    return RelationshipReplyGuidance(
        facts=tuple(dict.fromkeys(facts)),
        must_include=tuple(dict.fromkeys(must_include)),
        must_not_do=tuple(dict.fromkeys(must_not_do)),
        style_anchor=tuple(dict.fromkeys(style_anchor)),
        repair_required=repair_required,
        proactive_permission=proactive_permission,
        aftereffect_outcome=outcome,
    )


__all__ = [
    "RelationshipReplyGuidance",
    "build_relationship_reply_guidance",
]
