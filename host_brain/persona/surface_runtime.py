import inspect
import json
import os
import re
import time
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from host_brain.channels.vts_runtime_mapping import (
    load_runtime_emotion_mapping,
    normalize_runtime_emotion_tag,
    render_runtime_emotion_tag_guidance,
    runtime_emotion_expression_id,
)
from host_brain.persona.isolated_surface_harness import (
    GenerateSurface,
    IsolatedSurfaceInput,
    build_surface_original_quote_query,
    build_isolated_surface_input,
    merged_must_not_do,
    parse_surface_candidates,
    relationship_control_axes_for_distance,
    surface_prompt_facts,
)
from host_brain.persona.surface_original_quote_bank import render_surface_original_quote_guidance


@dataclass(frozen=True)
class SurfaceDirectCandidate:
    text: str
    source: str = "direct_first"
    hard_gate_observations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "source": self.source,
            "hard_gate_observations": list(self.hard_gate_observations),
        }


@dataclass(frozen=True)
class SurfaceRuntimeResult:
    request: IsolatedSurfaceInput
    messages: tuple[Mapping[str, str], ...]
    raw_output: str
    candidates: tuple[SurfaceDirectCandidate, ...]
    selected: SurfaceDirectCandidate | None
    selected_text: str
    observation_codes: tuple[str, ...]
    generation_trace: Mapping[str, Any]
    natsume_emotion_tag: str = "NEUTRAL"
    user_emotion_tag: str = "NEUTRAL"
    vts_expression_id: str = ""
    emotion_confidence: float = 0.0
    latency_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected_text": self.selected_text,
            "natsume_emotion_tag": self.natsume_emotion_tag,
            "user_emotion_tag": self.user_emotion_tag,
            "vts_expression_id": self.vts_expression_id,
            "emotion_confidence": round(float(self.emotion_confidence or 0.0), 4),
            "selected": self.selected.as_dict() if self.selected else None,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "observation_codes": list(self.observation_codes),
            "generation_trace": dict(self.generation_trace),
            "latency_ms": int(self.latency_ms),
        }


def build_surface_generation_trace(
    *,
    candidates: Sequence[SurfaceDirectCandidate],
    selected: SurfaceDirectCandidate | None,
    observation_codes: Sequence[str],
) -> dict[str, Any]:
    return {
        "selected": selected.as_dict() if selected else None,
        "candidates": [candidate.as_dict() for candidate in candidates],
        "observation_codes": list(observation_codes),
        "candidate_count": len(candidates),
        "selection": "direct_first_no_score",
    }


def surface_observation_codes(selected: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not selected:
        return ("NO_SELECTED",)
    selected_map = dict(selected)
    codes: list[str] = []
    if selected_map.get("hard_fail"):
        codes.append("HARD_FAIL")
    if selected_map.get("control_violations"):
        codes.append("CONTROL_VIOLATION")
    for reason in selected_map.get("reason_codes") or []:
        reason_text = str(reason)
        if reason_text.startswith(_OBSERVATION_REASON_PREFIXES):
            codes.append(reason_text.split(":", 1)[0])
    return tuple(dict.fromkeys(codes))


def _coerce_surface_request(payload: IsolatedSurfaceInput | Mapping[str, Any]) -> IsolatedSurfaceInput:
    if isinstance(payload, IsolatedSurfaceInput):
        return payload
    return build_isolated_surface_input(payload)


async def _maybe_await_text(value: str | Awaitable[str]) -> str:
    if inspect.isawaitable(value):
        value = await value
    return str(value or "").strip()


_PROCESS_PREFACE_MARKERS = (
    "_move",
    "response_move",
    "这个回复",
    "候选",
    "字段",
    "满足",
    "所以输出",
    "只输出文本",
)
_OBSERVATION_REASON_PREFIXES = (
    "AFFECT_PROCESS_LEAK",
    "AWKWARD_COLLOCATION",
    "BLAND_PASS_LINE",
    "BOUNDARY_REPAIR_",
    "CORE_DIMENSION_FLOOR",
    "CONTINUITY_THIN_GENERIC_REPLY",
    "CONTINUITY_TASK_PUSHBACK",
    "CURRENT_USER_TOPIC_MISMATCH",
    "CUSTOMER_SERVICE_TONE",
    "FORBIDDEN_TEMPLATE_TEXT",
    "LOW_ENERGY_OVERHARSH_TEASE",
    "MISPLACED_EMOTIONAL_CONTAINMENT",
    "MISPLACED_SUPPORT_TEMPLATE",
    "MORNING_GENERIC_BLESSING",
    "OBVIOUS_CUSTOMER_SERVICE_TEMPLATE",
    "OILY_OR_OVERBOUND_TONE",
    "PRESENCE_REPAIR_",
    "RAW_CONTEXT_BACKFILL_REQUEST",
    "RELATION_DISTANCE_OVERSTEP",
    "RELATION_LOVER_UNDERINTIMATE",
    "RELATION_NON_ROMANTIC_OVERSTEP",
    "SELF_DOUBT_LACKS_SMALL_STEP",
    "SELF_DOUBT_REPLY_TOO_THIN",
    "SELF_DOUBT_THIN_DEFERRAL",
    "SIGNATURE_TEXTURE_MISSING",
    "SURFACE_PROCESS_META_LANGUAGE",
    "THIN_DAILY_FOLLOWUP_REPLY",
    "UNNEEDED_CONTINUITY_MATERIAL_REQUEST",
    "WAIT_SCENE_PRESSURES_RETURN",
    "WRITTEN_REGISTER_TONE",
)
_PROCESS_PREFACE_SPLIT_RE = re.compile(
    r"(?:所以输出(?:这条|文本)?[：:。]|只输出(?:一条)?(?:最终回复)?文本[：:。]|_move[^。！？!?]{0,24}[。！？!?])"
)
_QUOTED_REPLY_BEFORE_PROCESS_RE = re.compile(r"^[\"“「](.{2,120}?)[\"”」]\s*_move")
_LEGACY_SELF_TITLE_RE = re.compile(r"天爷|天大人|妹妹我|本小姐我")
_TEXT_INPUT_SENSORY_PATTERNS = (
    re.compile(r"(?:你这|你的|你那|老哥这|尼尼这).{0,3}声音"),
    re.compile(r"(?:一听|听起来|听上去|听得出|听出来).{0,16}(?:你|老哥|尼尼|昨晚|没睡|困|累|声音)"),
    re.compile(r"(?:听出来了|听出来.{0,8}(?:这句|这段|这话|不通顺|怪|别扭))"),
    re.compile(r"(?:你这|你的|老哥这|这句|这段|这话|语气).{0,8}(?:听着|听起来|听上去).{0,12}(?:累|怪|别扭|像|不通顺|被抽干)"),
    re.compile(r"(?:我|本小姐).{0,6}(?:看到|看见|瞧见).{0,8}(?:你|老哥|尼尼|屏幕|画面)"),
    re.compile(r"(?:你|老哥|尼尼).{0,8}(?:脸色|表情|屏幕|画面|镜头)"),
)
_AUDITORY_CHECK_USER_RE = re.compile(r"(听得?到|听得?见|听到吗|听见吗|能听见|能听到)")
_AUDITORY_TEXT_GROUNDING_RE = re.compile(
    r"(?:只|仅|光)?(?:看见|看到|收到).{0,12}(?:文字|文本|消息|这条|你发来|发来的)|"
    r"(?:文字|文本|消息|这条|你发来|发来的).{0,12}(?:看见|看到|收到)|"
    r"(?:没|没有|不).{0,4}(?:听见|听到|听着|听出|听出来)|"
    r"(?:没|没有).{0,8}(?:声音|语音|音频)"
)
_AUDITORY_HEARD_CLAIM_RE = re.compile(r"(听到了|听见了|听得到|听得见|收到声音|收到语音|有声音)")
_AUDITORY_NEGATED_HEARD_RE = re.compile(r"(没|没有|不).{0,4}(听到|听见|听得见|听得到|声音|语音|音频)")
_INTERNAL_LEAK_PATTERNS = (
    re.compile(r"cortex_orchestrator|ReplyControlPacket|score_trace|candidate_count", re.IGNORECASE),
    re.compile(r"候选|评分|控制包|内部字段|EmotionTag", re.IGNORECASE),
    re.compile(r"^\s*[\{\[]"),
)
_LOW_INFO_SCENE_STATE_GUESS_RE = re.compile(
    r"主动(?:打招呼|来找|找我)|(?:今天|这次|回来).{0,8}(?:主动|老实|可疑|没睡醒|准时|挺早|挺快|太晚|够晚|干脆|难得|居然|真少见)"
    r"|(?:回得|回来得).{0,6}(?:干脆|挺早|挺快|够晚|太晚|准时)"
    r"|忙到半夜|加班到半夜|没太晚|摸鱼|偷偷|刚睡醒|干净利落|挺安静|这么客气|这么老实"
    r"|今天.{0,8}(?:没啥急事|没什么急事|不赶|不用急)"
    r"|突然想到我|叫这么正式|走错片场|惜字如金"
    r"|有点可疑|可疑哦|又干嘛|突然冒出来|出什么事|一句继续|没诚意|给点反应|外星人|无聊死|真少见"
)
_LOW_INFO_OVERREAD_MARKER_RE = re.compile(
    r"今天|突然|又|倒是|挺|真少见|来得|刚|就一?个|可别|糊弄|走神|有话|有事|出什么|干嘛"
    r"|正式|客气|老实|乖|可疑|真巧|无聊|外星人|准备好|正想|回得|回来得|省点口舌"
    r"|没诚意|给点反应|不会跑|欢迎回家|少见|不错不错|别催|进门|还记得|值得表扬|表扬一下|等你"
    r"|偷懒|只说|陪你聊|吃掉|知道你忙|你忙|出大事|接住|我接|(?:老哥|哥哥|尼尼)在呢"
    r"|居然|稀奇|人就在这儿|喊一声|就回个|回个[“\"'‘]?继续|就一声|你好好听着"
)
_ZERO_QUESTION_BUDGET_PROMPT_RE = re.compile(
    r"(?:下一步|给个方向|说句话|说下去|说完|接着说|继续说|怎么啦|怎么了|什么事|有话说|发我|贴给我|丢过来|甩过来).{0,8}[？?吧]"
    r"|[？?].{0,16}(?:给个方向|说句话|继续说|接着说|有话说|发我|贴给我|丢过来|甩过来)"
)
_UNSUPPORTED_SHARED_HISTORY_RE = re.compile(
    r"不是第一次|每次(?:都|总)|以前(?:也|就|说过|见过)|上次|之前(?:也|就|说过|见过)"
    r"|老是这样|总是这样|又(?:这样|来了|说这种话)"
    r"|陪你聊这么久|聊这么久|这么久|一直在这|一直陪|一直跟你"
)
_META_IDENTITY_LEAK_RE = re.compile(r"设定|系统|提示词|角色卡|模型|语言模型|(?<![A-Za-z_])AI(?![A-Za-z_])", re.IGNORECASE)
_ASCII_TILDE_RE = re.compile(r"~")
_WAVE_MARKER_RE = re.compile(r"[~～]")
_BOUNDARY_SOUR_WITHDRAWAL_RE = re.compile(
    r"不管就不管|不管不管|爱理不理|不管了|不帮了|不帮你了|你自己处理|偏不管|偏不帮|懒得管|(?:先)?不吵你"
)
_RELATIONSHIP_DOUBT_DISMISSIVE_RE = re.compile(r"不信拉倒|爱信不信|信不信由你|懒得解释|反正我还会继续这样")
_LOVER_CLICHE_RE = re.compile(r"多多益善|越多越好|这种话多说|这种话可以多说")
_MEMORY_FOLLOWUP_DISCONTINUITY_RE = re.compile(
    r"突然问这个|怎么突然|你突然|突然问|突然提这个|突然说这个|硬要说(?:的话)?|另说|换个话题|最近在看|"
    r"居然.{0,8}问|主动问我|会主动问|有点意外|挺意外|真意外"
)
_MEMORY_FOLLOWUP_SELECTION_USER_RE = re.compile(r"喜欢哪|哪本|哪部|哪个|好看|先看|选哪|挑哪|推荐哪")
_MEMORY_FOLLOWUP_REASON_MARKER_RE = re.compile(
    r"因为|理由|看着|读着|感觉|味道|余味|舒服|有意思|新鲜|设定|标题|名字|简介|更|比较|挺|有点|戳|想看"
)

def _direct_surface_guard_lines(request: IsolatedSurfaceInput) -> tuple[str, ...]:
    no_question_line = (
        "本轮 question_budget=0：不要写真正问句或追问；不要用“下一步呢/说句话呀/是不是/吧？”推动用户，直接接住当前话。"
        if int(getattr(request.control, "question_budget", 0) or 0) <= 0
        else f"本轮最多 {int(request.control.question_budget)} 个问句；问句只能用于完成当前动作。"
    )
    return (
        "你是中文私聊里的数字生命，不能编现实动作或现场状态；不要说我去倒水、坐下、过来、抱一下、在你旁边、看见你、等类似现实行动，除非必要事实明确给出。",
        "也不要安排用户身体动作或现实作息：坐下、闭眼、躺下、喝水、早点睡、明天再说都只能在必要事实或用户明确请求时使用。",
        "用户追问记不记得时：事实或记忆包里有内容就直接接；没有就说不乱猜、让对方给线索。不要承认走神、忘了、脑袋空白，也不要编刚才发生过的事。",
        "用户问“你是不是不记得我/你忘了我吗”是在要关系安定，不是在要事实校验；直接说没忘、收住不安，不要反复追问“指哪段/给提示”。",
        "不要猜时间、作息、忙闲、进度或现实状态；禁用“熬夜、没睡醒、准时、挺早、没啥急事、不赶、忙文本、加班、摸鱼、做完、偷偷、又、主动来找我、回得干脆、回来得晚、忙到半夜、被工作吃掉、难得”等未提供事实推断。",
        "当前输入是文字消息时，不要写“你这语气听着/这话听起来”这类听觉化判断；可改成“这句读起来/这话看着”。",
        "早安、回来、在吗这类低信息场景，只接当前消息本身，不评价对方来得早晚、是否主动、是否老实、是否刚睡醒。",
        "称呼和自称是调味，不是回复主体；低信息短接续里不要靠“妹妹我、人家、本小姐”等自称撑风格，也不要评价用户偷懒或没诚意。",
        "低信息场景不要写“我接/接住了/接得住/知道你忙/以为要出大事/居然/稀奇/喊一声人就在这儿/就回个继续/就一声继续/你好好听着”；“在吗”只能表达我在，不要写成“老哥在呢”。",
        "用户用文字问“听得到吗/能听到吗”时，必须说明这里只看到文字或没收到声音；不要只回“哈？”“在呢”，也不要无证据说听到了。",
        "记忆指代追问里，facts 就是当前上下文；像接着刚才聊天一样自然回答，不要写“突然问这个”“硬要说的话”“最近在看另一本”，也不要复述自己在回忆。",
        "选择类记忆追问不要只报编号和标题；可以给一句非常短的主观理由，但理由只能来自标题或 facts 里的简介，不要编剧情。",
        "“继续/接着”这类极短续话不要用波浪号收尾，直接说接着来或继续。",
        "低能量/难受场景不要编喝水、坐下、躺下、睡觉、明天再说这类现实安排；可以说先缓一下、别硬撑，但要短、克制、像聊天。",
        "被用户推开时先收住距离，允许嘴硬一句，但不要反压成“偏不管/不管就不管/爱理不理/我不吵你/谁管你答不答应”；收住后留一句可回来的入口。",
        "恋人亲密可以明确回一句想念，但不要写“多多益善/越多越好/这种话多说”这类模板甜话。",
        "用户怀疑你是不是在按设定说话时，不解释系统，也不要用“不信拉倒/反正我还会继续这样”顶回去；用当前这一句把关系稳住。",
        "不要输出 emoji、颜文字、表情符号或日语口癖。",
        no_question_line,
    )


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    if hasattr(value, "dict"):
        return dict(value.dict())
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _json_block(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True)


def _direct_surface_messages_from_request(request: IsolatedSurfaceInput) -> list[dict[str, str]]:
    control_payload = _model_dump(request.control)
    control_payload["facts"] = surface_prompt_facts(request)
    control_payload["must_not_do"] = merged_must_not_do(request)
    relationship_axes_payload = relationship_control_axes_for_distance(
        request.control.relationship_distance
    ).as_dict()
    quote_guidance = render_surface_original_quote_guidance(
        build_surface_original_quote_query(request, limit=8),
        limit=8,
    )
    system_lines = [
        "你是隔离的最终表层文本生成器，只负责一次性产出一条可发送的中文私聊回复，并给出这句话对应的运行用 EmotionTag。",
        "唯一允许读取的输入：Soul.md、当前用户输入、ReplyControlPacket、必要事实列表、must_not_do 列表，以及真实中文原句风格参考。",
        "禁止使用或推断任何未提供的历史对话、长期记忆、器官族原始输出、工具候选、RAG 文档或模块自然语言建议。",
        "Soul.md 是语言风格最高锚；不要从旧代码、旧 prompt 或字段名里借身份。",
        "ReplyControlPacket 是最终生成层的唯一运行控制面；不得要求更多 runtime state。",
        "ReplyControlPacket.style_anchor 里的句法锚只学结构和节奏，不要逐字照抄锚点文本；同一类短句可以自然换词。",
        "先抓当前用户这句话最明显的意图或情绪，再完成 response_move；不要只写空泛支持、空泛安慰或单纯撒娇。",
        "日常和关系场景优先像中文私聊：先有一点主观反应，再承接事实或给一步；不要写成任务回执、流程提示、安排记录或客服式确认。",
        "技术和任务场景里，有用性优先，角色口吻只做轻量包裹。",
        "文字优先通顺；语音化停顿、短促反应和省略号都不能牺牲语法。",
        "不要输出候选、评分、分析、解释、Markdown、括号动作、内部字段或控制包内容。",
        "",
        "## 结构化直出要求",
        '只输出 JSON 对象：{"reply_text":"...","natsume_emotion_tag":"...","user_emotion_tag":"...","emotion_confidence":0.0}。',
        render_runtime_emotion_tag_guidance(),
        "user_emotion_tag 是你从当前用户文字语义中理解到的用户情绪；不确定就用 NEUTRAL。",
        "natsume_emotion_tag 是你说出 reply_text 时自己的情绪，不是用户情绪，也不要为了显眼乱填高强度情绪。",
        "reply_text 里不能出现 JSON 字段名、EmotionTag、标签解释、候选、评分或内部分析。",
        "",
        "## 直出边界",
        *_direct_surface_guard_lines(request),
        "",
        "## Soul.md",
        request.soul_md.strip(),
    ]
    user_lines = [
        "## 当前用户输入",
        request.user_message,
        "",
        "## ReplyControlPacket",
        _json_block(control_payload),
        "",
        "## DerivedRelationshipControlAxes",
        _json_block(relationship_axes_payload),
        "",
        "## 必要事实",
        "\n".join(f"- {item}" for item in surface_prompt_facts(request)) or "- 无",
    ]
    if quote_guidance:
        user_lines.extend(["", quote_guidance])
    user_lines.extend(
        [
            "",
            "## must_not_do",
            "\n".join(f"- {item}" for item in merged_must_not_do(request)) or "- 无",
            "",
            "## 输出要求",
            '只输出 JSON 对象，格式为 {"reply_text":"...","natsume_emotion_tag":"...","user_emotion_tag":"...","emotion_confidence":0.0}。',
            "不要候选列表，不要解释，不要把 EmotionTag 写进 reply_text。",
        ]
    )
    return [
        {"role": "system", "content": "\n".join(system_lines)},
        {"role": "user", "content": "\n".join(user_lines)},
    ]


def strip_surface_process_preface(text: str) -> str:
    value = str(text or "").strip()
    if not value or not any(marker in value for marker in _PROCESS_PREFACE_MARKERS):
        return value
    quoted = _QUOTED_REPLY_BEFORE_PROCESS_RE.search(value)
    if quoted and _has_cjk(quoted.group(1)):
        return quoted.group(1).strip()
    split_parts = [part.strip() for part in _PROCESS_PREFACE_SPLIT_RE.split(value) if part.strip()]
    if split_parts and split_parts[-1] != value and _has_cjk(split_parts[-1]):
        return split_parts[-1]
    visible_lines: list[str] = []
    for line in value.splitlines():
        item = line.strip()
        if not item:
            continue
        if any(marker in item for marker in _PROCESS_PREFACE_MARKERS):
            continue
        if _has_cjk(item):
            visible_lines.append(item)
    return visible_lines[-1] if visible_lines else value


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(text or "")))


def build_direct_surface_messages(
    payload: IsolatedSurfaceInput | Mapping[str, Any],
) -> tuple[Mapping[str, str], ...]:
    request = _coerce_surface_request(payload)
    return tuple(_direct_surface_messages_from_request(request))


def parse_surface_generation_output(raw_output: Any) -> dict[str, Any]:
    raw = str(raw_output or "").strip()
    emotion_mapping = load_runtime_emotion_mapping()
    payload = _load_json_object(raw)
    if isinstance(payload, Mapping):
        reply_text = _clean_reply_text(
            payload.get("reply_text")
            or payload.get("reply")
            or payload.get("text")
            or payload.get("narrative")
            or ""
        )
        if not reply_text:
            candidates = parse_surface_candidates(payload)
            reply_text = _clean_reply_text(candidates[0] if candidates else "")
        natsume_tag = _normalize_emotion_tag(
            payload.get("natsume_emotion_tag")
            or payload.get("assistant_emotion_tag")
            or payload.get("emotion_tag")
            or "NEUTRAL",
            mapping=emotion_mapping,
        )
        user_tag = _normalize_emotion_tag(
            payload.get("user_emotion_tag")
            or payload.get("perceived_user_emotion_tag")
            or "NEUTRAL",
            mapping=emotion_mapping,
        )
        return {
            "reply_text": reply_text,
            "natsume_emotion_tag": natsume_tag,
            "user_emotion_tag": user_tag,
            "vts_expression_id": runtime_emotion_expression_id(natsume_tag, mapping=emotion_mapping),
            "emotion_confidence": _coerce_confidence(
                payload.get("emotion_confidence")
                or payload.get("confidence")
                or 0.0
            ),
        }

    candidates = parse_surface_candidates(raw)
    return {
        "reply_text": _clean_reply_text(candidates[0] if candidates else ""),
        "natsume_emotion_tag": "NEUTRAL",
        "user_emotion_tag": "NEUTRAL",
        "vts_expression_id": runtime_emotion_expression_id("NEUTRAL", mapping=emotion_mapping),
        "emotion_confidence": 0.0,
    }


def _load_json_object(raw: str) -> Mapping[str, Any] | None:
    if not raw:
        return None
    value = raw.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE).strip()
        value = re.sub(r"\s*```$", "", value).strip()
    for candidate in (value, _balanced_json_object(value)):
        if not candidate:
            continue
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, Mapping):
            return decoded
    return None


def _balanced_json_object(raw: str) -> str:
    start = raw.find("{")
    if start < 0:
        return ""
    depth = 0
    quote = ""
    escaped = False
    for index in range(start, len(raw)):
        char = raw[index]
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
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return raw[start : index + 1]
    return ""


def _clean_reply_text(value: Any) -> str:
    text = strip_surface_process_preface(str(value or "").strip())
    text = re.sub(r"[~～][。.!！]+(?=\s*\S)", "。", text)
    text = re.sub(r"([~～])(?:[。.!！]+)$", r"\1", text)
    text = re.sub(r"([。！？!?])\1+$", r"\1", text)
    return text.strip()


def _normalize_emotion_tag(value: Any, *, mapping: Mapping[str, str]) -> str:
    return normalize_runtime_emotion_tag(value, mapping=mapping)


def _coerce_confidence(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))


def _surface_hard_gate_violations(request: IsolatedSurfaceInput, text: str) -> tuple[str, ...]:
    value = str(text or "").strip()
    if not value:
        return ()
    codes: list[str] = []
    if _text_input_boundary_active(request) and any(pattern.search(value) for pattern in _TEXT_INPUT_SENSORY_PATTERNS):
        codes.append("SENSORY_SOURCE_HALLUCINATION")
    if _auditory_check_active(request) and _text_input_boundary_active(request):
        if _auditory_pretend_heard_hits(value):
            codes.append("AUDITORY_CHECK_PRETEND_HEARD")
        if not _auditory_text_grounding_hits(value):
            codes.append("AUDITORY_CHECK_MISSING_TEXT_GROUNDING")
    if _low_info_scene_state_guess_hits(request, value):
        codes.append("LOW_INFO_SCENE_STATE_GUESS")
    if _short_continue_wave_style_hits(request, value):
        codes.append("SHORT_CONTINUE_WAVE_STYLE")
    if _zero_question_budget_prompt_hits(request, value):
        codes.append("QUESTION_BUDGET_PROMPTING_USER")
    if _unsupported_shared_history_hits(request, value):
        codes.append("UNSUPPORTED_SHARED_HISTORY")
    if _meta_identity_boundary_hits(request, value):
        codes.append("SURFACE_META_IDENTITY_LEAK")
    if any(pattern.search(value) for pattern in _INTERNAL_LEAK_PATTERNS):
        codes.append("SURFACE_INTERNAL_LEAK")
    if _ASCII_TILDE_RE.search(value):
        codes.append("ASCII_TILDE_TEXT_STYLE")
    if _boundary_sour_withdrawal_hits(request, value):
        codes.append("BOUNDARY_SOUR_WITHDRAWAL")
    if _relationship_doubt_dismissive_hits(request, value):
        codes.append("RELATIONSHIP_DOUBT_DISMISSIVE")
    if _lover_cliche_hits(request, value):
        codes.append("LOVER_CLICHE")
    if _LEGACY_SELF_TITLE_RE.search(value):
        codes.append("LEGACY_SELF_TITLE")
    if _memory_followup_discontinuity_hits(request, value):
        codes.append("MEMORY_FOLLOWUP_DISCONTINUITY")
    if _memory_followup_missing_grounding(request, value):
        codes.append("MEMORY_FOLLOWUP_MISSING_GROUNDING")
    if _memory_followup_bare_echo_hits(request, value):
        codes.append("MEMORY_FOLLOWUP_BARE_ECHO")
    return tuple(dict.fromkeys(codes))


def _memory_followup_discontinuity_hits(request: IsolatedSurfaceInput, text: str) -> bool:
    if not _memory_followup_grounding_active(request):
        return False
    return bool(_MEMORY_FOLLOWUP_DISCONTINUITY_RE.search(str(text or "")))


def _memory_followup_missing_grounding(request: IsolatedSurfaceInput, text: str) -> bool:
    if not _memory_followup_grounding_active(request):
        return False
    value = str(text or "").strip()
    if not value:
        return False
    terms = _memory_followup_grounding_terms(request)
    if not terms:
        return False
    normalized = re.sub(r"\s+", "", value)
    return not any(term and term in normalized for term in terms)


def _memory_followup_bare_echo_hits(request: IsolatedSurfaceInput, text: str) -> bool:
    if not _memory_followup_grounding_active(request):
        return False
    user_text = str(getattr(request, "user_message", "") or "")
    if not _MEMORY_FOLLOWUP_SELECTION_USER_RE.search(user_text):
        return False
    value = re.sub(r"\s+", "", str(text or "").strip())
    if not value:
        return False
    if _MEMORY_FOLLOWUP_REASON_MARKER_RE.search(value):
        return False
    if len(value) <= 34 and re.search(r"第[一二三四五六七八九十\d]+[本部个]", value):
        return True
    return bool(re.fullmatch(r".{0,10}选第[一二三四五六七八九十\d]+[本部个][，,:：].{2,28}[。.!！?？]?", value))


def _memory_followup_grounding_active(request: IsolatedSurfaceInput) -> bool:
    task_mode = str(getattr(request.control, "task_mode", "") or "").strip().lower()
    response_move = str(getattr(request.control, "response_move", "") or "").strip().lower()
    if task_mode == "memory_followup" or response_move == "memory_followup":
        return True
    joined = "\n".join(
        [
            *[str(item or "") for item in list(getattr(request.control, "facts", []) or [])],
            *[str(item or "") for item in list(getattr(request, "facts", []) or [])],
            *[str(item or "") for item in list(getattr(request.control, "style_anchor", []) or [])],
            *[str(item or "") for item in list(getattr(request.control, "must_not_do", []) or [])],
        ]
    )
    return "指代选择追问" in joined


def _memory_followup_grounding_terms(request: IsolatedSurfaceInput) -> tuple[str, ...]:
    facts = [
        str(item or "")
        for item in [
            *list(getattr(request.control, "facts", []) or []),
            *list(getattr(request, "facts", []) or []),
        ]
    ]
    joined = re.sub(r"\s+", "", "\n".join(facts))
    if not joined:
        return ()
    terms: list[str] = []

    def add(term: str) -> None:
        cleaned = re.sub(r"[\s「」“”\"'《》\[\]（）()]+", "", str(term or ""))
        if len(cleaned) < 2 or cleaned in terms:
            return
        terms.append(cleaned)

    numbered = list(re.finditer(r"(?<!\d)(\d{1,2})[.、]\s*([^；。！？!?\n]+)", "\n".join(facts)))
    if numbered:
        for index, match in enumerate(numbered[:6], start=1):
            add(f"第{index}本")
            add(f"第{index}个")
            if index == 1:
                add("第一本")
            elif index == 2:
                add("第二本")
            elif index == 3:
                add("第三本")
            item = match.group(2)
            add(item[:24])
            for sequence in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]{2,20}", item):
                if sequence in {"推荐", "轻小说", "刚才", "通过", "回复", "用户提到"}:
                    continue
                add(sequence)
    if "推荐了3本" in joined or "推荐了三本" in joined:
        add("三本")
        add("推荐")
    return tuple(terms[:32])


def _normalized_short_user_text(value: str) -> str:
    return re.sub(r"[\s，,。.!！?？、]+", "", str(value or "").strip())


def _auditory_check_active(request: IsolatedSurfaceInput) -> bool:
    task_mode = str(getattr(request.control, "task_mode", "") or "").strip().lower()
    response_move = str(getattr(request.control, "response_move", "") or "").strip().lower()
    if task_mode == "auditory_check" or response_move == "auditory_probe":
        return True
    return bool(_AUDITORY_CHECK_USER_RE.search(str(getattr(request, "user_message", "") or "")))


def _auditory_text_grounding_hits(text: str) -> bool:
    return bool(_AUDITORY_TEXT_GROUNDING_RE.search(str(text or "")))


def _auditory_pretend_heard_hits(text: str) -> bool:
    value = str(text or "")
    if not _AUDITORY_HEARD_CLAIM_RE.search(value):
        return False
    return not bool(_AUDITORY_NEGATED_HEARD_RE.search(value))


def _low_info_scene_state_guess_hits(request: IsolatedSurfaceInput, text: str) -> tuple[str, ...]:
    user_text = str(getattr(request, "user_message", "") or "").strip()
    normalized_user = _normalized_short_user_text(user_text)
    low_info_scene = normalized_user in {
        "早",
        "早安",
        "早上好",
        "我回来了",
        "回来了",
        "回来啦",
        "在吗",
        "在吗？",
        "继续",
        "接着",
    } or normalized_user.startswith(("早啊", "早呀", "早安", "早上好"))
    if not low_info_scene:
        return ()
    value = str(text or "")
    return tuple(
        dict.fromkeys(
            [
                *[match.group(0) for match in _LOW_INFO_SCENE_STATE_GUESS_RE.finditer(value)],
                *[match.group(0) for match in _LOW_INFO_OVERREAD_MARKER_RE.finditer(value)],
            ]
        )
    )


def _short_continue_wave_style_hits(request: IsolatedSurfaceInput, text: str) -> tuple[str, ...]:
    normalized_user = _normalized_short_user_text(str(getattr(request, "user_message", "") or ""))
    if normalized_user not in {"继续", "接着"}:
        return ()
    return tuple(dict.fromkeys(match.group(0) for match in _WAVE_MARKER_RE.finditer(str(text or ""))))


def _zero_question_budget_prompt_hits(request: IsolatedSurfaceInput, text: str) -> tuple[str, ...]:
    if int(getattr(request.control, "question_budget", 0) or 0) > 0:
        return ()
    value = str(text or "")
    if "？" not in value and "?" not in value:
        return ()
    hits = tuple(dict.fromkeys(match.group(0) for match in _ZERO_QUESTION_BUDGET_PROMPT_RE.finditer(value)))
    if hits:
        return hits
    without_reaction = re.sub(r"^\s*(?:哈|啊|欸|诶|咦)[？?][，,。…\s]*", "", value)
    if "？" in without_reaction or "?" in without_reaction:
        return ("ZERO_QUESTION_BUDGET_QUESTION_MARK",)
    return ()


def _unsupported_shared_history_hits(request: IsolatedSurfaceInput, text: str) -> tuple[str, ...]:
    value = str(text or "")
    hits = tuple(dict.fromkeys(match.group(0) for match in _UNSUPPORTED_SHARED_HISTORY_RE.finditer(value)))
    if not hits:
        return ()
    evidence = "\n".join(
        [
            str(getattr(request, "user_message", "") or ""),
            *[str(item or "") for item in list(getattr(request.control, "facts", []) or [])],
            *[str(item or "") for item in list(getattr(request, "facts", []) or [])],
        ]
    )
    return tuple(hit for hit in hits if hit not in evidence)


def _meta_identity_boundary_hits(request: IsolatedSurfaceInput, text: str) -> tuple[str, ...]:
    user_text = str(getattr(request, "user_message", "") or "")
    task_mode = str(getattr(request.control, "task_mode", "") or "").strip().lower()
    response_move = str(getattr(request.control, "response_move", "") or "").strip().lower()
    active = (
        task_mode in {"identity", "relationship_boundary", "relationship_doubt"}
        or response_move in {"answer_doubt_without_meta", "answer_identity_without_meta"}
        or bool(re.search(r"设定|系统|提示词|角色卡|模型|语言模型|(?<![A-Za-z_])AI(?![A-Za-z_])", user_text, re.IGNORECASE))
    )
    if not active:
        return ()
    return tuple(dict.fromkeys(match.group(0) for match in _META_IDENTITY_LEAK_RE.finditer(str(text or ""))))


def _boundary_sour_withdrawal_hits(request: IsolatedSurfaceInput, text: str) -> tuple[str, ...]:
    task_mode = str(getattr(request.control, "task_mode", "") or "").strip().lower()
    response_move = str(getattr(request.control, "response_move", "") or "").strip().lower()
    active = task_mode in {"boundary_repair", "pushed_away_boundary"} or response_move in {
        "step_back_but_stay_available",
        "pushed_away_boundary",
    }
    if not active:
        return ()
    return tuple(dict.fromkeys(match.group(0) for match in _BOUNDARY_SOUR_WITHDRAWAL_RE.finditer(str(text or ""))))


def _relationship_doubt_dismissive_hits(request: IsolatedSurfaceInput, text: str) -> tuple[str, ...]:
    task_mode = str(getattr(request.control, "task_mode", "") or "").strip().lower()
    response_move = str(getattr(request.control, "response_move", "") or "").strip().lower()
    user_text = str(getattr(request, "user_message", "") or "")
    active = (
        task_mode in {"relationship_boundary", "relationship_doubt"}
        or response_move in {"answer_doubt_without_meta", "answer_identity_without_meta"}
        or bool(re.search(r"设定|系统|提示词|角色卡|模型|语言模型|按设定|(?<![A-Za-z_])AI(?![A-Za-z_])", user_text, re.IGNORECASE))
    )
    if not active:
        return ()
    return tuple(dict.fromkeys(match.group(0) for match in _RELATIONSHIP_DOUBT_DISMISSIVE_RE.finditer(str(text or ""))))


def _lover_cliche_hits(request: IsolatedSurfaceInput, text: str) -> tuple[str, ...]:
    task_mode = str(getattr(request.control, "task_mode", "") or "").strip().lower()
    response_move = str(getattr(request.control, "response_move", "") or "").strip().lower()
    relationship_distance = str(getattr(request.control, "relationship_distance", "") or "").strip().lower()
    active = (
        task_mode in {"lover_affection", "lover_presence"}
        or response_move in {"return_affection_concretely", "lover_presence"}
        or relationship_distance in {"lover", "lover_early", "lover_stable", "stable_lover"}
    )
    if not active:
        return ()
    return tuple(dict.fromkeys(match.group(0) for match in _LOVER_CLICHE_RE.finditer(str(text or ""))))


def _text_input_boundary_active(request: IsolatedSurfaceInput) -> bool:
    joined = "\n".join(
        [
            *list(getattr(request.control, "facts", []) or []),
            *list(getattr(request, "facts", []) or []),
            *list(getattr(request.control, "must_not_do", []) or []),
            *list(getattr(request, "must_not_do", []) or []),
        ]
    )
    if "音频输入：有" in joined or "语音输入：有" in joined:
        return False
    return True


def _minimal_hard_gate_repair(request: IsolatedSurfaceInput, text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    violations = _surface_hard_gate_violations(request, value)
    fallback = _hard_gate_fallback(request, violations)
    if fallback and not _surface_hard_gate_violations(request, fallback):
        return fallback
    chunks = [chunk.strip() for chunk in re.split(r"(?<=[。！？!?])", value) if chunk.strip()]
    if len(chunks) <= 1:
        return ""
    kept: list[str] = []
    for chunk in chunks:
        if _surface_hard_gate_violations(request, chunk):
            continue
        kept.append(chunk)
    return "".join(kept).strip()


def _hard_gate_fallback(request: IsolatedSurfaceInput, violations: Sequence[str]) -> str:
    codes = set(str(code or "") for code in violations)
    low_info = _low_info_scene_fallback(request)
    if low_info and codes:
        return low_info
    if "AUDITORY_CHECK_PRETEND_HEARD" in codes or "AUDITORY_CHECK_MISSING_TEXT_GROUNDING" in codes:
        return "哈？我这里只收到这条文字。"
    if "BOUNDARY_SOUR_WITHDRAWAL" in codes:
        return "行，那你自己待会儿。难受了就叫我，听见没。"
    if "RELATIONSHIP_DOUBT_DISMISSIVE" in codes or "SURFACE_META_IDENTITY_LEAK" in codes:
        return "哈？这话挺伤人的。我是在跟你说话，不是在背台词。"
    if "LOVER_CLICHE" in codes:
        return "我也想你了。笨蛋老哥。"
    if "SHORT_CONTINUE_WAVE_STYLE" in codes:
        return "嗯，接着来。"
    if "LEGACY_SELF_TITLE" in codes:
        return _low_info_scene_fallback(request) or ""
    if (
        "MEMORY_FOLLOWUP_DISCONTINUITY" in codes
        or "MEMORY_FOLLOWUP_MISSING_GROUNDING" in codes
        or "MEMORY_FOLLOWUP_BARE_ECHO" in codes
    ):
        return _memory_followup_fallback(request)
    return ""


def _memory_followup_fallback(request: IsolatedSurfaceInput) -> str:
    facts = [
        str(item or "")
        for item in [
            *list(getattr(request.control, "facts", []) or []),
            *list(getattr(request, "facts", []) or []),
        ]
    ]
    matches = list(re.finditer(r"(?<!\d)(\d{1,2})[.、]\s*([^；。！？!?\n]+)", "\n".join(facts)))
    if matches:
        pick_index = 1 if len(matches) > 1 else 0
        picked = re.sub(r"[\s「」“”\"'《》\[\]（）()]+", "", matches[pick_index].group(2)).strip()
        picked = re.sub(r"^\d{2,5}[.、]\s*", "", picked)
        picked = picked[:42].rstrip("，,、；;。")
        label = "第二" if pick_index == 1 else "第一"
        if picked:
            return f"那三本里我会选{label}本，{picked}。名字看着更有余味。"
    for fact in facts:
        cleaned = re.sub(r"^(记忆线索|长期记忆|召回线索)[:：]", "", str(fact or "").strip()).strip()
        if cleaned:
            return cleaned[:80].rstrip("，,、；;") + "。"
    return ""


def _low_info_scene_fallback(request: IsolatedSurfaceInput) -> str:
    normalized_user = _normalized_short_user_text(str(getattr(request, "user_message", "") or "").strip())
    if normalized_user in {"早", "早安", "早上好"} or normalized_user.startswith(("早啊", "早呀", "早安", "早上好")):
        return "老哥早～"
    if normalized_user in {"我回来了", "回来了", "回来啦"}:
        return "老哥欢迎回来～"
    if normalized_user in {"在吗", "在吗？"}:
        return "在呢，老哥。"
    if normalized_user in {"继续", "接着"}:
        return "嗯，接着来。"
    return ""


class SurfaceRuntime:
    def __init__(
        self,
        generate: GenerateSurface,
        *,
        empty_generation_retries: int = 1,
    ) -> None:
        self._generate = generate
        self._empty_generation_retries = max(0, int(empty_generation_retries or 0))

    async def run(self, payload: IsolatedSurfaceInput | Mapping[str, Any]) -> SurfaceRuntimeResult:
        request = _coerce_surface_request(payload)
        started = time.perf_counter()
        messages = build_direct_surface_messages(request)
        raw_output = await _maybe_await_text(self._generate(messages))
        structured = parse_surface_generation_output(raw_output)
        candidate_texts = [structured["reply_text"]] if structured["reply_text"] else []
        hard_gate_observations: tuple[str, ...] = ()
        if candidate_texts:
            hard_gate_observations = _surface_hard_gate_violations(request, candidate_texts[0])
        hard_gate_retry_enabled = _surface_hard_gate_retry_enabled()
        if hard_gate_observations and not hard_gate_retry_enabled:
            repaired = _minimal_hard_gate_repair(request, candidate_texts[0])
            if repaired and not _surface_hard_gate_violations(request, repaired):
                structured = {**structured, "reply_text": repaired}
                candidate_texts = [repaired]
                hard_gate_observations = ()
        if hard_gate_observations and hard_gate_retry_enabled:
            retry_messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "上一条回复违反最终文本 hard gate："
                        + "、".join(hard_gate_observations)
                        + "。重新生成一条；保留当前关系口吻，但不要编造音频/视觉/现实感知，不要猜早晚、主动、忙闲、作息或进度，不要泄露内部状态。"
                        + "没有明确事实时，也不要说上次、以前、每次、不是第一次、老是这样这类共同过去或频率判断。"
                        + "如果用户质疑身份或设定，不要复用“设定、系统、模型、提示词、角色卡”这些元词，直接按当前关系回应。"
                        + "如果是记忆或指代选择追问，把 facts 当作刚才对话现场，自然接住；点出对象、编号或标题，但不要说“突然问这个”“硬要说的话”“最近在看”。"
                        + "如果是在问喜欢哪本/哪部/哪个，不要只复述编号和标题，要补一句很短的主观理由，且不要编 facts 之外的剧情。"
                    ),
                },
            ]
            raw_output = await _maybe_await_text(self._generate(retry_messages))
            structured = parse_surface_generation_output(raw_output)
            candidate_texts = [structured["reply_text"]] if structured["reply_text"] else []
            if candidate_texts:
                hard_gate_observations = _surface_hard_gate_violations(request, candidate_texts[0])
                if hard_gate_observations:
                    repaired = _minimal_hard_gate_repair(request, candidate_texts[0])
                    if repaired and not _surface_hard_gate_violations(request, repaired):
                        structured = {**structured, "reply_text": repaired}
                        candidate_texts = [repaired]
                        hard_gate_observations = ()
                    else:
                        structured = {**structured, "reply_text": ""}
                        candidate_texts = []
            hard_gate_observations = (
                _surface_hard_gate_violations(request, candidate_texts[0])
                if candidate_texts
                else ()
            )
            if hard_gate_observations and candidate_texts:
                repaired = _minimal_hard_gate_repair(request, candidate_texts[0])
                if repaired and not _surface_hard_gate_violations(request, repaired):
                    structured = {**structured, "reply_text": repaired}
                    candidate_texts = [repaired]
                    hard_gate_observations = ()
                else:
                    structured = {**structured, "reply_text": ""}
                    candidate_texts = []
        for _ in range(self._empty_generation_retries):
            if candidate_texts:
                break
            retry_messages = [
                *messages,
                {
                    "role": "user",
                    "content": '上一次输出为空。只输出 JSON 对象：{"reply_text":"...","natsume_emotion_tag":"NEUTRAL","user_emotion_tag":"NEUTRAL","emotion_confidence":0.0}。',
                },
            ]
            raw_output = await _maybe_await_text(self._generate(retry_messages))
            structured = parse_surface_generation_output(raw_output)
            candidate_texts = [structured["reply_text"]] if structured["reply_text"] else []
        candidates = tuple(
            SurfaceDirectCandidate(
                text=text,
                hard_gate_observations=tuple(hard_gate_observations if index == 0 else ()),
            )
            for index, text in enumerate(candidate_texts[:1])
        )
        selected = candidates[0] if candidates else None
        observation_codes = tuple(dict.fromkeys(hard_gate_observations))
        generation_trace = build_surface_generation_trace(
            candidates=candidates,
            selected=selected,
            observation_codes=observation_codes,
        )
        return SurfaceRuntimeResult(
            request=request,
            messages=tuple(messages),
            raw_output=raw_output,
            candidates=candidates,
            selected=selected,
            selected_text=selected.text if selected else "",
            observation_codes=observation_codes,
            generation_trace=generation_trace,
            natsume_emotion_tag=str(structured.get("natsume_emotion_tag") or "NEUTRAL"),
            user_emotion_tag=str(structured.get("user_emotion_tag") or "NEUTRAL"),
            vts_expression_id=str(structured.get("vts_expression_id") or ""),
            emotion_confidence=float(structured.get("emotion_confidence") or 0.0),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


def _surface_hard_gate_retry_enabled() -> bool:
    value = str(os.environ.get("HOST_SURFACE_MAIN_CHAIN_HARD_GATE_RETRY_ENABLED") or "").strip().lower()
    if not value:
        return True
    return value in {"1", "true", "yes", "on"}


__all__ = [
    "SurfaceRuntime",
    "SurfaceRuntimeResult",
    "build_direct_surface_messages",
    "SurfaceDirectCandidate",
    "build_surface_generation_trace",
    "parse_surface_generation_output",
    "strip_surface_process_preface",
    "surface_observation_codes",
]
