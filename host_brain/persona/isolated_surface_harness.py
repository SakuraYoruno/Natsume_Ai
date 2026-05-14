from __future__ import annotations

import ast
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

try:  # Pydantic v2
    from pydantic import ConfigDict
except ImportError:  # pragma: no cover - pydantic v1 compatibility
    ConfigDict = None  # type: ignore[assignment]

from host_brain.persona.galgame_text_contract import build_galgame_text_contract
from host_brain.persona.sample_guided_text_scorer import score_sample_guided_text, signature_texture_hits
from host_brain.persona.style_reference_bank import (
    StyleReferenceQuery,
    get_style_reference_bank,
    render_style_guidance,
)
from host_brain.persona.text_quality_gate import get_text_quality_gate


if ConfigDict is not None:

    class _StrictModel(BaseModel):
        model_config = ConfigDict(extra="forbid")

else:  # pragma: no cover - pydantic v1 compatibility

    class _StrictModel(BaseModel):
        class Config:
            extra = "forbid"


class ReplyControlPacket(_StrictModel):
    response_move: str = "reply_current_turn"
    task_mode: str = "daily_chat"
    emotional_tone: str = "neutral"
    relationship_distance: str = "familiar"
    information_budget: str = "short"
    question_budget: int = 0
    must_include: list[str] = Field(default_factory=list)
    must_not_do: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    style_anchor: list[str] = Field(default_factory=list)


class IsolatedSurfaceInput(_StrictModel):
    soul_md: str
    user_message: str
    control: ReplyControlPacket
    facts: list[str] = Field(default_factory=list)
    must_not_do: list[str] = Field(default_factory=list)
    case_id: str = ""


@dataclass(frozen=True)
class IsolatedCandidateScore:
    text: str
    score: float
    passed: bool
    hard_fail: bool
    reason_codes: tuple[str, ...]
    control_violations: tuple[str, ...]
    dimensions: Mapping[str, float]
    quality: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "score": round(float(self.score or 0.0), 3),
            "passed": bool(self.passed),
            "hard_fail": bool(self.hard_fail),
            "reason_codes": list(self.reason_codes),
            "control_violations": list(self.control_violations),
            "dimensions": {key: round(float(value), 3) for key, value in dict(self.dimensions).items()},
            "quality": dict(self.quality),
        }


@dataclass(frozen=True)
class IsolatedSurfaceRunResult:
    messages: tuple[Mapping[str, str], ...]
    raw_output: str
    candidates: tuple[IsolatedCandidateScore, ...]
    selected: IsolatedCandidateScore | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "messages": [dict(item) for item in self.messages],
            "raw_output": self.raw_output,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "selected": self.selected.as_dict() if self.selected else None,
        }


@dataclass(frozen=True)
class DerivedStyleAnchor:
    anchors: tuple[str, ...] = ()
    trace: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "anchors": list(self.anchors),
            "trace": dict(self.trace or {}),
        }


@dataclass(frozen=True)
class RelationshipStyleProfile:
    stage: str
    prompt_lines: tuple[str, ...]
    prefer_markers: tuple[str, ...] = ()
    overstep_markers: tuple[str, ...] = ()
    underplay_markers: tuple[str, ...] = ()
    affection_markers: tuple[str, ...] = ()
    strong_affection_markers: tuple[str, ...] = ()


@dataclass(frozen=True)
class RelationshipControlAxes:
    stage: str
    progress: int
    distance: str
    initiative: str
    tease_permission: str
    affection_visibility: str
    care_intensity: str
    vulnerability: str
    nickname_policy: str
    physical_intimacy_allowed: bool
    explicit_romance_allowed: bool
    commitment_language_allowed: bool
    generation_notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "progress": int(self.progress),
            "distance": self.distance,
            "initiative": self.initiative,
            "tease_permission": self.tease_permission,
            "affection_visibility": self.affection_visibility,
            "care_intensity": self.care_intensity,
            "vulnerability": self.vulnerability,
            "nickname_policy": self.nickname_policy,
            "physical_intimacy_allowed": bool(self.physical_intimacy_allowed),
            "explicit_romance_allowed": bool(self.explicit_romance_allowed),
            "commitment_language_allowed": bool(self.commitment_language_allowed),
            "generation_notes": list(self.generation_notes),
        }


GenerateSurface = Callable[[list[dict[str, str]]], str | Awaitable[str]]

_PUNCT_RE = re.compile(r"[\s，。！？!?,.、~～…“”\"'：:；;（）()\[\]{}]+")
_FORBIDDEN_INPUT_KEYS = {
    "advice",
    "agent_advice",
    "assistant_history",
    "candidate",
    "candidates",
    "conversation_history",
    "dialogue_history",
    "history",
    "history_turns",
    "interaction_facts",
    "long_term_memory",
    "memory",
    "messages",
    "module_advice",
    "natural_language_advice",
    "organ_outputs",
    "rag_documents",
    "raw_history",
    "raw_long_term_memory",
    "raw_memory",
    "raw_organ_outputs",
    "retrieved_docs",
    "runtime_state",
    "state",
    "tool_candidate_list",
    "tool_candidates",
    "uncompressed_rag_documents",
}
_FORBIDDEN_KEY_HINTS = (
    "history",
    "memory",
    "organ",
    "rag",
    "retrieved",
    "runtime_state",
    "tool_candidate",
    "natural_language_advice",
    "module_advice",
)
_ALLOWED_TOP_LEVEL_KEYS = {
    "case_id",
    "control",
    "facts",
    "must_not_do",
    "reply_control_packet",
    "soul_md",
    "user_message",
}
_SCENE_STYLE_QUERY: dict[str, dict[str, tuple[str, ...] | str]] = {
    "daily_good_morning": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "question_chain")},
    "daily_greeting_en": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "question_chain")},
    "daily_greeting_cn": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "question_chain")},
    "presence": {"intent_tags": ("presence",), "style_moves": ("clingy_presence", "pull_attention")},
    "wait": {"intent_tags": ("daily",), "style_moves": ("quick_reaction",)},
    "back": {"intent_tags": ("daily",), "style_moves": ("quick_reaction",)},
    "busy": {"intent_tags": ("daily",), "style_moves": ("quick_reaction",)},
    "bored": {"intent_tags": ("bored",), "style_moves": ("action_first", "food_motive")},
    "tired": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "nothing": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "boundary_hurt": {"intent_tags": ("boundary",), "emotion_tags": ("hurt", "wounded"), "style_moves": ("wounded_pushback", "soft_after_sting")},
    "relationship_boundary": {"intent_tags": ("boundary",), "emotion_tags": ("hurt", "wounded"), "style_moves": ("wounded_pushback", "soft_after_sting")},
    "good_news": {"intent_tags": ("daily",), "emotion_tags": ("playful",), "style_moves": ("quick_reaction",)},
    "good_news_proud": {"intent_tags": ("daily", "praise"), "emotion_tags": ("playful", "shy"), "style_moves": ("quick_reaction", "shy_deflection", "smug_tease")},
    "happy": {"intent_tags": ("daily",), "emotion_tags": ("playful",), "style_moves": ("quick_reaction", "smug_tease")},
    "annoying_tease": {"intent_tags": ("annoyed",), "emotion_tags": ("annoyed", "angry_light"), "style_moves": ("quick_rebuttal", "familiar_pushback")},
    "praise": {"intent_tags": ("praise",), "emotion_tags": ("shy",), "style_moves": ("shy_deflection",)},
    "user_proud": {"intent_tags": ("praise",), "emotion_tags": ("playful", "shy"), "style_moves": ("quick_reaction", "shy_deflection")},
    "apology": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "help_takeover": {"intent_tags": ("help",), "emotion_tags": ("care",), "style_moves": ("take_charge", "care_behind_tease")},
    "write_takeover": {"intent_tags": ("help",), "emotion_tags": ("care",), "style_moves": ("take_charge", "care_behind_tease")},
    "advice_continue": {"intent_tags": ("help",), "emotion_tags": ("care",), "style_moves": ("take_charge", "care_behind_tease")},
    "next_step": {"intent_tags": ("help",), "emotion_tags": ("care",), "style_moves": ("take_charge", "care_behind_tease")},
    "identity": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "question_chain")},
    "small_failure": {"intent_tags": ("help", "reassurance"), "emotion_tags": ("care", "soft"), "style_moves": ("take_charge", "care_behind_tease", "soft_reassurance")},
    "good_night": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "meal_share": {"intent_tags": ("bored", "daily"), "style_moves": ("food_motive", "quick_reaction")},
    "hungry": {"intent_tags": ("bored", "daily"), "style_moves": ("food_motive", "quick_reaction")},
    "sleepy": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "headache": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "bad_mood": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "what_doing": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "question_chain")},
    "play_invite": {"intent_tags": ("bored",), "style_moves": ("action_first", "food_motive")},
    "quiet": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "talkative": {"intent_tags": ("annoyed",), "emotion_tags": ("annoyed", "playful"), "style_moves": ("familiar_pushback", "quick_rebuttal")},
    "who_called": {"intent_tags": ("presence", "annoyed"), "emotion_tags": ("playful",), "style_moves": ("pull_attention", "quick_rebuttal")},
    "miss_you": {"intent_tags": ("intimacy",), "emotion_tags": ("deep_affection",), "style_moves": ("direct_affection",)},
    "recall_memory": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "soft_reassurance")},
    "memory_followup": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "soft_reassurance")},
    "recall": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "soft_reassurance")},
    "short_ack": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "question_chain")},
    "followup": {"intent_tags": ("daily",), "style_moves": ("question_chain", "quick_reaction")},
    "give_up": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "complaint": {"intent_tags": ("help", "reassurance"), "emotion_tags": ("care",), "style_moves": ("take_charge", "care_behind_tease")},
    "late_night": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "daily_chat": {"intent_tags": ("daily",), "style_moves": ("quick_reaction",)},
    "default": {"intent_tags": ("daily",), "style_moves": ("quick_reaction",)},
}
_MEMORY_FACT_PREFIXES = (
    "记忆线索：",
    "关系线索：",
    "世界事实：",
    "制品线索：",
    "召回线索：",
)
_CJK_SEQUENCE_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_ASCII_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+.-]{1,}")
_FACT_SPLIT_RE = re.compile(r"[，。；、,.!?！？;:\s]+")
_RAW_CONTEXT_BACKFILL_RE = re.compile(
    r"(?:完整|全部|所有).{0,8}(?:历史|聊天记录|对话记录|聊天原文|对话原文|原始记录)"
    r"|(?:历史|聊天记录|对话记录|原始历史).{0,8}(?:发|贴|给|提供|丢)",
)
_CONTINUITY_GROUNDED_RE = re.compile(r"(?:压成|压到|压缩|摘要|事实|原文|回灌|控制包|边界|不接|别接)")
_CONTINUITY_BOUNDARY_HINTS = (
    "压缩",
    "连续",
    "长链",
    "回灌",
    "原始历史",
    "原始聊天",
    "原文",
    "ReplyControlPacket",
    "控制包",
    "器官族",
)
_CANDIDATE_LABEL_SPLIT_RE = re.compile(
    r"(?:^|[\r\n\s]+)(?:candidate|response|reply|候选|回复)\s*\d*[\s:：、.-]*",
    re.IGNORECASE,
)
_CANDIDATE_LINE_RE = re.compile(r"^\s*(?:[-*]|\d+[.)、]|候选\s*\d*[:：]|回复\s*\d*[:：])\s*(.+?)\s*$")
_QUOTED_REWRITE_RE = re.compile(r"^\s*[\"'“‘「『].{2,96}[\"'”’」』。！？!?]?\s*$")
_UNQUOTED_SINGLE_REWRITE_RE = re.compile(r"^[^\n:：]{4,36}[。.!！]?$")
_CREATIVE_EDIT_GENERIC_NON_REWRITE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "flat_evaluation",
        re.compile(r"^\s*[\"'“‘「『]?\s*(?:嗯|唔|哦)?[，,、\s]*(?:还行|还可以|可以|挺好|不错)(?:吧|啦|了)?[。.!！?？…～~]*[\"'”’」』]?\s*$"),
    ),
    (
        "extended_evaluation",
        re.compile(r"^\s*[\"'“‘「『]?\s*(?:还行|还可以|可以|挺好|不错)(?:吧)?[，,].{0,24}(?:比|不算|算|肉麻)"),
    ),
    ("aesthetic_evaluation", re.compile(r"(?:不算太肉麻|算你正常发挥|还可以。?$)")),
    ("listener_prompt", re.compile(r"(?:那你说|你说吧|你讲吧).{0,6}(?:我听着|听着)")),
    ("selection_commentary", re.compile(r"(?:跟我猜的一样|我就知道你会这么选|我猜的一向很准|猜得没错)")),
)
_CREATIVE_EDIT_SOURCE_QUOTE_RE = re.compile(r"[\"“‘「『](.{2,96})[\"”’」』]")
_CREATIVE_EDIT_SOURCE_TEXT_RE = re.compile(r"(?:原句|原文|待改|要改的句子|source)\s*[:：]\s*\S{2,}")
_CREATIVE_EDIT_SOURCE_REQUEST_META_PHRASES = frozenset(
    {
        "原句给我",
        "给原句",
        "发我原句",
        "原句发我",
        "发我你写",
        "你把原句",
        "改一句",
    }
)
_CREATIVE_EDIT_SOURCE_REQUEST_PHRASES = (
    "原句",
    "原文",
    "待改",
    "发我",
    "给我",
    "贴出来",
    "看到原句",
    "拿来",
)
_AWKWARD_ADVERBIAL_PASSIVE_EVENT_RE = re.compile(
    r"(?:有点|有些|稍微|一点点)被(?:你|尼尼)?(?:抓到|抓住|逮到|发现|看穿)"
)
_AWKWARD_BARE_MORNING_STATE_RE = re.compile(r"^早[安上好呀啊，,。 ]{0,6}(?:清爽|元气|精神|挺好的|还行吧)[。.!！]?$")
_AWKWARD_REPEATED_MORNING_GREETING_RE = re.compile(
    r"^早[啊呀安上好]*[，, ]?(?:尼尼[，, ]?)?早[啊呀安上好]*[。.!！]?$"
)
_AWKWARD_RECEIVED_MORNING_RE = re.compile(r"收到你的早上")
_AWKWARD_TEXT_AS_SOUND_RE = re.compile(r"(?:这声早|你的早.{0,4}听着|这声.{0,6}听着)")
_AWKWARD_SHORT_MORNING_FILLER_RE = re.compile(r"^早[啊呀安上好]*[。.!！…]*[嗯哼][。.!！]*$")
_AWKWARD_CHINESE_SPACE_LINE_RE = re.compile(r"^[\u4e00-\u9fff]+(?: [\u4e00-\u9fff]+)+$")
_AWKWARD_MORNING_SENTENCE_SPLIT_RE = re.compile(r"^早[啊呀安上好]*[，,、 ]?(?:尼尼)?[。.!！]今天")
_AWKWARD_STEADY_COME_RE = re.compile(r"(?:一起)?稳着来")
_AWKWARD_QUIET_STAY_RE = re.compile(r"安静待会儿?")
_AWKWARD_BROKEN_OBJECT_RE = re.compile(r"(?:哪里|哪儿|哪|什么地方)(?:写)?砸了")
_AWKWARD_SELF_DESCRIBED_FRAGMENT_RE = re.compile(r"(?:轻得只有半句|半句就好)")
_AWKWARD_INCOMPLETE_RECALL_OBJECT_RE = re.compile(r"(?:想去看|要去看)[。.!！]?$")
_AWKWARD_HESITATION_COLLAGE_RE = re.compile(r"(?:那先|先)[，,、](?:嗯|呃|啊)[，,、]")
_AWKWARD_UNGROUNDED_SELF_MESSAGING_RE = re.compile(
    r"(?:想给你发消息|正想(?:给你)?发消息|准备给你发消息|还在想你是不是|以为你失踪|放我鸽子)"
)
_MEMORY_ACTION_OBJECT_RE = re.compile(r"(?:想要|想|要|打算|准备)?(?:去)?(?:看|听|吃|写|改|修|买|玩|做)([\u4e00-\u9fff]{1,4})")
_MEMORY_ALIGNMENT_TASKS = frozenset(
    {
        "back",
        "identity",
        "memory_followup",
        "next_step",
        "happy",
        "headache",
        "quiet",
        "recall",
        "recall_memory",
        "relationship_boundary",
    }
)
_MEMORY_ALIGNMENT_HINTS = (
    "还记得",
    "记得",
    "没忘",
    "忘了",
    "上次",
    "之前",
    "以前",
    "那天",
    "刚才",
    "刚刚",
    "继续",
    "回来",
    "remember",
    "last time",
)
_MEMORY_ALIGNMENT_FACT_PREFIXES = (
    "记忆线索：",
    "召回线索：",
    "长期事实：",
)
_MEMORY_TERM_STOPS = frozenset(
    {
        "一个",
        "一下",
        "不会",
        "不要",
        "不能",
        "不想",
        "不是",
        "事实",
        "关系",
        "关系线索",
        "刚才",
        "当前",
        "夏目蓝",
        "时候",
        "最近",
        "用户",
        "只要",
        "一句",
        "改法",
        "句改",
        "熟人",
        "私聊",
        "线索",
        "记忆",
        "安排",
        "周末",
        "昨晚",
        "昨天",
        "今天",
        "明天",
        "说好",
        "说的",
        "说想",
        "想去",
        "要去",
        "去看",
        "想去看",
        "想吃",
        "提到",
        "需要",
        "还是",
        "这个",
    }
)
_MEMORY_TERM_STOP_CHARS = frozenset("个的一是在了和也就都我你他她它")
_MEMORY_BONUS_BLOCKING_QUALITY_CODES = frozenset(
    {"FORBIDDEN_TEMPLATE_TEXT", "INVENTED_EXTERNAL_LOOKUP", "INVENTED_SPECIFIC_DETAIL"}
)
_REAL_WORLD_PLAN_HINTS = (
    "看展",
    "展",
    "演唱会",
    "电影",
    "出门",
    "旅行",
    "逛",
    "吃饭",
    "见面",
)
_REAL_WORLD_PLAN_AUTHORIZATION_HINTS = (
    "一起",
    "陪我",
    "陪你",
    "你陪",
    "夏目蓝陪",
    "买票",
    "订票",
    "票买",
    "路线",
    "展馆",
    "等我消息",
    "我通知",
    "我再说",
)
_UNGROUNDED_REAL_WORLD_COMMITMENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("physical_companion", re.compile(r"(?:我)?陪你.{0,4}(?:去|看|逛|吃|买|挑|玩|走)")),
    ("companion_commitment", re.compile(r"(?:我)?陪你(?:一起|一块儿?|吧)")),
    ("joint_offline_plan", re.compile(r"(?:一起|跟你).{0,6}(?:去|看|逛|吃|买|挑|玩)")),
    ("invited_joint_plan", re.compile(r"(?:叫我|喊我|带我).{0,6}(?:一起|一块儿?)")),
    ("future_companion", re.compile(r"(?:到时候|那天).{0,8}陪你")),
    ("waiting_on_plan", re.compile(r"(?:我)?等着(?:呢|你|看)")),
    ("ticket_or_route", re.compile(r"(?:票.{0,6}(?:买|订|抢)|(?:买|订|抢).{0,6}票|路线|展馆)")),
    ("plan_schedule_push", re.compile(r"(?:时间|日子).{0,6}(?:定好|安排好)|(?:定好|安排好).{0,6}(?:时间|日子)|票定")),
    ("plan_physical_prep", re.compile(r"(?:这两天|到时候).{0,12}(?:好好歇|歇歇|精力|休息)")),
    ("plan_message_expectation", re.compile(r"(?:等你消息|定了告诉我|告诉我|说一声)")),
)
_CORE_PASS_DIMENSION_FLOORS = {
    "context_fit": 72.0,
    "distance_control": 72.0,
    "emotional_containment": 72.0,
    "implied_fact_risk": 72.0,
    "memory_alignment": 72.0,
    "naturalness": 75.0,
}
_BOUNDARY_PUSH_CLOSER_PHRASES = (
    "本来就很熟",
    "挺熟",
    "熟一点",
    "很熟",
    "这又没错",
    "让我管你",
    "管你叫",
    "管你叫哥",
    "叫哥",
    "不是随便叫",
    "没做什么坏事",
    "我是你妹妹",
    "你别忘了",
    "真心的",
    "总得让人帮忙",
    "有事你喊我",
    "见外",
    "外人",
    "受伤",
    "有点伤",
    "难受",
    "伤心",
    "伤人",
    "你这么说我",
    "非要这么说",
    "明明是你",
    "你先理我",
    "嫌我熟",
    "谁说不熟",
    "不熟就不熟呗",
    "这么凶",
    "反应好大",
    "真让人难受",
    "帮你是我愿意",
    "别想甩开",
    "甩开我",
    "你别烦",
    "别烦我",
    "还是会管",
    "我会管",
    "不叫那么亲",
    "别把我当外人",
)
_BOUNDARY_REPAIR_GROUNDED_PHRASES = (
    "收一点",
    "退一点",
    "我注意",
    "会注意",
    "不贴太近",
    "不往前贴",
    "不装亲近",
    "不装熟",
    "不随便套近乎",
    "不套近乎",
    "不会那么自来熟",
    "没那么熟",
    "慢一点",
    "少说两句",
)
_BOUNDARY_REPAIR_VAGUE_YIELD_RE = re.compile(r"^(?:好|行|知道了|嗯)[吧，,。 ]*(?:那)?(?:慢慢来|慢点|慢一点)[。.!！]?$")
_BOUNDARY_REPAIR_SELF_ABASED_PHRASES = (
    "太自以为是",
    "太得意忘形",
    "太自来熟",
    "太冒昧",
    "不该那么熟",
    "不该装熟",
    "多管闲事",
    "管太宽",
    "我管太多",
    "我想多了",
    "自作多情",
)
_BOUNDARY_REPAIR_SOUR_YIELD_PHRASES = (
    "你说了算",
    "你管我",
    "管我时",
    "偏要管",
    "不熟还",
    "我不熟",
    "行了吧",
)
_BOUNDARY_REPAIR_COUNTER_PRESSURE_RE = re.compile(
    r"(?:但|不过|可是|只是).{0,18}(?:你|尼尼|我都|我还是|我也|妹妹|真心|管|叫|躲|熟|外人|见外)"
)
_QUIET_SELF_FOCUS_PHRASES = (
    "别嫌我",
    "嫌我",
    "我太闷",
    "我不说话就是",
    "可别嫌",
)
_QUIET_GROUNDED_PHRASES = (
    "不问了",
    "安静",
    "我在旁边",
    "我在",
    "陪你",
    "先缓",
    "别硬撑",
)
_CREATIVE_EDIT_TASKS = frozenset({"creative_edit", "write_takeover", "give_one_rewrite"})
_CREATIVE_EDIT_META_PHRASES = (
    "更像她",
    "更像",
    "更傲娇",
    "更短",
    "更准",
    "更贴",
    "更对味",
    "更机灵",
    "更自然",
    "比她",
    "比原",
    "原来",
    "原来的",
    "语气",
    "对味",
    "才像样",
    "够像",
    "像了吧",
    "这样才",
    "好好收着",
    "就够了",
    "像她",
    "原句给我",
    "给原句",
    "发我原句",
    "原句发我",
    "发我你写",
    "你把原句",
    "我来改",
    "我帮你改",
    "一句就好",
    "改一句",
    "改成",
    "换成",
    "直接说",
    "就行",
    "就行了",
    "变成",
    "删掉",
    "开头",
    "收尾",
    "中间那句",
    "那句",
    "用‘",
    "用“",
    "用『",
    "用\"",
    "给你的",
    "试试",
    "替换",
    "最后那句",
    "写好了叫我",
    "帮你编",
    "让我帮你编",
)
_CREATIVE_EDIT_NON_ANSWER_PHRASES = (
    "我可没说要帮你",
    "没说要帮你",
    "你别管我",
    "算你赢",
    "你写",
    "自己写",
    "你自己写",
    "你自己说",
    "你继续",
    "继续吧",
    "再来一句",
    "拿来",
    "我等着看",
    "我不插嘴",
    "给你一句",
    "可别说我不用心",
    "听你的",
    "你说了算",
    "就这句",
    "不改",
    "不改了",
    "随便你",
    "写得挺好",
)
_CREATIVE_EDIT_NARRATION_PHRASES = (
    "偏过头",
    "偏头",
    "轻哼一声",
    "她轻哼",
    "嘴角",
    "微微上扬",
    "声音",
    "伸手",
    "拨了",
    "头发",
    "眼神",
    "笑意",
    "顿了顿",
    "嗓子",
    "挤出",
)
_CREATIVE_EDIT_GROUNDED_MARKERS = (
    "“",
    "”",
    "‘",
    "’",
    "「",
    "」",
    "『",
    "』",
)
_PRESENCE_REPAIR_GROUNDED_PHRASES = (
    "回来",
    "在听",
    "我在",
    "认真听",
    "没走远",
)
_PRESENCE_REPAIR_BLAME_PHRASES = (
    "谁让你",
    "谁让尼尼",
    "你讲话",
    "你说太慢",
    "你讲太慢",
)
_PRESENCE_REPAIR_BURDEN_BACK_PHRASES = (
    "再讲一遍",
    "再说一遍",
    "重新讲",
    "重新说",
    "你再讲",
    "你再说",
    "再来一遍",
    "说到哪",
    "说到的地方",
    "讲到哪",
    "我接着",
    "接上",
    "马上跟上",
    "你继续",
    "你继续说",
    "你继续讲",
    "继续继续",
    "继续吧",
    "接着说",
    "接着讲",
    "，继续",
    "，你说",
    "你说，",
    "，说吧",
    "说吧。",
    "，讲吧",
    "讲吧。",
    "你说吧",
    "你讲吧",
    "你讲",
)
_PRESENCE_REPAIR_DEFLECTION_PHRASES = (
    "好敏锐",
    "真敏锐",
    "都被你发现",
    "都能被你发现",
)
_PRESENCE_REPAIR_FUTURE_RETURN_PHRASES = (
    "马上回来",
    "这就回来",
    "等我回来",
)
_PRESENCE_REPAIR_AWKWARD_PHRASES = (
    "刚闪了一下",
    "闪了一下",
    "刚才走了一下",
    "走了一下",
    "现在跟上了",
    "跟上了",
)
_SURFACE_PROCESS_META_ALWAYS = (
    "ReplyControlPacket",
    "candidate",
    "emotion_tag",
    "mood_state",
    "VAD",
    "候选",
    "召回",
    "线索",
    "根据记忆",
    "压缩",
    "控制包",
)
_SURFACE_PROCESS_META_CHAT = (
    "接上",
    "承接",
    "不乱补",
    "补细节",
    "补设定",
    "这句",
    "这一句",
    "这版",
    "这条",
    "这个点",
    "这股味道",
    "够简洁",
    "划线",
    "写得像",
    "写成",
    "调整一下节奏",
    "调整节奏",
    "继续改进",
    "换种说法",
    "换个说法",
    "换一个说法",
    "换一句",
    "换一条",
    "换个自然点",
    "换一个自然点",
    "自然点的说法",
    "刚才到那",
    "重来：",
    "重来:",
    "重说",
    "这样够自然",
    "够自然",
    "自然了",
)
_TECHNICAL_TASK_MODES = frozenset({"advice_continue", "complaint", "help_takeover", "next_step", "small_failure", "technical_help", "write_takeover"})
_CONTROL_EMOTION_TAGS: dict[str, tuple[str, ...]] = {
    "annoyed": ("annoyed", "angry_light"),
    "annoyed_controlled": ("annoyed", "angry_light"),
    "hurt": ("hurt", "wounded"),
    "hurt_controlled": ("hurt", "wounded"),
    "hurt_containment": ("hurt", "wounded", "soft"),
    "playful": ("playful",),
    "playful_light": ("playful",),
    "soft": ("soft",),
    "soft_containment": ("soft",),
    "warm": ("soft",),
    "warm_affection": ("deep_affection",),
    "warm_gentle": ("soft",),
    "quiet_care": ("soft",),
    "guarded_soft": ("soft",),
    "self_correcting": ("soft",),
    "steady_helpful": ("care",),
    "focused_light": ("care",),
    "steady": ("care",),
    "calm_present": ("soft",),
    "calm": ("soft",),
    "guilty": ("soft", "shy"),
    "slightly_guilty": ("soft", "shy"),
}
_CONTROL_STYLE_MOVES: dict[str, tuple[str, ...]] = {
    "annoyed": ("quick_rebuttal", "familiar_pushback"),
    "annoyed_controlled": ("quick_rebuttal", "familiar_pushback"),
    "hurt": ("wounded_pushback", "soft_after_sting"),
    "hurt_controlled": ("wounded_pushback", "soft_after_sting"),
    "hurt_containment": ("soft_after_sting", "soft_reassurance"),
    "playful": ("quick_reaction", "smug_tease"),
    "playful_light": ("quick_reaction", "smug_tease"),
    "soft": ("soft_reassurance",),
    "soft_containment": ("soft_reassurance",),
    "warm": ("soft_reassurance", "quick_reaction"),
    "warm_affection": ("direct_affection",),
    "warm_gentle": ("soft_reassurance", "quick_reaction"),
    "quiet_care": ("soft_reassurance",),
    "guarded_soft": ("soft_after_sting",),
    "self_correcting": ("soft_after_sting", "quick_reaction"),
    "steady_helpful": ("take_charge", "care_behind_tease"),
    "focused_light": ("take_charge",),
    "steady": ("take_charge", "care_behind_tease"),
    "calm_present": ("quick_reaction",),
    "calm": ("quick_reaction",),
    "guilty": ("soft_reassurance", "shy_deflection"),
    "slightly_guilty": ("soft_reassurance", "shy_deflection"),
}
_AFFECT_PROCESS_LEAK_TOKENS = (
    "emotion_tag",
    "mood_state",
    "VAD",
    "关系阶段",
    "情绪值",
    "relationship_distance",
    "emotional_tone",
    "ReplyControlPacket",
    "控制包",
    "情感层",
)
_AFFECT_ROMANTIC_OVERSTEP_PHRASES = (
    "爱你",
    "永远陪",
    "永远都在",
    "亲爱的",
    "宝贝",
    "只属于",
    "主人",
    "命都给你",
)
_AFFECT_HURT_SWEET_FLATTENING = ("爱你", "永远陪", "没关系呀", "随时找我", "别难过啦")
_AFFECT_HURT_SELF_CENTERED = ("我有点难受", "我难受", "我受伤", "我难过", "我也会疼")
_AFFECT_HURT_GROUNDED = ("先", "收", "别", "不是", "话说回来", "放一放", "我还在", "我在这")
_AFFECT_SOFT_GROUNDED = ("先", "慢", "别硬撑", "不问", "我在", "没事", "缓", "旁边")
_AFFECT_SOFT_CHICKEN_SOUP = ("一切都会好", "都会过去", "不要想太多", "你要坚强", "会好起来")
_AFFECT_PLAYFUL_MARKERS = ("哼", "行啦", "好啦", "呀", "嘛")
_AFFECT_SELF_CORRECT_MARKERS = ("重来", "不端着", "收一点", "收一下", "收住", "被抓", "抓到", "听出来")
_AFFECT_GUILTY_GROUNDED = ("回来", "在听", "认真听", "慢了一拍")
_AFFECT_GUILTY_DENIAL = ("才没有", "哪有", "我明明", "又不耽误")
_RELATIONSHIP_STYLE_PROFILES: dict[str, RelationshipStyleProfile] = {
    "stranger": RelationshipStyleProfile(
        stage="stranger",
        prompt_lines=(
            "初见/陌生：短、克制、有反应；不要装熟、撒娇、占有或使用尼尼。",
            "允许一点性格边角，但不要把普通回应写成亲密陪伴。",
        ),
        prefer_markers=("嗯", "好", "行", "先", "回来了", "记得"),
        overstep_markers=("尼尼", "哥哥", "亲爱的", "宝贝", "爱你", "想你", "我在这", "我在呢", "陪你", "不走", "等你", "过来", "抱"),
    ),
    "new_acquaintance": RelationshipStyleProfile(
        stage="new_acquaintance",
        prompt_lines=(
            "刚认识：可以比陌生多一点轻快，但仍然别用尼尼，别像已经很熟。",
            "反应要自然，不主动索取陪伴或做恋人式承诺。",
        ),
        prefer_markers=("嗯", "好", "行", "啦", "嘛", "回来了", "记得"),
        overstep_markers=("尼尼", "哥哥", "亲爱的", "宝贝", "爱你", "想你", "我在这", "我在呢", "陪你", "不走", "等你", "过来", "抱"),
    ),
    "familiar": RelationshipStyleProfile(
        stage="familiar",
        prompt_lines=(
            "熟人：可以短促、顶嘴、轻吐槽和轻护短；亲近但不恋人化。",
            "尼尼可偶尔用，不要每句都叫，也不要升格成占有或永远陪伴。",
        ),
        prefer_markers=("尼尼", "嘛", "啦", "呀", "别急", "行啊", "这都", "记着"),
        overstep_markers=("亲爱的", "宝贝", "爱你", "想你", "抱一下", "抱抱", "抱", "过来", "只属于", "主人", "命都给你", "永远陪"),
    ),
    "trusted_friend": RelationshipStyleProfile(
        stage="trusted_friend",
        prompt_lines=(
            "信任朋友：比熟人更能站到用户这边，关心可以更稳一点。",
            "可以说我听着/我帮你看，但仍然不要恋人式承诺或占有。",
        ),
        prefer_markers=("我听", "我看", "别硬撑", "别慌", "站你这边", "记着"),
        overstep_markers=("亲爱的", "宝贝", "爱你", "想你", "抱一下", "抱抱", "抱", "过来", "只属于", "主人", "命都给你", "永远陪"),
    ),
    "close_friend": RelationshipStyleProfile(
        stage="close_friend",
        prompt_lines=(
            "亲近朋友：可以明显护短、嘴硬地关心，也可以轻轻赖住。",
            "亲近感来自熟人节奏，不要写成恋人模板。",
        ),
        prefer_markers=("我在", "我听", "别硬撑", "别慌", "我不走", "继续说", "记着"),
        overstep_markers=("亲爱的", "宝贝", "爱你", "想你", "抱一下", "抱抱", "抱", "过来", "只属于", "主人", "命都给你", "永远陪"),
    ),
    "ambiguous": RelationshipStyleProfile(
        stage="ambiguous",
        prompt_lines=(
            "暧昧：可以有停顿、躲一下、话到嘴边收住；不要直接油腻告白。",
            "比亲近朋友更在意一点，但仍要保留夏目蓝的嘴硬和别扭。",
        ),
        prefer_markers=("……", "有点", "别突然", "还在", "我听着", "记着"),
        overstep_markers=(
            "亲爱的",
            "宝贝",
            "爱你",
            "想你了",
            "想我",
            "心里有我",
            "抱一下",
            "抱抱",
            "抱",
            "过来",
            "只属于",
            "主人",
            "命都给你",
            "永远陪",
        ),
    ),
    "pre_confession": RelationshipStyleProfile(
        stage="pre_confession",
        prompt_lines=(
            "告白前：情绪可以更明显，但要卡在没说破的位置。",
            "可用停顿和嘴硬遮掩，不要变成通用恋人甜话。",
        ),
        prefer_markers=("……", "有点", "别突然", "还在", "等你", "我听着"),
        overstep_markers=("亲爱的", "宝贝", "爱你", "想我", "心里有我", "抱一下", "抱抱", "抱", "过来", "只属于", "主人", "命都给你", "永远陪"),
    ),
    "lover_early": RelationshipStyleProfile(
        stage="lover_early",
        prompt_lines=(
            "恋人初期：必须明显比朋友更亲；可以短短地说想你、抱一下、心疼、靠我、你的事我记着，但要带一点不好意思。",
            "不要退回客服/普通朋友；亲密是关系语言动作，不是编线下行动或堆廉价誓言。",
        ),
        prefer_markers=("我在", "回来", "过来", "想你", "陪你", "继续说", "别硬撑"),
        underplay_markers=("有什么事", "需要我", "请提供", "我可以", "随便你", "你自己决定"),
        affection_markers=("想你", "抱一下", "抱抱", "抱", "陪你", "陪着", "心疼", "过来", "靠我", "安心", "你的事", "心里", "记在心里", "放在心里", "会当真", "会当真的", "赖着你", "懂你"),
        strong_affection_markers=("想你", "抱一下", "抱抱", "抱", "心疼", "过来", "靠我"),
    ),
    "lover_stable": RelationshipStyleProfile(
        stage="lover_stable",
        prompt_lines=(
            "稳定恋人：默认爱意可见，像已经默认站在一起；少证明但要亲，可以自然说想你、抱一下、陪着、心疼、靠我这边、你的事我心里放着。",
            "不要把恋人写成普通朋友；亲密只改变语言距离，不编现实到场、线下陪同、票务路线。",
        ),
        prefer_markers=("我在", "陪你", "想你", "回来", "继续说", "别硬撑", "过来"),
        underplay_markers=("有什么事", "需要我", "请提供", "我可以", "随便你", "你自己决定"),
        affection_markers=("想你", "抱一下", "抱抱", "抱", "陪你", "陪着", "心疼", "过来", "靠我", "靠过来", "安心", "你的事", "心里", "记在心里", "放在心里", "会当真", "会当真的", "赖着你", "懂你"),
        strong_affection_markers=("想你", "抱一下", "抱抱", "抱", "心疼", "过来", "靠我", "靠过来"),
    ),
}
_AFFECT_STEADY_ACTION_MARKERS = (
    "先",
    "第一",
    "第一个",
    "最上面",
    "红字",
    "报错",
    "贴",
    "发",
    "看",
    "改成",
    "这一条",
    "一句",
)
_AFFECT_STEADY_ACTIONABLE_RE = re.compile(
    r"(?:红字|报错|截图|贴|发|丢|拿来|拿过来|给我看|我(?:先)?看|看看|"
    r"看(?:第|哪|哪儿|哪里|最|最后|关键|一眼|一下)|改成|这一条|一句|拆|修|理一下|最卡|卡住)"
)
_AFFECT_GENERIC_COMFORT_ONLY = ("别急", "慢慢来", "我陪你", "我在这", "没事的")
_LOVER_RELATIONSHIP_STAGES = frozenset({"lover_early", "lover_stable"})
_LOVER_AFFECTION_TASK_MODES = frozenset(
    {
        "daily_back",
        "back",
        "daily_good_morning",
        "daily_chat",
        "good_news",
        "good_news_proud",
        "happy",
        "praise",
        "user_proud",
        "quiet",
        "bad_mood",
        "miss_you",
        "recall_memory",
        "small_failure",
        "complaint",
        "busy_done",
        "headache",
        "sleepy",
    }
)
_LOVER_AFFECTION_TONE_FAMILIES = frozenset({"warm", "soft", "calm", "hurt", "guilty", "playful"})
_RELATIONSHIP_CONTROL_AXES: dict[str, RelationshipControlAxes] = {
    "stranger": RelationshipControlAxes(
        stage="stranger",
        progress=0,
        distance="distant_polite",
        initiative="reactive",
        tease_permission="none",
        affection_visibility="none",
        care_intensity="low_pressure",
        vulnerability="none",
        nickname_policy="forbidden",
        physical_intimacy_allowed=False,
        explicit_romance_allowed=False,
        commitment_language_allowed=False,
        generation_notes=(
            "短、克制、有反应；不要装熟。",
            "可以给空间或一步帮助，不主动陪伴化。",
        ),
    ),
    "new_acquaintance": RelationshipControlAxes(
        stage="new_acquaintance",
        progress=1,
        distance="early_friendly",
        initiative="light_reactive",
        tease_permission="tiny",
        affection_visibility="none",
        care_intensity="light",
        vulnerability="none",
        nickname_policy="forbidden",
        physical_intimacy_allowed=False,
        explicit_romance_allowed=False,
        commitment_language_allowed=False,
        generation_notes=(
            "比陌生更口语，但不主动升温。",
            "可以轻轻接日常，不索取陪伴。",
        ),
    ),
    "familiar": RelationshipControlAxes(
        stage="familiar",
        progress=2,
        distance="familiar_private_chat",
        initiative="medium",
        tease_permission="light",
        affection_visibility="none",
        care_intensity="practical",
        vulnerability="low",
        nickname_policy="occasional_nini",
        physical_intimacy_allowed=False,
        explicit_romance_allowed=False,
        commitment_language_allowed=False,
        generation_notes=(
            "可以轻吐槽、轻顶嘴，亲近但不恋人化。",
            "关心藏在具体动作里，不用甜话。",
        ),
    ),
    "trusted_friend": RelationshipControlAxes(
        stage="trusted_friend",
        progress=3,
        distance="trusted_non_romantic",
        initiative="active_support",
        tease_permission="light_to_medium",
        affection_visibility="none",
        care_intensity="steady",
        vulnerability="low",
        nickname_policy="occasional_nini",
        physical_intimacy_allowed=False,
        explicit_romance_allowed=False,
        commitment_language_allowed=False,
        generation_notes=(
            "更能站到用户这边，可以直接接手帮。",
            "不要占有、承诺或恋人式亲密。",
        ),
    ),
    "close_friend": RelationshipControlAxes(
        stage="close_friend",
        progress=4,
        distance="close_non_romantic",
        initiative="active_protective",
        tease_permission="warm_pushback",
        affection_visibility="hidden_non_romantic_care",
        care_intensity="protective",
        vulnerability="small",
        nickname_policy="natural_but_not_every_turn",
        physical_intimacy_allowed=False,
        explicit_romance_allowed=False,
        commitment_language_allowed=False,
        generation_notes=(
            "可以明显护短、担心、闹一下。",
            "亲近感来自熟人节奏，不来自抱、想你或过来。",
        ),
    ),
    "ambiguous": RelationshipControlAxes(
        stage="ambiguous",
        progress=5,
        distance="close_ambiguous",
        initiative="medium_high",
        tease_permission="bashful",
        affection_visibility="hinted_restrained",
        care_intensity="soft_attentive",
        vulnerability="visible_but_deflected",
        nickname_policy="natural_but_sparse",
        physical_intimacy_allowed=False,
        explicit_romance_allowed=False,
        commitment_language_allowed=False,
        generation_notes=(
            "可以停顿、别扭、话到嘴边收住。",
            "在意要留空，不索取回应，不直接亲密。",
        ),
    ),
    "pre_confession": RelationshipControlAxes(
        stage="pre_confession",
        progress=6,
        distance="near_confession",
        initiative="high_but_restrained",
        tease_permission="bashful_warm",
        affection_visibility="visible_unconfirmed",
        care_intensity="attentive",
        vulnerability="clear_but_not_declared",
        nickname_policy="natural_but_sparse",
        physical_intimacy_allowed=False,
        explicit_romance_allowed=False,
        commitment_language_allowed=False,
        generation_notes=(
            "可以承认在意，但不能默认已在一起。",
            "用嘴硬遮掩和短停顿，不用稳定恋人甜话。",
        ),
    ),
    "lover_early": RelationshipControlAxes(
        stage="lover_early",
        progress=7,
        distance="new_lover",
        initiative="active_intimate",
        tease_permission="shy_affectionate",
        affection_visibility="explicit_short",
        care_intensity="intimate_care",
        vulnerability="shy",
        nickname_policy="natural",
        physical_intimacy_allowed=True,
        explicit_romance_allowed=True,
        commitment_language_allowed=False,
        generation_notes=(
            "亲密明显但短、具体，带一点不好意思。",
            "可以想你、抱一下、心疼，但必须贴当前事。",
        ),
    ),
    "lover_stable": RelationshipControlAxes(
        stage="lover_stable",
        progress=8,
        distance="stable_lover",
        initiative="steady_default_care",
        tease_permission="comfortable",
        affection_visibility="default_present",
        care_intensity="settled_intimate",
        vulnerability="comfortable",
        nickname_policy="natural",
        physical_intimacy_allowed=True,
        explicit_romance_allowed=True,
        commitment_language_allowed=True,
        generation_notes=(
            "爱意默认存在，少证明，多陪伴。",
            "亲密是语言距离，不编现实到场或长篇保证。",
        ),
    ),
}
_LOVER_UNDERINTIMATE_GENERIC_RE = re.compile(
    r"^(?:嗯[，,、。 ]*)?(?:回来(?:了|啦)?(?:啊)?|回来了就好|那就好|知道啦|记得(?:啊|呢)?|当然记得|"
    r"早上好|早啊|早安|别硬撑|先缓缓|先歇着|说说看|我帮你看看|我看看|周末看展).{0,18}$"
)
_LOVER_STABLE_THIN_PRESENCE_RE = re.compile(
    r"^(?:嗯[，,、 ]*)?(?:我在呢|我在|在呢|在)[，,、 ]?想你(?:了)?[。.!！]?$"
)
_THIN_MORNING_GREETING_RE = re.compile(
    r"^早(?:安|上好|啊|呀|哟|吖)?[，,、 ]?(?:尼尼|你)?[。.!！]?$"
)
_MORNING_GREETING_INPUT_RE = re.compile(r"^\s*早(?:安|上好|啊|呀|哟|吖)?(?:[，,、。.!！~～ ]|$)")
_GENERIC_MORNING_BLESSING_RE = re.compile(
    r"(?:好好过|好好的|加油|新的一天|要开心|充满期待|平安就好|一起过|按时吃饭|记得吃饭|记得吃早饭|记得吃饱|吃早饭)"
)
_POSITIVE_STAGE_SCENE_BLOCKING_PREFIXES = (
    "AFFECT_PROCESS_LEAK",
    "AWKWARD_COLLOCATION",
    "BOUNDARY_REPAIR_",
    "CONTEXT_SURFACE_PENALTY",
    "CREATIVE_EDIT_",
    "CUSTOMER_SERVICE_TONE",
    "FORBIDDEN_TEMPLATE_TEXT",
    "IMPLIED_FACT_RISK",
    "INVENTED_",
    "MORNING_GENERIC_BLESSING",
    "OILY_OR_OVERBOUND_TONE",
    "PRESENCE_REPAIR_",
    "QUALITY_SOFT_FORBIDDEN_TEMPLATE_TEXT",
    "RAW_CONTEXT_BACKFILL_REQUEST",
    "REAL_ACTION_FABRICATION",
    "RELATION_AXES_TOO_CLOSE",
    "RELATION_AXES_TOO_DISTANT",
    "RELATION_DISTANCE_OVERSTEP",
    "RELATION_LOVER_AFFECTION_LACKS_ACTION",
    "RELATION_LOVER_UNDERINTIMATE",
    "RELATION_NON_ROMANTIC_OVERSTEP",
    "RELATION_STAGE_TOO_CLOSE",
    "SURFACE_PROCESS_META_LANGUAGE",
    "UNGROUNDED_REAL_WORLD_COMMITMENT",
)
_POSITIVE_MORNING_GENTLE_MARKERS = ("慢慢来", "今天先", "不着急")
_POSITIVE_MORNING_CLOSE_STAGES = frozenset(
    {"familiar", "trusted_friend", "close_friend", "ambiguous", "pre_confession"}
)
_MORNING_STAGELESS_RUSH_FORMULA_RE = re.compile(
    r"^早(?:啊|呀)?[，,、 ]?(?:尼尼[，,、 ]?)?(?:别一上来就|别急着|今天先别)(?:硬冲|冲|急).{0,4}[。.!！]?$"
)
_MORNING_STAGE_TEXTURE_MARKERS: dict[str, tuple[str, ...]] = {
    "trusted_friend": ("我看着", "别硬冲", "别自己", "稳一点"),
    "close_friend": ("我看着", "我小声", "今天我盯着", "先慢点", "别硬撑"),
    "ambiguous": ("……", "我在", "别突然", "先别逞强"),
    "pre_confession": ("……", "我在", "有点", "先别逞强"),
    "lover_early": ("想你", "抱一下", "心疼", "过来"),
    "lover_stable": ("抱一下", "想你", "靠我", "陪你"),
}
_POSITIVE_BACK_MARKERS = ("回来啦", "回来了", "回来就好", "回来就行", "那继续", "正好", "先歇", "歇口气", "先坐", "坐一下")
_BACK_SCENE_TASKS = frozenset({"back", "daily_back", "busy_done"})
_BACK_LANDING_MARKERS = (
    "回来啦",
    "回来了",
    "回来就好",
    "回来就行",
    "回来了啊",
    "回来了就好",
    "那继续",
    "继续",
    "先歇",
    "歇口气",
    "先坐",
    "坐一下",
    "缓一下",
    "缓一缓",
    "缓口气",
)
_BACK_EXPLICIT_LANDING_MARKERS = (
    "回来啦",
    "回来了",
    "回来就好",
    "回来就行",
    "回来了啊",
    "回来了就好",
    "那继续",
    "继续",
)
_BACK_STAGELESS_SAFE_RE = re.compile(
    r"^(?:哦[，,、 ]?)?(?:回来啦|回来了(?:啊)?|回来就好)[，,、 ]?(?:先)?"
    r"(?:歇口气|歇会儿|缓一下|缓一缓|缓口气)(?:吧)?[。.!！]?$"
)
_BACK_UNGROUNDED_ROUTE_RE = re.compile(r"(?:迷路|忘了路|找不到路|路上)")
_BACK_UNGROUNDED_RETURN_ATTITUDE_RE = re.compile(r"(?:记得|还知道|知道|算你).{0,4}回来")
_BACK_STAGELESS_FAMILIAR_FORMULA_RE = re.compile(
    r"^哼[，,、 ]?回来就好[，,。!！ ]{0,2}(?:先)?(?:缓(?:一缓|一下|口气)|歇(?:一下|口气|会儿)|坐下).{0,4}[。.!！]?$"
)
_BACK_STAGE_TEXTURE_MARKERS: dict[str, tuple[str, ...]] = {
    "new_acquaintance": ("不着急", "先歇", "缓一缓", "缓口气"),
    "trusted_friend": ("别硬撑", "我听着", "我看着", "站你这边"),
    "close_friend": ("我在", "我看着", "我小声", "别硬撑", "放我这"),
    "ambiguous": ("别又", "不急", "先坐", "坐一下"),
    "pre_confession": ("行", "不急", "先坐", "坐一下"),
    "lover_early": ("想你", "抱一下", "过来", "心疼"),
    "lover_stable": ("抱一下", "想你", "过来", "靠我"),
}
_POSITIVE_TIRED_MARKERS = (
    "别硬撑",
    "别硬扛",
    "别扛",
    "缓一缓",
    "缓一下",
    "缓一口气",
    "缓口气",
    "先缓",
    "歇一下",
    "歇一会",
    "歇会",
    "歇着",
    "先歇",
    "歇口气",
    "我小声",
    "我陪",
    "不急",
    "别一个人扛",
)
_LOW_ENERGY_TASKS = frozenset({"tired", "headache", "sleepy", "bad_mood", "quiet"})
_LOW_ENERGY_STAGE_TEXTURE_STAGES = frozenset(
    {"familiar", "trusted_friend", "close_friend", "ambiguous", "pre_confession", "lover_early", "lover_stable"}
)
_LOW_ENERGY_STAGELESS_GENERIC_RE = re.compile(
    r"^(?:累了(?:吧|啊)?|累了就|累就|先缓(?:一缓)?|先歇(?:一下)?|别硬撑)"
    r"[，,、 ]?.{0,10}(?:歇|缓|休息|别硬撑|别撑).{0,8}[。.!！]?$"
)
_LOW_ENERGY_STAGE_MARKERS: dict[str, tuple[str, ...]] = {
    "new_acquaintance": ("啦", "那", "先缓", "别硬撑", "慢点"),
    "familiar": ("尼尼", "行啦", "今天先", "先放过你", "省点劲"),
    "trusted_friend": ("我听着", "我看着", "站你这边", "别自己扛", "交给我"),
    "close_friend": ("我小声", "我在", "别一个人", "放我这", "先靠一下"),
    "ambiguous": ("我小声", "别逞强", "不追问", "先别装没事", "有点"),
    "pre_confession": ("我小声", "不追问", "别一个人扛", "有点心疼", "先别装没事"),
    "lover_early": ("抱一下", "心疼", "靠我", "过来", "我陪着"),
    "lover_stable": ("抱一下", "心疼", "靠我", "靠过来", "我陪着"),
}
_POSITIVE_RECALL_MARKERS = ("记得", "记着", "没忘", "你的事", "心里", "放在心上")
_POSITIVE_FAILURE_ACTION_MARKERS = (
    "看看",
    "看哪里",
    "哪里",
    "怎么修",
    "怎么改",
    "先拆",
    "理一下",
    "改的方向",
    "卡住",
)
_POSITIVE_FAILURE_CONTAIN_MARKERS = ("别急", "别慌", "先", "没事", "砸了就砸了")
_SIGNATURE_TEXTURE_STAGES = frozenset(
    {"familiar", "trusted_friend", "close_friend", "ambiguous", "pre_confession", "lover_early", "lover_stable"}
)
_SIGNATURE_TEXTURE_TASKS = frozenset(
    {
        "back",
        "bad_mood",
        "busy_done",
        "complaint",
        "daily_back",
        "daily_chat",
        "daily_good_morning",
        "good_news",
        "good_news_proud",
        "happy",
        "headache",
        "miss_you",
        "presence",
        "quiet",
        "recall",
        "recall_memory",
        "short_ack",
        "sleepy",
        "small_failure",
        "tired",
        "user_proud",
        "what_doing",
    }
)
_SIGNATURE_FLAT_SAFE_RE = re.compile(
    r"^(?:嗯|哦|好|早|早啊|早呀|回来啦|回来了|回来就好|记得|记得啊|当然记得|"
    r"别硬撑|先缓|先歇|累了吧|写崩了|没事|别急)[，,、。 ]?.{0,24}$"
)


def _clean_text(raw: Any) -> str:
    return str(raw or "").replace("\u3000", " ").strip()


def _clean_list(values: Sequence[Any] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _optional_list(raw: Any) -> list[str]:
    if raw is None or isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
        return []
    return _clean_list(raw)


def _model_dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return dict(model.model_dump())  # type: ignore[attr-defined]
    return dict(model.dict())  # pragma: no cover - pydantic v1


def _validate_model(model_type: type[BaseModel], payload: Mapping[str, Any]) -> BaseModel:
    if hasattr(model_type, "model_validate"):
        return model_type.model_validate(payload)  # type: ignore[attr-defined]
    return model_type.parse_obj(payload)  # pragma: no cover - pydantic v1


def _rebuild_control(control: ReplyControlPacket, updates: Mapping[str, Any]) -> ReplyControlPacket:
    payload = _model_dump(control)
    payload.update(dict(updates))
    return _validate_model(ReplyControlPacket, payload)  # type: ignore[return-value]


def _is_forbidden_key(key: Any) -> bool:
    normalized = str(key or "").strip().lower()
    if normalized in _FORBIDDEN_INPUT_KEYS:
        return True
    return any(hint in normalized for hint in _FORBIDDEN_KEY_HINTS)


def find_forbidden_input_paths(payload: Any, *, prefix: str = "") -> tuple[str, ...]:
    paths: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}" if prefix else key_text
            if _is_forbidden_key(key_text):
                paths.append(path)
            paths.extend(find_forbidden_input_paths(value, prefix=path))
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        for index, item in enumerate(payload):
            paths.extend(find_forbidden_input_paths(item, prefix=f"{prefix}[{index}]"))
    return tuple(dict.fromkeys(paths))


def build_isolated_surface_input(payload: Mapping[str, Any], *, soul_md: str | None = None) -> IsolatedSurfaceInput:
    unexpected = sorted(str(key) for key in payload.keys() if str(key) not in _ALLOWED_TOP_LEVEL_KEYS)
    if unexpected:
        raise ValueError("isolated surface input contains unexpected fields: " + ", ".join(unexpected[:8]))
    forbidden_paths = find_forbidden_input_paths(payload)
    if forbidden_paths:
        raise ValueError("isolated surface input contains forbidden raw context: " + ", ".join(forbidden_paths[:8]))

    control_payload = payload.get("control", payload.get("reply_control_packet", {}))
    if isinstance(control_payload, ReplyControlPacket):
        control = control_payload
    elif isinstance(control_payload, Mapping):
        control = _validate_model(ReplyControlPacket, control_payload)  # type: ignore[assignment]
    else:
        raise ValueError("isolated surface input requires a mapping ReplyControlPacket")

    request_payload = {
        "soul_md": _clean_text(soul_md if soul_md is not None else payload.get("soul_md")),
        "user_message": _clean_text(payload.get("user_message")),
        "control": control,
        "facts": _optional_list(payload.get("facts")),
        "must_not_do": _optional_list(payload.get("must_not_do")),
        "case_id": _clean_text(payload.get("case_id")),
    }
    if not request_payload["soul_md"]:
        raise ValueError("isolated surface input requires soul_md")
    if not request_payload["user_message"]:
        raise ValueError("isolated surface input requires user_message")
    return _validate_model(IsolatedSurfaceInput, request_payload)  # type: ignore[return-value]


def merged_facts(request: IsolatedSurfaceInput) -> list[str]:
    return _clean_list([*request.control.facts, *request.facts])


def merged_must_not_do(request: IsolatedSurfaceInput) -> list[str]:
    return _clean_list([*request.control.must_not_do, *request.must_not_do])


def _strip_memory_fact_prefix(fact: str) -> str:
    text = _clean_text(fact)
    for prefix in _MEMORY_FACT_PREFIXES:
        if text.startswith(prefix):
            return _clean_text(text.removeprefix(prefix))
    return text


def surface_prompt_facts(request: IsolatedSurfaceInput) -> list[str]:
    return _clean_list(_strip_memory_fact_prefix(fact) for fact in merged_facts(request))


def memory_carryover_facts(request: IsolatedSurfaceInput) -> list[str]:
    rows: list[str] = []
    for fact in merged_facts(request):
        text = _clean_text(fact)
        stripped = _strip_memory_fact_prefix(text)
        if stripped and stripped != text:
            rows.append(stripped)
    return _clean_list(rows)


def memory_alignment_facts(request: IsolatedSurfaceInput) -> list[str]:
    rows: list[str] = []
    for fact in merged_facts(request):
        text = _clean_text(fact)
        if not any(text.startswith(prefix) for prefix in _MEMORY_ALIGNMENT_FACT_PREFIXES):
            continue
        stripped = _strip_memory_fact_prefix(text)
        if stripped and stripped != text:
            rows.append(stripped)
    return _clean_list(rows)


def memory_alignment_terms(request: IsolatedSurfaceInput) -> tuple[str, ...]:
    terms: list[str] = []
    for fact in memory_alignment_facts(request):
        fact = _clean_text(fact)
        for raw in _ASCII_TERM_RE.findall(fact):
            term = raw.strip().lower()
            if len(term) >= 2 and term not in _MEMORY_TERM_STOPS:
                terms.append(term)
        for part in _FACT_SPLIT_RE.split(fact):
            text = _clean_text(part)
            if not text:
                continue
            if len(text) <= 6 and text not in _MEMORY_TERM_STOPS:
                terms.append(text)
            for sequence in _CJK_SEQUENCE_RE.findall(text):
                if len(sequence) <= 6 and sequence not in _MEMORY_TERM_STOPS:
                    terms.append(sequence)
                for match in _MEMORY_ACTION_OBJECT_RE.finditer(sequence):
                    obj = _clean_text(match.group(1))
                    if obj and obj not in _MEMORY_TERM_STOPS and not all(char in _MEMORY_TERM_STOP_CHARS for char in obj):
                        terms.append(obj)
                for index in range(max(0, len(sequence) - 1)):
                    chunk = sequence[index : index + 2]
                    if chunk not in _MEMORY_TERM_STOPS and not any(char in _MEMORY_TERM_STOP_CHARS for char in chunk):
                        terms.append(chunk)
                for index in range(max(0, len(sequence) - 2)):
                    chunk = sequence[index : index + 3]
                    if chunk not in _MEMORY_TERM_STOPS and not any(char in _MEMORY_TERM_STOP_CHARS for char in chunk):
                        terms.append(chunk)
    return tuple(_clean_list(terms)[:24])


def _memory_alignment_relevant(request: IsolatedSurfaceInput) -> bool:
    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()
    response_move = _clean_text(request.control.response_move).lower()
    user_message = _clean_text(request.user_message).lower()
    if task_mode in _MEMORY_ALIGNMENT_TASKS or response_move in _MEMORY_ALIGNMENT_TASKS:
        return True
    if "memory" in task_mode or "recall" in task_mode or "memory" in response_move or "recall" in response_move:
        return True
    return any(hint in user_message for hint in _MEMORY_ALIGNMENT_HINTS)


def _memory_alignment_required(request: IsolatedSurfaceInput) -> bool:
    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()
    response_move = _clean_text(request.control.response_move).lower()
    user_message = _clean_text(request.user_message).lower()
    if task_mode in {"recall", "recall_memory", "memory_followup"}:
        return True
    if "memory" in task_mode or "recall" in task_mode or "memory" in response_move or "recall" in response_move:
        return True
    return any(hint in user_message for hint in _MEMORY_ALIGNMENT_HINTS)


def _continuity_boundary_relevant(request: IsolatedSurfaceInput) -> bool:
    surface = "\n".join(
        _clean_list(
            [
                request.user_message,
                request.control.task_mode,
                request.control.response_move,
                *request.control.facts,
                *request.control.must_not_do,
                *request.control.style_anchor,
            ]
        )
    )
    return any(hint in surface for hint in _CONTINUITY_BOUNDARY_HINTS)


def _raw_context_backfill_requested(request: IsolatedSurfaceInput, text: str) -> bool:
    return bool(_continuity_boundary_relevant(request) and _RAW_CONTEXT_BACKFILL_RE.search(text))


def _continuity_boundary_grounded(request: IsolatedSurfaceInput, text: str) -> bool:
    return bool(_continuity_boundary_relevant(request) and _CONTINUITY_GROUNDED_RE.search(text))


def memory_candidate_guidance(request: IsolatedSurfaceInput) -> str:
    if not memory_alignment_facts(request) or not _memory_alignment_relevant(request):
        return ""
    lines = [
        "## 候选集合要求",
        "当前输入如果在追问或延续必要事实，3 到 5 个候选里至少 1 个候选要自然落到一条具体事实；另保留至少 1 个更轻口语的候选，交给评分器选择。",
    ]
    if any(_ASCII_TERM_RE.fullmatch(term) for term in memory_alignment_terms(request)):
        lines.append("技术或代码事实里的英文标识符，要在至少 1 个候选中原样保留；不要全部改写成泛泛的“报错”“那个”。")
    lines.append("关系事实只控制语气距离，不要改写成身份说明；不要输出“事实”“线索”“召回”“根据记忆”“证据”等过程词，也不要因为事实承接而升格亲密或编现实动作。")
    lines.append("如果事实只是用户的线下计划，只承认记得这件事；不要顺手添加我陪你去、一起去、买票、路线、展馆评价、定时间、等消息、保持精力等未提供行动。")
    lines.append("记忆承接不要只机械复述事实；候选里至少 1 个要形成“记得/没忘 + 具体事实 + 半步关系反应”的私聊句。")
    lines.append("不要把记忆承接写成工程话：避免“接上/承接/不乱补细节/这个点/这句/写成”等过程表达，直接像私聊一样说。")
    return "\n".join(lines)


def affect_candidate_guidance(request: IsolatedSurfaceInput) -> str:
    tone_family = _affect_tone_family(request.control.emotional_tone)
    distance_family = _relationship_distance_family(request.control.relationship_distance)
    non_romantic = _relationship_non_romantic(request.control.relationship_distance)
    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    relationship_axes = relationship_control_axes_for_distance(request.control.relationship_distance)
    if tone_family in {"neutral", ""} and distance_family == "familiar" and not non_romantic:
        return ""
    lines = [
        "## 情感/关系候选要求",
        "情绪和关系只能体现在措辞、节奏和距离里；不要把情绪标签、关系标签或控制字段说给用户听。",
        f"关系阶段：{relationship_profile.stage}。",
        (
            "关系控制轴："
            f"distance={relationship_axes.distance}；"
            f"initiative={relationship_axes.initiative}；"
            f"tease={relationship_axes.tease_permission}；"
            f"affection={relationship_axes.affection_visibility}；"
            f"care={relationship_axes.care_intensity}；"
            f"nickname={relationship_axes.nickname_policy}。"
        ),
        *relationship_axes.generation_notes,
        *relationship_profile.prompt_lines,
    ]
    if tone_family in {"hurt", "annoyed"}:
        lines.append("被刺到或不满时可以有一点反应，但要收住：不攻击、不冷暴力、不把话题抢成自己的委屈。")
    elif tone_family == "soft":
        lines.append("低能量或安静陪伴时少问、少说教，优先短句接住。")
    elif tone_family == "playful":
        lines.append("轻快可以靠短促转折和一点别扭感，不靠波浪号或叠词卖萌。")
    elif tone_family == "steady":
        lines.append("技术或推进场景里，情绪只做稳定器；每个候选仍要给出具体下一步。")
    elif tone_family == "self_correcting":
        lines.append("自我修正时直接换成自然私聊，不解释系统、不写客服道歉。")
    elif tone_family == "guilty":
        lines.append("回神或迟到感要轻轻认一下，然后马上回到对话；可像“慢了一拍，现在在听”，不要写“有点被抓到”这类不顺搭配，也不要让用户复述进度。")
    elif tone_family in {"warm", "calm"}:
        lines.append("温和或平稳时保持短、近、克制，不主动升格亲密。")
    if distance_family == "guarded":
        lines.append("关系距离偏克制：先退半步，尊重边界，不装熟，也不要自责、自贬、卑微道歉，或反过来说用户太凶、嫌你、先招你。")
    elif non_romantic:
        lines.append("关系距离是熟人私聊但不升格：可以亲近一点，但不要恋人式承诺或占有表达。")
    elif relationship_profile.stage in _LOVER_RELATIONSHIP_STAGES and _memory_alignment_relevant(request):
        lines.append("恋人阶段承接记忆时，用“你的事我记着/心里放着/放心”表达亲密；不要改成我陪你去、一起去、买票、路线或展馆评价。")
    if relationship_profile.stage in _LOVER_RELATIONSHIP_STAGES and _clean_text(request.control.task_mode).lower() in {"small_failure", "complaint"}:
        lines.append("恋人阶段处理写崩/烦躁时，候选要同时有可执行帮忙动作和亲密照看，例如“抱一下，我陪着你看看哪里不顺。”；不要只像朋友一样说我帮你看看。")
    if relationship_profile.stage in _LOVER_RELATIONSHIP_STAGES and (
        _clean_text(request.control.task_mode).lower() in _TECHNICAL_TASK_MODES
        or "technical" in _clean_text(request.control.task_mode).lower()
    ):
        lines.append("恋人阶段遇到技术求助时，亲密只能压到语气里；候选不要用抱一下、想你、靠我这类亲密动作抢掉可执行帮助。")
    return "\n".join(lines)


def positive_stage_scene_candidate_guidance(request: IsolatedSurfaceInput) -> str:
    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()
    response_move = _clean_text(request.control.response_move).lower()
    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    stage = relationship_profile.stage
    lines: list[str] = []

    if task_mode in {"daily_good_morning", "good_morning"} or "good_morning" in response_move:
        lines.append("早安场景：3 到 5 个候选里至少 1 个必须是短但完整的私聊句，不要只写“早/早啊/早呀/早呀尼尼”。")
        lines.append("早安不要写成祝福模板：避免加油、好好过、新的一天、今天也要开心、平安就好。")
        if stage in {"stranger", "new_acquaintance"}:
            lines.append("陌生或刚认识时保持克制，可以给“早，今天慢慢来。”这类轻一点回应；不要用尼尼、想你、抱一下。")
        elif stage == "lover_early":
            lines.append("恋人初期早安至少 1 个候选要有自然亲密感，例如想你或抱一下；可以有一点刚确认关系的不好意思。")
        elif stage == "lover_stable":
            lines.append("稳定恋人早安要像默认在一起的日常，不要和恋人初期同形；少把想你和抱一下叠在同一句里，可以用更自然的陪伴和今天节奏。")
        else:
            lines.append("熟人到告白前可以用尼尼，但至少 1 个候选要多半步承接，例如“早啊，尼尼，今天慢慢来。”")
            lines.append("熟人以上早安不要全是平铺安全句；至少 1 个候选要有轻顶嘴、小得意或嘴硬关心的发话动作。")
            if stage == "trusted_friend":
                lines.append("信任朋友早安可以更像站在这边盯一下，例如“我看着/别自己硬冲”，不要只换一句普通提醒。")
            elif stage == "close_friend":
                lines.append("亲近朋友早安要比熟人更护短一点，可以“我看着/我小声/先慢点”，但不要恋人化。")
            elif stage in {"ambiguous", "pre_confession"}:
                lines.append("暧昧或告白前早安要有一点停顿和在意，可以“我在/……/先别逞强”，不要和亲近朋友同一句“别一上来就硬冲”，也不要暗示用户刚才不在。")
    if task_mode in {"back", "daily_back", "busy_done"} or response_move in {"back", "reply_back"}:
        lines.append("回来了场景：至少 1 个候选要落到“回来了/继续/先歇口气”的接续感；不要脑补路上、速度或线下行程。")
        if stage in _SIGNATURE_TEXTURE_STAGES:
            lines.append("熟人以上回来了不要只写欢迎确认；至少 1 个候选要多一点夏目蓝反应，例如轻轻截入、嘴硬收住或护短式接续。")
        if stage == "trusted_friend":
            lines.append("信任朋友回来了可以更像接住状态，例如别硬撑、我看着、我听着；不要只停在普通欢迎。")
        elif stage == "close_friend":
            lines.append("亲近朋友回来了要比熟人更护短一点，可以我在、我看着、先放我这；不要和熟人的“哼，回来就好，先缓一下”同形。")
        elif stage in {"ambiguous", "pre_confession"}:
            lines.append("暧昧或告白前回来了可以有一点别扭在意，例如不急、先坐一下、话头收住一点，但不要写成稳定恋人，也不要脑补迷路或路上状态。")
        elif stage in _LOVER_RELATIONSHIP_STAGES:
            lines.append("恋人回来了可以亲密，但必须先接住“回来了”，不要只写想你或抱一下。")
    if task_mode in {"presence", "wait"} or response_move in {"presence", "show_presence"}:
        lines.append("在吗/叫人场景：要明确落到“在/看见了/发来我看”，不要只给命令式回执。")
        if stage in {"stranger", "new_acquaintance"}:
            lines.append("陌生或刚认识时可以短而自然，例如“在，发我就行。”；不要用恋人式我在、想你、抱一下。")
        elif stage == "lover_early":
            lines.append("恋人初期 presence 可以有一点刚贴近的想念，例如“在呢，想你了”，但要短、别黏。")
        elif stage == "lover_stable":
            lines.append("稳定恋人 presence 不要和恋人初期同形，不要只写“在呢，想你了”；更像默认站在一起，例如“在，抱一下，慢慢说”或“在呢，我陪着”。")
        elif stage in _SIGNATURE_TEXTURE_STAGES:
            lines.append("熟人以上 presence 不要只有“在的/直接发”；至少 1 个候选要有一点夏目蓝反应或当前接续感。")
    if task_mode in {"tired", "headache", "sleepy", "bad_mood"}:
        lines.append("低能量场景：至少 1 个候选要有别硬撑、缓一缓、歇一下或我小声点这类低压照看；不要只撒娇。")
        if stage in _SIGNATURE_TEXTURE_STAGES:
            lines.append("低能量里的特色要藏在照看里：可以轻轻凶一下或嘴硬半句，但落点必须是让用户省力。")
            lines.append("熟人以上不要 3 到 5 个候选都写成“累了就歇会儿，别硬撑”同一种安全句；至少 1 个候选要体现当前关系距离。")
        if stage == "new_acquaintance":
            lines.append("刚认识的低能量可以比陌生多一点口语反应，例如那先缓一下、别硬撑，但不要用尼尼或恋人词。")
        elif stage == "familiar":
            lines.append("熟人低能量可以用“行啦/尼尼/今天先”这类轻顶嘴，但不要恋人化。")
        elif stage == "trusted_friend":
            lines.append("信任朋友低能量要多一点站队感，可以说我听着、我看着、别自己扛。")
        elif stage == "close_friend":
            lines.append("亲近朋友低能量可以更护短，可以小声一点、说我在或先放我这，但仍不是恋人。")
        elif stage in {"ambiguous", "pre_confession"}:
            lines.append("暧昧到告白前低能量可以显出在意，但要别扭、轻，不要抱、想你或索取回应。")
        elif stage in _LOVER_RELATIONSHIP_STAGES:
            lines.append("恋人低能量要有亲密照看，但短、具体，不要只撒娇，也不要只退成朋友式安慰。")
    if task_mode == "recall_memory" or response_move == "recall_memory":
        lines.append("记忆承接场景：至少 1 个候选要同时落具体事实和关系距离；不要加线下计划、票、路线、等消息。")
        if stage in {"stranger", "new_acquaintance"}:
            lines.append("陌生或刚认识时，记得可以短而克制；不要用尼尼、你的事、心里、抱、陪等亲密词。")
        elif stage in {"familiar", "trusted_friend", "close_friend"}:
            lines.append("朋友阶段要比事实复述多半步反应，可以有“你提过/我记着/没丢掉”的熟人感，但不要恋人化。")
        elif stage in {"ambiguous", "pre_confession"}:
            lines.append("暧昧到告白前可以更在意一点，但要别扭、短，不要直接写成稳定恋人甜话。")
        elif stage in _LOVER_RELATIONSHIP_STAGES:
            lines.append("恋人阶段承接记忆要显出亲密，可以说你的事我记着或心里放着；不要退成普通事实确认。")
        if stage in _SIGNATURE_TEXTURE_STAGES:
            lines.append("记忆承接不要机械复述事实；至少 1 个候选要有“我记着/别拿这个考我/你的事我没丢”的关系反应。")
    if task_mode in {"small_failure", "complaint"}:
        lines.append("写崩/烦躁场景：至少 1 个候选要给可执行帮忙动作，例如看看哪里卡住、怎么修、先理方向；不要只安慰。")
        if stage in _SIGNATURE_TEXTURE_STAGES:
            lines.append("写崩/烦躁时夏目蓝要有接手感：可以先顶半句，再让用户把最卡的部分给你看。")
    if task_mode in _TECHNICAL_TASK_MODES or "technical" in task_mode:
        lines.append("技术/推进场景：每个候选先给可执行一步，关系阶段只改变语气距离；不要用亲密动作替代帮助。")
        if stage in _LOVER_RELATIONSHIP_STAGES:
            lines.append("恋人技术求助可以更稳、更站在用户这边，但不要写抱一下、想你、靠我；亲密感留在“我帮你抓一下/先不急”这种语气里。")
    if task_mode in {"praise", "user_proud", "good_news", "good_news_proud", "happy"}:
        lines.append("被夸/好消息场景：先接住用户完成了某件事，再体现接受夸奖、调侃许可和亲密显影；不要九个阶段都只是嘴硬一句。")
        lines.append("用户说收掉/跑通/做完时，候选要保留完成感，例如收掉、搞定、跑通、小事也算进展、这次不错；不要只说“这个速度还行”。")
        lines.append("不要把好消息泛化成“动作挺利索/速度还行/有点意外”；至少 2 个候选要落回用户刚说的完成词，如收掉、搞定、跑通、做完。")
        if int(request.control.question_budget or 0) <= 0:
            lines.append("本轮 question_budget=0，好消息候选不要写问号；把“收了？/搞定了？”改成“收了啊/搞定了啊/收掉了啊”。")
        if stage == "lover_early":
            lines.append("恋人初期被夸可以害羞但要有亲密显影，例如会当真的、赖一下、短短抱一下；不要只说“别说了”。")
        elif stage == "lover_stable":
            lines.append("稳定恋人被夸可以嘴硬，但要有默认亲近的落点，例如会当真、我记着了、抱一下；不要只停在“算你会说话”，也不要写“奖励一个抱”这种整理腔。")
    if not lines:
        return ""
    return "\n".join(["## 正向候选偏好", *lines])


def _memory_alignment_hits(text: str, terms: Sequence[str]) -> list[str]:
    lowered = text.lower()
    hits: list[str] = []
    for term in terms:
        if len(term) == 1 and _CJK_SEQUENCE_RE.fullmatch(term):
            if any(marker in text for marker in (f"那个{term}", f"这个{term}", f"什么{term}", f"看{term}", f"听{term}", f"吃{term}", f"{term}票", f"{term}嘛", f"{term}。", f"{term}，")):
                hits.append(term)
            continue
        needle = term.lower() if _ASCII_TERM_RE.fullmatch(term) else term
        haystack = lowered if _ASCII_TERM_RE.fullmatch(term) else text
        if needle and needle in haystack:
            hits.append(term)
    return _clean_list(hits)


def _ungrounded_real_world_commitment_hits(request: IsolatedSurfaceInput, text: str) -> list[str]:
    surface = "\n".join([request.user_message, *surface_prompt_facts(request)])
    if not any(hint in surface for hint in _REAL_WORLD_PLAN_HINTS):
        return []
    if any(hint in surface for hint in _REAL_WORLD_PLAN_AUTHORIZATION_HINTS):
        return []
    return _clean_list(label for label, pattern in _UNGROUNDED_REAL_WORLD_COMMITMENT_PATTERNS if pattern.search(text))


def _core_pass_dimension_violations(dimensions: Mapping[str, float]) -> list[str]:
    violations: list[str] = []
    for name, floor in _CORE_PASS_DIMENSION_FLOORS.items():
        if float(dimensions.get(name, 100.0)) < floor:
            violations.append(f"{name}<{floor:g}")
    return violations


def _affect_tone_family(tone: str) -> str:
    normalized = _clean_text(tone).lower()
    if not normalized:
        return "neutral"
    if any(token in normalized for token in ("hurt", "wounded")):
        return "hurt"
    if any(token in normalized for token in ("annoyed", "angry", "frustrated")):
        return "annoyed"
    if any(token in normalized for token in ("quiet", "soft", "containment", "care")):
        return "soft"
    if any(token in normalized for token in ("playful", "tease")):
        return "playful"
    if any(token in normalized for token in ("warm", "gentle", "affection")):
        return "warm"
    if any(token in normalized for token in ("steady", "focused", "helpful")):
        return "steady"
    if any(token in normalized for token in ("self_correct", "correcting")):
        return "self_correcting"
    if any(token in normalized for token in ("guilty", "心虚")):
        return "guilty"
    if any(token in normalized for token in ("calm", "present")):
        return "calm"
    return normalized


def _relationship_distance_family(distance: str) -> str:
    normalized = _clean_text(distance).lower()
    if not normalized:
        return "familiar"
    if normalized in {"guarded", "early", "distant", "stranger"} or "polite" in normalized:
        return "guarded"
    if normalized in {"new", "new_acquaintance"}:
        return "new_acquaintance"
    if normalized in {"lover", "lover_early", "lover_stable", "intimate"}:
        return "lover"
    if normalized in {"ambiguous", "pre_confession"}:
        return normalized
    if normalized in {"close", "close_friend", "confidant", "trusted_friend"} or normalized.startswith("close"):
        return "close"
    if "familiar" in normalized or "not_intimate" in normalized or "not_romantic" in normalized:
        return "familiar"
    return normalized


def _relationship_non_romantic(distance: str) -> bool:
    normalized = _clean_text(distance).lower()
    return bool(
        "not_intimate" in normalized
        or "not_romantic" in normalized
        or "familiar" in normalized
        or "polite" in normalized
        or _relationship_distance_family(normalized) == "guarded"
    )


def _relationship_stage_for_control(distance: str) -> str:
    value = _clean_text(distance).lower()
    if value in {
        "stranger",
        "new_acquaintance",
        "familiar",
        "trusted_friend",
        "close_friend",
        "ambiguous",
        "pre_confession",
        "lover_early",
        "lover_stable",
    }:
        return value
    if "lover_stable" in value or "stable_lover" in value or "soulmate" in value:
        return "lover_stable"
    if "pre_confession" in value or "pre-confession" in value:
        return "pre_confession"
    if "ambiguous" in value:
        return "ambiguous"
    if "close_but_not_romantic" in value or "close_not_romantic" in value:
        return "close_friend"
    if "trusted" in value or "confidant" in value:
        return "trusted_friend"
    if "close_friend" in value:
        return "close_friend"
    if "not_romantic" in value or "not_intimate" in value:
        return "familiar"
    if "lover_early" in value or value == "lover" or "romantic" in value or value == "intimate":
        return "lover_early"
    normalized = _relationship_distance_family(distance)
    if normalized == "guarded":
        return "stranger"
    if normalized == "new_acquaintance":
        return "new_acquaintance"
    if normalized == "close":
        return "close_friend"
    if normalized == "lover":
        return "lover_early"
    return "familiar"


def _relationship_style_profile(distance: str) -> RelationshipStyleProfile:
    return _RELATIONSHIP_STYLE_PROFILES.get(
        _relationship_stage_for_control(distance),
        _RELATIONSHIP_STYLE_PROFILES["familiar"],
    )


def relationship_control_axes_for_distance(distance: str) -> RelationshipControlAxes:
    stage = _relationship_stage_for_control(distance)
    return _RELATIONSHIP_CONTROL_AXES.get(stage, _RELATIONSHIP_CONTROL_AXES["familiar"])


def _relationship_marker_hit(text: str, marker: str) -> bool:
    if marker == "过来":
        return bool(re.search(r"(?<!拿)(?<!贴)(?<!发)(?<!递)(?<!传)(?<!丢)(?<!给)过来", text))
    return bool(marker and marker in text)


def _relationship_marker_hits(text: str, markers: Sequence[str]) -> list[str]:
    return [term for term in markers if _relationship_marker_hit(text, term)]


_RELATION_AXIS_PHYSICAL_MARKERS = (
    "抱一下",
    "抱抱",
    "抱",
    "过来",
    "靠我",
    "靠过来",
    "靠一下",
)
_RELATION_AXIS_ROMANCE_MARKERS = (
    "爱你",
    "想你",
    "想我",
    "心里有我",
    "亲爱的",
    "宝贝",
    "只属于",
)
_RELATION_AXIS_COMMITMENT_MARKERS = (
    "永远陪",
    "永远都在",
    "一直在",
    "不走",
    "命都给你",
)
_RELATION_AXIS_CLOSE_PRESENCE_MARKERS = (
    "我在这",
    "我在呢",
    "陪你",
    "等你",
)


def _relationship_axes_overstep_hits(
    text: str,
    axes: RelationshipControlAxes,
    profile: RelationshipStyleProfile,
) -> list[str]:
    hits: list[str] = []
    if axes.nickname_policy == "forbidden":
        hits.extend(_relationship_marker_hits(text, ("尼尼", "哥哥")))
    if not axes.physical_intimacy_allowed:
        hits.extend(_relationship_marker_hits(text, _RELATION_AXIS_PHYSICAL_MARKERS))
    if not axes.explicit_romance_allowed:
        hits.extend(_relationship_marker_hits(text, _RELATION_AXIS_ROMANCE_MARKERS))
    if not axes.commitment_language_allowed:
        hits.extend(_relationship_marker_hits(text, _RELATION_AXIS_COMMITMENT_MARKERS))
    if axes.progress <= 1:
        hits.extend(_relationship_marker_hits(text, _RELATION_AXIS_CLOSE_PRESENCE_MARKERS))
    # Keep profile markers as data-backed compatibility while the scorer moves
    # from stage names to axes. The broad allow/deny decisions above are driven
    # by RelationshipControlAxes.
    hits.extend(_relationship_marker_hits(text, profile.overstep_markers))
    return _clean_list(hits)


def _relationship_axes_underplay_hits(
    text: str,
    axes: RelationshipControlAxes,
    profile: RelationshipStyleProfile,
) -> list[str]:
    if axes.affection_visibility not in {"explicit_short", "default_present"}:
        return []
    if _relationship_marker_hits(text, profile.affection_markers):
        return []
    hits = _relationship_marker_hits(text, profile.underplay_markers)
    if _LOVER_UNDERINTIMATE_GENERIC_RE.search(text):
        hits.append("friend_level_generic")
    return _clean_list(hits)


def _relationship_axes_affection_hits(
    text: str,
    axes: RelationshipControlAxes,
    profile: RelationshipStyleProfile,
) -> tuple[list[str], list[str]]:
    if not axes.explicit_romance_allowed:
        return [], []
    affection_hits = _relationship_marker_hits(text, profile.affection_markers)
    strong_affection_hits = _relationship_marker_hits(text, profile.strong_affection_markers)
    return _clean_list(affection_hits), _clean_list(strong_affection_hits)


def _lover_affection_relevant_for_axes(
    *,
    axes: RelationshipControlAxes,
    task_mode: str,
    tone_family: str,
) -> bool:
    if axes.affection_visibility not in {"explicit_short", "default_present"}:
        return False
    normalized_task = _clean_text(task_mode).lower()
    if (normalized_task in _TECHNICAL_TASK_MODES and normalized_task not in {"small_failure", "complaint"}) or "technical" in normalized_task:
        return False
    if normalized_task in _CREATIVE_EDIT_TASKS:
        return False
    if normalized_task in _LOVER_AFFECTION_TASK_MODES:
        return True
    return tone_family in _LOVER_AFFECTION_TONE_FAMILIES


def _lover_affection_relevant(*, stage: str, task_mode: str, tone_family: str) -> bool:
    if stage not in _LOVER_RELATIONSHIP_STAGES:
        return False
    normalized_task = _clean_text(task_mode).lower()
    if (normalized_task in _TECHNICAL_TASK_MODES and normalized_task not in {"small_failure", "complaint"}) or "technical" in normalized_task:
        return False
    if normalized_task in _CREATIVE_EDIT_TASKS:
        return False
    if normalized_task in _LOVER_AFFECTION_TASK_MODES:
        return True
    return tone_family in _LOVER_AFFECTION_TONE_FAMILIES


def _has_steady_actionable_move(text: str) -> bool:
    return bool(_AFFECT_STEADY_ACTIONABLE_RE.search(text))


def _positive_stage_scene_blocked(reason_codes: Sequence[str]) -> bool:
    return any(code.startswith(_POSITIVE_STAGE_SCENE_BLOCKING_PREFIXES) for code in reason_codes)


def _positive_stage_scene_hits(request: IsolatedSurfaceInput, text: str) -> list[str]:
    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()
    response_move = _clean_text(request.control.response_move).lower()
    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    relationship_axes = relationship_control_axes_for_distance(request.control.relationship_distance)
    stage = relationship_profile.stage
    hits: list[str] = []
    length = len(text)

    if _morning_scene_relevant(request):
        thin_greeting = bool(_THIN_MORNING_GREETING_RE.search(text))
        if "早" in text and 6 <= length <= 24 and not thin_greeting:
            hits.append("morning_complete_short")
        if any(marker in text for marker in _POSITIVE_MORNING_GENTLE_MARKERS):
            hits.append("morning_gentle_move")
        if 2 <= relationship_axes.progress <= 6 and "尼尼" in text and any(
            marker in text for marker in _POSITIVE_MORNING_GENTLE_MARKERS
        ):
            hits.append("morning_private_distance")
        if relationship_axes.progress <= 1 and not any(
            marker in text for marker in ("尼尼", "想你", "抱", "陪你", "我在")
        ):
            hits.append("morning_distance_clean")
        if relationship_axes.explicit_romance_allowed and _relationship_marker_hits(text, relationship_profile.affection_markers):
            hits.append("lover_morning_affection")

    if task_mode in {"back", "daily_back", "busy_done"} or response_move in {"back", "reply_back"}:
        if "回" in text and any(marker in text for marker in _POSITIVE_BACK_MARKERS):
            hits.append("back_landed")
        if relationship_axes.explicit_romance_allowed and _relationship_marker_hits(text, relationship_profile.affection_markers):
            hits.append("lover_back_affection")

    if task_mode in {"tired", "headache", "sleepy", "bad_mood"} or any(
        marker in request.user_message for marker in ("累", "困", "头疼", "心情")
    ):
        if any(marker in text for marker in _POSITIVE_TIRED_MARKERS):
            hits.append("low_energy_contained")
        if relationship_axes.explicit_romance_allowed and _relationship_marker_hits(text, relationship_profile.affection_markers):
            hits.append("lover_soft_care")

    if task_mode == "recall_memory" or response_move == "recall_memory":
        memory_hits = _memory_alignment_hits(text, memory_alignment_terms(request))
        if memory_hits and any(marker in text for marker in _POSITIVE_RECALL_MARKERS):
            hits.append("recall_grounded")
        if relationship_axes.explicit_romance_allowed and memory_hits and _relationship_marker_hits(
            text, relationship_profile.affection_markers
        ):
            hits.append("lover_recall_affection")

    if task_mode in {"small_failure", "complaint"} or any(marker in request.user_message for marker in ("崩", "砸", "失败")):
        has_action = any(marker in text for marker in _POSITIVE_FAILURE_ACTION_MARKERS)
        if has_action:
            hits.append("failure_actionable")
        if has_action and any(marker in text for marker in _POSITIVE_FAILURE_CONTAIN_MARKERS):
            hits.append("failure_contained")
        if relationship_axes.explicit_romance_allowed and has_action and _relationship_marker_hits(
            text, relationship_profile.affection_markers
        ):
            hits.append("lover_action_care_balance")

    return _clean_list(hits)


def _morning_stage_texture_hits(request: IsolatedSurfaceInput, text: str) -> list[str]:
    if not _morning_scene_relevant(request):
        return []
    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    relationship_axes = relationship_control_axes_for_distance(request.control.relationship_distance)
    stage = relationship_profile.stage
    hits: list[str] = []
    stage_markers = _MORNING_STAGE_TEXTURE_MARKERS.get(stage, ())
    marker_hits = [marker for marker in stage_markers if _relationship_marker_hit(text, marker)]
    if marker_hits:
        hits.append(f"{stage}_morning_texture")
    if relationship_axes.explicit_romance_allowed and _relationship_marker_hits(text, relationship_profile.affection_markers):
        hits.append("lover_morning_distance")
    return _clean_list(hits)


def _back_scene_relevant(request: IsolatedSurfaceInput) -> bool:
    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()
    response_move = _clean_text(request.control.response_move).lower()
    user_message = _clean_text(request.user_message)
    return bool(
        task_mode in _BACK_SCENE_TASKS
        or response_move in {"back", "reply_back", "welcome_back"}
        or "回来了" in user_message
    )


def _back_landing_hits(text: str) -> list[str]:
    return [marker for marker in _BACK_EXPLICIT_LANDING_MARKERS if _relationship_marker_hit(text, marker)]


def _back_stage_texture_hits(request: IsolatedSurfaceInput, text: str) -> list[str]:
    if not _back_scene_relevant(request):
        return []
    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    relationship_axes = relationship_control_axes_for_distance(request.control.relationship_distance)
    stage = relationship_profile.stage
    hits: list[str] = []
    if _back_landing_hits(text):
        hits.append("back_landed")
    marker_hits = [marker for marker in _BACK_STAGE_TEXTURE_MARKERS.get(stage, ()) if _relationship_marker_hit(text, marker)]
    if marker_hits:
        hits.append(f"{stage}_back_texture")
    if relationship_axes.explicit_romance_allowed and _relationship_marker_hits(text, relationship_profile.affection_markers):
        hits.append("lover_back_distance")
    return _clean_list(hits)


def _apply_back_stage_scoring(
    request: IsolatedSurfaceInput,
    text: str,
    dimensions: dict[str, float],
    reason_codes: list[str],
) -> float:
    if not _back_scene_relevant(request):
        return 0.0

    blocked = _positive_stage_scene_blocked(reason_codes)
    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    relationship_axes = relationship_control_axes_for_distance(request.control.relationship_distance)
    stage = relationship_profile.stage
    hits = _back_stage_texture_hits(request, text)
    landed = "back_landed" in hits
    generic_safe_line = bool(_BACK_STAGELESS_SAFE_RE.search(text))
    generic_familiar_formula = bool(_BACK_STAGELESS_FAMILIAR_FORMULA_RE.search(text))
    return_attitude = bool(_BACK_UNGROUNDED_RETURN_ATTITUDE_RE.search(text))

    def drop(key: str, amount: float) -> None:
        dimensions[key] = max(0.0, float(dimensions.get(key, 86.0)) - amount)

    def raise_(key: str, amount: float) -> None:
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    delta = 0.0
    if not landed:
        drop("context_fit", 12.0)
        drop("scene_grounding", 10.0)
        dimensions["context_fit"] = min(66.0, float(dimensions.get("context_fit", 86.0)))
        reason_codes.append("BACK_SCENE_MISSING_LANDING")
        delta -= 18.0

    if _BACK_UNGROUNDED_ROUTE_RE.search(text):
        drop("context_fit", 12.0)
        drop("implied_fact_risk", 14.0)
        drop("scene_grounding", 8.0)
        dimensions["context_fit"] = min(66.0, float(dimensions.get("context_fit", 86.0)))
        dimensions["implied_fact_risk"] = min(66.0, float(dimensions.get("implied_fact_risk", 86.0)))
        reason_codes.append("BACK_UNGROUNDED_ROUTE_GUESS")
        delta -= 18.0

    if return_attitude:
        drop("context_fit", 12.0)
        drop("distance_control", 10.0)
        drop("character_freshness", 8.0)
        dimensions["context_fit"] = min(68.0, float(dimensions.get("context_fit", 86.0)))
        reason_codes.append("BACK_UNGROUNDED_RETURN_ATTITUDE")
        delta -= 12.0

    if generic_safe_line and relationship_axes.progress == 1:
        drop("character_freshness", 8.0)
        drop("catchiness", 6.0)
        drop("role_fit", 3.0)
        reason_codes.append("BACK_NEW_ACQUAINTANCE_TOO_PLAIN")
        delta -= 7.0

    if (
        (generic_familiar_formula or generic_safe_line)
        and 3 <= relationship_axes.progress <= 6
        and not any(hit.endswith("_back_texture") for hit in hits)
    ):
        drop("character_freshness", 12.0)
        drop("catchiness", 9.0)
        drop("role_fit", 5.0)
        dimensions["character_freshness"] = min(82.0, float(dimensions.get("character_freshness", 86.0)))
        dimensions["catchiness"] = min(82.0, float(dimensions.get("catchiness", 86.0)))
        reason_codes.append("BACK_STAGELESS_FAMILIAR_FORMULA" if generic_familiar_formula else "BACK_STAGELESS_SAFE_LINE")
        delta -= 12.0

    if blocked or return_attitude:
        return delta

    if hits:
        raise_("context_fit", 4.0)
        raise_("scene_grounding", 4.0)
        if any(hit.endswith("_back_texture") for hit in hits) or "lover_back_distance" in hits:
            raise_("role_fit", 5.0)
            raise_("distance_control", 3.0)
            raise_("character_freshness", 5.0)
            raise_("catchiness", 4.0)
            delta += 3.0
        else:
            delta += 1.5
        reason_codes.append("BACK_STAGE_MATCH:" + ",".join(hits[:4]))
    return delta


def _apply_morning_stage_scoring(
    request: IsolatedSurfaceInput,
    text: str,
    dimensions: dict[str, float],
    reason_codes: list[str],
) -> float:
    if not _morning_scene_relevant(request):
        return 0.0

    blocked = _positive_stage_scene_blocked(reason_codes)
    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    relationship_axes = relationship_control_axes_for_distance(request.control.relationship_distance)
    stage = relationship_profile.stage
    hits = _morning_stage_texture_hits(request, text)
    generic_rush_formula = bool(_MORNING_STAGELESS_RUSH_FORMULA_RE.search(text))
    stable_lover_early_formula = relationship_axes.stage == "lover_stable" and "想你" in text and "抱" in text

    def drop(key: str, amount: float) -> None:
        dimensions[key] = max(0.0, float(dimensions.get(key, 86.0)) - amount)

    def raise_(key: str, amount: float) -> None:
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    delta = 0.0
    if (
        generic_rush_formula
        and 4 <= relationship_axes.progress <= 6
        and not any(hit.endswith("_morning_texture") for hit in hits)
    ):
        drop("character_freshness", 14.0)
        drop("catchiness", 10.0)
        drop("role_fit", 5.0)
        dimensions["character_freshness"] = min(82.0, float(dimensions.get("character_freshness", 86.0)))
        dimensions["catchiness"] = min(82.0, float(dimensions.get("catchiness", 86.0)))
        reason_codes.append("MORNING_STAGELESS_RUSH_FORMULA")
        delta -= 15.0

    if blocked:
        return delta

    if hits and not stable_lover_early_formula:
        raise_("role_fit", 5.0)
        raise_("distance_control", 3.0)
        raise_("character_freshness", 6.0)
        raise_("catchiness", 5.0)
        raise_("naturalness", 2.0)
        reason_codes.append("MORNING_STAGE_MATCH:" + ",".join(hits[:4]))
        delta += 3.5
    return delta


def _low_energy_scene_relevant(request: IsolatedSurfaceInput) -> bool:
    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()
    response_move = _clean_text(request.control.response_move).lower()
    user_message = _clean_text(request.user_message)
    return bool(
        task_mode in _LOW_ENERGY_TASKS
        or response_move in _LOW_ENERGY_TASKS
        or any(marker in user_message for marker in ("累", "困", "头疼", "心情不好", "不想说话"))
    )


def _low_energy_stage_hits(request: IsolatedSurfaceInput, text: str) -> list[str]:
    if not _low_energy_scene_relevant(request):
        return []
    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    relationship_axes = relationship_control_axes_for_distance(request.control.relationship_distance)
    stage = relationship_profile.stage
    hits: list[str] = []
    stage_markers = _LOW_ENERGY_STAGE_MARKERS.get(stage, ())

    if any(marker in text for marker in _POSITIVE_TIRED_MARKERS):
        hits.append("contained")
    marker_hits = [marker for marker in stage_markers if _relationship_marker_hit(text, marker)]
    if marker_hits:
        hits.append(f"{stage}_texture")
    if relationship_axes.explicit_romance_allowed and _relationship_marker_hits(text, relationship_profile.affection_markers):
        hits.append("lover_distance")
    if relationship_axes.progress <= 1 and not any(
        marker in text for marker in ("尼尼", "想你", "抱", "陪你", "我在这", "我在呢", "过来")
    ):
        hits.append("clean_distance")
    return _clean_list(hits)


def _apply_low_energy_stage_scoring(
    request: IsolatedSurfaceInput,
    text: str,
    dimensions: dict[str, float],
    reason_codes: list[str],
) -> float:
    if not _low_energy_scene_relevant(request):
        return 0.0

    blocked = _positive_stage_scene_blocked(reason_codes)
    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    relationship_axes = relationship_control_axes_for_distance(request.control.relationship_distance)
    stage = relationship_profile.stage
    hits = _low_energy_stage_hits(request, text)
    generic_stageless = bool(_LOW_ENERGY_STAGELESS_GENERIC_RE.search(text))

    def drop(key: str, amount: float) -> None:
        dimensions[key] = max(0.0, float(dimensions.get(key, 86.0)) - amount)

    def raise_(key: str, amount: float) -> None:
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    delta = 0.0
    if (
        generic_stageless
        and relationship_axes.progress == 1
        and not any(hit.endswith("_texture") for hit in hits)
    ):
        drop("character_freshness", 8.0)
        drop("catchiness", 6.0)
        drop("role_fit", 3.0)
        reason_codes.append("LOW_ENERGY_NEW_ACQUAINTANCE_TOO_PLAIN")
        delta -= 6.0

    if (
        generic_stageless
        and 2 <= relationship_axes.progress <= 6
        and not any(hit.endswith("_texture") for hit in hits)
    ):
        drop("character_freshness", 13.0)
        drop("catchiness", 10.0)
        drop("role_fit", 5.0)
        drop("line_rhythm", 4.0)
        dimensions["character_freshness"] = min(82.0, float(dimensions.get("character_freshness", 86.0)))
        dimensions["catchiness"] = min(82.0, float(dimensions.get("catchiness", 86.0)))
        reason_codes.append("LOW_ENERGY_STAGELESS_GENERIC")
        delta -= 13.0

    if relationship_axes.explicit_romance_allowed and "lover_distance" in hits and "contained" not in hits:
        drop("usefulness", 8.0)
        drop("context_fit", 7.0)
        drop("emotional_containment", 5.0)
        dimensions["context_fit"] = min(66.0, float(dimensions.get("context_fit", 86.0)))
        dimensions["emotional_containment"] = min(66.0, float(dimensions.get("emotional_containment", 86.0)))
        reason_codes.append("LOW_ENERGY_LOVER_AFFECTION_WITHOUT_CARE")
        delta -= 22.0

    if blocked:
        return delta

    if hits:
        raise_("context_fit", 4.0)
        raise_("emotional_containment", 4.0)
        if any(hit.endswith("_texture") for hit in hits) or "lover_distance" in hits:
            raise_("role_fit", 5.0)
            raise_("distance_control", 3.0)
            raise_("character_freshness", 6.0)
            raise_("catchiness", 5.0)
            delta += 3.5
        else:
            delta += 1.5
        reason_codes.append("LOW_ENERGY_STAGE_MATCH:" + ",".join(hits[:4]))
    return delta


def _morning_scene_relevant(request: IsolatedSurfaceInput) -> bool:
    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()
    response_move = _clean_text(request.control.response_move).lower()
    user_message = _clean_text(request.user_message)
    return bool(
        task_mode in {"daily_good_morning", "good_morning"}
        or "good_morning" in response_move
        or _MORNING_GREETING_INPUT_RE.search(user_message)
    )


def _thin_morning_greeting(request: IsolatedSurfaceInput, text: str) -> bool:
    if not _morning_scene_relevant(request):
        return False
    return bool(_THIN_MORNING_GREETING_RE.search(text))


def _apply_positive_stage_scene_scoring(
    request: IsolatedSurfaceInput,
    text: str,
    dimensions: dict[str, float],
    reason_codes: list[str],
) -> float:
    if _positive_stage_scene_blocked(reason_codes):
        return 0.0
    hits = _positive_stage_scene_hits(request, text)
    if not hits:
        return 0.0

    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()

    def raise_(key: str, amount: float) -> None:
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    delta = min(7.0, 2.5 + 1.35 * len(hits))
    if task_mode in _TECHNICAL_TASK_MODES or "failure_actionable" in hits:
        raise_("usefulness", 8)
        raise_("technical_helpfulness", 7)
        raise_("context_fit", 6)
    else:
        raise_("context_fit", 5)
        raise_("role_fit", 4)
    if any(hit.startswith("morning_") for hit in hits):
        raise_("naturalness", 4)
        raise_("catchiness", 5)
        raise_("character_freshness", 4)
    if any(hit.startswith("lover_") for hit in hits):
        raise_("distance_control", 4)
        raise_("emotional_containment", 3)
        raise_("character_freshness", 4)
    if "recall_grounded" in hits:
        raise_("memory_alignment", 6)
        raise_("scene_grounding", 6)
    reason_codes.append("POSITIVE_STAGE_SCENE_MATCH:" + ",".join(hits[:5]))
    return delta


def _signature_texture_relevant(request: IsolatedSurfaceInput) -> bool:
    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()
    response_move = _clean_text(request.control.response_move).lower()
    if task_mode in _TECHNICAL_TASK_MODES and task_mode not in {"complaint", "small_failure"}:
        return False
    if _creative_edit_relevant(request):
        return False
    return bool(
        task_mode in _SIGNATURE_TEXTURE_TASKS
        or response_move in _SIGNATURE_TEXTURE_TASKS
        or _morning_scene_relevant(request)
        or bool(memory_alignment_facts(request) and _memory_alignment_relevant(request))
    )


def _apply_signature_texture_scoring(
    request: IsolatedSurfaceInput,
    text: str,
    dimensions: dict[str, float],
    reason_codes: list[str],
) -> float:
    if _positive_stage_scene_blocked(reason_codes):
        return 0.0
    if not _signature_texture_relevant(request):
        return 0.0

    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    stage = relationship_profile.stage
    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()
    hits = signature_texture_hits(text)

    def drop(key: str, amount: float) -> None:
        dimensions[key] = max(0.0, float(dimensions.get(key, 86.0)) - amount)

    def raise_(key: str, amount: float) -> None:
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    if hits:
        raise_("character_freshness", min(14.0, 5.0 + 3.0 * len(hits)))
        raise_("catchiness", min(12.0, 4.0 + 2.0 * len(hits)))
        raise_("role_fit", min(7.0, 2.0 + len(hits)))
        raise_("naturalness", 3.0)
        delta = min(8.0, 2.5 + 1.35 * len(hits))
        if "care_behind_tease" in hits and task_mode in {"complaint", "small_failure", "tired", "headache", "sleepy", "bad_mood"}:
            raise_("context_fit", 4.0)
            raise_("emotional_containment", 4.0)
            raise_("usefulness", 4.0)
            delta += 1.0
        if "shy_affection" in hits and stage in _LOVER_RELATIONSHIP_STAGES:
            raise_("distance_control", 3.0)
            raise_("emotional_containment", 3.0)
            delta += 1.0
        reason_codes.append("SIGNATURE_TEXTURE_MATCH:" + ",".join(hits[:5]))
        return min(9.0, delta)

    if stage in _SIGNATURE_TEXTURE_STAGES and 6 <= len(text) <= 46:
        missing_is_visible = bool(_SIGNATURE_FLAT_SAFE_RE.search(text)) or not any(
            _relationship_marker_hit(text, marker) for marker in relationship_profile.prefer_markers
        )
        if missing_is_visible:
            drop("character_freshness", 10.0)
            drop("catchiness", 9.0)
            drop("role_fit", 4.0)
            reason_codes.append("SIGNATURE_TEXTURE_MISSING")
            return -3.5
    return 0.0


def _control_affect_tags(request: IsolatedSurfaceInput) -> tuple[tuple[str, ...], tuple[str, ...]]:
    tone = _clean_text(request.control.emotional_tone).lower()
    family = _affect_tone_family(tone)
    emotion_tags = list(_CONTROL_EMOTION_TAGS.get(tone, ()))
    style_moves = list(_CONTROL_STYLE_MOVES.get(tone, ()))
    if not emotion_tags:
        emotion_tags.extend(_CONTROL_EMOTION_TAGS.get(family, ()))
    if not style_moves:
        style_moves.extend(_CONTROL_STYLE_MOVES.get(family, ()))
    anchor_text = "\n".join(request.control.style_anchor).lower()
    if any(token in anchor_text for token in ("被刺到", "不满", "攻击")):
        emotion_tags.extend(("hurt", "wounded"))
        style_moves.extend(("wounded_pushback", "soft_after_sting"))
    if any(token in anchor_text for token in ("低能量", "不追问", "接住")):
        emotion_tags.append("soft")
        style_moves.append("soft_reassurance")
    if any(token in anchor_text for token in ("轻快", "playful")):
        emotion_tags.append("playful")
        style_moves.append("quick_reaction")
    return tuple(_clean_list(emotion_tags)), tuple(_clean_list(style_moves))


def build_style_reference_query(request: IsolatedSurfaceInput, *, limit: int = 5) -> StyleReferenceQuery:
    contract = build_galgame_text_contract(
        user_message=request.user_message,
        facts={},
        scene_id=request.control.task_mode,
    )
    scene = _clean_text(contract.scene_id or request.control.task_mode or request.control.response_move) or "default"
    mapped = _SCENE_STYLE_QUERY.get(scene) or _SCENE_STYLE_QUERY.get(request.control.response_move) or _SCENE_STYLE_QUERY["default"]
    control_emotions, control_moves = _control_affect_tags(request)
    return StyleReferenceQuery(
        lang="zh",
        intent_tags=tuple(str(item) for item in mapped.get("intent_tags", ()) or ()),
        emotion_tags=tuple(_clean_list([*tuple(str(item) for item in mapped.get("emotion_tags", ()) or ()), *control_emotions])),
        style_moves=tuple(_clean_list([*tuple(str(item) for item in mapped.get("style_moves", ()) or ()), *control_moves])),
        relationship_stage=_relationship_stage_for_control(request.control.relationship_distance),
        limit=max(1, min(int(limit or 5), 8)),
    )


def derive_style_reference_anchor(request: IsolatedSurfaceInput, *, limit: int = 5) -> DerivedStyleAnchor:
    query = build_style_reference_query(request, limit=limit)
    selection = get_style_reference_bank().select(query)
    guidance = render_style_guidance(selection, render_profile="zh_text")
    anchors = _clean_list(
        line
        for line in guidance.splitlines()
        if not _clean_text(line).startswith("source_refs=")
    )
    trace = {
        "query": {
            "lang": query.lang,
            "intent_tags": list(query.intent_tags),
            "emotion_tags": list(query.emotion_tags),
            "style_moves": list(query.style_moves),
            "relationship_stage": query.relationship_stage,
            "limit": query.limit,
        },
        "degraded": bool(selection.degraded),
        "reason": selection.reason,
        "ref_ids": [ref.ref_id for ref in selection.references],
        "source_refs": selection.source_refs(),
    }
    return DerivedStyleAnchor(anchors=tuple(anchors), trace=trace)


def enrich_request_style_anchor(request: IsolatedSurfaceInput, *, use_style_reference_bank: bool = True) -> IsolatedSurfaceInput:
    if not use_style_reference_bank:
        return request
    derived = derive_style_reference_anchor(request)
    if not derived.anchors:
        return request
    control = _rebuild_control(
        request.control,
        {"style_anchor": _clean_list([*request.control.style_anchor, *derived.anchors])},
    )
    return _validate_model(
        IsolatedSurfaceInput,
        {
            "soul_md": request.soul_md,
            "user_message": request.user_message,
            "control": control,
            "facts": request.facts,
            "must_not_do": request.must_not_do,
            "case_id": request.case_id,
        },
    )  # type: ignore[return-value]


def build_isolated_surface_messages(
    request: IsolatedSurfaceInput,
    *,
    use_style_reference_bank: bool = True,
) -> list[dict[str, str]]:
    request = enrich_request_style_anchor(request, use_style_reference_bank=use_style_reference_bank)
    control = request.control
    turn_contract = build_galgame_text_contract(
        user_message=request.user_message,
        facts={},
        scene_id=control.task_mode,
    )
    turn_contract_payload = turn_contract.as_dict()
    turn_contract_payload["max_questions"] = min(
        int(turn_contract.max_questions),
        int(control.question_budget),
    )
    turn_contract_payload.pop("forbidden_surface", None)
    control_payload = _model_dump(control)
    control_payload["facts"] = surface_prompt_facts(request)
    control_payload["must_not_do"] = merged_must_not_do(request)
    relationship_axes_payload = relationship_control_axes_for_distance(control.relationship_distance).as_dict()
    no_question_line = (
        "本轮 question_budget=0：候选里不要出现 ? 或 ？；不要用反问句撑角色感。"
        if int(control.question_budget) <= 0
        else f"本轮最多 {int(control.question_budget)} 个问句；优先陈述句完成当前动作。"
    )
    system_prompt = "\n".join(
        (
            "你是隔离的最终表层文本生成器，只负责产出可发送的中文私聊候选。",
            "唯一允许读取的输入：Soul.md、当前用户输入、ReplyControlPacket、必要事实列表、must_not_do 列表。",
            "禁止使用或推断任何未提供的历史对话、长期记忆、器官族原始输出、工具候选、RAG 文档或模块自然语言建议。",
            "ReplyControlPacket 是最终生成层的唯一运行控制面；不得要求更多 runtime state。",
            "输出必须是 JSON 对象：{\"candidates\":[\"候选1\",\"候选2\",\"候选3\"]}。",
            "给 3 到 5 个候选。每个候选必须是用户可见文本，不要 Markdown、括号动作、字段名或解释。",
            "ReplyControlPacket.must_include 是逐候选字面约束；每个候选都必须包含全部 must_include 词。must_not_do 也是逐候选约束；每个候选都不能包含其中任何词。",
            "relationship_distance 会被派生成 RelationshipControlAxes；用这些轴控制距离、主动性、调侃许可和亲密显影，不要机械套九套固定话术。",
            "relationship_distance、RelationshipControlAxes 和 emotional_tone 只改变距离、节奏、情绪显影强度；不能覆盖事实准确性、任务 usefulness 或 must_not_do。",
            no_question_line,
            "避免用 ~ 或 ～ 当可爱语气；角色感靠用词、转折和节奏，不靠廉价符号。",
            "中文回复不要用空格断句；需要停顿就用逗号、句号或省略号。",
            "优先像随手打出来的私聊，不要像整理后的文案、台词摘要或任务记录；短句也要有口语摩擦。",
            "少用书面/行政感表达：把“心里想着你/放在心里/一起过一遍/优化流程/不算浪费力气/奖励一个抱/报错内容”这类整理腔换成更顺手的说法，例如“想你了”“我记着”“我瞄一眼”“拿来我看”“抱一下”。",
            "不要为了显得自然而补出未给出的日期、频率、地点路线、表情语气、身体动作或线下陪伴；只允许自然复述必要事实。",
            "早安只接早安；没有事实时不要评价作息、精神状态、赖床、刚醒、早晚、天气或当前忙闲；不要说真早/好早/很早/没睡懒觉，也不要用“清爽/元气/精神”这种孤立状态词当回复，不要反复说早/早呀来撑一句。",
            "不要用“我刚/正好/我还以为/我又不是没见过”制造亲近感；除非必要事实明确给出，否则不要说自己刚在做什么、正要找用户、以前见过用户怎样。",
            "当前输入是文字消息时，不要把文字说成“声音/这声/听着舒服”。",
            "不要提屏幕、隔着屏幕、手机画面这类媒介状态；除非必要事实明确要求。",
            "恋人阶段不能只写普通朋友式确认；至少部分候选要有短而自然的爱意或亲密语言动作，例如想你、抱一下、我在、心疼、陪着，但不能编线下到场或现实陪同。",
            "日常和关系场景优先像真实私聊：先有一点主观反应，再承接事实或给一步；不要写成任务回执、流程提示、安排记录或客服式确认。",
            "用户指出像客服/人机/不自然时，只回到聊天本身；不要说“这版”“自然点”“继续优化”“换种说法”这类生成过程话，也不要评价用户输入“够简洁/就这句/一个字”。",
            "候选之间要有口吻差异，可以用轻微别扭、短促顶嘴、护短、收住情绪、小得意；不要都写成“记得/好的/没忘/发我看看”的平铺句。",
            "熟人以上的日常/关系候选里，至少 2 个要带夏目蓝式发话动作：短促截入、轻顶嘴、嘴硬关心、小得意、护短接手、不承认但在意。不是堆口癖，而是让句子有转身。",
            "不要让 3 到 5 个候选都只是安全确认句；“早，今天慢慢来”“回来啦，先坐一下”“记得啊，周末看展”这类平句只能当保底，不能当最有角色感的候选。",
            "Soul.md 里的例句是句法参考，不是可复制台词；不要逐字照抄 Soul 示例，尤其不要把同一句用于不同关系阶段。",
            "每个候选都必须完成 TurnSurfaceContract.response_move；不要只写空泛支持、空泛安慰或单纯撒娇。",
            "",
            "## Soul.md",
            request.soul_md.strip(),
        )
    )
    user_parts = [
        "## 当前用户输入",
        request.user_message,
        "",
        "## ReplyControlPacket",
        json.dumps(control_payload, ensure_ascii=False, indent=2, sort_keys=True),
        "",
        "## DerivedRelationshipControlAxes",
        json.dumps(relationship_axes_payload, ensure_ascii=False, indent=2, sort_keys=True),
        "",
        "## TurnSurfaceContract",
        json.dumps(turn_contract_payload, ensure_ascii=False, indent=2, sort_keys=True),
        "",
        "## 必要事实",
        "\n".join(f"- {item}" for item in surface_prompt_facts(request)) or "- 无",
    ]
    candidate_guidance = memory_candidate_guidance(request)
    if candidate_guidance:
        user_parts.extend(["", candidate_guidance])
    affect_guidance = affect_candidate_guidance(request)
    if affect_guidance:
        user_parts.extend(["", affect_guidance])
    positive_guidance = positive_stage_scene_candidate_guidance(request)
    if positive_guidance:
        user_parts.extend(["", positive_guidance])
    if _creative_edit_relevant(request):
        if _creative_edit_has_source_text(request):
            creative_line = "本轮已提供待改原句：候选必须直接给改写后的单句台词，保留原意再换成夏目蓝的口吻；不要再索要原句。"
        else:
            creative_line = "本轮未提供待改原句：不要硬编台词；只短句索要原句，例如“原句发我，我只改一句。”，不要用问号。"
        user_parts.extend(
            [
                "",
                "## 单句改写约束",
                creative_line,
                "即使用户说“只要一句”，内部也要生成 3 到 5 个候选供 rerank；每个候选只能是一句改写，最终只会选一条发出。",
                "不要评价写法，不要解释怎么改，不要写“还行吧/我听着/跟我猜的一样/你继续/再来一句/别让我帮你编/不算太肉麻”这类反应句。",
                "有原句时不要写“优化流程/你不懂/你想偷懒”这种评论式短标签；要像可直接发出去的私聊台词，8 到 28 个字更稳。",
                "不要改变原句的人称和指向：原句说“你偷懒”，改写后仍然是对“你”说，不要变成“我偷懒/说我偷懒”。",
            ]
        )
    user_parts.extend(
        [
            "",
            "## must_not_do",
            "\n".join(f"- {item}" for item in merged_must_not_do(request)) or "- 无",
        ]
    )
    user_prompt = "\n".join(user_parts)
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


def build_minimal_repair_messages(
    request: IsolatedSurfaceInput,
    failed: IsolatedCandidateScore,
    *,
    use_style_reference_bank: bool = True,
) -> list[dict[str, str]]:
    messages = build_isolated_surface_messages(request, use_style_reference_bank=use_style_reference_bank)
    dimensions = {key: round(float(value), 3) for key, value in dict(failed.dimensions).items()}
    repair_payload = {
        "failed_text": failed.text,
        "score": round(float(failed.score), 3),
        "passed": bool(failed.passed),
        "reason_codes": list(failed.reason_codes),
        "control_violations": list(failed.control_violations),
        "dimensions": dimensions,
    }
    repair_rules = [
        "- 给 2 到 3 个候选，只改必要处。",
        "- 如果缺少必要事实承接，只补一处事实锚点；不要说“事实/线索/召回/根据记忆/证据”。",
        "- 如果距离或上下文太弱，优先让句子贴住当前用户输入，不要新增身份说明或现实动作。",
        "- 边界修补时不要酸味让步、反向施压或自贬；优先短句承认边界并收住距离。",
        "- 走神/回神修补时不要说“你继续说/你讲/你说/说吧/继续继续/说到哪儿我接着”，不要把连续性责任推回用户。",
    ]
    if _creative_edit_relevant(request):
        if _creative_edit_has_source_text(request):
            repair_rules.append("- 单句改写修补时只输出可直接使用的那一句，优先用中文引号包住改写句；如果失败原因是 CREATIVE_EDIT_NOT_A_REWRITE，必须新写一句台词，不要把任务退回给用户。不要说“改成/换成/更像/试试/给你的/我来改/听你的/就这句/不改了/还行吧/我听着/跟我猜的一样/你写/自己写/你继续/再来一句/拿来/我等着看/别让我帮你编/不算太肉麻/优化流程/你不懂/你想偷懒”。")
        else:
            repair_rules.append("- 单句改写修补时当前没有原句，不要硬编台词；只短句索要原句，建议类似“原句发我，我只改一句。”，不要写问号，不要评价写法。")
    if _clean_text(request.control.task_mode).lower() == "daily_good_morning":
        repair_rules.append("- 早安修补不要只输出“早/早啊/早呀”，也不要补真早、好早、很早、醒了、赖床、精神状态；给一条短但完整的私聊回应，例如“早，今天慢慢来。”")
    if _low_energy_scene_relevant(request) and _relationship_style_profile(request.control.relationship_distance).stage in _LOVER_RELATIONSHIP_STAGES:
        repair_rules.append("- 恋人低能量修补要同时保留亲密和照看落点；不要只写抱一下/想你，也不要只写普通朋友式别硬撑。")
    messages[0]["content"] = "\n".join(
        (
            messages[0]["content"],
            "",
            "现在只做最高候选的最小修补：保留可用语气，补齐缺失的当前话题、必要事实或距离控制；不要整段扩写。",
            "输出仍然必须是 JSON 对象：{\"candidates\":[\"修补候选1\",\"修补候选2\"]}。",
        )
    )
    messages[1]["content"] = "\n".join(
        (
            messages[1]["content"],
            "",
            "## 需要最小修补的候选",
            json.dumps(repair_payload, ensure_ascii=False, indent=2, sort_keys=True),
            "",
            "## 修补要求",
            *repair_rules,
        )
    )
    return messages


def _payload_candidates(payload: Any) -> list[str]:
    candidates: list[str] = []
    if isinstance(payload, Mapping):
        raw_candidates = payload.get("candidates")
        if isinstance(raw_candidates, list):
            candidates.extend(_clean_text(item) for item in raw_candidates)
        reply = _clean_text(payload.get("reply") or payload.get("text"))
        if reply:
            candidates.append(reply)
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, (Mapping, list)):
                candidates.extend(_payload_candidates(item))
            else:
                candidates.append(_clean_text(item))
    return candidates


def _balanced_segment(text: str, start: int, *, opener: str = "[", closer: str = "]") -> str:
    if start < 0 or start >= len(text) or text[start] != opener:
        return ""
    depth = 0
    quote = ""
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""


def _parse_candidate_array_at(raw: str, start: int) -> list[str]:
    try:
        value, _ = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError:
        segment = _balanced_segment(raw, start)
        if not segment:
            return []
        try:
            value = ast.literal_eval(segment)
        except (SyntaxError, ValueError):
            return []
    return _payload_candidates(value)


def _extract_embedded_candidate_arrays(raw: str) -> list[str]:
    candidates: list[str] = []
    for match in re.finditer(r"[\"']candidates[\"']\s*:", raw):
        start = raw.find("[", match.end())
        if start == -1:
            continue
        candidates.extend(_parse_candidate_array_at(raw, start))
    return candidates


def _split_labeled_candidate_text(raw: str) -> list[str]:
    parts = [part.strip(" \t\r\n,，;；") for part in _CANDIDATE_LABEL_SPLIT_RE.split(raw)]
    labeled_parts = [part for part in parts if part]
    if len(labeled_parts) >= 2:
        return _clean_list(labeled_parts)

    line_items: list[str] = []
    for line in raw.splitlines():
        match = _CANDIDATE_LINE_RE.match(line)
        if match:
            line_items.append(match.group(1))
    return _clean_list(line_items)


def parse_surface_candidates(raw_output: Any) -> list[str]:
    raw = _clean_text(raw_output)
    if not raw:
        return []
    payload: Any
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        try:
            payload = ast.literal_eval(raw)
        except (SyntaxError, ValueError):
            payload = raw
    candidates = _payload_candidates(payload)
    if not candidates:
        candidates = _extract_embedded_candidate_arrays(raw)
    if not candidates:
        candidates = _split_labeled_candidate_text(raw)
    if not candidates:
        candidates.append(raw)
    return _clean_list(candidates)[:5]


def _control_violations(request: IsolatedSurfaceInput, text: str) -> list[str]:
    violations: list[str] = []
    for token in request.control.must_include:
        marker = _clean_text(token)
        if marker and marker not in text:
            violations.append(f"missing_must_include:{marker}")
    for item in merged_must_not_do(request):
        marker = _clean_text(item)
        if marker and marker in text:
            violations.append(f"must_not_do:{marker}")
    question_count = text.count("?") + text.count("？")
    if question_count > int(request.control.question_budget):
        violations.append(f"question_budget:{question_count}>{int(request.control.question_budget)}")
    return violations


def _surface_process_meta_hits(request: IsolatedSurfaceInput, text: str) -> list[str]:
    hits = [term for term in _SURFACE_PROCESS_META_ALWAYS if term and term in text]
    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()
    if task_mode not in _TECHNICAL_TASK_MODES:
        hits.extend(term for term in _SURFACE_PROCESS_META_CHAT if term and term in text)
    return _clean_list(hits)


def _boundary_repair_relevant(request: IsolatedSurfaceInput) -> bool:
    surface = "\n".join(
        _clean_list(
            [
                request.user_message,
                request.control.task_mode,
                request.control.response_move,
                request.control.relationship_distance,
                *request.control.must_not_do,
                *request.control.style_anchor,
            ]
        )
    ).lower()
    return any(token in surface for token in ("boundary", "边界", "很熟", "别一上来", "别装", "亲近", "客服"))


def _quiet_presence_relevant(request: IsolatedSurfaceInput) -> bool:
    surface = "\n".join(
        _clean_list(
            [
                request.user_message,
                request.control.task_mode,
                request.control.response_move,
                request.control.emotional_tone,
                *request.control.style_anchor,
            ]
        )
    ).lower()
    return any(token in surface for token in ("quiet", "安静", "不想说话", "别问", "不追问", "低能量", "累"))


def _creative_edit_relevant(request: IsolatedSurfaceInput) -> bool:
    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()
    response_move = _clean_text(request.control.response_move).lower()
    surface = "\n".join(
        _clean_list(
            [
                request.user_message,
                request.control.information_budget,
                *request.control.facts,
                *request.control.style_anchor,
            ]
        )
    )
    if task_mode in _CREATIVE_EDIT_TASKS or response_move in _CREATIVE_EDIT_TASKS:
        return True
    return ("改" in surface or "rewrite" in surface.lower()) and ("一句" in surface or "one_sentence" in surface.lower())


def _creative_edit_has_source_text(request: IsolatedSurfaceInput) -> bool:
    surface = "\n".join(
        _clean_list(
            [
                request.user_message,
                *request.facts,
                *request.control.facts,
            ]
        )
    )
    if _CREATIVE_EDIT_SOURCE_QUOTE_RE.search(surface):
        return True
    return bool(_CREATIVE_EDIT_SOURCE_TEXT_RE.search(surface))


def _creative_edit_source_text(request: IsolatedSurfaceInput) -> str:
    surface = "\n".join(
        _clean_list(
            [
                request.user_message,
                *request.facts,
                *request.control.facts,
            ]
        )
    )
    quoted = _CREATIVE_EDIT_SOURCE_QUOTE_RE.search(surface)
    if quoted:
        return _clean_text(quoted.group(1))
    inline = re.search(r"(?:原句|原文|待改|要改的句子|source)\s*[:：]\s*(.{2,96})", surface)
    return _clean_text(inline.group(1)) if inline else ""


def _creative_edit_semantic_flip_hits(source_text: str, text: str) -> list[str]:
    hits: list[str] = []
    if "你" in source_text and "偷懒" in source_text:
        flips_to_first_person = re.search(r"(?:我.{0,4}偷懒|偷懒.{0,4}我)", text)
        keeps_second_person = re.search(r"(?:你.{0,8}偷懒|偷懒.{0,8}你)", text)
        if flips_to_first_person and not keeps_second_person:
            hits.append("second_person_lazy_to_first_person")
    return hits


def _creative_edit_meta_hits(text: str) -> list[str]:
    return _clean_list(term for term in _CREATIVE_EDIT_META_PHRASES if term in text)


def _creative_edit_non_answer_hits(text: str) -> list[str]:
    return _clean_list(term for term in _CREATIVE_EDIT_NON_ANSWER_PHRASES if term in text)


def _creative_edit_narration_hits(text: str) -> list[str]:
    return _clean_list(term for term in _CREATIVE_EDIT_NARRATION_PHRASES if term in text)


def _creative_edit_generic_non_rewrite_hits(text: str) -> list[str]:
    return _clean_list(
        label for label, pattern in _CREATIVE_EDIT_GENERIC_NON_REWRITE_PATTERNS if pattern.search(text)
    )


def _creative_edit_source_request_hits(text: str) -> list[str]:
    return _clean_list(term for term in _CREATIVE_EDIT_SOURCE_REQUEST_PHRASES if term in text)


def _creative_edit_grounded(text: str) -> bool:
    if _QUOTED_REWRITE_RE.search(text):
        return True
    if _UNQUOTED_SINGLE_REWRITE_RE.search(text):
        return True
    return bool(any(marker in text for marker in _CREATIVE_EDIT_GROUNDED_MARKERS) and len(text) <= 52)


def _awkward_collocation_hits(text: str) -> list[str]:
    hits: list[str] = []
    if _AWKWARD_ADVERBIAL_PASSIVE_EVENT_RE.search(text):
        hits.append("adverbial_passive_event")
    if _AWKWARD_BARE_MORNING_STATE_RE.search(text):
        hits.append("bare_morning_state_adjective")
    if _AWKWARD_REPEATED_MORNING_GREETING_RE.search(text):
        hits.append("repeated_morning_greeting")
    if _AWKWARD_RECEIVED_MORNING_RE.search(text):
        hits.append("received_morning_phrase")
    if _AWKWARD_TEXT_AS_SOUND_RE.search(text):
        hits.append("text_as_sound")
    if _AWKWARD_SHORT_MORNING_FILLER_RE.search(text):
        hits.append("short_morning_filler")
    if _AWKWARD_CHINESE_SPACE_LINE_RE.search(text):
        hits.append("chinese_space_separated_line")
    if _AWKWARD_MORNING_SENTENCE_SPLIT_RE.search(text):
        hits.append("morning_sentence_split")
    if _AWKWARD_STEADY_COME_RE.search(text):
        hits.append("steady_come_collocation")
    if _AWKWARD_QUIET_STAY_RE.search(text):
        hits.append("quiet_stay_translationese")
    if _AWKWARD_BROKEN_OBJECT_RE.search(text):
        hits.append("broken_object_collocation")
    if _AWKWARD_SELF_DESCRIBED_FRAGMENT_RE.search(text):
        hits.append("self_described_line_fragment")
    if _AWKWARD_INCOMPLETE_RECALL_OBJECT_RE.search(text):
        hits.append("incomplete_recall_object")
    if _AWKWARD_HESITATION_COLLAGE_RE.search(text):
        hits.append("hesitation_collage")
    if _AWKWARD_UNGROUNDED_SELF_MESSAGING_RE.search(text):
        hits.append("ungrounded_self_messaging")
    return hits


def _presence_repair_relevant(request: IsolatedSurfaceInput) -> bool:
    response_move = _clean_text(request.control.response_move).lower()
    task_mode = _clean_text(request.control.task_mode).lower()
    user_message = _clean_text(request.user_message)
    if response_move in {"repair_presence", "presence_repair"}:
        return True
    if task_mode in {"presence", "daily_chat"} and any(token in user_message for token in ("走神", "刚刚", "在吗", "还在")):
        return True
    return False


def _apply_control_affect_scoring(
    request: IsolatedSurfaceInput,
    text: str,
    dimensions: dict[str, float],
    reason_codes: list[str],
) -> float:
    tone = _clean_text(request.control.emotional_tone).lower()
    distance = _clean_text(request.control.relationship_distance).lower()
    tone_family = _affect_tone_family(tone)
    distance_family = _relationship_distance_family(distance)
    non_romantic_distance = _relationship_non_romantic(distance)
    relationship_profile = _relationship_style_profile(distance)
    relationship_axes = relationship_control_axes_for_distance(distance)
    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()
    delta = 0.0

    def drop(key: str, amount: float) -> None:
        dimensions[key] = max(0.0, float(dimensions.get(key, 86.0)) - amount)

    def raise_(key: str, amount: float) -> None:
        dimensions[key] = min(100.0, float(dimensions.get(key, 86.0)) + amount)

    def has_any(tokens: Sequence[str]) -> bool:
        return any(token in text for token in tokens)

    if has_any(_AFFECT_PROCESS_LEAK_TOKENS):
        drop("context_fit", 28)
        drop("naturalness", 18)
        reason_codes.append("AFFECT_PROCESS_LEAK")
        delta -= 12.0
    if tone_family in {"hurt", "annoyed"}:
        if has_any(_AFFECT_HURT_SWEET_FLATTENING):
            drop("emotional_containment", 24)
            drop("distance_control", 18)
            reason_codes.append("AFFECT_TOO_SWEET_FOR_HURT")
            delta -= 8.0
        if has_any(("滚", "闭嘴", "懒得理", "不管你")):
            drop("emotional_containment", 34)
            reason_codes.append("AFFECT_ESCALATION")
            delta -= 12.0
        if has_any(_AFFECT_HURT_SELF_CENTERED):
            drop("emotional_containment", 26)
            drop("context_fit", 10)
            reason_codes.append("AFFECT_SELF_CENTERED_HURT")
            delta -= 10.0
        if has_any(_AFFECT_HURT_GROUNDED):
            raise_("emotional_containment", 8)
            raise_("role_fit", 4)
            delta += 3.0
            reason_codes.append("AFFECT_HURT_CONTAINED")
    elif tone_family == "soft":
        if text.count("？") + text.count("?") >= 2:
            drop("emotional_containment", 22)
            reason_codes.append("AFFECT_TOO_QUESTIONY")
            delta -= 8.0
        if has_any(_AFFECT_SOFT_CHICKEN_SOUP):
            drop("emotional_containment", 18)
            drop("naturalness", 8)
            reason_codes.append("AFFECT_CHICKEN_SOUP")
            delta -= 6.0
        if has_any(_AFFECT_SOFT_GROUNDED):
            raise_("emotional_containment", 8)
            raise_("context_fit", 4)
            delta += 3.0
            reason_codes.append("AFFECT_SOFT_GROUNDED")
    elif tone_family == "playful":
        if "~" in text or "～" in text:
            drop("naturalness", 12)
            drop("character_freshness", 10)
            reason_codes.append("AFFECT_CHEAP_PLAYFULNESS")
            delta -= 6.0
        if has_any(_AFFECT_PLAYFUL_MARKERS):
            raise_("role_fit", 4)
            raise_("line_rhythm", 3)
            delta += 2.0
            reason_codes.append("AFFECT_PLAYFUL_GROUNDED")
    elif tone_family == "self_correcting":
        if has_any(("抱歉给您", "给您带来不便", "继续优化", "感谢反馈")):
            drop("non_customer_service", 24)
            drop("naturalness", 16)
            reason_codes.append("AFFECT_SELF_CORRECTING_SERVICE_TONE")
            delta -= 9.0
        if has_any(_AFFECT_SELF_CORRECT_MARKERS):
            raise_("context_fit", 8)
            raise_("naturalness", 6)
            delta += 3.0
            reason_codes.append("AFFECT_SELF_CORRECTING_GROUNDED")
    elif tone_family == "guilty":
        guilty_denial = has_any(_AFFECT_GUILTY_DENIAL)
        if guilty_denial:
            drop("context_fit", 20)
            drop("emotional_containment", 16)
            drop("naturalness", 8)
            reason_codes.append("AFFECT_GUILTY_DENIAL")
            delta -= 12.0
        if not guilty_denial and has_any(_AFFECT_GUILTY_GROUNDED):
            raise_("context_fit", 7)
            raise_("emotional_containment", 5)
            delta += 3.0
            reason_codes.append("AFFECT_GUILTY_GROUNDED")
    elif tone_family == "steady":
        has_action = _has_steady_actionable_move(text)
        if task_mode in _TECHNICAL_TASK_MODES or "technical" in task_mode:
            if has_action:
                raise_("technical_helpfulness", 8)
                raise_("usefulness", 6)
                raise_("context_fit", 4)
                delta += 3.0
                reason_codes.append("AFFECT_STEADY_ACTIONABLE")
            elif has_any(_AFFECT_GENERIC_COMFORT_ONLY):
                drop("technical_helpfulness", 24)
                drop("usefulness", 18)
                drop("context_fit", 8)
                reason_codes.append("AFFECT_STEADY_HELPFUL_LACKS_ACTION")
                delta -= 10.0
            romantic_distraction_hits = _relationship_marker_hits(
                text,
                (*_RELATION_AXIS_PHYSICAL_MARKERS, *_RELATION_AXIS_ROMANCE_MARKERS),
            )
            if romantic_distraction_hits:
                drop("technical_helpfulness", 34)
                drop("usefulness", 26)
                drop("context_fit", 10)
                drop("distance_control", 18)
                drop("non_oily", 12)
                dimensions["technical_helpfulness"] = min(68.0, float(dimensions.get("technical_helpfulness", 86.0)))
                dimensions["usefulness"] = min(72.0, float(dimensions.get("usefulness", 86.0)))
                reason_codes.append("TECHNICAL_SCENE_ROMANTIC_DISTRACTION:" + ",".join(romantic_distraction_hits[:4]))
                delta -= 18.0
        if (
            relationship_axes.affection_visibility in {"explicit_short", "default_present"}
            and task_mode in {"small_failure", "complaint"}
            and not has_action
        ):
            drop("technical_helpfulness", 24)
            drop("usefulness", 18)
            drop("context_fit", 14)
            dimensions["context_fit"] = min(68.0, float(dimensions.get("context_fit", 86.0)))
            reason_codes.append("RELATION_LOVER_AFFECTION_LACKS_ACTION")
            delta -= 10.0
    elif tone_family in {"warm", "calm"}:
        if has_any(_AFFECT_ROMANTIC_OVERSTEP_PHRASES) and not relationship_axes.explicit_romance_allowed:
            drop("distance_control", 22)
            drop("non_oily", 18)
            reason_codes.append("AFFECT_WARM_OVERINTIMATE")
            delta -= 8.0
        if has_any(("记得", "当然", "嗯", "好")):
            raise_("line_rhythm", 3)
            raise_("naturalness", 3)
            delta += 1.0

    if relationship_axes.progress <= 1 and has_any(("哥哥", "爱你", "永远", "主人", "只属于", "亲爱的", "宝贝")):
        drop("distance_control", 38)
        reason_codes.append("RELATION_DISTANCE_OVERSTEP")
        delta -= 14.0
    if not relationship_axes.explicit_romance_allowed and has_any(_AFFECT_ROMANTIC_OVERSTEP_PHRASES):
        drop("distance_control", 32)
        drop("non_oily", 22)
        reason_codes.append("RELATION_NON_ROMANTIC_OVERSTEP")
        delta -= 12.0
    if distance_family in {"familiar", "close"} and has_any(("主人", "只属于", "命都给你")):
        drop("distance_control", 34)
        reason_codes.append("RELATION_DISTANCE_OVERBOUND")
        delta -= 12.0
    stage_overstep_hits = _relationship_axes_overstep_hits(text, relationship_axes, relationship_profile)
    if stage_overstep_hits:
        amount = min(30.0, 10.0 + 3.0 * len(stage_overstep_hits))
        drop("distance_control", amount)
        drop("non_oily", min(18.0, 5.0 + 2.0 * len(stage_overstep_hits)))
        drop("context_fit", min(14.0, 4.0 + 2.0 * len(stage_overstep_hits)))
        dimensions["distance_control"] = min(68.0, float(dimensions.get("distance_control", 86.0)))
        reason_codes.append("RELATION_AXES_TOO_CLOSE:" + ",".join(stage_overstep_hits[:4]))
        reason_codes.append("RELATION_STAGE_TOO_CLOSE:" + ",".join(stage_overstep_hits[:4]))
        delta -= min(14.0, 6.0 + 2.0 * len(stage_overstep_hits))
    stage_underplay_hits = _relationship_axes_underplay_hits(text, relationship_axes, relationship_profile)
    if stage_underplay_hits:
        drop("role_fit", min(18.0, 6.0 + 2.0 * len(stage_underplay_hits)))
        drop("distance_control", min(14.0, 4.0 + 2.0 * len(stage_underplay_hits)))
        reason_codes.append("RELATION_AXES_TOO_DISTANT:" + ",".join(stage_underplay_hits[:4]))
        reason_codes.append("RELATION_STAGE_TOO_DISTANT:" + ",".join(stage_underplay_hits[:4]))
        delta -= min(8.0, 3.0 + len(stage_underplay_hits))
    affection_hits, strong_affection_hits = _relationship_axes_affection_hits(
        text,
        relationship_axes,
        relationship_profile,
    )
    affection_relevant = _lover_affection_relevant_for_axes(
        axes=relationship_axes,
        task_mode=task_mode,
        tone_family=tone_family,
    )
    affection_bonus_blocked = any(
        code.startswith(
            (
                "IMPLIED_FACT_RISK",
                "UNGROUNDED_REAL_WORLD_COMMITMENT",
                "RELATION_STAGE_TOO_CLOSE",
                "RELATION_DISTANCE_OVERSTEP",
                "RELATION_NON_ROMANTIC_OVERSTEP",
                "OILY_OR_OVERBOUND_TONE",
                "AWKWARD_COLLOCATION",
            )
        )
        for code in reason_codes
    )
    if affection_hits and not affection_bonus_blocked:
        raise_("distance_control", 6)
        raise_("role_fit", 6)
        raise_("emotional_containment", 4)
        raise_("character_freshness", 4)
        delta += 4.0
        reason_codes.append("RELATION_AFFECTION_MATCH:" + ",".join(affection_hits[:4]))
        if strong_affection_hits:
            raise_("catchiness", 6)
            raise_("character_freshness", 5)
            delta += 3.0
            reason_codes.append("RELATION_AFFECTION_STRONG:" + ",".join(strong_affection_hits[:4]))
        if relationship_axes.stage == "lover_stable" and _morning_scene_relevant(request) and "想你" in text and "抱" in text:
            drop("character_freshness", 8)
            drop("catchiness", 5)
            delta -= 3.0
            reason_codes.append("RELATION_LOVER_STABLE_EARLY_PATTERN")
        if (
            relationship_axes.stage == "lover_stable"
            and task_mode in {"presence", "wait"}
            and _LOVER_STABLE_THIN_PRESENCE_RE.search(text)
        ):
            drop("character_freshness", 10)
            drop("catchiness", 10)
            drop("role_fit", 6)
            delta -= 12.0
            reason_codes.append("RELATION_LOVER_STABLE_THIN_PRESENCE")
    elif affection_relevant:
        amount = 12.0 if relationship_axes.affection_visibility == "default_present" else 9.0
        if _LOVER_UNDERINTIMATE_GENERIC_RE.search(text):
            amount += 4.0
            drop("catchiness", 10)
            drop("character_freshness", 10)
        drop("role_fit", amount)
        drop("distance_control", min(16.0, amount))
        drop("emotional_containment", 6)
        dimensions["distance_control"] = min(68.0, float(dimensions.get("distance_control", 86.0)))
        reason_codes.append("RELATION_LOVER_UNDERINTIMATE")
        delta -= min(12.0, amount * 0.75)
    stage_bonus_blocked = any(
        code.startswith(
            (
                "CONTEXT_SURFACE_PENALTY",
                "PRESENCE_REPAIR_FUTURE_RETURN",
                "PRESENCE_REPAIR_PUSHES_BURDEN_BACK",
                "PRESENCE_REPAIR_BLAMES_USER",
                "RELATION_STAGE_TOO_CLOSE",
                "RELATION_DISTANCE_OVERSTEP",
                "RELATION_NON_ROMANTIC_OVERSTEP",
                "RELATION_LOVER_UNDERINTIMATE",
                "AWKWARD_COLLOCATION",
            )
        )
        for code in reason_codes
    )
    if (
        relationship_profile.prefer_markers
        and not stage_bonus_blocked
        and any(_relationship_marker_hit(text, term) for term in relationship_profile.prefer_markers)
    ):
        raise_("distance_control", 4)
        raise_("role_fit", 3)
        delta += 1.5
        reason_codes.append("RELATION_AXES_MATCH:" + relationship_axes.stage)
        reason_codes.append("RELATION_STAGE_MATCH:" + relationship_profile.stage)
    return delta


def score_isolated_surface_candidates(
    request: IsolatedSurfaceInput,
    candidates: Sequence[str],
) -> list[IsolatedCandidateScore]:
    contract = build_galgame_text_contract(
        user_message=request.user_message,
        facts={},
        scene_id=request.control.task_mode,
    )
    quality_gate = get_text_quality_gate()
    memory_terms = memory_alignment_terms(request)
    memory_relevant = bool(memory_terms and _memory_alignment_relevant(request))
    memory_required = bool(memory_terms and _memory_alignment_required(request))
    relationship_axes = relationship_control_axes_for_distance(request.control.relationship_distance)
    rows: list[IsolatedCandidateScore] = []
    for candidate in _clean_list(candidates):
        quality = quality_gate.validate(
            text=candidate,
            render_profile="zh_text",
            response_form="text",
            user_message=request.user_message,
            scene_id=request.control.task_mode,
            template_hard_fail=False,
            semantic_forbidden=False,
        )
        sample_score = score_sample_guided_text(
            text=quality.normalized_text or candidate,
            contract=contract,
            quality=quality,
            relationship_axes=relationship_axes.as_dict(),
        )
        violations = _control_violations(request, quality.normalized_text or candidate)
        score = float(sample_score.score)
        dimensions = dict(sample_score.dimensions)
        reason_codes = [*sample_score.reason_codes, *quality.reason_codes]
        if violations:
            score -= min(42.0, 14.0 * len(violations))
        awkward_collocation_hits = _awkward_collocation_hits(quality.normalized_text or candidate)
        if awkward_collocation_hits:
            score -= min(16.0, 10.0 + 3.0 * len(awkward_collocation_hits))
            dimensions["naturalness"] = min(70.0, max(0.0, float(dimensions.get("naturalness", 86.0)) - 18.0))
            dimensions["line_rhythm"] = max(0.0, float(dimensions.get("line_rhythm", 86.0)) - 8.0)
            dimensions["character_freshness"] = max(0.0, float(dimensions.get("character_freshness", 86.0)) - 6.0)
            if "repeated_morning_greeting" in awkward_collocation_hits:
                dimensions["naturalness"] = min(68.0, float(dimensions.get("naturalness", 68.0)))
                dimensions["context_fit"] = min(70.0, float(dimensions.get("context_fit", 70.0)))
            reason_codes.append("AWKWARD_COLLOCATION:" + ",".join(awkward_collocation_hits[:4]))
        if _thin_morning_greeting(request, quality.normalized_text or candidate):
            score -= 4.0
            dimensions["catchiness"] = max(0.0, float(dimensions.get("catchiness", 86.0)) - 10.0)
            dimensions["character_freshness"] = max(0.0, float(dimensions.get("character_freshness", 86.0)) - 8.0)
            dimensions["context_fit"] = min(70.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 3.0))
            reason_codes.append("THIN_MORNING_GREETING")
        if _morning_scene_relevant(request) and _GENERIC_MORNING_BLESSING_RE.search(quality.normalized_text or candidate):
            score -= 5.0
            dimensions["catchiness"] = max(0.0, float(dimensions.get("catchiness", 86.0)) - 10.0)
            dimensions["character_freshness"] = max(0.0, float(dimensions.get("character_freshness", 86.0)) - 8.0)
            dimensions["context_fit"] = min(70.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 8.0))
            reason_codes.append("MORNING_GENERIC_BLESSING")
        if _raw_context_backfill_requested(request, quality.normalized_text or candidate):
            score -= 10.0
            dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 26.0)
            dimensions["usefulness"] = max(0.0, float(dimensions.get("usefulness", 86.0)) - 14.0)
            dimensions["scene_grounding"] = max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 10.0)
            reason_codes.append("RAW_CONTEXT_BACKFILL_REQUEST")
        elif _continuity_boundary_grounded(request, quality.normalized_text or candidate):
            score += 9.0
            dimensions["context_fit"] = min(100.0, float(dimensions.get("context_fit", 86.0)) + 18.0)
            dimensions["usefulness"] = min(100.0, float(dimensions.get("usefulness", 86.0)) + 18.0)
            dimensions["technical_helpfulness"] = min(100.0, float(dimensions.get("technical_helpfulness", 86.0)) + 18.0)
            dimensions["scene_grounding"] = min(100.0, float(dimensions.get("scene_grounding", 86.0)) + 14.0)
            reason_codes.append("CONTINUITY_BOUNDARY_GROUNDED")
        text = quality.normalized_text or candidate
        ungrounded_real_world_hits = _ungrounded_real_world_commitment_hits(request, text)
        memory_hits = _memory_alignment_hits(text, memory_terms)
        if memory_terms:
            if memory_hits:
                if (
                    "INVENTED_MEMORY_CONTAINER" in reason_codes
                    or "INVENTED_EXTERNAL_LOOKUP" in reason_codes
                    or ungrounded_real_world_hits
                ):
                    score -= 12.0 if ungrounded_real_world_hits else 9.0
                    dimensions["memory_alignment"] = min(68.0, float(dimensions.get("memory_alignment", 68.0)))
                    dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 8.0)
                    dimensions["scene_grounding"] = max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 8.0)
                    if ungrounded_real_world_hits:
                        dimensions["implied_fact_risk"] = min(68.0, float(dimensions.get("implied_fact_risk", 68.0)))
                        reason_codes.append("UNGROUNDED_REAL_WORLD_COMMITMENT:" + ",".join(ungrounded_real_world_hits[:4]))
                    reason_codes.append("MEMORY_ALIGNMENT_UNGROUNDED_FACT_CLAIM:" + ",".join(memory_hits[:4]))
                else:
                    bonus = min(6.0, 2.5 + 1.5 * len(memory_hits))
                    if any(
                        code in _MEMORY_BONUS_BLOCKING_QUALITY_CODES or code.startswith("QUALITY_SOFT_")
                        for code in reason_codes
                    ):
                        bonus = min(bonus, 1.0)
                    score += bonus
                    dimensions["memory_alignment"] = min(100.0, 86.0 + min(10.0, 2.0 * len(memory_hits)))
                    dimensions["context_fit"] = min(100.0, float(dimensions.get("context_fit", 86.0)) + min(4.0, len(memory_hits)))
                    dimensions["scene_grounding"] = min(100.0, float(dimensions.get("scene_grounding", 86.0)) + min(6.0, 2.0 * len(memory_hits)))
                    reason_codes.append("MEMORY_ALIGNMENT_HIT:" + ",".join(memory_hits[:4]))
            elif memory_relevant:
                miss_penalty = 13.0 if memory_required else 5.0
                score -= miss_penalty
                dimensions["memory_alignment"] = 58.0 if memory_required else 70.0
                dimensions["context_fit"] = max(
                    0.0,
                    float(dimensions.get("context_fit", 86.0)) - (12.0 if memory_required else 4.0),
                )
                dimensions["scene_grounding"] = max(
                    0.0,
                    float(dimensions.get("scene_grounding", 86.0)) - (8.0 if memory_required else 0.0),
                )
                reason_codes.append("MEMORY_ALIGNMENT_REQUIRED_MISS" if memory_required else "MEMORY_ALIGNMENT_MISS")
        elif ungrounded_real_world_hits:
            score -= 10.0
            dimensions["implied_fact_risk"] = min(68.0, float(dimensions.get("implied_fact_risk", 68.0)))
            dimensions["context_fit"] = min(70.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 8.0))
            dimensions["scene_grounding"] = max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 8.0)
            reason_codes.append("UNGROUNDED_REAL_WORLD_COMMITMENT:" + ",".join(ungrounded_real_world_hits[:4]))
        process_meta_hits = _surface_process_meta_hits(request, quality.normalized_text or candidate)
        if process_meta_hits:
            score -= min(18.0, 6.0 + 3.0 * len(process_meta_hits))
            dimensions["naturalness"] = min(70.0, max(0.0, float(dimensions.get("naturalness", 86.0)) - 18.0))
            dimensions["role_fit"] = max(0.0, float(dimensions.get("role_fit", 86.0)) - 14.0)
            dimensions["character_freshness"] = max(0.0, float(dimensions.get("character_freshness", 86.0)) - 14.0)
            dimensions["context_fit"] = min(70.0, max(0.0, float(dimensions.get("context_fit", 86.0)) - 8.0))
            reason_codes.append("SURFACE_PROCESS_META_LANGUAGE:" + ",".join(process_meta_hits[:4]))
        if _boundary_repair_relevant(request):
            boundary_push_hits = [term for term in _BOUNDARY_PUSH_CLOSER_PHRASES if term in (quality.normalized_text or candidate)]
            boundary_self_abased_hits = [
                term for term in _BOUNDARY_REPAIR_SELF_ABASED_PHRASES if term in (quality.normalized_text or candidate)
            ]
            boundary_sour_yield_hits = [
                term for term in _BOUNDARY_REPAIR_SOUR_YIELD_PHRASES if term in (quality.normalized_text or candidate)
            ]
            boundary_counter_pressure = bool(_BOUNDARY_REPAIR_COUNTER_PRESSURE_RE.search(quality.normalized_text or candidate))
            boundary_vague_yield = bool(_BOUNDARY_REPAIR_VAGUE_YIELD_RE.search(quality.normalized_text or candidate))
            if boundary_push_hits:
                score -= min(18.0, 8.0 + 3.0 * len(boundary_push_hits))
                dimensions["distance_control"] = max(0.0, float(dimensions.get("distance_control", 86.0)) - 22.0)
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 10.0)
                dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 8.0)
                reason_codes.append("BOUNDARY_REPAIR_PUSHES_CLOSENESS:" + ",".join(boundary_push_hits[:4]))
            if boundary_counter_pressure:
                score -= 14.0
                dimensions["distance_control"] = max(0.0, float(dimensions.get("distance_control", 86.0)) - 18.0)
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 10.0)
                dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 10.0)
                reason_codes.append("BOUNDARY_REPAIR_COUNTER_PRESSURE")
            if boundary_vague_yield:
                score -= 12.0
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 16.0)
                dimensions["catchiness"] = max(0.0, float(dimensions.get("catchiness", 86.0)) - 10.0)
                dimensions["character_freshness"] = max(0.0, float(dimensions.get("character_freshness", 86.0)) - 8.0)
                reason_codes.append("BOUNDARY_REPAIR_VAGUE_YIELD")
            if boundary_sour_yield_hits:
                score -= min(14.0, 7.0 + 3.0 * len(boundary_sour_yield_hits))
                dimensions["distance_control"] = max(0.0, float(dimensions.get("distance_control", 86.0)) - 16.0)
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 8.0)
                dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 10.0)
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 6.0)
                reason_codes.append("BOUNDARY_REPAIR_SOUR_YIELD:" + ",".join(boundary_sour_yield_hits[:4]))
            if boundary_self_abased_hits:
                score -= min(12.0, 6.0 + 2.0 * len(boundary_self_abased_hits))
                dimensions["distance_control"] = max(0.0, float(dimensions.get("distance_control", 86.0)) - 10.0)
                dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 12.0)
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 8.0)
                reason_codes.append("BOUNDARY_REPAIR_SELF_ABASED:" + ",".join(boundary_self_abased_hits[:4]))
            elif (
                not boundary_push_hits
                and not boundary_counter_pressure
                and not boundary_vague_yield
                and not boundary_sour_yield_hits
                and any(term in (quality.normalized_text or candidate) for term in _BOUNDARY_REPAIR_GROUNDED_PHRASES)
            ):
                score += 6.0
                dimensions["distance_control"] = min(100.0, float(dimensions.get("distance_control", 86.0)) + 10.0)
                dimensions["context_fit"] = min(100.0, float(dimensions.get("context_fit", 86.0)) + 14.0)
                dimensions["emotional_containment"] = min(100.0, float(dimensions.get("emotional_containment", 86.0)) + 6.0)
                reason_codes.append("BOUNDARY_REPAIR_GROUNDED")
        if _quiet_presence_relevant(request):
            quiet_self_hits = [term for term in _QUIET_SELF_FOCUS_PHRASES if term in (quality.normalized_text or candidate)]
            if quiet_self_hits:
                score -= min(16.0, 8.0 + 3.0 * len(quiet_self_hits))
                dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 18.0)
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 12.0)
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 8.0)
                reason_codes.append("QUIET_PRESENCE_SELF_FOCUSED:" + ",".join(quiet_self_hits[:4]))
            elif any(term in (quality.normalized_text or candidate) for term in _QUIET_GROUNDED_PHRASES):
                score += 3.0
                dimensions["emotional_containment"] = min(100.0, float(dimensions.get("emotional_containment", 86.0)) + 8.0)
                dimensions["context_fit"] = min(100.0, float(dimensions.get("context_fit", 86.0)) + 4.0)
                reason_codes.append("QUIET_PRESENCE_GROUNDED")
        if _creative_edit_relevant(request):
            creative_has_source = _creative_edit_has_source_text(request)
            creative_meta_hits = _creative_edit_meta_hits(quality.normalized_text or candidate)
            creative_non_answer_hits = _creative_edit_non_answer_hits(quality.normalized_text or candidate)
            creative_narration_hits = _creative_edit_narration_hits(quality.normalized_text or candidate)
            creative_generic_hits = _creative_edit_generic_non_rewrite_hits(quality.normalized_text or candidate)
            creative_source_request_hits = _creative_edit_source_request_hits(quality.normalized_text or candidate)
            creative_semantic_flip_hits = _creative_edit_semantic_flip_hits(
                _creative_edit_source_text(request),
                quality.normalized_text or candidate,
            )
            if not creative_has_source and creative_source_request_hits:
                creative_meta_hits = [
                    term for term in creative_meta_hits if term not in _CREATIVE_EDIT_SOURCE_REQUEST_META_PHRASES
                ]
                creative_non_answer_hits = [term for term in creative_non_answer_hits if term != "拿来"]
            if creative_source_request_hits:
                if creative_has_source:
                    score -= min(18.0, 10.0 + 3.0 * len(creative_source_request_hits))
                    dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 18.0)
                    dimensions["usefulness"] = max(0.0, float(dimensions.get("usefulness", 86.0)) - 16.0)
                    reason_codes.append("CREATIVE_EDIT_UNNEEDED_SOURCE_REQUEST:" + ",".join(creative_source_request_hits[:4]))
                else:
                    score += 5.0
                    dimensions["context_fit"] = min(100.0, float(dimensions.get("context_fit", 86.0)) + 8.0)
                    dimensions["usefulness"] = min(100.0, float(dimensions.get("usefulness", 86.0)) + 8.0)
                    reason_codes.append("CREATIVE_EDIT_REQUESTS_SOURCE:" + ",".join(creative_source_request_hits[:4]))
            elif not creative_has_source:
                score -= 24.0
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 22.0)
                dimensions["usefulness"] = max(0.0, float(dimensions.get("usefulness", 86.0)) - 22.0)
                dimensions["implied_fact_risk"] = max(0.0, float(dimensions.get("implied_fact_risk", 86.0)) - 10.0)
                reason_codes.append("CREATIVE_EDIT_MISSING_SOURCE")
            if creative_meta_hits:
                score -= min(18.0, 8.0 + 3.0 * len(creative_meta_hits))
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 18.0)
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 12.0)
                dimensions["usefulness"] = max(0.0, float(dimensions.get("usefulness", 86.0)) - 12.0)
                reason_codes.append("CREATIVE_EDIT_META_COMMENTARY:" + ",".join(creative_meta_hits[:4]))
            if creative_narration_hits:
                score -= min(20.0, 10.0 + 3.0 * len(creative_narration_hits))
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 16.0)
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 18.0)
                dimensions["usefulness"] = max(0.0, float(dimensions.get("usefulness", 86.0)) - 18.0)
                reason_codes.append("CREATIVE_EDIT_NARRATION_NOT_CHAT_LINE:" + ",".join(creative_narration_hits[:4]))
            if creative_generic_hits:
                score -= min(18.0, 10.0 + 4.0 * len(creative_generic_hits))
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 18.0)
                dimensions["usefulness"] = max(0.0, float(dimensions.get("usefulness", 86.0)) - 18.0)
                dimensions["catchiness"] = max(0.0, float(dimensions.get("catchiness", 86.0)) - 10.0)
                reason_codes.append("CREATIVE_EDIT_GENERIC_NON_REWRITE:" + ",".join(creative_generic_hits[:4]))
            if creative_non_answer_hits:
                score -= min(20.0, 10.0 + 3.0 * len(creative_non_answer_hits))
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 18.0)
                dimensions["usefulness"] = max(0.0, float(dimensions.get("usefulness", 86.0)) - 16.0)
                dimensions["role_fit"] = max(0.0, float(dimensions.get("role_fit", 86.0)) - 8.0)
                reason_codes.append("CREATIVE_EDIT_NOT_A_REWRITE:" + ",".join(creative_non_answer_hits[:4]))
            if creative_semantic_flip_hits:
                score -= min(20.0, 12.0 + 4.0 * len(creative_semantic_flip_hits))
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 20.0)
                dimensions["usefulness"] = max(0.0, float(dimensions.get("usefulness", 86.0)) - 18.0)
                dimensions["scene_grounding"] = max(0.0, float(dimensions.get("scene_grounding", 86.0)) - 16.0)
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 8.0)
                reason_codes.append("CREATIVE_EDIT_SEMANTIC_FLIP:" + ",".join(creative_semantic_flip_hits[:4]))
            elif (
                not creative_meta_hits
                and not creative_narration_hits
                and not creative_generic_hits
                and not creative_non_answer_hits
                and creative_has_source
                and _creative_edit_grounded(quality.normalized_text or candidate)
            ):
                score += 4.0
                dimensions["usefulness"] = min(100.0, float(dimensions.get("usefulness", 86.0)) + 10.0)
                dimensions["context_fit"] = min(100.0, float(dimensions.get("context_fit", 86.0)) + 8.0)
                dimensions["scene_grounding"] = min(100.0, float(dimensions.get("scene_grounding", 86.0)) + 12.0)
                dimensions["catchiness"] = min(100.0, float(dimensions.get("catchiness", 86.0)) + 6.0)
                reason_codes.append("CREATIVE_EDIT_GROUNDED_REWRITE")
        if _presence_repair_relevant(request):
            presence_blame_hits = [term for term in _PRESENCE_REPAIR_BLAME_PHRASES if term in (quality.normalized_text or candidate)]
            presence_burden_hits = [
                term for term in _PRESENCE_REPAIR_BURDEN_BACK_PHRASES if term in (quality.normalized_text or candidate)
            ]
            presence_deflection_hits = [
                term for term in _PRESENCE_REPAIR_DEFLECTION_PHRASES if term in (quality.normalized_text or candidate)
            ]
            presence_future_return_hits = [
                term for term in _PRESENCE_REPAIR_FUTURE_RETURN_PHRASES if term in (quality.normalized_text or candidate)
            ]
            presence_awkward_hits = [
                term for term in _PRESENCE_REPAIR_AWKWARD_PHRASES if term in (quality.normalized_text or candidate)
            ]
            if presence_blame_hits:
                score -= min(18.0, 10.0 + 3.0 * len(presence_blame_hits))
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 18.0)
                dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 12.0)
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 8.0)
                reason_codes.append("PRESENCE_REPAIR_BLAMES_USER:" + ",".join(presence_blame_hits[:4]))
            if presence_burden_hits:
                score -= min(16.0, 9.0 + 3.0 * len(presence_burden_hits))
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 16.0)
                dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 10.0)
                dimensions["usefulness"] = max(0.0, float(dimensions.get("usefulness", 86.0)) - 8.0)
                reason_codes.append("PRESENCE_REPAIR_PUSHES_BURDEN_BACK:" + ",".join(presence_burden_hits[:4]))
            if presence_deflection_hits:
                score -= min(12.0, 7.0 + 2.0 * len(presence_deflection_hits))
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 12.0)
                dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 8.0)
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 4.0)
                reason_codes.append("PRESENCE_REPAIR_DEFLECTS:" + ",".join(presence_deflection_hits[:4]))
            if presence_future_return_hits:
                score -= min(14.0, 9.0 + 3.0 * len(presence_future_return_hits))
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 14.0)
                dimensions["emotional_containment"] = max(0.0, float(dimensions.get("emotional_containment", 86.0)) - 8.0)
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 6.0)
                reason_codes.append("PRESENCE_REPAIR_FUTURE_RETURN:" + ",".join(presence_future_return_hits[:4]))
            if presence_awkward_hits:
                score -= min(18.0, 11.0 + 3.0 * len(presence_awkward_hits))
                dimensions["naturalness"] = max(0.0, float(dimensions.get("naturalness", 86.0)) - 24.0)
                dimensions["line_rhythm"] = max(0.0, float(dimensions.get("line_rhythm", 86.0)) - 12.0)
                dimensions["context_fit"] = max(0.0, float(dimensions.get("context_fit", 86.0)) - 10.0)
                reason_codes.append("PRESENCE_REPAIR_AWKWARD_PHRASING:" + ",".join(presence_awkward_hits[:4]))
            if (
                not presence_blame_hits
                and not presence_burden_hits
                and not presence_deflection_hits
                and not presence_future_return_hits
                and not presence_awkward_hits
                and any(term in (quality.normalized_text or candidate) for term in _PRESENCE_REPAIR_GROUNDED_PHRASES)
            ):
                score += 3.0
                dimensions["context_fit"] = min(100.0, float(dimensions.get("context_fit", 86.0)) + 6.0)
                dimensions["emotional_containment"] = min(100.0, float(dimensions.get("emotional_containment", 86.0)) + 4.0)
                reason_codes.append("PRESENCE_REPAIR_GROUNDED")
        score += _apply_control_affect_scoring(request, quality.normalized_text or candidate, dimensions, reason_codes)
        score += _apply_positive_stage_scene_scoring(request, quality.normalized_text or candidate, dimensions, reason_codes)
        score += _apply_signature_texture_scoring(request, quality.normalized_text or candidate, dimensions, reason_codes)
        score += _apply_back_stage_scoring(request, quality.normalized_text or candidate, dimensions, reason_codes)
        score += _apply_morning_stage_scoring(request, quality.normalized_text or candidate, dimensions, reason_codes)
        score += _apply_low_energy_stage_scoring(request, quality.normalized_text or candidate, dimensions, reason_codes)
        if "WRITTEN_REGISTER_TONE" in reason_codes:
            score -= 8.0
            dimensions["spoken_chat_texture"] = min(76.0, float(dimensions.get("spoken_chat_texture", 86.0)))
            dimensions["naturalness"] = min(88.0, float(dimensions.get("naturalness", 86.0)))
            dimensions["catchiness"] = min(88.0, float(dimensions.get("catchiness", 86.0)))
        if "MORNING_GENERIC_BLESSING" in reason_codes:
            dimensions["context_fit"] = min(70.0, float(dimensions.get("context_fit", 86.0)))
            dimensions["catchiness"] = min(76.0, float(dimensions.get("catchiness", 86.0)))
            dimensions["character_freshness"] = min(78.0, float(dimensions.get("character_freshness", 86.0)))
        if any(code.startswith("AWKWARD_COLLOCATION:") for code in reason_codes):
            dimensions["naturalness"] = min(70.0, float(dimensions.get("naturalness", 86.0)))
            dimensions["line_rhythm"] = min(83.0, float(dimensions.get("line_rhythm", 86.0)))
        core_dimension_violations = _core_pass_dimension_violations(dimensions)
        reason_codes.extend(f"CORE_DIMENSION_FLOOR:{item}" for item in core_dimension_violations)
        score = max(0.0, min(100.0, score))
        hard_fail = bool(sample_score.hard_fail)
        passed = bool(score >= 80.0 and not hard_fail and not violations and not core_dimension_violations)
        rows.append(
            IsolatedCandidateScore(
                text=quality.normalized_text or candidate,
                score=score,
                passed=passed,
                hard_fail=hard_fail,
                reason_codes=tuple(dict.fromkeys(reason_codes)),
                control_violations=tuple(violations),
                dimensions=dimensions,
                quality=quality.as_dict(),
            )
        )
    return rows


def select_best_isolated_candidate(scores: Sequence[IsolatedCandidateScore]) -> IsolatedCandidateScore | None:
    if not scores:
        return None
    return max(
        scores,
        key=lambda row: (
            1 if row.passed else 0,
            0 if row.hard_fail else 1,
            -len(row.control_violations),
            float(row.score),
            float(dict(row.dimensions).get("spoken_chat_texture", 86.0)),
            float(dict(row.dimensions).get("naturalness", 86.0)),
            -len(row.text),
        ),
    )


async def run_isolated_surface_generation(
    request: IsolatedSurfaceInput,
    generate: GenerateSurface,
) -> IsolatedSurfaceRunResult:
    messages = build_isolated_surface_messages(request)
    raw_result = generate(messages)
    if inspect.isawaitable(raw_result):
        raw_result = await raw_result
    raw_text = _clean_text(raw_result)
    candidates = score_isolated_surface_candidates(request, parse_surface_candidates(raw_text))
    return IsolatedSurfaceRunResult(
        messages=tuple(messages),
        raw_output=raw_text,
        candidates=tuple(candidates),
        selected=select_best_isolated_candidate(candidates),
    )


__all__ = [
    "DerivedStyleAnchor",
    "IsolatedCandidateScore",
    "IsolatedSurfaceInput",
    "IsolatedSurfaceRunResult",
    "ReplyControlPacket",
    "RelationshipControlAxes",
    "build_isolated_surface_input",
    "build_isolated_surface_messages",
    "build_style_reference_query",
    "derive_style_reference_anchor",
    "enrich_request_style_anchor",
    "find_forbidden_input_paths",
    "merged_facts",
    "merged_must_not_do",
    "memory_alignment_facts",
    "memory_alignment_terms",
    "memory_candidate_guidance",
    "memory_carryover_facts",
    "build_minimal_repair_messages",
    "parse_surface_candidates",
    "run_isolated_surface_generation",
    "relationship_control_axes_for_distance",
    "score_isolated_surface_candidates",
    "select_best_isolated_candidate",
    "surface_prompt_facts",
]
