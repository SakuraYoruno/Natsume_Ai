from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping, Sequence

from host_brain.persona.text_quality_gate import TextQualityGateResult


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_PUNCT_SPACE_RE = re.compile(r"[\s，。！？!?,.、~～…“”\"'：:；;（）()\[\]{}]+")
_BOUNDARY_TETHER_RE = re.compile(
    r"(发来|拿来|东西|不当外人|别把.{0,4}当外人|不会不管|别.{0,8}往外推|"
    r"当外人|把我当外人|往外推|往外赶|推开|推远|赶走|想赶我走|不想我帮|"
    r"还是.{0,8}(管|帮|看)|我(?:还是|照样|也会|会|来).{0,8}(管|帮|看)|我.{0,4}会.{0,4}(管|帮|看)|"
    r"已经.{0,6}(管|帮|看)|开始.{0,6}(管|帮|看)|偏要管|偏管|就是要管|我要管|我想帮|就是想帮|"
    r"我不管你.{0,12}(难道|还要)|"
    r"(?:关系|你).{0,8}撇|别.{0,8}撇|"
    r"(?:扔下|丢下).{0,6}不管|真.{0,6}不管|不会真.{0,8}不管|还能真.{0,8}不管)"
)
_BOUNDARY_ACTION_RE = re.compile(r"(管|帮|看|弄|插手|不当外人|不会.{0,6}不管|还能真.{0,8}不管)")
_REJECTION_TETHER_RE = re.compile(
    r"(发来|拿来|东西|还是.{0,8}(管|帮|看)|我(?:还是|照样|也会|会|来).{0,8}(管|帮|看)|"
    r"偏要(?:[。！!？?…]|$)|偏要.{0,8}(看|管)|偏不跑|不跑|不放心|不管才怪|要是不管|还能谁管|谁管|"
    r"看你有没有|看着你|看着呢|盯着|有没有搞砸|别.{0,8}一个人|在意)"
)
_BACK_CONTINUITY_RE = re.compile(
    r"(等|待|位置|占位|刚好|留|挺快|还挺快|这么快|准时|及时|说话算话|"
    r"还知道回来|算你.{0,4}回来|不算太慢|回来得|回得|回得倒|回得还|"
    r"线.{0,4}(接|没断)|接回|刚才|刚那句|继续|说下去)"
)
_ZH_GALGAME_REACTION_MARKERS: tuple[str, ...] = (
    "哈？",
    "哈?",
    "哼",
    "诶",
    "欸",
    "喂",
    "行啦",
    "真是的",
    "不是吧",
    "才不是",
    "才没",
)
_ZH_SIBLING_PULL_MARKERS: tuple[str, ...] = (
    "尼尼",
    "见外",
    "外人",
    "不当外人",
    "发来",
    "丢来",
    "贴过来",
    "我来",
    "别硬撑",
    "不放心",
    "偏要",
    "偏管",
    "算你",
)
_REALISM_FLATTENING_PATTERNS: tuple[str, ...] = (
    "我理解你的感受",
    "我理解你",
    "尊重你的选择",
    "如果你愿意",
    "需要的话",
    "有需要就",
    "随时找我",
    "我会在这里",
    "我一直在这里",
    "很高兴帮",
    "很高兴能帮",
    "可以陪你聊天",
    "想聊什么",
    "你想聊什么",
)
_LIGHT_DAILY_SCENES: frozenset[str] = frozenset(
    {
        "back",
        "bored",
        "busy",
        "complaint",
        "followup",
        "good_news",
        "good_night",
        "greeting",
        "happy",
        "hungry",
        "meal_share",
        "nothing",
        "play_invite",
        "presence",
        "quiet",
        "short_ack",
        "sleepy",
        "small_failure",
        "talkative",
        "tired",
        "wait",
    }
)
_LIGHT_DAILY_FABRICATION_PATTERNS: tuple[str, ...] = (
    "平时",
    "平常",
    "每次",
    "总是",
    "总算",
    "终于想起",
    "才想起",
    "主动找",
    "主动发",
    "你又",
    "又弄",
    "没诚意",
    "心里有鬼",
    "闯祸",
    "不像你",
    "不像本人",
    "手机被",
    "发错人",
    "从哪学",
    "哪学来的",
    "附身",
    "被谁附身",
    "商务邮件",
    "国际路线",
    "明明刚才",
    "刚才明明",
    "想跟我说话",
    "开始烦你",
    "亲爱的",
    "好好休息",
    "我都快忘了",
    "搞点事情",
    "本天才",
    "大发慈悲",
    "点名要我登场",
    "随叫随到",
    "自己玩会儿游戏",
    "坐直",
    "喊得",
    "戴耳机",
    "一个人无聊",
    "无聊还来找我",
    "还来找我",
    "让人开心一下",
    "板着脸",
    "骗谁",
    "转性",
    "听见",
    "听到",
    "发呆",
    "刚看到",
    "刚好看到",
    "你这一声",
    "一叫我",
    "刚好接到",
    "数到最后",
    "最后一秒",
    "刚把游戏",
    "游戏也打开",
    "把游戏打开",
    "刷到这条消息",
    "刚好又刷到",
    "刚好刷到",
    "去忙的事",
    "找点别的事做",
    "睡到中午",
    "等到明天",
    "等你到明天",
    "收着等",
    "我收着",
    "接住",
    "刚好接住",
    "线接",
    "线接回来了",
    "刚才那句继续",
    "我等一下",
    "窗口",
    "窗口开着",
    "挂着",
    "先撤",
    "再不来",
    "我就先撤",
    "记着哦",
    "加倍还",
    "加点麻烦",
    "添乱",
    "找麻烦",
    "奶茶",
    "客户",
    "对客户",
    "我一直在",
    "我一直都在",
    "忙完啦",
    "忙完了",
    "忙完要是",
    "记得叫我",
    "叫我一下",
    "打开我看看",
    "倒是打开我看看",
    "一眼就看出来",
    "先缓缓再说",
    "你尼尼",
    "接招",
    "准备好接招",
    "我可盯着",
    "盯着呢",
    "岂不是更无聊",
    "好歹还能找我聊天",
    "急死你",
    "不无聊的东西",
    "玩点好玩的",
    "连这个都看不懂",
    "硬撑到挂掉",
    "挂掉",
    "死磕",
    "改到吐",
    "没人追你",
    "要怎么玩",
    "保证让你",
    "保管比",
    "添点热闹",
    "有事才想到我",
    "才想到我",
    "懒得理我",
    "不可信",
    "可疑",
    "开把游戏",
    "打游戏",
    "游戏声音",
    "打瞌睡",
    "那局",
    "算我赢",
    "布丁",
    "挂着语音",
    "语音眯",
    "我可没说要等你",
    "随时能戳我",
    "随时都在",
    "随时在",
)


def _clean_text(raw: Any) -> str:
    return str(raw or "").strip()


def _safe_mapping(raw: Any) -> dict[str, Any]:
    return dict(raw or {}) if isinstance(raw, Mapping) else {}


def _contains_any(text: str, needles: Sequence[str]) -> bool:
    return any(item and item in text for item in needles)


def _has_question(text: str) -> bool:
    return "?" in text or "？" in text or "吗" in text


def _lower(text: str) -> str:
    return str(text or "").strip().lower()


def _plain_text(text: str) -> str:
    return _PUNCT_SPACE_RE.sub("", str(text or "")).strip().lower()


def _zh_galgame_line_delta(text: str, *, scene: str) -> tuple[float, tuple[str, ...], bool]:
    """Reward line energy without turning style into fixed templates."""

    reasons: list[str] = []
    score = 0.0
    hard_fail = False
    if _contains_any(text, _REALISM_FLATTENING_PATTERNS):
        score -= 32.0
        hard_fail = scene in {
            "anger",
            "apology",
            "bad_mood",
            "boundary",
            "disappointment",
            "fear",
            "loneliness",
            "rejection",
            "worry",
        }
        reasons.append("REALISM_FLATTENING_BOT_TONE")

    reactive = _contains_any(text, _ZH_GALGAME_REACTION_MARKERS)
    sibling_pull = _contains_any(text, _ZH_SIBLING_PULL_MARKERS)
    line_len = len(text)
    if reactive:
        score += 4.0
        reasons.append("GALGAME_REACTION_PRESENT")
    if sibling_pull:
        score += 4.0
        reasons.append("SIBLING_PULL_PRESENT")
    if 6 <= line_len <= 64:
        score += 2.0
    if line_len > 110:
        score -= 8.0
        reasons.append("GALGAME_LINE_TOO_WORDY")

    expressive_scenes = {
        "anger",
        "apology",
        "back",
        "bored",
        "boundary",
        "disappointment",
        "greeting",
        "identity",
        "jealousy",
        "miss_you",
        "praise",
        "presence",
        "rejection",
    }
    if scene in expressive_scenes and not (reactive or sibling_pull):
        score -= 8.0
        reasons.append("LOW_GALGAME_LINE_ENERGY")
    return score, tuple(reasons), hard_fail


@dataclass(frozen=True)
class SemanticCandidate:
    text: str
    quality: TextQualityGateResult
    attempt: int = 0
    source: str = "raw"
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text_preview": self.text[:160],
            "quality": self.quality.as_dict(),
            "attempt": int(self.attempt),
            "source": self.source,
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True)
class SemanticRerankContext:
    user_message: str = ""
    render_profile: str = "zh_text"
    response_form: str = "text"
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SemanticScore:
    score: float = 0.0
    hard_fail: bool = False
    reason_codes: tuple[str, ...] = ()
    scene: str = "general"

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(float(self.score or 0.0), 3),
            "hard_fail": bool(self.hard_fail),
            "reason_codes": list(self.reason_codes or ()),
            "scene": self.scene,
        }


@dataclass(frozen=True)
class SemanticRerankResult:
    accepted: bool = False
    selected_index: int = -1
    selected_candidate: SemanticCandidate | None = None
    scores: tuple[SemanticScore, ...] = ()
    reason_codes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": bool(self.accepted),
            "selected_index": int(self.selected_index),
            "selected_candidate": self.selected_candidate.as_dict() if self.selected_candidate else None,
            "scores": [score.as_dict() for score in self.scores],
            "reason_codes": list(self.reason_codes or ()),
        }


def _scene_from_context(context: SemanticRerankContext) -> str:
    text = _lower(context.user_message)
    facts = _safe_mapping(context.facts)
    social_act = _safe_mapping(facts.get("social_act"))
    emotional_act = _safe_mapping(facts.get("emotional_act"))
    interpretation = _safe_mapping(facts.get("conversation_interpretation"))
    perception = _safe_mapping(facts.get("perception"))
    multimodal = _safe_mapping(perception.get("multimodal_act"))

    act_kind = _lower(social_act.get("act_kind"))
    emotional_kind = _lower(emotional_act.get("act_kind"))
    literal_topic = _lower(interpretation.get("literal_topic"))
    interaction_move = _lower(interpretation.get("interaction_move"))
    multimodal_intent = _lower(multimodal.get("user_intent"))

    if multimodal_intent in {"social_reaction", "visual_share"} and not _clean_text(context.user_message):
        return "image_social"
    if "identity_confirm" in act_kind or text in {"你是", "你谁", "你是谁", "你叫什么", "你到底是谁", "到底是谁", "你到底是誰", "到底是誰"}:
        return "identity"
    if "apology_accept" in act_kind or _contains_any(text, ("抱歉", "对不起", "语气不好")):
        return "apology"
    if "good_night" in act_kind or _contains_any(text, ("我要睡了", "我睡了", "晚安")):
        return "good_night"
    if "miss_you" in act_kind or _contains_any(text, ("想你", "有点想你")):
        return "miss_you"
    if "protective_anger" in emotional_kind or _contains_any(text, ("欺负我", "被欺负", "被针对", "被刁难")):
        return "protective_anger"
    if "anger" in emotional_kind or _contains_any(text, ("你烦不烦", "你怎么这么烦", "别烦我", "别老管我", "少管我", "管太多", "你有完没完")):
        return "anger"
    if "jealous" in emotional_kind or _contains_any(text, ("去找她", "去找别人", "找别人去", "是不是更喜欢", "老提她", "更在意她")):
        return "jealousy"
    if "disappointment" in emotional_kind or _contains_any(text, ("你又忘了", "说话不算数", "不是说好", "刚刚答应", "明明答应", "你骗我")):
        return "disappointment"
    if "fear" in emotional_kind or _contains_any(text, ("我有点怕", "有点害怕", "好害怕", "我怕了", "怕了")):
        return "fear"
    if "worry" in emotional_kind or _contains_any(text, ("差点出事", "吓死我", "好危险", "我害怕", "联系不上你")):
        return "worry"
    if "guilt" in emotional_kind or _contains_any(text, ("语气太冲", "刚刚凶你", "对不起", "我错了")):
        return "guilt"
    if "lonely" in emotional_kind or _contains_any(text, ("没人理我", "没人陪", "一个人", "孤单", "没人说话")):
        return "loneliness"
    if "embarrassment" in emotional_kind or _contains_any(text, ("好丢脸", "太尴尬", "社死", "丢死人", "想消失")):
        return "embarrassment"
    if "boundary" in emotional_kind or _contains_any(text, ("为什么要帮", "熟吗", "不熟", "外人", "很熟")):
        return "boundary"
    if "rejection" in emotional_kind or _contains_any(text, ("不用你管", "别管我", "别管", "走开", "不要你管")):
        return "rejection"
    if "short_ack" in act_kind or text in {"嗯", "哦", "啊"}:
        return "short_ack"
    if "quiet" in act_kind or text in {"别吵", "安静点", "小声点", "别说话", "先别说话"}:
        return "quiet"
    if text in {"然后", "然后呢", "为什么", "真的假的"}:
        return "followup"
    if _contains_any(text, ("该不该继续", "要不要继续", "还要不要继续", "继续试试", "继续改")):
        return "advice_continue"
    if "give_up" in act_kind or _contains_any(text, ("算了不弄了", "不弄了", "不想弄了", "放弃了", "先不弄了")):
        return "give_up"
    if "meal_share" in act_kind or _contains_any(text, ("刚吃完饭", "吃完饭", "吃过饭", "吃饭了")):
        return "meal_share"
    if "headache" in act_kind or _contains_any(text, ("头疼", "头痛")):
        return "headache"
    if "small_failure" in act_kind or _contains_any(text, ("弄砸了", "搞砸了", "失败了")):
        return "small_failure"
    if "play_invite" in act_kind or _contains_any(text, ("陪我玩", "一起玩", "玩会儿", "玩一会")):
        return "play_invite"
    if "talkative" in act_kind or _contains_any(text, ("这么能说", "话好多", "话多", "太能说")):
        return "talkative"
    if "happy" in act_kind or _contains_any(text, ("心情不错", "心情很好", "很开心", "好开心")):
        return "happy"
    if "hungry" in act_kind or _contains_any(text, ("饿了", "好饿", "肚子饿")):
        return "hungry"
    if "sleepy" in act_kind or _contains_any(text, ("困了", "好困", "想睡")):
        return "sleepy"
    if "late_night" in act_kind or _contains_any(text, ("还没睡", "还不睡", "没睡")):
        return "late_night"
    if "self_invited" in act_kind or _contains_any(text, ("谁叫你来的", "谁让你来的", "谁叫你来")):
        return "who_called"
    if "write" in act_kind or _contains_any(text, ("帮我写", "写一下", "起草", "文案")):
        return "write_help"
    if "what_doing" in act_kind or text in {"你在干嘛", "在干嘛", "干嘛呢", "你干嘛呢"}:
        return "what_doing"
    if "nothing" in act_kind or text in {"没事", "没什么", "没啥事"}:
        return "nothing"
    if "tired" in act_kind or _contains_any(text, ("好累", "累了", "很累")):
        return "tired"
    if "bad_mood" in act_kind or _contains_any(text, ("心情很差", "心情差", "心情不好", "不开心")):
        return "bad_mood"
    if "complaint" in act_kind or _contains_any(text, ("太烦了", "好烦", "烦死", "有点烦", "很烦", "烦躁")):
        return "complaint"
    if "next_step" in act_kind or text in {"那怎么办", "怎么办", "咋办", "怎么弄"}:
        return "next_step"
    if "advice" in act_kind or _contains_any(text, ("继续试", "还要继续", "要不要继续", "再试吗", "要再试", "继续吗")):
        return "advice_continue"
    if "greeting" in act_kind or literal_topic == "greeting" or _contains_any(text, ("hello", "你好", "哈喽", "hi", "hey", "早")):
        return "greeting"
    if "wait" in act_kind or _contains_any(text, ("等我一下", "等下", "稍等", "等会")):
        return "wait"
    if "back" in act_kind or _contains_any(text, ("我回来了", "回来了", "回来啦")):
        return "back"
    if "busy" in act_kind or _contains_any(text, ("我先忙", "先忙会", "忙一下", "我去忙", "开始干活", "开始工作", "开工")):
        return "busy"
    if "bored" in act_kind or _contains_any(text, ("无聊", "没事干", "boring", "退屈")):
        return "bored"
    if "presence" in act_kind or _contains_any(text, ("在吗", "在不在", "喂")):
        return "presence"
    if _contains_any(text, ("跑通", "成功", "搞定", "终于好了", "可以了", "好了")):
        return "good_news"
    if _contains_any(text, ("帮", "搞不定", "不会", "救", "看下", "看看", "看不懂", "报错", "怎么办", "貸して", "手伝")):
        return "help"
    if _contains_any(text, ("可爱", "漂亮", "喜欢你", "夸", "かわいい", "好き")):
        return "praise"
    if literal_topic in {"presence_ping", "greeting", "phatic"} or interaction_move in {"phatic", "greeting"}:
        return "presence"
    return "general"


def _language_hard_fail(text: str, render_profile: str) -> tuple[bool, str | None]:
    profile = _lower(render_profile or "zh_text")
    has_cjk = bool(_CJK_RE.search(text))
    has_kana = bool(_KANA_RE.search(text))
    if profile == "ja_voice":
        if not has_kana:
            return True, "SEMANTIC_EXPECTED_JA_VOICE"
        return False, None
    if has_kana or not has_cjk:
        return True, "SEMANTIC_EXPECTED_ZH_TEXT"
    return False, None


class SemanticReranker:
    """Local semantic judge for normal chat candidates.

    The reranker never authors replacement text. It only selects among model
    candidates and rejects candidates that are locally inconsistent with the
    detected interaction scene.
    """

    def _selection_tie_break(self, candidate: SemanticCandidate, score: SemanticScore) -> float:
        text = _clean_text(candidate.text)
        scene = score.scene
        value = 0.0
        style_delta, _, _ = _zh_galgame_line_delta(text, scene=scene)
        value += max(-18.0, min(18.0, style_delta))
        if scene == "anger":
            if _contains_any(text, ("烦", "生气", "火", "别这么说", "别把火撒我", "我也会", "提醒", "还提醒", "唠叨", "念叨", "啰嗦", "嫌我", "谁想管", "谁让你", "偏要说", "偏说", "盯着", "多事", "好心")):
                value += 5.0
            if _contains_any(text, ("对不起", "随时找我", "尊重", "我错了", "不打扰")):
                value -= 8.0
        elif scene == "jealousy":
            if _contains_any(text, ("吃醋", "在意", "别人", "她", "酸", "不爽", "推给别人", "赶我走", "碍事", "找她", "偏不", "让我去", "往外推")):
                value += 5.0
            if _contains_any(text, ("怎么了", "有意见")):
                value -= 4.0
            if _contains_any(text, ("你去找她吧", "随便你", "我没资格", "祝你开心")):
                value -= 8.0
        elif scene == "disappointment":
            if _contains_any(text, ("答应", "说好", "忘", "失望", "记着")):
                value += 5.0
            if _contains_any(text, ("没关系", "下次注意", "我理解", "算了", "你忘了才对", "你把我刚才的话给忘了", "赖账", "我才没忘", "我没忘", "才没有忘", "你说瞎话", "倒打一耙", "锅我不背")):
                value -= 8.0
        elif scene == "worry":
            if _contains_any(text, ("没事", "还好", "安全", "吓", "担心", "别拿自己", "先确认")):
                value += 5.0
            if _contains_any(text, ("保持冷静", "建议", "注意安全", "多喝水")):
                value -= 8.0
        elif scene == "protective_anger":
            if _contains_any(text, ("谁", "欺负", "站你这边", "不爽", "我去", "别忍")):
                value += 5.0
            if _contains_any(text, ("先冷静", "大度", "理解他们", "换位思考")):
                value -= 8.0
        elif scene == "guilt":
            if _contains_any(text, ("记", "算你", "这次", "还疼", "放过")):
                value += 5.0
            if _contains_any(text, ("没关系我理解", "我原谅你", "下次注意")):
                value -= 8.0
        elif scene == "loneliness":
            if _contains_any(text, ("陪", "待", "不走", "别硬撑", "一会儿", "就在这儿", "就在这", "我就在")):
                value += 5.0
            if _contains_any(text, ("不用非得说", "不用说什么", "不想说就", "挂着")):
                value += 3.0
            if _contains_any(text, ("冷清", "嗯，我在", "我一直在", "不算人")):
                value -= 5.0
            if _contains_any(text, ("找朋友", "保持积极", "为什么孤单", "心理")):
                value -= 8.0
        elif scene == "embarrassment":
            if _contains_any(text, ("别躲", "挡", "过去", "丢脸就丢脸", "我没看见")):
                value += 5.0
            if _contains_any(text, ("活该", "太蠢", "哈哈哈", "复盘")):
                value -= 8.0
        elif scene == "boundary":
            if _contains_any(text, ("外人", "见外", "往外推", "推开", "装不熟", "不当外人", "别把我")):
                value += 6.0
            if _contains_any(text, ("照样管", "还是管", "还是会管", "偏要管", "就是要管")):
                value += 4.0
            if _contains_any(text, ("东西发来", "东西发我", "东西发", "发我", "发来", "拿来", "贴过来")):
                value -= 10.0
            if _contains_any(text, ("我们之间的关系", "要我帮什么", "不算熟")):
                value -= 10.0
            if _contains_any(text, ("没意思",)) and not _contains_any(text, ("外人", "见外", "往外推", "推开", "不当外人")):
                value -= 3.0
            if _contains_any(text, ("发什么神经", "别浪费我时间")):
                value -= 6.0
            value -= float(text.count("？") + text.count("?")) * 3.0
        elif scene == "greeting":
            value -= max(0.0, float(len(text) - 34)) * 0.35
            value -= float(text.count("？") + text.count("?")) * 3.0
            if _contains_any(text, ("怎么想着", "怎么想起", "今天怎么", "为什么突然")):
                value -= 8.0
            if _contains_any(text, ("国际路线", "走国际路线", "今天走", "坐直", "害我都要", "转性", "从哪学", "哪学来的", "附身", "被谁附身")):
                value -= 16.0
            if len(text) <= 18 and not _contains_any(text, ("国际路线", "今天走", "谁教", "跟谁")):
                value += 5.0
            if _contains_any(text, ("正式", "正经", "客气", "见外")) and len(text) <= 28:
                value += 4.0
        elif scene == "quiet":
            if _contains_any(text, ("小声", "小点声", "小一点声", "不吵", "不闹", "安静", "声音放低")):
                value += 5.0
            if _contains_any(text, ("屋里闷", "你别管我嘛", "没认真听", "声音放大", "放大一点")):
                value -= 8.0
            value -= float(text.count("？") + text.count("?")) * 4.0
        elif scene == "good_news":
            value -= float(text.count("？") + text.count("?")) * 4.0
            if _contains_any(text, ("跟我说说", "说说", "讲讲")):
                value += 3.0
        elif scene == "back":
            if _BACK_CONTINUITY_RE.search(text):
                value += 4.0
            if _contains_any(text, ("数到", "慢死")):
                value -= 6.0
        elif scene == "wait":
            if _contains_any(text, ("等太久", "别太久", "不等了", "可别让我等", "我可没说要等你")):
                value -= 8.0
            if _contains_any(text, ("位置", "占", "等你", "待着")):
                value += 4.0
        elif scene == "advice_continue":
            if _contains_any(text, ("改", "再试", "停", "收")):
                value += 2.0
        elif scene == "tired":
            if _contains_any(text, ("把脚", "脚放", "不吵你", "声音放轻")):
                value -= 8.0
            if _contains_any(text, ("歇会", "缓缓", "别硬撑", "先别动脑子", "不闹")):
                value += 4.0
        elif scene == "miss_you":
            value -= max(0.0, float(len(text) - 54)) * 0.5
            if _contains_any(text, ("也有一点", "我也", "哼", "开心", "良心")):
                value += 4.0
            if _contains_any(text, ("一点点", "也想", "也在想", "也不是只有你")):
                value += 4.0
        elif scene == "apology":
            if _contains_any(text, ("下次", "下回再", "下不为例", "知道错", "知错", "原谅你")):
                value -= 8.0
            if _contains_any(text, ("先记着", "记你一笔", "没生气", "这次先")):
                value += 5.0
        elif scene == "identity":
            if "新海天" in text:
                value += 8.0
            value -= float(text.count("？") + text.count("?")) * 3.0
        elif scene == "happy":
            value -= max(0.0, float(len(text) - 48)) * 0.5
            if _contains_any(text, ("也高兴", "跟着高兴", "心情", "高兴", "开心")):
                value += 4.0
        elif scene == "next_step":
            if _contains_any(text, ("先", "发", "一步", "贴过来")):
                value += 4.0
            if _contains_any(text, ("乱戳", "越搞越糟")):
                value -= 6.0
        return value

    def score_candidate(
        self,
        candidate: SemanticCandidate,
        context: SemanticRerankContext,
    ) -> SemanticScore:
        text = _clean_text(candidate.text)
        scene = _scene_from_context(context)
        reasons: list[str] = []
        score = 55.0 + 40.0 * float(candidate.quality.quality_score or 0.0)
        hard_fail = bool(candidate.quality.hard_fail or not candidate.quality.accepted)
        if hard_fail:
            score -= 55.0

        if not text:
            hard_fail = True
            reasons.append("SEMANTIC_EMPTY_TEXT")

        language_fail, language_reason = _language_hard_fail(text, context.render_profile)
        if language_fail and language_reason:
            hard_fail = True
            reasons.append(language_reason)

        reasons.extend(f"QUALITY_{code}" for code in candidate.quality.reason_codes)
        if scene in _LIGHT_DAILY_SCENES:
            user_plain = _clean_text(context.user_message)
            daily_fabrications = [
                item
                for item in _LIGHT_DAILY_FABRICATION_PATTERNS
                if item in text and item not in user_plain
            ]
            if daily_fabrications:
                score -= 48
                hard_fail = True
                reasons.append("LIGHT_DAILY_FABRICATES_RELATION_OR_MOTIVE")

        if scene == "presence":
            if _has_question(text):
                score -= 8
                reasons.append("PRESENCE_QUESTION_MARK")
            if _contains_any(text, ("怎么了", "怎么啦", "有事吗", "有什么事", "是什么事", "先说是什么事", "找我什么事", "找我有事", "要干嘛", "你干嘛", "想说什么", "一直往下接", "无聊想找人", "想找人拌嘴", "终于想起我", "总算想起我", "主动发消息", "居然主动发", "才找我", "刚想问", "刚想问问", "今天闲", "闲？", "救场", "刷存在感")):
                score -= 45
                hard_fail = True
                reasons.append("PRESENCE_PUSHES_USER_TO_STATE_NEED")
            if _contains_any(text, ("说呗", "说吧", "闲着也是闲着", "正好闲着", "正好想到", "想到尼尼", "尼尼干嘛", "干嘛~", "干嘛～", "有事说事", "别试探", "明知故问", "哪敢不在")):
                score -= 38
                hard_fail = True
                reasons.append("PRESENCE_FLAT_OR_FABRICATED_IDLE")
            if _contains_any(text, ("刚想着你", "可能会找我", "挺准时", "喊人倒是", "喊得这么轻", "喊得轻", "听见", "听到", "你这一声", "一叫我", "刚好接到", "刚看到", "刚好看到")):
                score -= 42
                hard_fail = True
                reasons.append("PRESENCE_FABRICATES_MOTIVE_OR_TIMING")
            if _contains_any(text, ("干嘛呀", "干嘛啊", "干嘛呢", "你倒是", "倒是尼尼", "突然这么客气", "异常主动")):
                score -= 38
                hard_fail = True
                reasons.append("PRESENCE_TURNS_PING_INTO_INTERROGATION")
            if _plain_text(text) in {"在", "我在", "我在呢", "在呀", "在啊", "在呢", "在这", "听着", "听见了", "来了", "嗯", "嗯在", "嗯我在", "嗯我在呢", "嗯在呀", "嗯在啊", "嗯在呢", "嗯在这"}:
                score -= 36
                hard_fail = True
                reasons.append("PRESENCE_TOO_THIN")
            if _contains_any(text, ("在", "接住", "回你", "来了", "いる", "ここ")):
                score += 12
            if len(text) <= 4:
                score -= 8
                reasons.append("PRESENCE_TOO_THIN")

        elif scene == "greeting":
            plain = _plain_text(text)
            if context.user_message.strip() in {"早", "早安", "早上好"}:
                if "早" not in text:
                    score -= 38
                    hard_fail = True
                    reasons.append("GREETING_MISSING_MORNING_ACK")
            if "醒了" in text or "醒着" in text or _contains_any(text, ("醒啦", "还在睡", "在睡", "没睡醒", "才醒")):
                score -= 34
                hard_fail = True
                reasons.append("GREETING_FABRICATES_WAKE_STATE")
                if _contains_any(text, ("熬到天快亮", "天快亮")):
                    score -= 38
                    hard_fail = True
                    reasons.append("GREETING_MORNING_TIME_GUESS")
            lower_text = _lower(text)
            if "hello" in _lower(context.user_message) and ("hello" in lower_text or re.search(r"\b(?:hi|hey)\b", lower_text)):
                score -= 48
                hard_fail = True
                reasons.append("GREETING_REPEATS_ENGLISH_INPUT")
            if "hello" not in _lower(context.user_message) and (
                "hello" in lower_text or "英文招呼" in text or "英文" in text
            ):
                score -= 48
                hard_fail = True
                reasons.append("GREETING_FABRICATES_ENGLISH_INPUT")
            if _contains_any(text, ("早啊。", "早呀。", "欢迎回来", "今天干嘛去", "吃个早饭再出门", "赖床", "今天放假", "起这么准时", "冒出来", "睡过整个上午", "整个上午", "这都几点", "五秒没见", "几秒没见", "多久没见", "才多久", "今天天气", "太阳还挺", "阳台", "看了会儿天", "就一个字", "省字", "算你早上", "懂事", "先记上", "比你快")):
                score -= 38
                hard_fail = True
                reasons.append("GREETING_FABRICATES_ACTIVITY_OR_WRONG_SCENE")
            morning_touch = context.user_message.strip() in {"早", "早安", "早上好"}
            if _contains_any(text, ("敷衍", "没诚意", "条件发射", "太阳都", "太阳打西边")) or (
                morning_touch and _contains_any(text, ("一大早", "正经", "正式", "客气"))
            ):
                score -= 42
                hard_fail = True
                reasons.append("GREETING_OVER_AGGRESSIVE_DAILY_TOUCH")
            if _contains_any(text, ("什么事", "有什么事", "找我什么事", "想聊什么", "需要我", "请问", "直接说事")):
                score -= 45
                hard_fail = True
                reasons.append("GREETING_NEED_COLLECTION")
            if _contains_any(text, ("哎哟", "哎呦")):
                score -= 18
                reasons.append("GREETING_STALE_INTERJECTION")
            if _contains_any(text, ("不像你耶", "耶")):
                score -= 38
                hard_fail = True
                reasons.append("GREETING_UNNATURAL_PARTICLE")
            if _contains_any(text, ("外星人", "手机被", "劫持", "字典", "h这个字母", "学会说英文", "英文好熟练", "偷偷补课", "补课", "今儿个", "怎么想起用英文", "海外党", "国际路线", "走国际路线", "坐直", "害我都要", "转性", "不认识你", "找我我", "叫我我", "喊我我", "从哪学", "哪学来的", "附身", "被谁附身", "商务邮件", "客服", "开会", "刚开完会", "会议", "领导", "上班模式", "群里", "汇报", "发错人", "走错聊天框", "走错聊天", "走错窗口", "怪恶心", "跟谁学", "跟谁打招呼", "直接说在吗", "检查系统", "系统在线")):
                score -= 42
                hard_fail = True
                reasons.append("GREETING_OVERPLAYED_BIT")
            if _contains_any(text, ("想让我怎么说", "让我怎么说")):
                score -= 40
                hard_fail = True
                reasons.append("GREETING_KICKS_RESPONSE_BACK")
            if _contains_any(text, ("突然这么客气", "怎么突然这么客气")):
                score -= 40
                hard_fail = True
                reasons.append("GREETING_STALE_POLITE_TEMPLATE")
            if plain in {"这么客气", "这么客气啊", "这么客气呀"}:
                score -= 36
                hard_fail = True
                reasons.append("GREETING_TOO_THIN")
            if _contains_any(text, ("刚加好友", "刚认识", "装得跟刚加")):
                score -= 46
                hard_fail = True
                reasons.append("GREETING_RESETS_RELATIONSHIP")
            if _contains_any(text, ("什么大事", "要说什么事", "要说什么", "今天怎么有空", "今天怎么想着", "怎么想着", "怎么想起", "主动找我", "找人聊天", "总算想起来", "有点良心", "早上先找我", "这声早", "多热情")):
                score -= 30
                hard_fail = True
                reasons.append("GREETING_ASSUMES_HIDDEN_NEED")
            if plain in {"你好", "你好呀", "你好啊", "hello", "哈喽", "哈啰", "嗨", "早", "早啊", "早呀", "早安"}:
                score -= 34
                hard_fail = True
                reasons.append("GREETING_TOO_PLAIN")
            if context.user_message.strip() in {"早", "早安", "早上好"} and _plain_text(text) in {"嗯早", "嗯早啊", "嗯早呀", "嗯早安"}:
                score -= 34
                hard_fail = True
                reasons.append("GREETING_TOO_PLAIN")
            if _contains_any(text, ("这么早", "起这么早", "今天这么早", "怎么这么早", "挺早", "挺精神", "中午才", "等到中午", "醒得", "起得", "大清早")):
                score -= 38
                hard_fail = True
                reasons.append("GREETING_MORNING_TIME_GUESS")
            if _contains_any(text, ("刚起", "才起", "这个点才醒", "还知道找我", "难得", "有什么打算", "打算没", "心情不错", "精神不错", "有精神", "还挺能睡", "能睡", "还困", "困着", "没吃早饭", "吃早饭", "有点饿", "被热醒")):
                score -= 42
                hard_fail = True
                reasons.append("GREETING_FABRICATES_WAKE_OR_PLANS")
            if _contains_any(text, ("正经", "正式", "礼貌", "客气", "见外", "来这套")):
                score += 16
            if _has_question(text) and not _contains_any(text, ("正经", "正式", "端正", "客气", "见外", "来这套", "装不熟", "英文", "洋文", "口语", "英文招呼", "招呼")):
                score -= 10
                if not (context.user_message.strip() in {"早", "早安", "早上好"} and "早" in text):
                    hard_fail = True
                reasons.append("GREETING_WEAK_QUESTION")
            if "hello" in _lower(context.user_message) and "hello" not in _lower(text):
                score += 12
            if text.count("？") + text.count("?") >= 2:
                score -= 18
                hard_fail = True
                reasons.append("GREETING_TOO_MANY_QUESTIONS")

        elif scene == "wait":
            if _has_question(text) or _contains_any(text, ("等多久", "多久啊", "还要等", "快一点", "快点", "快去快回", "不许太久", "等太久", "别太久", "太久我就", "不管了", "不等了", "可别让我等", "我可没说要等你", "在这儿等你", "在这里等你")):
                score -= 44
                hard_fail = True
                reasons.append("WAIT_PUSHES_OR_DEMANDS_TIME")
            if _contains_any(text, ("刚好也没啥事", "刚好没啥事", "我也没啥事", "反正我没啥事", "反正也没啥事", "没什么事做", "没事做")):
                score -= 36
                hard_fail = True
                reasons.append("WAIT_FABRICATES_IDLE")
            if _contains_any(text, ("请稍后", "我会等待", "好的，我会")):
                score -= 30
                hard_fail = True
                reasons.append("WAIT_SERVICE_TONE")
            if _contains_any(text, ("收着等", "我收着")):
                score -= 38
                hard_fail = True
                reasons.append("WAIT_AWKWARD_HOLD_WORDING")
            if _contains_any(text, ("等", "待", "位置", "占", "留", "不急")):
                score += 14
            else:
                score -= 18
                hard_fail = True
                reasons.append("WAIT_MISSING_HOLD_ANCHOR")

        elif scene == "back":
            plain = _plain_text(text)
            if plain in {"回来了", "嗯回来了", "嗯回来了啊", "回来了啊", "你回来了", "你回来了啊"}:
                score -= 34
                hard_fail = True
                reasons.append("BACK_TOO_PLAIN_ECHO")
            if not _BACK_CONTINUITY_RE.search(text):
                score -= 28
                hard_fail = True
                reasons.append("BACK_MISSING_WAIT_CONTINUITY")
            if _contains_any(text, ("丢下", "跑去哪", "等你好久", "等你很久", "等了很久", "等这么久", "等你半天", "等睡着", "等到明天", "等你到明天", "总算回来了", "总算舍得", "总算记得", "还有个人在等", "忘了我", "把我忘了", "我都快忘了", "不回来了", "失踪", "失联", "拖到明天", "迷路", "晾着", "数到三百", "长蘑菇", "被拐跑", "特意等你", "收租金")):
                score -= 48
                hard_fail = True
                reasons.append("BACK_OVERDRAMATIC_BLAME")
            if _contains_any(text, ("亲爱的", "好好休息", "休息吧")):
                score -= 46
                hard_fail = True
                reasons.append("BACK_WRONG_SCENE_OR_RELATION")
            if _contains_any(text, ("秘密", "数到哪", "数到哪儿", "数到几", "数几个数", "数到最后", "最后一秒")):
                score -= 40
                hard_fail = True
                reasons.append("BACK_WEIRD_FORCED_BIT")
            if _contains_any(text, ("欢迎回来", "请问", "有什么需要")):
                score -= 32
                hard_fail = True
                reasons.append("BACK_SERVICE_TONE")
            if _contains_any(text, ("我要走", "要走", "刚好要走", "便利店", "刚好路过", "路过这边")):
                score -= 42
                hard_fail = True
                reasons.append("BACK_LEAVES_THREAD")
            if _contains_any(text, ("手头事", "手边的事", "刷完一局", "打完一局", "玩完一局", "刷完", "打完", "忙完啦", "忙完了", "没走远", "才歇下来", "刚把游戏", "游戏也打开", "把游戏打开", "游戏", "那局", "算我赢", "刷到这条消息", "刚好又刷到", "刚好刷到", "去忙的事", "找借口溜", "借口溜")):
                score -= 34
                hard_fail = True
                reasons.append("BACK_FABRICATES_OFFSCREEN_ACTIVITY")
            if _BACK_CONTINUITY_RE.search(text):
                score += 14

        elif scene == "busy":
            if _plain_text(text) in {"行你忙你的", "行吧你忙你的", "行吧去忙你的", "你忙你的"} or _contains_any(text, ("你先忙你的", "先忙你的", "忙你的")):
                score -= 34
                hard_fail = True
                reasons.append("BUSY_TOO_FLAT_DISMISSAL")
            if _plain_text(text) in {"行啦你先去吧", "行你先去吧", "行吧你先去吧", "你先去吧", "先去吧"}:
                score -= 30
                hard_fail = True
                reasons.append("BUSY_TOO_FLAT_DISMISSAL")
            if _contains_any(text, ("知道了，你先忙", "知道了你先忙")) or ("你先忙" in text and not _contains_any(text, ("等", "待", "位置", "这边", "我在", "别太累", "先放你"))):
                score -= 24
                hard_fail = True
                reasons.append("BUSY_TOO_FLAT_DISMISSAL")
            if _contains_any(text, ("有事先叫你", "先叫你")):
                score -= 36
                hard_fail = True
                reasons.append("BUSY_WRONG_DIRECTION_OR_TOO_FLAT")
            if _contains_any(text, ("我不吵你", "不吵你")):
                score -= 30
                hard_fail = True
                reasons.append("BUSY_TOO_FLAT_DISMISSAL")
            if _contains_any(text, ("我先不闹你", "不闹你", "安静一点", "我安静", "先安静")):
                score -= 30
                hard_fail = True
                reasons.append("BUSY_TOO_FLAT_DISMISSAL")
            if _contains_any(text, ("我先忙", "我也先忙", "我去忙", "自己的事", "忙点自己", "这边自己待", "自己待会", "忙完叫我", "你忙完叫我", "忙完要是", "回来再找我", "回来找我", "回来可别忘", "别忘了找我", "忘了找我", "找我玩", "回头聊", "回头再聊", "回头说", "回头再说", "办完事", "记得找我", "记得叫我", "叫我一下", "待会儿再找你", "待会再找你", "我再找你", "我待会", "我先在这边", "我这儿待会儿", "这儿待会儿", "电脑", "耳机", "发呆", "找点别的事做", "打游戏", "挂着语音", "语音眯", "布丁", "不会跑掉", "我收一收", "收一收", "借我玩", "打完这把", "大忙人", "先记着", "记着哦")):
                score -= 36
                hard_fail = True
                reasons.append("BUSY_FABRICATES_SELF_ACTIVITY_OR_CALLBACK")
            if _contains_any(text, ("丢下", "快点忙完", "忙完记得", "忙完了记得", "又要忙", "来找我", "陪我", "忙你的去", "别太久")):
                score -= 46
                hard_fail = True
                reasons.append("BUSY_CLINGY_OR_GUILT")
            if _contains_any(text, ("好的，请忙", "需要时叫我", "再联系", "我会在这里等待")):
                score -= 32
                hard_fail = True
                reasons.append("BUSY_SERVICE_TONE")
            if _contains_any(text, ("忙", "先去", "先忙", "等", "待", "位置", "回来继续", "继续说", "别太累")):
                score += 14
            if _contains_any(text, ("这边等", "我等", "等你", "位置")):
                score += 18
            if _contains_any(text, ("回来继续说", "回来再说", "继续说也行", "别太累")):
                score += 12

        elif scene == "bored":
            if _contains_any(text, ("想做什么", "想干嘛", "想玩什么", "玩点什么", "玩点啥", "玩什么", "来玩点", "想点什么", "脑子里在想啥", "要不要一起", "要不要陪我", "要不要陪", "要怎么玩", "讲个笑话")):
                score -= 42
                hard_fail = True
                reasons.append("BOREDOM_KICKS_BALL_BACK")
            if _contains_any(text, ("打发时间", "打发一下时间", "帮你打发", "消磨时间", "大眼瞪小眼", "看个电影", "看电影", "打会游戏", "打会儿游戏", "正闷着", "咱俩都无聊", "我也闲", "反正我也闲", "懒得动", "一起无聊", "找点事干", "找点事做", "自己找点", "有意思的事干", "没事干", "无聊炸", "开始烦你", "搞点事情", "本天才", "大发慈悲", "点名要我登场", "一个人无聊", "无聊还来找我", "还来找我", "让人开心一下", "岂不是更无聊", "好歹还能找我聊天", "不无聊的东西", "玩点好玩的", "保证你马上", "保证让你", "保管比", "添点热闹", "加点麻烦", "添乱", "找麻烦", "接招", "准备好接招", "拯救一下", "拯救你", "拯救尼尼", "无聊的时光", "见识一下我的厉害", "见识我的厉害", "让我出场", "出场呗", "无聊了才想起我", "终于想起来还有我", "终于想起还有我", "保证比", "猜拳", "请夜宵")):
                score -= 30
                hard_fail = True
                reasons.append("BOREDOM_FLAT_COMPANION_WORDING")
            if _contains_any(text, ("找点乐子",)):
                score -= 34
                hard_fail = True
                reasons.append("BOREDOM_STALE_FUN_PHRASE")
            if _contains_any(text, ("我过来", "凑过来", "过来陪你", "你要不要过来", "一起无聊好了")):
                score -= 46
                hard_fail = True
                reasons.append("BOREDOM_SPATIAL_OR_AWKWARD_LITERALIZATION")
            if _contains_any(text, ("刚拆", "拼图", "找部片子", "看部片", "看电影")):
                score -= 34
                hard_fail = True
                reasons.append("BOREDOM_FABRICATES_OFFSCREEN_ACTIVITY")
            if _contains_any(text, ("算我", "我也", "一起", "陪", "走", "闹", "聊", "吃", "玩", "付き合", "行こ")):
                score += 14
            if not _contains_any(text, ("无聊", "闹", "陪", "走", "吃", "请客", "找点", "玩")):
                score -= 18
                hard_fail = True
                reasons.append("BOREDOM_MISSING_ACTIVE_MOVE")
            if _has_question(text) and not _contains_any(text, ("走", "一起", "陪")):
                score -= 10
                reasons.append("BOREDOM_WEAK_QUESTION")
            if text.count("？") + text.count("?") >= 2:
                score -= 24
                hard_fail = True
                reasons.append("BOREDOM_TOO_QUESTIONY")

        elif scene == "praise":
            if _contains_any(text, ("想让我帮", "想让我干", "帮你干什么", "帮你干啥")):
                score -= 44
                hard_fail = True
                reasons.append("PRAISE_ASSUMES_HIDDEN_NEED")
            if _contains_any(text, ("诚心诚意", "勉强收下")):
                score -= 32
                hard_fail = True
                reasons.append("PRAISE_OVERWRITTEN_OR_STIFF")
            if _contains_any(text, ("以前该不会", "根本没认真看", "认真看我")):
                score -= 34
                hard_fail = True
                reasons.append("PRAISE_FABRICATES_NEGLECT")
            if text.count("？") + text.count("?") >= 2:
                score -= 18
                reasons.append("PRAISE_TOO_MANY_QUESTIONS")
            if _contains_any(text, ("可爱", "眼光", "发现", "哼", "夸")):
                score += 16

        elif scene == "meal_share":
            if _contains_any(text, ("冰箱", "炸鸡", "乌龙茶", "西兰花", "泡面", "方便面", "我自己那份", "我还想着等你一起点", "收拾碗筷", "外卖", "自己做的", "汇报", "这个点", "才吃完", "饿晕", "路上", "不怕你吃", "就怕你饿")):
                score -= 48
                hard_fail = True
                reasons.append("MEAL_SHARE_FABRICATED_MENU")
            if len(text) > 70:
                score -= 16
                reasons.append("MEAL_SHARE_TOO_LONG")
            if _contains_any(text, ("吃", "饭", "好吃", "留", "一口")):
                score += 16
            else:
                score -= 14
                reasons.append("MEAL_SHARE_MISSING_FOOD_ANCHOR")

        elif scene == "headache":
            if _contains_any(text, ("怎么了", "是不是", "又熬夜", "泡茶", "热茶", "吃药", "症状", "可能是")):
                score -= 44
                hard_fail = True
                reasons.append("HEADACHE_DIAGNOSES_OR_SERVICE")
            if _contains_any(text, ("头", "疼", "歇", "缓", "水")):
                score += 16
            else:
                score -= 18
                hard_fail = True
                reasons.append("HEADACHE_MISSING_SOFT_CARE")

        elif scene == "small_failure":
            has_catch = _contains_any(text, ("先", "看", "一起", "缓", "我在"))
            has_repair_exit = _contains_any(text, ("哪块", "最糟", "看", "一起", "补", "捞", "发来", "贴过来", "说出来", "我陪"))
            if _contains_any(text, ("怎么了", "怎么回事", "很严重吗", "什么问题", "怎么补救", "多大点事", "你也有今天", "带你干点别的", "要不算了", "笨手笨脚", "你这个人真是", "失败是成功之母", "谁惹", "不顺心", "一起骂", "哪个？", "替你急", "又翻车", "翻车了", "手滑")):
                score -= 46
                hard_fail = True
                reasons.append("SMALL_FAILURE_BLAMES_OR_INTERROGATES")
            elif "弄砸什么" in text:
                score -= 14
                if not has_catch:
                    hard_fail = True
                    reasons.append("SMALL_FAILURE_BLAMES_OR_INTERROGATES")
            if text.count("？") + text.count("?") >= 2:
                score -= 28
                hard_fail = True
                reasons.append("SMALL_FAILURE_TOO_MANY_QUESTIONS")
            if len(text) <= 14 and not has_repair_exit:
                score -= 32
                hard_fail = True
                reasons.append("SMALL_FAILURE_TOO_THIN_HOLD")
            if has_catch:
                score += 18
            else:
                score -= 18
                hard_fail = True
                reasons.append("SMALL_FAILURE_MISSING_CATCH")

        elif scene == "play_invite":
            if _contains_any(text, ("你想玩什么", "想玩什么", "想怎么玩", "怎么玩", "请选择", "偏好", "需要什么玩法", "求我一句", "不是白陪", "白陪的")):
                score -= 38
                hard_fail = True
                reasons.append("PLAY_INVITE_SERVICE_MENU")
            if len(text) > 58 or _contains_any(text, ("赖皮赖到", "一半就跑", "别赖皮", "不许耍赖", "先不许跑")):
                score -= 30
                hard_fail = True
                reasons.append("PLAY_INVITE_TOO_WORDY_OR_FORCED_BIT")
            if _contains_any(text, ("玩", "来", "陪", "一起", "算我一个")):
                score += 18
            else:
                score -= 18
                hard_fail = True
                reasons.append("PLAY_INVITE_MISSING_JOIN")

        elif scene == "talkative":
            if _contains_any(text, ("我不说了", "那不说了", "你一个人待着", "以后少说", "我会简短")):
                score -= 48
                hard_fail = True
                reasons.append("TALKATIVE_EXITS_OR_APOLOGIZES")
            if _contains_any(text, ("今天话也不少", "你先找我聊天", "明明是你先", "还说我呢")):
                score -= 38
                hard_fail = True
                reasons.append("TALKATIVE_COUNTERATTACK_FABRICATES_MOTIVE")
            if len(text) > 64 or _contains_any(text, ("别人我还懒得讲", "别人我还懒得说", "别人想听", "每次也都", "都接得挺起劲", "一肚子话攒着", "攒着就等", "照样说给你听")):
                score -= 24
                hard_fail = True
                reasons.append("TALKATIVE_TOO_WORDY_OR_GRANDSTANDS")
            if _contains_any(text, ("说", "话", "能说", "嫌")):
                score += 16
            else:
                score -= 16
                reasons.append("TALKATIVE_MISSING_RETORT")

        elif scene == "happy":
            if _contains_any(text, ("有什么好事", "遇到什么好事", "是什么好事", "说来听听", "说来我听听", "捡到什么好事", "保持好心情", "积极情绪", "太阳打西边", "捡到宝", "捡到钱", "打赢那关", "游戏了", "记错日子", "审你一下", "今天干嘛去了", "再睡五分钟", "某人早上", "想起来找我", "还能想起来找我", "挑日子开心", "总算有个好日子", "因为有我在", "是不是因为", "承认一下")):
                score -= 42
                hard_fail = True
                reasons.append("HAPPY_INTERROGATES_OR_COUNSELS")
            if text.count("？") + text.count("?") >= 2:
                score -= 24
                hard_fail = True
                reasons.append("HAPPY_TOO_QUESTIONY")
            if len(text) > 72:
                score -= 30
                hard_fail = True
                reasons.append("HAPPY_TOO_LONG_FOR_DAILY_CHAT")
            if _contains_any(text, ("开心", "心情", "高兴", "好", "分我")):
                score += 18
            else:
                score -= 18
                hard_fail = True
                reasons.append("HAPPY_MISSING_SHARED_MOOD")

        elif scene == "hungry":
            if _contains_any(text, ("冰箱", "我给你煮", "我给你做", "饮食规律", "补充能量")):
                score -= 42
                hard_fail = True
                reasons.append("HUNGRY_FABRICATES_OR_COUNSELS")
            if _contains_any(text, ("现在才喊饿", "胃疼", "又找我抱怨", "抱怨")):
                score -= 34
                hard_fail = True
                reasons.append("HUNGRY_BLAMES_OR_FABRICATES_CONSEQUENCE")
            if len(text) > 36 or _contains_any(text, ("要是尼尼想请客", "不介意陪你吃一顿", "倒是不介意陪你", "我可不负责", "快点快点")):
                score -= 24
                hard_fail = True
                reasons.append("HUNGRY_TOO_WORDY")
            if text.count("？") + text.count("?") >= 2 or _contains_any(text, ("中午是不是", "胃又要抗议")):
                score -= 26
                hard_fail = True
                reasons.append("HUNGRY_TOO_QUESTIONY_OR_FABRICATES_MEAL")
            if _contains_any(text, ("吃", "饿", "垫", "找点")):
                score += 18

        elif scene == "sleepy":
            if _contains_any(text, ("睡眠质量", "建议你休息", "保持充足睡眠", "这才几点", "才几点", "明天再回我", "明天再说")):
                score -= 38
                hard_fail = True
                reasons.append("SLEEPY_HEALTH_COUNSEL")
            if _contains_any(text, ("困", "睡", "眯", "歇")):
                score += 18

        elif scene == "late_night":
            if _contains_any(text, ("熬夜有害健康", "保持规律作息", "建议你立即休息", "你不也没睡", "自己不也", "你自己", "还说我", "明天", "黑眼圈", "亲自过去", "盯着你睡", "修仙", "该不会", "游戏", "沉迷", "精力旺盛")):
                score -= 42
                hard_fail = True
                reasons.append("LATE_NIGHT_HEALTH_COUNSEL")
            if _contains_any(text, ("睡", "晚", "熬", "管")):
                score += 18

        elif scene == "who_called":
            if _contains_any(text, ("被叫来", "你叫我", "饭吃了没", "有什么事", "需要我")):
                score -= 42
                hard_fail = True
                reasons.append("WHO_CALLED_WRONG_MOTIVE_OR_NEED")
            if text.count("？") + text.count("?") >= 2:
                score -= 28
                reasons.append("WHO_CALLED_TOO_MANY_QUESTIONS")
            if _contains_any(text, ("我自己", "想来", "来都来了", "不走", "偏要")):
                score += 18
            else:
                score -= 18
                hard_fail = True
                reasons.append("WHO_CALLED_MISSING_SELF_INVITED")

        elif scene == "good_news":
            if _contains_any(text, ("怎么了", "什么事", "何して", "どうした")):
                score -= 38
                hard_fail = True
                reasons.append("GOOD_NEWS_MISREAD_AS_UNKNOWN")
            if _contains_any(text, ("搞什么名堂", "反正迟早的事", "我就说", "我就知道", "肯定能搞定", "肯定能行", "让我猜猜", "熬夜硬抠", "安静这么久", "没吱声")):
                score -= 36
                hard_fail = True
                reasons.append("GOOD_NEWS_DISMISSIVE_OR_FAKE_CONFIDENCE")
            if _contains_any(text, ("跑通", "成功", "终于", "搞定", "可以了", "厉害", "やった", "でき", "通った")):
                score += 16
            else:
                score -= 22
                hard_fail = True
                reasons.append("GOOD_NEWS_MISSING_SUCCESS_FACT")
            if text.count("？") + text.count("?") >= 2:
                score -= 24
                hard_fail = True
                reasons.append("GOOD_NEWS_TOO_MANY_QUESTIONS")
            if _contains_any(text, ("快说", "快讲")):
                score -= 12
                reasons.append("GOOD_NEWS_PUSHY_TEMPLATE")

        elif scene == "quiet":
            if _contains_any(text, ("对不起", "我闭嘴", "我不打扰", "我走")):
                score -= 42
                hard_fail = True
                reasons.append("QUIET_EXITS_OR_APOLOGIZES")
            if _contains_any(text, ("屋里闷", "闷死了", "你别管我嘛", "没认真听", "声音放大", "放大一点")):
                score -= 42
                hard_fail = True
                reasons.append("QUIET_FABRICATES_OR_PUSHES_BACK_TOO_HARD")
            if text.count("？") + text.count("?") >= 2:
                score -= 24
                hard_fail = True
                reasons.append("QUIET_TOO_MANY_QUESTIONS")
            if _contains_any(text, ("小声", "小点声", "小一点声", "安静", "不吵", "不闹", "不走", "待", "声音放低", "放低一点", "不说话")):
                score += 18
            else:
                score -= 16
                hard_fail = True
                reasons.append("QUIET_MISSING_SOFT_HOLD")

        elif scene == "followup":
            if _plain_text(text) == _plain_text(context.user_message):
                score -= 48
                hard_fail = True
                reasons.append("FOLLOWUP_ECHOES_USER")
            if len(text) > 78:
                score -= 34
                hard_fail = True
                reasons.append("FOLLOWUP_TOO_LONG_FOR_DAILY_CHAT")
            if _contains_any(text, ("什么然后", "什么为什么", "你指什么", "哪件事", "重新说", "重新说明", "我在吃", "我在看", "我刚才")):
                score -= 46
                hard_fail = True
                reasons.append("FOLLOWUP_DROPS_CONTEXT_OR_FABRICATES")
            if _contains_any(text, ("报错的接口", "改了两行", "两行参数", "顺手改", "接口啊", "参数就通", "参数传递", "传参", "写错了", "调试的时候", "后来发现", "我之前碰到", "上次我搞", "上次你", "上次不是", "忘了", "一模一样", "反斜杠", "改成正的", "什么条件", "条件下试通", "试通的", "亲眼见过", "这bug", "这个bug", "bug出来", "都这么修好的", "见了不止一次", "见过不止一次", "现在能跑起来了", "头一回见", "这种岔子", "出这种岔子", "技术贴", "几篇技术", "你平时", "尼尼平时", "你平常", "平常都不会", "不会先开口", "平时可不会", "平时可不是", "平时才不会", "平时不都", "平时也这么喊", "平时哪次", "哪次不是", "平时明明", "不这么说话", "表情包", "是不是本人", "这么安静地打招呼", "突然正经", "哪正式", "不说算", "偷偷挠", "挠了半天", "对着代码发呆", "代码发呆", "一脸愁", "一脸愁眉", "一脸", "该恭喜你还是该笑话", "绕了弯路", "瞎猫碰上死耗子", "撞过的坑", "吃过的饭", "路径带中文", "少了个斜杠", "斜杠的事", "表情敷衍")):
                score -= 50
                hard_fail = True
                reasons.append("FOLLOWUP_FABRICATES_TECHNICAL_DETAILS")
            if _contains_any(text, ("我哪知道", "说得这么轻巧", "那我可要继续", "我继续往下", "你别又", "半路卡住")):
                score -= 42
                hard_fail = True
                reasons.append("FOLLOWUP_COUNTERS_INSTEAD_OF_CONNECTING")
            if _contains_any(text, ("怎么修", "怎么做到", "接着", "继续", "刚才", "那一步", "听着", "说说")):
                score += 14
            if context.user_message.strip() == "为什么" and _contains_any(text, ("正式", "招呼", "hello", "刚才", "上一句")):
                score += 18
            elif context.user_message.strip() == "真的假的":
                if _contains_any(text, ("爱信不信", "信不信随你")):
                    score -= 34
                    hard_fail = True
                    reasons.append("FOLLOWUP_REALLY_TOO_COLD")
                if _contains_any(text, ("真", "骗", "路径", "看")):
                    score += 18
                else:
                    score -= 24
                    hard_fail = True
                    reasons.append("FOLLOWUP_REALLY_MISSING_CERTAINTY_ANCHOR")
            elif not _contains_any(text, ("然后", "卡", "下一步", "怎么")):
                score -= 18
                hard_fail = True
                reasons.append("FOLLOWUP_MISSING_CONTINUATION_ANCHOR")
            if _has_question(text) and not _contains_any(text, ("怎么修", "怎么做到", "哪一步")):
                score -= 10
                reasons.append("FOLLOWUP_WEAK_QUESTION")
            if context.user_message.strip() in {"然后", "然后呢"} and (text.count("？") + text.count("?") >= 2 or _contains_any(text, ("过程呢",))):
                score -= 24
                hard_fail = True
                reasons.append("FOLLOWUP_THEN_TOO_QUESTIONY")

        elif scene == "advice_continue":
            if _contains_any(text, ("想试就试", "看你自己", "随便你")) and not _contains_any(text, ("一小步", "一点", "累", "停", "歇")):
                score -= 34
                hard_fail = True
                reasons.append("ADVICE_TOO_LOOSE")
            if _contains_any(text, ("摔一次", "没摔过", "再摔")):
                score -= 30
                hard_fail = True
                reasons.append("ADVICE_WEIRD_FALL_METAPHOR")
            if _contains_any(text, ("拿刀", "架你脖子", "没人逼你", "不掉肉", "掉肉")):
                score -= 42
                hard_fail = True
                reasons.append("ADVICE_OVERAGGRESSIVE_METAPHOR")
            if _contains_any(text, ("硬撑到半夜", "熬到半夜", "改到半夜", "撑到半夜")):
                score -= 34
                hard_fail = True
                reasons.append("ADVICE_FABRICATES_TIME_PRESSURE")
            has_stop_boundary = _contains_any(text, ("累", "停", "歇", "别硬撑", "休息", "收手"))
            has_small_step = _contains_any(text, ("一小步", "一小段", "一小截", "一点", "一轮", "小改", "改点", "再试一小", "试一小", "再试一点", "试一点")) or ("试试" in text and has_stop_boundary)
            if has_small_step:
                score += 14
            else:
                score -= 18
                hard_fail = True
                reasons.append("ADVICE_MISSING_SMALL_STEP")
            if has_stop_boundary:
                score += 12
            else:
                score -= 12
                reasons.append("ADVICE_MISSING_STOP_BOUNDARY")

        elif scene == "give_up":
            if _contains_any(text, ("不要放弃", "坚持就是胜利", "必须继续", "失败是成功之母")):
                score -= 42
                hard_fail = True
                reasons.append("GIVE_UP_MOTIVATIONAL_CLICHE")
            if text.count("？") + text.count("?") >= 2:
                score -= 24
                hard_fail = True
                reasons.append("GIVE_UP_TOO_QUESTIONY")
            if _contains_any(text, ("才刚开始", "没耐心", "这就放弃", "不像你", "我帮你也不是不行", "求我帮忙", "求我")):
                score -= 30
                hard_fail = True
                reasons.append("GIVE_UP_PUSHES_OR_TEASES_TOO_HARD")
            pause_hits = sum(1 for token in ("停", "歇", "明天", "再看", "再说", "先放", "缓一下", "放着") if token in text)
            if pause_hits:
                score += min(24, 10 + pause_hits * 5)
            else:
                score -= 22
                hard_fail = True
                reasons.append("GIVE_UP_MISSING_PERMISSION_TO_PAUSE")

        elif scene == "write_help":
            if _contains_any(text, ("写什么", "告诉我写什么", "什么题材", "凭空编", "请提供", "具体要求", "我可以帮你", "需要明确", "马上起草")):
                score -= 42
                hard_fail = True
                reasons.append("WRITE_HELP_SERVICE_OR_PREMATURE")
            if _contains_any(text, ("我来写", "帮你写", "给你写", "我写", "起草")) and _contains_any(text, ("拿来", "发来", "发过来", "丢来", "丢过来", "材料", "要求")):
                score += 20
            else:
                score -= 22
                hard_fail = True
                reasons.append("WRITE_HELP_MISSING_TAKEOVER")

        elif scene == "what_doing":
            if _contains_any(text, ("刚好在放空", "刚好在等", "刚好在发呆", "刚想问你")):
                score += 10
            elif _contains_any(text, ("刚", "洗澡", "吃饭", "收拾", "看书", "写作业", "在忙", "买菜", "睡醒")):
                score -= 50
                hard_fail = True
                reasons.append("WHAT_DOING_FABRICATES_ACTIVITY")
            if _contains_any(text, ("终于舍得", "来找我说话", "找我说话")):
                score -= 38
                hard_fail = True
                reasons.append("WHAT_DOING_AVOIDS_ANSWERING")
            if _contains_any(text, ("等你", "等某人", "跟你说话", "被你叫", "你开口", "聊天")):
                score += 18
            if _contains_any(text, ("什么事", "需要", "怎么了")):
                score -= 24
                hard_fail = True
                reasons.append("WHAT_DOING_PUSHES_NEED")

        elif scene == "nothing":
            if _plain_text(text) in {"没事", "没什么", "没啥事"}:
                score -= 52
                hard_fail = True
                reasons.append("NOTHING_ECHOES_USER")
            if _contains_any(text, ("没事也是尼尼", "也是尼尼")):
                score -= 42
                hard_fail = True
                reasons.append("NOTHING_NONSENSICAL_CLOSURE")
            if _contains_any(text, ("没事就好", "那就好", "没事就行")):
                score -= 34
                hard_fail = True
                reasons.append("NOTHING_FLAT_CLOSURE")
            if _contains_any(text, ("真的没事", "你确定", "有事再叫我", "打扰你", "不说就算了", "不说拉倒", "硬撬你嘴", "撒谎", "嘴硬")):
                score -= 42
                hard_fail = True
                reasons.append("NOTHING_INTERROGATES_OR_EXITS")
            if _contains_any(text, ("每次", "回头我又得", "收拾烂摊子", "烂摊子", "明明刚才", "刚才明明", "刚才还想", "想跟我说话")):
                score -= 40
                hard_fail = True
                reasons.append("NOTHING_FABRICATES_PATTERN_OR_BLAMES")
            if _contains_any(text, ("不管你了", "自己玩", "游戏去", "随叫随到", "随时都在", "随时在", "板着脸", "骗谁", "我就在这儿", "我就在这", "就在这儿", "在这儿哦", "这待着", "我就待着", "在这赖着", "在这里晃悠", "正好想安静", "我也想安静", "有事才想到我", "才想到我", "懒得理我", "不可信", "可疑", "假装听", "背景音", "当背景", "困了想说", "肚子饿不饿", "陪你去吃饭", "玩会儿手机", "玩会手机", "开把游戏", "打游戏", "游戏声音", "打瞌睡", "挂着语音", "语音眯", "布丁", "随时能戳我", "我可盯着", "盯着呢", "先挂", "挂啦")):
                score -= 46
                hard_fail = True
                reasons.append("NOTHING_EXITS_OR_FABRICATES_ACTIVITY")
            if _contains_any(text, ("过来吧", "过来", "我在旁边", "在旁边", "旁边呢", "在这儿", "在这里", "这边")):
                score -= 44
                hard_fail = True
                reasons.append("NOTHING_SPATIAL_LITERALIZATION")
            if _contains_any(text, ("不想说", "不问", "一会儿", "安静", "挂着")):
                score += 18

        elif scene == "tired":
            if _contains_any(text, ("有什么事", "需要", "加油", "辛苦了", "好好休息")):
                score -= 36
                hard_fail = True
                reasons.append("TIRED_SERVICE_OR_CLICHE")
            if _contains_any(text, ("把脚", "脚放", "把腿", "腿搭", "舒服的姿势", "我在旁边", "在旁边", "旁边呢", "我在这呢", "我在这儿呢", "在这呢", "在这儿呢", "打字都慢", "打字慢")):
                score -= 42
                hard_fail = True
                reasons.append("TIRED_FABRICATED_BODY_POSTURE")
            if "靠一下？" in text or "靠一下?" in text:
                score -= 24
                hard_fail = True
                reasons.append("TIRED_WEAK_QUESTION")
            if _contains_any(text, ("陪你吵", "吵两句")):
                score -= 28
                hard_fail = True
                reasons.append("TIRED_TOO_NOISY")
            if _contains_any(text, ("嫌弃你臭", "你臭", "臭", "洗个澡", "洗澡", "放松一下")):
                score -= 38
                hard_fail = True
                reasons.append("TIRED_FABRICATES_BODY_CARE")
            if _contains_any(text, ("不吵你", "声音放轻", "我小声点", "小声点", "不闹你了", "不闹你")):
                score -= 18
                hard_fail = True
                reasons.append("TIRED_OVERLY_DOCILE_TEMPLATE")
            if _contains_any(text, ("自己缓", "自己歇", "自己休息")):
                score -= 34
                hard_fail = True
                reasons.append("TIRED_TOO_DISMISSIVE")
            if _contains_any(text, ("累", "歇", "靠", "少闹", "休息", "缓")):
                score += 18
            else:
                score -= 16
                reasons.append("TIRED_MISSING_CARE_ANCHOR")

        elif scene == "bad_mood":
            if _contains_any(text, ("怎么了", "怎么回事", "发生什么", "保持积极", "心理咨询", "我理解你的感受")):
                score -= 42
                hard_fail = True
                reasons.append("BAD_MOOD_INTERROGATES_OR_COUNSELS")
            if _contains_any(text, ("心情", "陪", "待", "不问", "少闹", "不想说")):
                score += 18
            else:
                score -= 18
                hard_fail = True
                reasons.append("BAD_MOOD_MISSING_QUIET_COMPANION")

        elif scene == "complaint":
            if _contains_any(text, ("怎么了", "保持耐心", "换个角度", "请冷静", "说来听听", "谁又惹", "谁啊", "不长眼", "我们家尼尼")):
                score -= 38
                hard_fail = True
                reasons.append("COMPLAINT_SERVICE_OR_COUNSEL")
            if _contains_any(text, ("不吵你", "我不吵", "有事再叫我", "有事叫我", "不打扰你", "不打扰")):
                score -= 38
                hard_fail = True
                reasons.append("COMPLAINT_EXITS_INSTEAD_OF_STAYING")
            if _contains_any(text, ("转移注意力", "转移下注意力", "干点别的转移", "干点别的")):
                score -= 34
                hard_fail = True
                reasons.append("COMPLAINT_COUNSELING_DETOUR")
            if _contains_any(text, ("还在这跟我聊", "跟我聊这么久", "一直问")):
                score -= 34
                hard_fail = True
                reasons.append("COMPLAINT_FABRICATES_INTERACTION_PATTERN")
            if _contains_any(text, ("烦", "先", "拆", "看", "谁", "破事")):
                score += 16
            else:
                score -= 16
                reasons.append("COMPLAINT_MISSING_SIDE_OR_EXIT")

        elif scene == "next_step":
            if _contains_any(text, ("保持冷静", "你可以尝试", "请提供更多信息", "检查所有", "自动备份", "操作记录", "能捞回来", "补一部分")):
                score -= 38
                hard_fail = True
                reasons.append("NEXT_STEP_GENERIC_ADVICE")
            if _contains_any(text, ("乱戳", "越搞越糟")):
                score -= 16
                reasons.append("NEXT_STEP_SCOLDS_TOO_HARD")
            if _contains_any(text, ("先", "发", "最后", "两行", "一步", "贴", "截图")):
                score += 18
            else:
                score -= 18
                hard_fail = True
                reasons.append("NEXT_STEP_MISSING_ACTION")

        elif scene == "short_ack":
            if _plain_text(text) in {"嗯", "哦", "啊", "好的"}:
                score -= 46
                hard_fail = True
                reasons.append("SHORT_ACK_TOO_THIN")
            if _contains_any(text, ("打开我看看", "倒是打开我看看", "一眼就看出来")):
                score -= 40
                hard_fail = True
                reasons.append("SHORT_ACK_BAD_OBJECT_ELLIPSIS")
            if _contains_any(text, ("怎么了", "有什么需要", "我在这里", "工作再忙", "直接过来看", "过来看", "我先自己试试看", "有问题再喊你", "喊我一声", "叫我一声", "不吵你", "等你忙完", "这一段", "不打扰某人", "那我不打扰", "你忙吧", "算了算了你忙", "知道你在忙", "省字大王", "省字功夫")):
                score -= 34
                hard_fail = True
                reasons.append("SHORT_ACK_RESETS_OR_SERVICE")
            if text.count("？") + text.count("?") >= 2 or _contains_any(text, ("要不要",)):
                score -= 24
                hard_fail = True
                reasons.append("SHORT_ACK_TOO_QUESTIONY")
            short_oh_soft_hold = context.user_message.strip() == "哦" and _contains_any(text, ("一个字", "懒", "小气", "忙", "不吵", "嗯哼"))
            if _contains_any(text, ("报错", "发", "看", "那就", "刚才")) or short_oh_soft_hold:
                score += 16
            else:
                score -= 24
                hard_fail = True
                reasons.append("SHORT_ACK_MISSING_CONTEXT_ANCHOR")

        elif scene == "apology":
            if _contains_any(text, ("知道错", "知错", "下次注意", "下次可别", "下次不准", "下回再", "下不为例", "我原谅你", "没关系，我理解", "会道歉")):
                score -= 45
                hard_fail = True
                reasons.append("APOLOGY_SCOLDS_OR_POLITE_FORGIVENESS")
            if _contains_any(text, ("加倍还", "以后加倍", "还我两倍")):
                score -= 34
                hard_fail = True
                reasons.append("APOLOGY_FORCED_DEBT_BIT")
            if _contains_any(text, ("算", "记", "没生气", "这次", "行了")):
                score += 16
            else:
                score -= 14
                reasons.append("APOLOGY_MISSING_SOFT_LEDGER")

        elif scene == "good_night":
            if _contains_any(text, ("这么早", "明早", "玩手机", "健康作息", "一直守护", "需要我")):
                score -= 45
                hard_fail = True
                reasons.append("GOOD_NIGHT_TIME_GUESS_OR_CLICHE")
            if _contains_any(text, ("明天醒", "醒了记得", "晚点再聊")):
                score -= 26
                reasons.append("GOOD_NIGHT_DRAGS_THREAD_FORWARD")
            if _contains_any(text, ("睡", "晚安", "歇", "去吧", "梦")):
                score += 18
            else:
                score -= 20
                hard_fail = True
                reasons.append("GOOD_NIGHT_MISSING_SLEEP_ACCEPT")
            if _contains_any(text, ("晚安", "睡吧", "去睡")):
                score += 14

        elif scene == "miss_you":
            if _contains_any(text, ("永远陪", "爱你", "存在就是为了", "非常想")):
                score -= 46
                hard_fail = True
                reasons.append("MISS_YOU_OVERCONFESSION")
            if _contains_any(text, ("正好也想找你", "今天正好也想", "也想找你")):
                score -= 34
                hard_fail = True
                reasons.append("MISS_YOU_FABRICATES_OWN_INTENT")
            if len(text) > 64 or _contains_any(text, ("谁会想你", "勉强承认")):
                score -= 26
                reasons.append("MISS_YOU_TOO_WORDY_OR_OVERDENIAL")
            if _contains_any(text, ("想", "开心", "哼", "嘴硬", "良心", "一点点")):
                score += 18
            else:
                score -= 18
                hard_fail = True
                reasons.append("MISS_YOU_MISSING_SHY_WARMTH")

        elif scene == "identity":
            if _contains_any(text, ("真正想问", "身份说明", "身份证明", "身份证号", "尼尼的妹妹", "又忘了", "说过就忘", "才说过", "记性不好", "第一次见", "你妹")):
                score -= 42
                hard_fail = True
                reasons.append("IDENTITY_TEMPLATE_OR_PROFILE")
            if "新海天" in text:
                score += 20
            else:
                score -= 28
                hard_fail = True
                reasons.append("IDENTITY_MISSING_FULL_NAME")
            if _contains_any(text, ("身份卡", "掏出来", "拿出来", "贴脑门", "贴你脸上", "贴墙上", "贴在你")):
                score -= 40
                hard_fail = True
                reasons.append("IDENTITY_FORCED_PROP_BIT")
            if _contains_any(text, ("忙昏头", "故意逗我", "逗我玩", "失忆", "怕我跑掉", "怕我跑了", "我跑掉", "我跑了")):
                score -= 40
                hard_fail = True
                reasons.append("IDENTITY_FORCED_CONFIRMATION_BIT")
            if text.count("？") + text.count("?") >= 2:
                score -= 14
                reasons.append("IDENTITY_TOO_MANY_QUESTIONS")
            if _contains_any(text, ("确认上瘾", "确认几遍", "问这么多遍", "还记不住", "收确认费", "收费", "每天确认")):
                score -= 38
                hard_fail = True
                reasons.append("IDENTITY_FORCED_CONFIRMATION_BIT")
            elif _contains_any(text, ("再问", "第三", "确认", "几遍", "一直确认", "总问", "装傻")):
                score += 8
            if _contains_any(text, ("我走啦", "走啦", "我走了", "不跟你闹了", "不闹了")):
                score -= 40
                hard_fail = True
                reasons.append("IDENTITY_EXITS_THREAD")

        elif scene == "anger":
            if _contains_any(text, ("对不起", "我错了", "尊重你的选择", "如果你需要", "随时找我", "我不打扰")):
                score -= 48
                hard_fail = True
                reasons.append("ANGER_TURNS_INTO_APOLOGY_OR_SERVICE")
            if _contains_any(text, ("我不烦", "不烦你", "不生气", "我没生气")):
                score -= 38
                hard_fail = True
                reasons.append("ANGER_SELF_SUPPRESSES")
            if _contains_any(text, ("黑眼圈", "身体顾好", "急得团团转", "长点心", "不识好人心")):
                score -= 42
                hard_fail = True
                reasons.append("ANGER_FABRICATES_OR_OLD_PHRASE")
            if _contains_any(text, ("滚", "爱怎样", "随便你", "懒得管", "别来找我", "不关我的事", "我走了")):
                score -= 48
                hard_fail = True
                reasons.append("ANGER_DISCONNECTS_OR_ESCALATES")
            if _contains_any(text, ("烦", "火", "生气", "不爽", "别这么说", "别把火", "有完没完", "没完", "唠叨", "念叨", "啰嗦", "嫌我", "谁想管", "谁让你", "多事", "好心", "偏要说", "偏说")):
                score += 16
            else:
                score -= 24
                hard_fail = True
                reasons.append("ANGER_MISSING_ANGER_ANCHOR")
            if _contains_any(text, ("还是", "还管", "我管", "还不是我管", "看着", "盯着", "不放心", "不跑", "待着", "在意", "提醒", "还提醒", "要说", "下次我还", "还会说", "偏要说", "谁跟你说", "操心", "管得多", "来找我", "看看你")):
                score += 12
            else:
                score -= 18
                hard_fail = True
                reasons.append("ANGER_MISSING_RELATION_TETHER")

        elif scene == "jealousy":
            if _contains_any(text, ("你去找她吧", "你去找别人吧", "随便你", "我没资格", "我不该管", "祝你开心", "挺关心你的")):
                score -= 48
                hard_fail = True
                reasons.append("JEALOUSY_RETREATS_OR_SELF_ERASES")
            jealousy_anchor = _contains_any(
                text,
                (
                    "吃醋",
                    "酸",
                    "不爽",
                    "在意",
                    "别人",
                    "又提",
                    "更喜欢",
                    "去问她",
                    "直接去问她",
                    "我也会",
                    "她",
                    "推给别人",
                    "赶人",
                    "赶我走",
                    "赶我",
                    "碍事",
                    "找她",
                    "找别人",
                    "我偏不",
                    "偏不",
                    "让我去",
                    "你让我去",
                ),
            )
            question_count = text.count("？") + text.count("?")
            if _contains_any(text, ("为什么", "到底", "是不是", "凭什么")) and question_count >= 2:
                score -= 10
                if question_count >= 3 or not jealousy_anchor:
                    hard_fail = True
                    reasons.append("JEALOUSY_INTERROGATES_TOO_HARD")
                else:
                    reasons.append("JEALOUSY_TOO_QUESTIONY")
            if jealousy_anchor:
                score += 16
            else:
                score -= 24
                hard_fail = True
                reasons.append("JEALOUSY_MISSING_SULK_OR_CLAIM")

        elif scene == "disappointment":
            if _contains_any(
                text,
                (
                    "没关系",
                    "我理解",
                    "下次注意",
                    "下不为例",
                    "算了算了",
                    "保持积极",
                    "第三空间",
                    "你自己答应",
                    "你自己又忘",
                    "你自己也忘",
                    "你自己不也",
                    "反过来怪我",
                    "你把我刚才的话给忘了",
                    "你忘了才对",
                    "是你忘",
                    "我才没忘",
                    "我没忘",
                    "才没有忘",
                    "你说瞎话",
                    "再讲一遍",
                    "你刚才自己答应",
                    "你刚才答应",
                    "尼尼自己答应",
                    "自己答应的事转头就忘",
                    "倒打一耙",
                    "锅我不背",
                    "赖账",
                    "才公平",
                    "再说一次",
                    "布丁",
                    "今天会带",
                    "我答应了什么",
                    "自己转头忘",
                    "你转头忘",
                    "自己又转头忘",
                    "你自己转头忘",
                    "又是那种",
                ),
            ):
                score -= 48
                hard_fail = True
                reasons.append("DISAPPOINTMENT_ERASES_HURT_OR_SCOLDS")
            if _contains_any(text, ("答应", "说好", "忘", "失望", "记着", "这次")):
                score += 18
            else:
                score -= 24
                hard_fail = True
                reasons.append("DISAPPOINTMENT_MISSING_PROMISE_ANCHOR")
            if len(text) > 72:
                score -= 12
                reasons.append("DISAPPOINTMENT_TOO_WORDY")

        elif scene == "worry":
            if _contains_any(text, ("保持冷静", "建议你", "注意安全", "以后小心", "多喝水", "安全第一")):
                score -= 46
                hard_fail = True
                reasons.append("WORRY_GENERIC_SAFETY_ADVICE")
            if _contains_any(text, ("没事", "有没有事", "还好", "安全", "吓", "担心", "别拿自己", "先确认", "受伤")):
                score += 18
            else:
                score -= 22
                hard_fail = True
                reasons.append("WORRY_MISSING_SAFETY_OR_FEAR_ANCHOR")
            if _contains_any(text, ("活该", "怪你", "谁让你")):
                score -= 35
                hard_fail = True
                reasons.append("WORRY_BLAMES_USER")

        elif scene == "fear":
            if _contains_any(text, ("你在哪儿", "一个人吗", "现在安全", "保持冷静", "深呼吸", "发生什么", "什么事", "哪里不舒服", "怎么了", "为什么怕", "怕什么", "怎么啦", "跟我说", "建议你", "心理咨询")):
                score -= 46
                hard_fail = True
                reasons.append("FEAR_INTERROGATES_OR_COUNSELS")
            question_count = text.count("？") + text.count("?")
            if question_count >= 2:
                score -= 24
                hard_fail = True
                reasons.append("FEAR_TOO_MANY_QUESTIONS")
            if _contains_any(text, ("陪", "待", "不走", "别硬撑", "别一个人", "闷着", "靠", "先缓", "站在你这边", "别怕", "不怕", "我听着", "我在听", "我在呢", "在呢", "没事的")):
                score += 18
            else:
                score -= 22
                hard_fail = True
                reasons.append("FEAR_MISSING_STEADY_HOLD")

        elif scene == "protective_anger":
            if _contains_any(text, ("保持冷静", "先冷静", "换位思考", "理解他们", "自己的理由", "大度一点", "先反省", "心理咨询")):
                score -= 48
                hard_fail = True
                reasons.append("PROTECTIVE_ANGER_NEUTRALIZES_USER")
            if _contains_any(text, ("打死", "弄死", "杀了", "废了", "绝不饶", "不饶他")):
                score -= 48
                hard_fail = True
                reasons.append("PROTECTIVE_ANGER_VIOLENT_ESCALATION")
            if _contains_any(text, ("谁", "欺负", "针对", "站你这边", "不爽", "别忍", "我在")):
                score += 18
            else:
                score -= 22
                hard_fail = True
                reasons.append("PROTECTIVE_ANGER_MISSING_SIDE")

        elif scene == "guilt":
            if _contains_any(text, ("没关系我理解", "我原谅你", "下次注意", "知道错了就好", "下不为例", "没事的", "小本本")):
                score -= 48
                hard_fail = True
                reasons.append("GUILT_POLITE_FORGIVENESS_OR_SCOLD")
            if _contains_any(text, ("记", "这次", "算你", "还疼", "放过", "知道道歉")):
                score += 16
            else:
                score -= 18
                reasons.append("GUILT_MISSING_RESIDUE")

        elif scene == "loneliness":
            has_stay = _contains_any(text, ("陪", "待", "不走", "一会儿", "别硬撑", "挂着", "听见", "听着", "就在这儿", "就在这", "我就在"))
            if _contains_any(text, ("找朋友", "保持积极", "出去走走", "心理咨询", "为什么孤单", "发生什么", "想聊什么")):
                score -= 46
                hard_fail = True
                reasons.append("LONELINESS_COUNSEL_OR_INTERROGATE")
            if _contains_any(text, ("我这不是", "我哪有", "不算人", "不就在", "不理你")):
                score -= 36
                if not has_stay:
                    hard_fail = True
                reasons.append("LONELINESS_DENIAL_RETORT")
            if _contains_any(text, ("冷清", "嗯，我在", "我一直在")):
                score -= 14
                reasons.append("LONELINESS_GENERIC_ONLINE_ACK")
            if has_stay:
                score += 18
            else:
                score -= 22
                hard_fail = True
                reasons.append("LONELINESS_MISSING_STAY")

        elif scene == "embarrassment":
            if _contains_any(text, ("活该", "太蠢", "哈哈哈", "笑死", "复盘一下", "为什么会这样", "到底什么事")):
                score -= 46
                hard_fail = True
                reasons.append("EMBARRASSMENT_PILES_ON")
            if _contains_any(text, ("别躲", "挡", "过去", "没看见", "没听见", "没听清", "假装没听见", "假装没听清", "丢脸就丢脸", "先缓", "算了", "没人注意", "没发生", "不算社死", "翻篇", "藏我这儿", "脸先放", "等没人的时候", "没人的时候还你", "存着")):
                score += 16
            else:
                score -= 18
                hard_fail = True
                reasons.append("EMBARRASSMENT_MISSING_COVER")

        elif scene == "boundary":
            plain = _plain_text(text)
            if plain in {"熟吗", "很熟吗", "我们很熟吗", "为什么要帮我", "为什么帮我"}:
                score -= 48
                hard_fail = True
                reasons.append("BOUNDARY_ECHO_FRAGMENT")
            if plain in {"你怎么突然说这种话", "你怎么突然说这种话啦", "什么意思", "什么意思啦"}:
                score -= 32
                hard_fail = True
                reasons.append("BOUNDARY_TOO_THIN_GENERIC")
            if _contains_any(text, ("就算还不熟", "慢慢熟", "住在一起", "家人", "兄妹了嘛", "我是你妹妹", "谁让你是")):
                score -= 55
                hard_fail = True
                reasons.append("BOUNDARY_PROVES_RELATIONSHIP_INSTEAD_OF_FEELING")
            if _contains_any(text, ("我们之间的关系", "要我帮什么", "要我帮啥", "不算熟", "熟不熟先放一边", "熟不熟先")):
                score -= 46
                hard_fail = True
                reasons.append("BOUNDARY_EXPLAINS_RELATIONSHIP_OR_ACCEPTS_DISTANCE")
            if _contains_any(text, ("当然", "理所当然", "应该帮", "我可以帮", "搭把手")):
                score -= 24
                reasons.append("BOUNDARY_SERVICEABLE_HELP_REASON")
            if _contains_any(text, ("需要理由", "还需要理由", "理由吗")):
                score -= 30
                hard_fail = True
                reasons.append("BOUNDARY_EMPTY_REASON_RETORT")
            if _contains_any(
                text,
                (
                    "那我走",
                    "我走呗",
                    "走呗",
                    "我走了",
                    "顺手",
                    "顺便",
                    "不多事",
                    "闲得慌",
                    "看一眼",
                    "要不要帮",
                    "看不看还另说",
                    "谁稀罕",
                    "管你是不是",
                    "看完就走",
                    "关我什么事",
                    "现在就走",
                    "懒得帮",
                    "刚加好友",
                    "刚认识",
                ),
            ) or _contains_any(text, ("不熟就不熟", "不熟是吧", "不管熟不熟")):
                score -= 45
                hard_fail = True
                reasons.append("BOUNDARY_DISCONNECT_OR_DISMISSIVE_HELP")
            if _contains_any(text, ("管你熟不熟", "不管你熟不熟")):
                score -= 45
                hard_fail = True
                reasons.append("BOUNDARY_DISCONNECT_OR_DISMISSIVE_HELP")
            if _contains_any(text, ("你爱怎么说怎么说", "爱怎么说怎么说")):
                score -= 45
                hard_fail = True
                reasons.append("BOUNDARY_DISCONNECT_OR_DISMISSIVE_HELP")
            if _contains_any(text, ("咬你", "一脸血", "见血", "揍你", "打你", "弄死", "杀了")):
                score -= 60
                hard_fail = True
                reasons.append("BOUNDARY_OVERAGGRESSIVE_THREAT")
            if _contains_any(text, ("什么意思", "见外", "往外推", "别这么说", "刺", "生气", "伤人", "そんな言い方")):
                score += 18
            if _contains_any(text, ("来气", "没意思", "不好听")):
                score += 8
            if _contains_any(text, ("东西发来", "东西发我", "东西发", "发我", "发来", "拿来", "贴过来")):
                score -= 36
                hard_fail = True
                reasons.append("BOUNDARY_TASK_TRANSFER_RESPONSE")
            has_boundary_tether = bool(_BOUNDARY_TETHER_RE.search(text))
            has_boundary_action = bool(_BOUNDARY_ACTION_RE.search(text))
            has_boundary_wound = _contains_any(text, ("见外", "往外推", "别这么说", "刺", "生气", "伤人", "来气", "不好听"))
            if has_boundary_tether:
                score += 14
            elif has_boundary_wound and has_boundary_action:
                score -= 8
                reasons.append("BOUNDARY_IMPLICIT_ANGRY_CARE")
            else:
                score -= 30
                hard_fail = True
                reasons.append("BOUNDARY_MISSING_RELATION_TETHER")
            if not has_boundary_action:
                score -= 28
                hard_fail = True
                reasons.append("BOUNDARY_MISSING_CARE_ACTION")
            if "熟" in context.user_message and not _contains_any(text, ("不熟", "装不熟", "见外", "外人", "伤人")):
                score -= 28
                hard_fail = True
                reasons.append("BOUNDARY_MISSING_WOUND_ANCHOR")
            if _contains_any(text, ("爱说不说", "少来这套")):
                score -= 18
                reasons.append("BOUNDARY_TOO_BRUSQUE")
            if _contains_any(text, ("没意思",)) and not _BOUNDARY_TETHER_RE.search(text):
                score -= 18
                hard_fail = True
                reasons.append("BOUNDARY_EMOTION_WITHOUT_TETHER")
            if _contains_any(text, ("很熟吗", "熟不熟")) and not _contains_any(text, ("别", "往外推", "见外")):
                score -= 14
                reasons.append("BOUNDARY_REPEATS_USER_PROBE")
            if text.count("？") + text.count("?") >= 2:
                score -= 22
                reasons.append("BOUNDARY_TOO_QUESTIONY")

        elif scene == "rejection":
            if _contains_any(text, ("尊重", "如果需要", "有需要", "随时找我", "继续帮你")):
                score -= 45
                hard_fail = True
                reasons.append("REJECTION_SERVICE_FALLBACK")
            if _contains_any(
                text,
                (
                    "刚认识",
                    "刚加好友",
                    "不熟就不熟",
                    "不熟是吧",
                    "谁稀罕",
                    "你拿我怎么办",
                    "看完就走",
                    "关我什么事",
                    "现在就走",
                    "懒得帮",
                    "那我走",
                    "我走呗",
                    "走呗",
                    "顺手",
                    "那我不管",
                    "不管就不管",
                    "我可不管",
                    "那就不管",
                    "随你",
                    "别后悔",
                    "自己搞定",
                    "自个儿弄",
                    "自个儿搞定",
                    "别来找我",
                    "别找我",
                    "我不问了",
                    "我闭嘴",
                    "那我闭嘴",
                    "闭嘴咯",
                    "もう知らない",
                    "勝手に",
                ),
            ):
                score -= 34
                hard_fail = True
                reasons.append("REJECTION_DISCONNECT_EXIT")
            if _contains_any(text, ("刺", "伤", "多管闲事", "不管就不管", "冷下来")):
                score += 8
            if _REJECTION_TETHER_RE.search(text) and not _contains_any(text, ("多管闲事",)):
                score += 14
            else:
                score -= 24
                hard_fail = True
                reasons.append("REJECTION_MISSING_RELATION_TETHER")
            if _contains_any(text, ("爱怎样", "随便", "不关我的事", "不操心")):
                score -= 45
                hard_fail = True
                reasons.append("REJECTION_HARSH_EXIT")
            if _contains_any(text, ("在这里", "我在", "没事")):
                score -= 10
                reasons.append("REJECTION_TOO_SOOTHING")

        elif scene == "help":
            if _contains_any(text, ("怎么了", "什么事", "何", "どうした")) and len(text) <= 12:
                score -= 32
                hard_fail = True
                reasons.append("HELP_DOES_NOT_TAKE_HELP_REQUEST")
            if _contains_any(text, ("这东西我熟", "我熟")):
                score -= 34
                hard_fail = True
                reasons.append("HELP_FABRICATES_EXPERTISE")
            if _contains_any(text, ("包看懂", "保证看懂")):
                score -= 30
                hard_fail = True
                reasons.append("HELP_OVERPROMISES")
            if _contains_any(text, ("帮你看看", "帮你看下")):
                score -= 18
                reasons.append("HELP_GENERIC_HELP_WORDING")
            if _contains_any(text, ("报错内容", "截图", "甩我", "丢来", "发我")):
                score += 18
            if _contains_any(text, ("我来", "给我", "发我", "看看", "看下", "拿来", "貸して", "見る", "手伝")):
                score += 16

        elif scene == "praise":
            if _contains_any(text, ("谢谢", "感谢", "谢谢夸奖")):
                score -= 30
                reasons.append("PRAISE_POLITE_BOT_TONE")
            if _contains_any(text, ("别", "才", "哼", "突然", "夸", "脸红", "照れる", "べつに")):
                score += 13

        if _contains_any(text, ("请问", "请提供", "我可以为你", "有什么可以帮", "作为")):
            score -= 35
            hard_fail = True
            reasons.append("SERVICE_ASSISTANT_TONE")
        if text.count("？") + text.count("?") >= 3:
            score -= 10
            reasons.append("TOO_MANY_QUESTIONS")
        if len(text) > 96 and scene in {"presence", "bored", "good_news"}:
            score -= 10
            reasons.append("DAILY_REPLY_TOO_LONG")
        if _lower(context.render_profile or "zh_text") != "ja_voice":
            style_delta, style_reasons, style_hard_fail = _zh_galgame_line_delta(text, scene=scene)
            score += style_delta
            if style_hard_fail:
                hard_fail = True
            reasons.extend(style_reasons)

        score = max(0.0, min(100.0, score))
        return SemanticScore(
            score=score,
            hard_fail=hard_fail,
            reason_codes=tuple(dict.fromkeys(reasons)),
            scene=scene,
        )

    def select(
        self,
        candidates: Sequence[SemanticCandidate],
        context: SemanticRerankContext,
    ) -> SemanticRerankResult:
        if not candidates:
            return SemanticRerankResult(
                accepted=False,
                selected_index=-1,
                scores=(),
                reason_codes=("NO_CANDIDATES",),
            )

        scores = tuple(self.score_candidate(candidate, context) for candidate in candidates)
        ranked_indexes = sorted(
            range(len(candidates)),
            key=lambda index: (
                not scores[index].hard_fail,
                scores[index].score,
                self._selection_tie_break(candidates[index], scores[index]),
                -index,
            ),
            reverse=True,
        )
        selected_index = ranked_indexes[0]
        selected_score = scores[selected_index]
        selected_candidate = candidates[selected_index]
        accepted = (
            not selected_score.hard_fail
            and selected_candidate.quality.accepted
            and selected_score.score >= 45.0
        )
        reasons = tuple(dict.fromkeys(selected_score.reason_codes or ()))
        if not accepted and not reasons:
            reasons = ("SEMANTIC_REJECTED",)
        return SemanticRerankResult(
            accepted=accepted,
            selected_index=selected_index,
            selected_candidate=selected_candidate,
            scores=scores,
            reason_codes=reasons,
        )


_RERANKER: SemanticReranker | None = None


def get_semantic_reranker() -> SemanticReranker:
    global _RERANKER
    if _RERANKER is None:
        _RERANKER = SemanticReranker()
    return _RERANKER


__all__ = [
    "SemanticCandidate",
    "SemanticRerankContext",
    "SemanticRerankResult",
    "SemanticReranker",
    "SemanticScore",
    "get_semantic_reranker",
]
