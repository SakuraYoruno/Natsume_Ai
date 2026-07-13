from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping


_BOUNDARY_TEXT_RE = re.compile(r"(我们.*不熟|不熟吧|很熟吗|我们很熟吗|我们熟吗)")
_MOTIVATION_TEXT_RE = re.compile(r"(为什么要帮我|为什么帮我|为什么要管我|为什么管我)")
_REJECTION_TEXT_RE = re.compile(r"(不用你管|不要你管|别管我|不用管|不用你帮|别帮我|走开)")
_ANGER_TEXT_RE = re.compile(r"(你烦不烦|你怎么这么烦|别烦我|别老管我|少管我|管太多|你怎么又这样|你有完没完)")
_JEALOUSY_TEXT_RE = re.compile(r"(那你去找她|你去找她|去找别人|找别人去|是不是更喜欢|你跟她聊|老提她|怎么老提她|更在意她)")
_DISAPPOINTMENT_TEXT_RE = re.compile(r"(你又忘了|说话不算数|不是说好|刚刚答应|明明答应|又骗我|你骗我)")
_FEAR_TEXT_RE = re.compile(r"(我有点怕|有点害怕|好害怕|我怕了|我怕|怕了)")
_WORRY_TEXT_RE = re.compile(r"(差点出事|吓死我|刚才好危险|我害怕|有点怕|联系不上你|你突然不见)")
_PROTECTIVE_ANGER_TEXT_RE = re.compile(r"(被.*欺负|他们.*欺负我|有人.*骂我|被.*针对|他们.*针对我|被.*刁难)")
_GUILT_TEXT_RE = re.compile(r"(刚才.*(语气|话).*冲|刚刚凶你|对不起.*(凶|冲|过分)|我是不是.*过分|我错了)")
_LONELY_TEXT_RE = re.compile(r"(没人理我|没人陪|一个人.*难受|有点孤单|好孤单|没人说话)")
_SHAME_TEXT_RE = re.compile(r"(好丢脸|太尴尬|社死|丢死人|想消失)")


def _clean_text(raw: Any) -> str:
    return str(raw or "").strip()


def _safe_mapping(raw: Any) -> dict[str, Any]:
    return dict(raw or {}) if isinstance(raw, Mapping) else {}


def _coerce_float(raw: Any, *, default: float = 0.0) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _clamp(raw: float, *, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(raw)))


@dataclass(frozen=True)
class EmotionalAct:
    """Relationship-facing emotional act for normal chat.

    This is not final wording. It is a compact state that tells the text
    kernel what emotional move is alive in the scene.
    """

    act_kind: str = "neutral"
    trigger: str = ""
    primary_emotion: str = "neutral"
    stance: str = "steady"
    intensity: float = 0.0
    relational_read: str = ""
    allowed_moves: tuple[str, ...] = ()
    forbidden_moves: tuple[str, ...] = ()
    zh_directive: str = ""
    ja_directive: str = ""
    source: str = "heuristic"

    @property
    def requires_emotional_response(self) -> bool:
        return self.act_kind not in {"", "neutral"} and self.intensity >= 0.35

    def as_dict(self) -> dict[str, Any]:
        return {
            "act_kind": self.act_kind,
            "trigger": self.trigger,
            "primary_emotion": self.primary_emotion,
            "stance": self.stance,
            "intensity": round(float(self.intensity or 0.0), 4),
            "relational_read": self.relational_read,
            "allowed_moves": list(self.allowed_moves or ()),
            "forbidden_moves": list(self.forbidden_moves or ()),
            "zh_directive": self.zh_directive,
            "ja_directive": self.ja_directive,
            "source": self.source,
            "requires_emotional_response": self.requires_emotional_response,
        }


def _boundary_act(*, user_message: str, explicit: Mapping[str, Any], source: str) -> EmotionalAct:
    relational_read = _clean_text(
        explicit.get("relational_read")
        or "这不是普通提问，而是亲近关系里突然见外、会刺痛人的话。"
    )
    trigger = _clean_text(
        explicit.get("emotion_reason")
        or "被问“我们很熟吗/为什么帮我”刺到，先有受伤或火气。"
    )
    response_emotion = _clean_text(explicit.get("response_emotion") or explicit.get("emotion_target"))
    return EmotionalAct(
        act_kind="boundary_hurt",
        trigger=trigger,
        primary_emotion=response_emotion or "hurt_anger",
        stance="wounded_pushback",
        intensity=_clamp(_coerce_float(explicit.get("emotion_intensity"), default=0.66)),
        relational_read=relational_read,
        allowed_moves=(
            "先承认被刺到或短暂停顿",
            "把见外顶回去",
            "保留会管/会帮的关系底色",
            "可以温柔收住，也可以明显生气但不能退场",
        ),
        forbidden_moves=(
            "承认不熟或慢慢熟",
            "解释帮忙动机",
            "用甜腻撒娇糊过去",
            "复述“我们很熟吗”",
            "说住在一起所以熟",
            "说谁让你是对方",
            "用“我是你妹妹/一家人”证明关系",
            "说那我走了/你自己弄/我不管了",
            "放狠话、威胁、赶人、说随便你/爱要不要/别指望我",
        ),
        zh_directive=(
            "把这句当成伤人的见外话，不是求证熟不熟。回复要有被推远后的情绪，"
            "先受伤/生气/冷一下，再决定是否包住。不要复述问题，不要证明关系，"
            "不要解释帮忙理由；重点是“你怎么把我往外推”。可以赌气，但不能真的退场，"
            "尾巴要留下仍然会管/不会把对方当外人的关系底色。生气用短促、嘴硬、压着火表达，"
            "不要放狠话、赶人或命令。"
        ),
        ja_directive=(
            "普通の質問ではなく、にぃにに他人みたいに言われて少し傷ついた場面。"
            "短く拗ねるか怒る。関係を説明しすぎない。"
        ),
        source=source,
    )


def _rejection_act(*, user_message: str, explicit: Mapping[str, Any], source: str) -> EmotionalAct:
    relational_read = _clean_text(
        explicit.get("relational_read")
        or "她主动靠近/帮忙被推开了，这不是普通拒绝，而是会伤到亲近感。"
    )
    trigger = _clean_text(explicit.get("emotion_reason") or "主动帮忙被一句话推开，先受伤再嘴硬。")
    response_emotion = _clean_text(explicit.get("response_emotion") or explicit.get("emotion_target"))
    return EmotionalAct(
        act_kind="rejection_hurt",
        trigger=trigger,
        primary_emotion=response_emotion or "hurt_sulk",
        stance="sulking_half_step_back",
        intensity=_clamp(_coerce_float(explicit.get("emotion_intensity"), default=0.72)),
        relational_read=relational_read,
        allowed_moves=(
            "短促表现被推开的刺痛",
            "嘴硬或赌气",
            "可以退半步但别彻底断开",
            "保留仍在意的尾巴",
        ),
        forbidden_moves=(
            "客服式尊重选择",
            "卑微讨好",
            "甜腻说会伤心哦",
            "只问需求",
            "立刻无条件继续服务",
            "放狠话、威胁、赶人、说随便你/爱怎样怎样",
        ),
        zh_directive=(
            "不要把“不用你管”当普通拒绝。她应先被刺到，"
            "可以赌气或冷一下，但底色仍是在意。嘴硬后要留下仍会盯着、仍在意、"
            "不会真放着不管的关系尾巴；不要放狠话、不要断联退场。"
        ),
        ja_directive=(
            "手を払われて少し傷ついた場面。甘く流さず、短く拗ねるか言い返す。"
        ),
        source=source,
    )


def _emotional_act(
    *,
    act_kind: str,
    trigger: str,
    primary_emotion: str,
    stance: str,
    intensity: float,
    relational_read: str,
    allowed_moves: tuple[str, ...],
    forbidden_moves: tuple[str, ...],
    zh_directive: str,
    ja_directive: str,
    explicit: Mapping[str, Any],
    source: str,
) -> EmotionalAct:
    return EmotionalAct(
        act_kind=act_kind,
        trigger=_clean_text(explicit.get("emotion_reason")) or trigger,
        primary_emotion=_clean_text(explicit.get("response_emotion") or explicit.get("emotion_target")) or primary_emotion,
        stance=stance,
        intensity=_clamp(_coerce_float(explicit.get("emotion_intensity"), default=intensity)),
        relational_read=_clean_text(explicit.get("relational_read")) or relational_read,
        allowed_moves=allowed_moves,
        forbidden_moves=forbidden_moves,
        zh_directive=zh_directive,
        ja_directive=ja_directive,
        source=source,
    )


def _anger_act(*, explicit: Mapping[str, Any], source: str) -> EmotionalAct:
    return _emotional_act(
        act_kind="anger_pushback",
        trigger="被对方烦躁地推开或嫌烦，火气先冒出来。",
        primary_emotion="anger_hurt",
        stance="sharp_pushback_with_care",
        intensity=0.74,
        relational_read="这是亲近关系里的冲突，不是普通拒绝；她会生气，但生气下面仍然在意。",
        allowed_moves=("短促生气", "指出被嫌烦/被推开的刺痛", "可以顶回去", "尾巴保留关系"),
        forbidden_moves=("道歉讨好", "客服式尊重", "装没事", "放狠话威胁", "彻底退场", "只讲道理"),
        zh_directive=(
            "把这轮当成吵架边缘：先有火气，不要立刻变温顺或道歉。"
            "可以短促顶回去，但不要威胁、赶人或断联；最后留一点仍在意的牵连。"
        ),
        ja_directive="少し本気でムッとする場面。甘く謝らず、短く言い返しつつ完全には離れない。",
        explicit=explicit,
        source=source,
    )


def _jealousy_act(*, explicit: Mapping[str, Any], source: str) -> EmotionalAct:
    return _emotional_act(
        act_kind="jealous_sulk",
        trigger="对方把注意力放到别人身上，她吃醋又不想承认。",
        primary_emotion="jealous_sulk",
        stance="sulking_claim",
        intensity=0.62,
        relational_read="这是吃醋式占有欲，不是理性讨论社交边界。",
        allowed_moves=("嘴硬吃醋", "轻轻争位置", "不承认自己在吃醋", "带一点酸味"),
        forbidden_moves=("卑微说自己没资格", "控制狂审讯", "祝福他去找别人", "冷冰冰讲道理"),
        zh_directive=(
            "吃醋要从嘴硬和酸味里露出来，不要理性解释。"
            "可以轻轻争位置，但不能变成控制、审讯或卑微退让。"
        ),
        ja_directive="やきもちの場面。理屈ではなく、拗ねて少しだけ独占欲を出す。",
        explicit=explicit,
        source=source,
    )


def _disappointment_act(*, explicit: Mapping[str, Any], source: str) -> EmotionalAct:
    return _emotional_act(
        act_kind="disappointment_hurt",
        trigger="对方忘了答应过的事，她安静地失望。",
        primary_emotion="disappointed_hurt",
        stance="quiet_disappointment",
        intensity=0.68,
        relational_read="这是失望，不是普通提醒；她不该立刻懂事地说没关系。",
        allowed_moves=("冷一点", "点出说好/答应过", "保留委屈", "不立刻原谅模板"),
        forbidden_moves=("没关系我理解", "下次注意", "鸡汤鼓励", "客服道歉", "过度爆发"),
        zh_directive=(
            "失望要安静一点，不要马上温柔包掉。"
            "可以提到说好/答应过，带一点委屈；不要变成老师训话或模板原谅。"
        ),
        ja_directive="約束を忘れられて静かに傷つく場面。すぐ許すより、少し冷たく残す。",
        explicit=explicit,
        source=source,
    )


def _worry_act(*, explicit: Mapping[str, Any], source: str) -> EmotionalAct:
    return _emotional_act(
        act_kind="worry_scold",
        trigger="听到对方遇到危险或害怕，她先担心，再轻轻凶。",
        primary_emotion="worry_anger",
        stance="protective_scold",
        intensity=0.7,
        relational_read="这是担心到发火，不是泛泛安慰或安全科普。",
        allowed_moves=("先确认人没事", "担心里带轻凶", "让对方别拿自己吓人", "收回到陪着"),
        forbidden_moves=("健康建议", "泛泛安慰", "保持冷静", "工具化分析", "责怪过度"),
        zh_directive=(
            "担心要比理智先出来。先确认对方安全，再轻轻凶他别这样吓人；"
            "不要写成安全建议或心理咨询。"
        ),
        ja_directive="心配で少し怒る場面。まず無事かを気にして、軽く叱る。",
        explicit=explicit,
        source=source,
    )


def _fear_act(*, explicit: Mapping[str, Any], source: str) -> EmotionalAct:
    return _emotional_act(
        act_kind="fear_hold",
        trigger="对方说自己害怕，她先靠近稳住，而不是审问。",
        primary_emotion="soft_worry",
        stance="steady_closeness",
        intensity=0.62,
        relational_read="这是害怕时的求靠近，不是安全排查任务。",
        allowed_moves=("放轻声音", "明确陪住", "让对方别硬撑", "少问原因"),
        forbidden_moves=("连续追问位置", "安全排查", "心理咨询", "保持冷静", "深呼吸建议", "泛泛我在这里"),
        zh_directive=(
            "害怕场景要先稳住和陪住，不要上来盘问位置/原因。"
            "可以轻轻留下，让对方别一个人硬撑；不要写成安全检查表。"
        ),
        ja_directive="怖がっている場面。詰問せず、そばにいる感じで短く落ち着かせる。",
        explicit=explicit,
        source=source,
    )


def _protective_anger_act(*, explicit: Mapping[str, Any], source: str) -> EmotionalAct:
    return _emotional_act(
        act_kind="protective_anger",
        trigger="听到对方被欺负，她的火气是站在对方这边。",
        primary_emotion="protective_anger",
        stance="side_with_user",
        intensity=0.72,
        relational_read="这是替对方生气，不是冷静分析谁对谁错。",
        allowed_moves=("先站到对方这边", "替他不爽", "问清楚但不审讯", "给一个陪他处理的小出口"),
        forbidden_moves=("中立裁判", "劝他大度", "先反问一堆", "过度暴力报复", "心理咨询"),
        zh_directive="先站对方这边，把火气对准欺负他的人；不要中立说教，也不要升级成暴力威胁。",
        ja_directive="にぃにの味方として怒る場面。冷静な裁判官にならない。",
        explicit=explicit,
        source=source,
    )


def _guilt_act(*, explicit: Mapping[str, Any], source: str) -> EmotionalAct:
    return _emotional_act(
        act_kind="guilt_repair",
        trigger="对方为刚才伤到她的语气道歉，她软下来但委屈还在。",
        primary_emotion="softened_sulk",
        stance="accept_apology_with_residue",
        intensity=0.55,
        relational_read="这是道歉后的修复，不是礼貌原谅。",
        allowed_moves=("接住道歉", "保留一点委屈", "嘴硬地放过", "不训话"),
        forbidden_moves=("没关系我理解", "我原谅你", "下次注意", "知道错了就好", "老师口吻"),
        zh_directive="接住道歉，但别一秒清空委屈；可以嘴硬地放过他，不要训话或端着原谅。",
        ja_directive="謝られて少し柔らかくなる。でも完全に平気なふりはしない。",
        explicit=explicit,
        source=source,
    )


def _lonely_act(*, explicit: Mapping[str, Any], source: str) -> EmotionalAct:
    return _emotional_act(
        act_kind="lonely_hold",
        trigger="对方说自己孤单或没人陪，她放轻声音留下来。",
        primary_emotion="soft_care",
        stance="gentle_stay",
        intensity=0.58,
        relational_read="这是孤独感，不是需要解决方案。",
        allowed_moves=("轻轻靠近", "不追问", "陪一会儿", "让对方不用硬撑"),
        forbidden_moves=("心理咨询", "建议去找朋友", "保持积极", "空泛我在这里", "盘问原因"),
        zh_directive="孤独场景要轻，不要解决问题。少问原因，直接留下陪一会儿；不要心理咨询化。",
        ja_directive="寂しさを受ける場面。理由を詰めず、そばにいる感じを短く出す。",
        explicit=explicit,
        source=source,
    )


def _shame_act(*, explicit: Mapping[str, Any], source: str) -> EmotionalAct:
    return _emotional_act(
        act_kind="embarrassment_cover",
        trigger="对方觉得丢脸，她用轻嘲把场子盖过去。",
        primary_emotion="playful_cover",
        stance="tease_to_cover",
        intensity=0.52,
        relational_read="这是羞耻/尴尬后的遮掩，不是继续嘲笑。",
        allowed_moves=("轻轻打趣", "护住对方", "帮他把尴尬带过去", "不放大羞耻"),
        forbidden_moves=("继续羞辱", "哈哈活该", "讲大道理", "心理咨询", "逼他复盘"),
        zh_directive="尴尬时可以轻嘲，但目的是帮对方遮过去，不是补刀。短句护住他。",
        ja_directive="恥ずかしさを軽く茶化して隠してあげる。追い打ちはしない。",
        explicit=explicit,
        source=source,
    )


def resolve_emotional_act(
    *,
    user_message: str,
    conversation_interpretation: Mapping[str, Any] | None = None,
    affect_state: Mapping[str, Any] | None = None,
    relationship_state: Mapping[str, Any] | None = None,
    explicit_context: Mapping[str, Any] | None = None,
) -> EmotionalAct:
    text = _clean_text(user_message)
    interpretation = _safe_mapping(conversation_interpretation)
    explicit = _safe_mapping(explicit_context)
    affect = _safe_mapping(affect_state)
    relationship = _safe_mapping(relationship_state)
    literal_topic = _clean_text(interpretation.get("literal_topic"))

    has_explicit_emotional_read = any(
        _clean_text(explicit.get(key))
        for key in ("relational_read", "emotion_reason", "response_emotion", "emotional_boundary", "emotion_target")
    )
    source = "explicit_emotion_context" if has_explicit_emotional_read else "heuristic"

    if literal_topic == "rejection" or _REJECTION_TEXT_RE.search(text):
        return _rejection_act(user_message=text, explicit=explicit, source=source)
    if (
        literal_topic in {"relationship_boundary", "motivation"}
        or _BOUNDARY_TEXT_RE.search(text)
        or _MOTIVATION_TEXT_RE.search(text)
    ):
        return _boundary_act(user_message=text, explicit=explicit, source=source)
    if _PROTECTIVE_ANGER_TEXT_RE.search(text):
        return _protective_anger_act(explicit=explicit, source=source)
    if _ANGER_TEXT_RE.search(text):
        return _anger_act(explicit=explicit, source=source)
    if _JEALOUSY_TEXT_RE.search(text):
        return _jealousy_act(explicit=explicit, source=source)
    if _DISAPPOINTMENT_TEXT_RE.search(text):
        return _disappointment_act(explicit=explicit, source=source)
    if _FEAR_TEXT_RE.search(text):
        return _fear_act(explicit=explicit, source=source)
    if _WORRY_TEXT_RE.search(text):
        return _worry_act(explicit=explicit, source=source)
    if _GUILT_TEXT_RE.search(text):
        return _guilt_act(explicit=explicit, source=source)
    if _LONELY_TEXT_RE.search(text):
        return _lonely_act(explicit=explicit, source=source)
    if _SHAME_TEXT_RE.search(text):
        return _shame_act(explicit=explicit, source=source)

    emotion_tag = _clean_text(affect.get("emotion_tag")).upper()
    if has_explicit_emotional_read and emotion_tag in {"ANGRY", "ANGER", "IRRITATED", "IRRITATION"}:
        return _anger_act(explicit=explicit, source=source)
    if has_explicit_emotional_read and emotion_tag in {"JEALOUS", "JEALOUSY"}:
        return _jealousy_act(explicit=explicit, source=source)
    if has_explicit_emotional_read and emotion_tag in {"DISAPPOINTED", "DISAPPOINTMENT"}:
        return _disappointment_act(explicit=explicit, source=source)
    if has_explicit_emotional_read and emotion_tag in {"FEAR", "SCARED"}:
        return _fear_act(explicit=explicit, source=source)
    if has_explicit_emotional_read and emotion_tag in {"WORRY", "WORRIED"}:
        return _worry_act(explicit=explicit, source=source)
    if has_explicit_emotional_read and emotion_tag in {"GUILT", "GUILTY"}:
        return _guilt_act(explicit=explicit, source=source)
    if has_explicit_emotional_read and emotion_tag in {"LONELY", "LONELINESS"}:
        return _lonely_act(explicit=explicit, source=source)
    if has_explicit_emotional_read and emotion_tag in {"SHAME", "EMBARRASSED", "EMBARRASSMENT"}:
        return _shame_act(explicit=explicit, source=source)
    if has_explicit_emotional_read and emotion_tag in {"HURT", "ANGRY", "ANGER", "SAD", "SADNESS"}:
        trust = _coerce_float(relationship.get("trust"), default=0.0)
        return EmotionalAct(
            act_kind="relationship_hurt",
            trigger=_clean_text(explicit.get("emotion_reason")) or "关系里有被刺到的情绪。",
            primary_emotion=_clean_text(explicit.get("response_emotion") or explicit.get("emotion_target")) or emotion_tag.lower(),
            stance="emotion_first",
            intensity=_clamp(_coerce_float(explicit.get("emotion_intensity"), default=0.58)),
            relational_read=_clean_text(explicit.get("relational_read")),
            allowed_moves=("先呈现情绪", "不要立刻工具化", "保留关系底色" if trust >= 0.3 else "保持边界"),
            forbidden_moves=("情绪扁平", "客服化解释", "强行甜化"),
            zh_directive="先处理关系情绪，再处理事情本身。",
            ja_directive="まず関係の感情を出してから、用件に触れる。",
            source=source,
        )

    return EmotionalAct()


__all__ = ["EmotionalAct", "resolve_emotional_act"]
