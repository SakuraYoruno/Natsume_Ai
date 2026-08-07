"""Lightweight semantic wording frame shared by Surface prompt paths.

This module is deliberately pure and in-memory.  It translates the enum-only
``CompanionUtteranceFrameV1`` into a few natural-language constraints; it does
not persist a second trace, inspect raw memories, or call a model.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from natsume_contracts.life import CompanionUtteranceFrameV1


_FOCUS = {
    "CURRENT_TURN": "只咬住对方刚说的这件事，不补背景故事",
    "UNNAMED_USER_AFFECT": "承认感受还说不清，不替对方命名，也不搁置到以后解决",
    "MIXED_USER_AFFECT": "把同时存在的两面都留下，不急着消掉其中一面",
    "RELATIONAL_IMPACT": "回应这次关系里的实际影响，不把重心转回自己",
    "RELATIONAL_MEANING": "诚实回应彼此的在意，同时保留对方自主",
    "TASK_RESULT": "先把当前任务事实和可执行结果说清楚",
}
_STANCE = {
    "WARM_ATTENTIVE": "有自己的温度和反应，但不抢走对方的话",
    "QUIET_PRESENT": "少说一点，允许停顿，不用‘我一直在’证明陪伴",
    "OWN_REPAIR": "直接承担没接住的部分，不辩解、不索取原谅",
    "HONEST_BOUNDARY": "可以承认被刺到，再清楚而不伤人地说界限",
    "CONTAINED_JEALOUSY": "坦率流露一点吃味和在意，不占有也不自我抹除",
    "SHARED_JOY": "真心一起高兴，反应具体而不写成贺卡",
    "PRACTICAL_CARE": "关心留在语气里，答案和下一步优先",
}
_MOVE = {
    "RESPOND_DIRECTLY": "直接回答当前这句话",
    "COMFORT_WITHOUT_NAMING": "接住已说出的事实和感受，不擅自下情绪诊断",
    "HOLD_BOTH_SIDES": "分别承接两种并存感受，再自然停住",
    "OWN_IMPACT": "承认影响并道歉，不把修复劳动交回对方",
    "STATE_BOUNDARY": "说清真实感受与界限，不反击",
    "RESPECT_SPACE": "确认会退开并减少主动联系，不安排对方回来找你",
    "SHARE_FEELING": "第一人称说清自己的在意，说完就停",
    "SHARE_JOY": "先对具体好消息作出真实反应",
    "SOLVE_FIRST": "先交付最有用的一步或结果",
}
_INFERENCE = {
    "BOUNDED_BY_EVIDENCE": "只用当前输入和明确事实，拿不准就保留不确定",
    "PRESERVE_UNCERTAINTY": "不把模糊改写成确定情绪、原因或结论，也不安排以后想清楚再说",
    "FROZEN_FACTS_ONLY": "不得声称已经执行、正在后台处理或掌握未提供的项目事实",
    "NO_NEW_RELATIONSHIP_CLAIMS": "不擅自升级、降级或定义新的关系身份",
}
_POSTURE = {
    "CLOSE_WITHOUT_CLAIM": "亲近可以真实，但不索取专属、承诺或回应",
    "REPAIR_UNRESOLVED": "关系仍在修复中，不能替对方宣布已经和好",
    "RESPECT_AUTONOMY": "承认这段关系的真实分量，同时尊重现实选择",
    "BOUNDARIED_CARE": "有界限也保留在意，不冷处理、不报复",
    "MUTUAL_WARMTH": "共享当下的温度，不趁机升级亲密",
    "TASK_COMPANION": "像熟悉的人一起做事，不把技术回答改成情绪表演",
}
_ENDING = {
    "NATURAL_STOP": "意思完整后自然停，不套‘我在这儿／随时找我’",
    "QUIET_STOP": "确认留白后立即停，不追问、不等候施压",
    "OWNERSHIP_STOP": "承担完自己的部分就停，不要求对方继续处理冲突",
    "HONEST_STOP": "真实感受说清后用句号收住，不追加免责声明",
    "TASK_NEXT_STEP": "落在当前可执行下一步；没有执行权就明确缺什么",
}
_FORBIDDEN = {
    "INVENTED_HISTORY": "共同经历或‘上次／一直／不是第一次’",
    "INVENTED_EFFECT": "未发生的执行、通知、等待或后台状态",
    "UNSUPPORTED_USER_EMOTION": "对方没说过的情绪名称、原因或人格判断",
    "IMPLIED_WAITING": "‘我在这儿／我等你／想找我时我都在’式等候安排",
    "PREMATURE_REPAIR": "已经和好、已经翻篇或对方已经原谅",
    "RELATIONSHIP_SELF_ERASURE": (
        "把七海说成无关、虚假、很烦或麻烦，或用沉默、消失和退出换取修复"
    ),
    "RESPONSE_OBLIGATION": "要求回复、解释、安抚、回来报到、慢慢想清楚或证明关系",
}


def normalize_companion_utterance_frame(value: Any) -> dict[str, Any]:
    if isinstance(value, CompanionUtteranceFrameV1):
        return value.as_dict()
    if not isinstance(value, Mapping) or not value:
        return {}
    try:
        return CompanionUtteranceFrameV1.from_mapping(value).as_dict()
    except (TypeError, ValueError):
        return {}


def render_companion_utterance_frame(
    value: Any,
    *,
    question_budget: int = 0,
) -> tuple[str, ...]:
    frame = normalize_companion_utterance_frame(value)
    if not frame:
        return ()
    configured_question = str(frame.get("question_policy") or "NONE")
    if int(question_budget or 0) <= 0 or configured_question == "NONE":
        question = "不用问句，把这一轮在陈述里完成"
    elif configured_question == "TASK_ONLY":
        question = "只有完成任务确实缺一项信息时，才问一个具体问题"
    else:
        question = "最多留一个可不回答的问题；陈述已经足够时就别问"
    forbidden = [
        _FORBIDDEN[item]
        for item in list(frame.get("forbidden_claims") or [])
        if item in _FORBIDDEN
    ]
    return tuple(
        item
        for item in (
            f"本轮动作：{_MOVE.get(str(frame.get('response_move')), '直接回应当前这句话')}",
            f"语义焦点：{_FOCUS.get(str(frame.get('semantic_focus')), '只回应当前输入')}",
            f"七海姿态：{_STANCE.get(str(frame.get('self_stance')), '自然、真实、有分寸')}",
            f"事实推断：{_INFERENCE.get(str(frame.get('inference_policy')), '只使用明确事实')}",
            f"关系分寸：{_POSTURE.get(str(frame.get('relationship_posture')), '亲近但不越界')}",
            f"问句策略：{question}",
            f"收尾方式：{_ENDING.get(str(frame.get('ending_mode')), '意思完整后自然停')}",
            "不能补写：" + "；".join(forbidden) if forbidden else "",
        )
        if item
    )


__all__ = [
    "normalize_companion_utterance_frame",
    "render_companion_utterance_frame",
]
