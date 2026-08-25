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
    r"^(?:\u65e9\u5b89|\u65e9\u4e0a\u597d|\u65e9\u6668\u597d|\u4e0a\u5348\u597d|\u65e9\u554a|\u65e9\u5440|\u65e9\u54e6|\u65e9)(?:[\u3002.!\uff01?\uff1f~\uff5e]|$)"
)
_MORNING_GREETING_MENTION_RE = re.compile(
    r"(?:\u65e9\u5b89|\u65e9\u4e0a\u597d|\u65e9\u6668\u597d|\u4e0a\u5348\u597d|\u65e9\u554a|\u65e9\u5440|\u65e9\u54e6)"
)
_TEMPORAL_GREETING_CORRECTION_RE = re.compile(
    r"(?:\u73b0\u5728|\u5f53\u524d|\u90fd|\u51e0\u70b9|\u4ec0\u4e48\u65f6\u5019|\u4e0b\u5348|\u665a\u4e0a|\u591c|\u665a\u4e86|\u8bf4\u65e9|\u600e\u4e48\u8fd8|\u600e\u4e48\u53c8)"
)


def morning_greeting_mismatch_candidate(text: str) -> bool:
    """Identify a temporal-greeting candidate; the caller still verifies the clock."""

    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    if _MORNING_GREETING_START_RE.search(compact):
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
