"""供实时 Surface 回复编译器使用的只读关系指导。

关系 journal 存储证据与状态，而非预制对话。本模块将可信的最小回合交接转换为
有界文案约束：可要求模型尊重边界或留出修复空间，但不能虚构亲密、暴露分数，
也不能把沉默重新解释为拒绝。
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
    """从一份 canonical 关系 head 派生的安全语言约束，绝不包含关系原始证据。"""

    facts: tuple[str, ...] = ()
    must_include: tuple[str, ...] = ()
    must_not_do: tuple[str, ...] = ()
    style_anchor: tuple[str, ...] = ()
    repair_required: bool = False
    proactive_permission: str = "UNKNOWN"
    aftereffect_outcome: str = ""

    @property
    def available(self) -> bool:
        """判断是否有任何可提供给 Surface 的指导项；空指导是正常兼容降级。"""
        return bool(
            self.facts
            or self.must_include
            or self.must_not_do
            or self.style_anchor
        )

    def trace(self) -> dict[str, Any]:
        """生成精简可观测投影，永不包含用户/回复正文。"""

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
    """仅在交接来自 AffectFusionService 时返回关系指导。

    外部 ``emotion_context`` 始终是不可信请求输入。内部标记只在输入运行时将
    durable Affective Life 结果规范化后引入，防止调用方伪造关系标记来操纵模型的
    权限、修复姿态或亲密边界。
    """

    context = _mapping(emotion_context)
    # 关系标记只能来自已规范化的 Affective Life 内部交接；外部请求即使伪造同名
    # 字段也只得到空指导，防止用户输入直接操纵关系权限或修复状态。
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

    # ``proactive_permission`` authorizes only an unsolicited Scheduler
    # delivery.  It stays in the trace for that owner, but must never make a
    # user who has already opened a foreground turn receive an implicit
    # silent-treatment prompt.  A current BOUNDARY aftereffect below still
    # supplies the actual foreground space constraint.

    if outcome == "BOUNDARY":
        # 边界后的指导是“给空间”的语言约束，而不是调度静默或撤回关系的命令；
        # 当前前台回合仍可回应用户，只是不能施压、追问或索取安抚。
        facts.extend(
            [
                "关系事实：用户已明确要求空间；本轮尊重停顿，不把沉默或不回复解释成拒绝关系。",
                "关系事实：上一轮触及了用户边界；先承认可能没有接住，再按用户给出的距离后退。",
            ]
        )
        facts.extend(
            [
                "自然确认会尊重对方的空间，不追问，不要求立刻回应。",
                "如需回应边界，用第一人称承担自己的分寸，不替自己辩解。",
            ]
        )
        must_not_do.extend(
            [
                "不要连续追问、催回复，或把担心、失落写成用户必须安抚你的责任。",
                "不要把主动联系、亲密表达或继续讨论当前关系当作默认权限。",
                "不要把边界包装成撒娇、玩笑、吃醋或更强烈的情感索取。",
            ]
        )
        style_anchor.extend(
            [
                "留白比补满更重要；短一些、稳一些，给对方保留退出空间。",
                "克制地道歉或安静陪着都可以，但修复不能由七海单方面宣布完成。",
            ]
        )
    elif outcome in _REPAIR_OUTCOMES or repair_required:
        # 未完成修复不等于要求用户继续谈。将节奏决定权留给用户，并阻止模型用
        # 一次道歉、解释或自我牺牲宣告裂痕已消失。
        facts.extend(
            [
                "关系事实：这里仍有未解决的误解或裂痕；先听清、承认可能没接住，再把节奏交还给用户。",
                "关系表达：不要假装已经和好；给用户决定是否继续、何时继续的空间。",
            ]
        )
        must_not_do.append("不要要求原谅、用解释压过感受，或把一次道歉当作自动修复。")
        style_anchor.append("先稳定关系安全感，再回答或处理眼前任务；技术任务也不丢掉基本尊重。")
    elif outcome == "REPAIR_ACCEPTED":
        # 接受修复只授权轻微回暖，不能推断永久承诺或关系升级；关系状态仍由
        # 生命周期命令链演化，Surface 在这里始终只是只读消费者。
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
