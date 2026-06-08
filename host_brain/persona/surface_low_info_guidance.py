from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SurfaceLowInfoGuidance:
    scene_kind: str = ""
    facts: tuple[str, ...] = ()
    style_anchor: tuple[str, ...] = ()
    must_not_do: tuple[str, ...] = ()

    @property
    def applied(self) -> bool:
        return bool(self.scene_kind)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_kind": self.scene_kind,
            "facts": list(self.facts),
            "style_anchor": list(self.style_anchor),
            "must_not_do": list(self.must_not_do),
            "applied": self.applied,
        }


_PUNCT_RE = re.compile(r"[\s，。！？!?,.、~～…“”\"'：:；;（）()\[\]{}]+")


def low_info_guidance_for_user_message(
    user_message: Any,
    *,
    task_mode: Any = "",
    response_move: Any = "",
) -> SurfaceLowInfoGuidance:
    """Return positive current-turn context for terse inputs.

    This is not a text-quality scorer and not a reply template. It only states
    what the short user message does and, just as importantly, what it does not
    establish as fact, so the final surface generator has less empty space to
    fill with guesses.
    """

    normalized = _normalize_message(user_message)
    if not normalized:
        return SurfaceLowInfoGuidance()

    if normalized in {"早", "早啊", "早呀", "早安", "早上好", "早哦"}:
        return SurfaceLowInfoGuidance(
            scene_kind="daily_morning",
            facts=(
                "当前用户只是在文字私聊里问早；没有提供作息、精神状态、天气、任务进度或今天安排。",
                "本轮只需要接住早安，不需要把话题拉回文本、测试或任务。",
                "问早不是在争谁先主动，也不需要奖惩、记功或收下用户这句问候。",
            ),
            style_anchor=(
                "早安场景：短短回应，可以有一点熟人亲近感，但不要补现实状态。",
                "早安句法锚：结构是“早/早啊/早安 + 可选称呼”，只落一句；可以用一个轻停顿或啦/呀调味，不要逐字复制锚点。",
            ),
            must_not_do=(
                "不要评价用户来得早晚、准时、赖床、刚醒、没睡懒觉、精神好或今天安排。",
                "不要提继续文本、继续任务、知道你忙、刚在等你或正要找你。",
                "低信息早安不要靠固定旧称呼、自称或模板口癖撑短句。",
            ),
        )

    if normalized in {"我回来了", "回来了", "回来啦", "我回来啦", "回来咯", "我回来咯"}:
        return SurfaceLowInfoGuidance(
            scene_kind="return_chat",
            facts=(
                "当前用户只是在文字私聊里说回来了；这不等于回家、进门或离开很久。",
                "没有提供用户去了哪里、离开多久、是否工作、是否迷路、是否很晚或你是否一直等待。",
            ),
            style_anchor=(
                "回来了场景：短短欢迎回来，可以带一点熟人嘴硬，但不要补现实剧情。",
                "回来了句法锚：结构是“回来啦/欢迎回来 + 可选称呼 + 一点轻停顿”，不加第二层剧情，不要逐字复制锚点。",
            ),
            must_not_do=(
                "不要编用户迷路、被工作绑架、被工作吃掉、进门、回来晚或让你等很久。",
                "不要把欢迎回来写成计时、责备、等候证明或现实动作。",
                "低信息回来不要靠固定旧称呼、自称或模板口癖撑短句。",
            ),
        )

    if normalized in {"在吗", "在嘛", "在么", "在不在", "你在吗", "你在嘛", "你在么"}:
        return SurfaceLowInfoGuidance(
            scene_kind="presence_check",
            facts=(
                "当前用户只是在确认你是否在线；没有表示出急事、出大事或需要长谈。",
                "本轮只需要确认我在，不需要追问用户要做什么。",
            ),
            style_anchor=(
                "在吗场景：直接表示在，短一点，别反问别加戏。",
                "在吗句法锚：结构是“在/在呢/在啊 + 可选称呼”，可补“我听着”但别追问；不要把自己写成登场人物。",
            ),
            must_not_do=(
                "不要写居然、稀奇、出大事、喊一声、人就在这儿、不会跑或默认称呼在呢。",
                "低信息在吗不要靠固定旧称呼、自称或模板口癖撑短句。",
            ),
        )

    if _is_continue_message(normalized):
        return SurfaceLowInfoGuidance(
            scene_kind="short_continue",
            facts=(
                "当前用户只是在请求沿着已有压缩话题继续；不要评价用户只说了继续。",
                "本轮没有新的现实状态、情绪变化或任务材料；只接当前话口，不新开剧情。",
                "没有提供你一直等待用户的事实；不要把短接续写成在这等着。",
            ),
            style_anchor=(
                "短接续场景：直接承接，可以有轻微熟人语气，但不要训话或让用户补材料。",
                "短接续句法锚：结构是“嗯/好/行/好啦 + 接着/往下/继续”，可用呗/啦调味一次，不要把继续写成表演台词。",
            ),
            must_not_do=(
                "不要写这才像话、就回个继续、就一声继续、偷懒、没诚意、你好好听着或我接住了。",
                "不要把继续写成用户催你、敷衍你、考验你或需要被教育。",
                "低信息继续不要靠固定旧称呼、自称或模板口癖撑短句。",
            ),
        )

    if normalized in {"嗯", "嗯嗯", "哦", "喔", "好", "行", "然后呢", "然后", "那然后呢"}:
        return SurfaceLowInfoGuidance(
            scene_kind="short_followup",
            facts=(
                "当前用户只给了极短回应；这通常是在保留话口，不代表敷衍、生气或有隐情。",
            ),
            style_anchor=(
                "极短回应场景：顺着当前压缩事实轻轻接半步，不审问，不评价字数。",
                "极短回应句法锚：结构是“嗯/好/行 + 往下/接着/我听着”，只做承接，不逐字复制锚点。",
            ),
            must_not_do=(
                "不要写就一个字、就这、惜字如金、敷衍、没诚意、外星人或肯定卡了很久。",
                "低信息短回应不要靠固定旧称呼、自称或模板口癖撑短句。",
            ),
        )

    return SurfaceLowInfoGuidance()


def _is_continue_message(normalized: str) -> bool:
    if normalized in {
        "继续",
        "继续吧",
        "继续呗",
        "接着",
        "接着吧",
        "接着来",
        "接着说",
        "往下",
        "往下吧",
        "继续做",
        "继续做吧",
    }:
        return True
    if normalized.startswith(("继续", "接着")) and len(normalized) <= 6:
        return True
    return False


def _normalize_message(value: Any) -> str:
    return _PUNCT_RE.sub("", str(value or "").strip())


__all__ = [
    "SurfaceLowInfoGuidance",
    "low_info_guidance_for_user_message",
]
