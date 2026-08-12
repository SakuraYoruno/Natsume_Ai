"""无依赖的 Surface 路由与质量策略常量。

这些正则和枚举只帮助输入编译器识别需要证据、技术直答或避免轻佻表达的场景；
它们不生成回复、不持久化状态，也不替代 Scene/Governor 的权威裁决。
"""

from __future__ import annotations

import re


TECHNICAL_TASK_MODES = frozenset(
    {
        "advice_continue",
        "complaint",
        "help_takeover",
        "next_step",
        "small_failure",
        "technical_help",
        "triage_first_step",
        "write_takeover",
    }
)
# 以下模式用于保持技术任务的表达目标，防止短续句被误降格成泛陪伴聊天。
AUTONOMOUS_SELF_QUERY_RE = re.compile(
    r"(你(?:刚才|刚刚|最近|现在|这会儿|平时)?(?:在)?(?:干嘛|做什么|忙什么|想什么|惦记什么)"
    r"|你(?:最近|这阵子|现在)?(?:有什?么变化|变了什?么|学到什?么|在成长什?么)"
    r"|你(?:自己|自个儿).{0,12}(?:项目|事情|议程|想做|在做)"
    r"|你是不是.{0,10}(?:只是在等我|一直等我|没有自己的事))",
    re.IGNORECASE,
)
MAIN_CHAIN_META_CHAT_RE = re.compile(
    r"(主链|链路|耗时|延迟|fallback|Codex|模型|路由|surface|thalamus|后台任务)",
    re.IGNORECASE,
)
BAD_TEASE_PREFIX_RE = re.compile(
    r"^\s*(?:哈？|啊？|嘛，?)?[^。！？!?]{0,40}(?:乱七八糟|又在琢磨|又在研究)[^。！？!?]*[。！？!?]\s*",
    re.IGNORECASE,
)
PERCEPTION_EVIDENCE_REQUIRED_RE = re.compile(
    r"(这几本|这几张|这几份|这张|哪张|图里|图上|图片|照片|截图|画面|看起来|"
    r"image|photo|screenshot|cover)",
    re.IGNORECASE,
)
PERCEPTION_PRODUCTION_TASK_RE = re.compile(
    r"(调用|推荐|抓|获取|找|搜索|采集|每日榜单|最新资源|轻小说|epub|封装|购买)",
    re.IGNORECASE,
)
CURRENT_COVER_CLAIM_RE = re.compile(
    r"(这|那|哪).{0,8}封面|封面(上|里|中|有|是|看起来)",
    re.IGNORECASE,
)
_MORNING_GREETING_START_RE = re.compile(
    r"^(?:早安|早上好|早晨好|上午好|早啊|早呀|早哦|早)(?:[。.!！?？~～]|$)"
)
_MORNING_GREETING_MENTION_RE = re.compile(
    r"(?:早安|早上好|早晨好|上午好|早啊|早呀|早哦)"
)
_TEMPORAL_GREETING_CORRECTION_RE = re.compile(
    r"(?:现在|当前|都|几点|什么时候|下午|晚上|夜|晚了|不是早上|还早上|说早|怎么还|怎么又)"
)


def morning_greeting_mismatch_candidate(text: str) -> bool:
    """识别问候时间与当前语义冲突的候选；只供提示调整，不能凭此断言真实时钟。"""
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    if _MORNING_GREETING_START_RE.search(compact):
        # 明确晨间开场可触发纠正候选，具体措辞仍由上层结合场景决定。
        return True
    return bool(
        _MORNING_GREETING_MENTION_RE.search(compact)
        and _TEMPORAL_GREETING_CORRECTION_RE.search(compact)
    )


__all__ = [
    "AUTONOMOUS_SELF_QUERY_RE",
    "BAD_TEASE_PREFIX_RE",
    "CURRENT_COVER_CLAIM_RE",
    "MAIN_CHAIN_META_CHAT_RE",
    "PERCEPTION_EVIDENCE_REQUIRED_RE",
    "PERCEPTION_PRODUCTION_TASK_RE",
    "TECHNICAL_TASK_MODES",
    "morning_greeting_mismatch_candidate",
]
