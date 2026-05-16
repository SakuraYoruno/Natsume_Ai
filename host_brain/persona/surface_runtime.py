import inspect
import json
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
    IsolatedCandidateScore,
    IsolatedSurfaceInput,
    build_isolated_surface_input,
    build_isolated_surface_messages,
    parse_surface_candidates,
    score_isolated_surface_candidates,
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
    "SIGNATURE_TEXTURE_MISSING",
    "LOW_ENERGY_OVERHARSH_TEASE",
    "SELF_DOUBT_THIN_DEFERRAL",
    "SELF_DOUBT_LACKS_SMALL_STEP",
    "SELF_DOUBT_REPLY_TOO_THIN",
    "SURFACE_PROCESS_META_LANGUAGE",
    "THIN_DAILY_FOLLOWUP_REPLY",
    "UNNEEDED_CONTINUITY_MATERIAL_REQUEST",
    "WAIT_SCENE_PRESSURES_RETURN",
    "WRITTEN_REGISTER_TONE",
)
_SOFT_QUALITY_RETRY_PREFIXES = (
    "MISPLACED_EMOTIONAL_CONTAINMENT",
    "MISPLACED_SUPPORT_TEMPLATE",
    "LOW_ENERGY_OVERHARSH_TEASE",
    "SELF_DOUBT_THIN_DEFERRAL",
    "SELF_DOUBT_LACKS_SMALL_STEP",
    "SELF_DOUBT_REPLY_TOO_THIN",
    "SELF_DOUBT_UNGROUNDED_TECH_OBJECT",
    "SELF_DOUBT_DEMEANING_SMALL_STEP",
    "UNNEEDED_CONTINUITY_MATERIAL_REQUEST",
)


@dataclass(frozen=True)
class SurfaceRuntimeResult:
    request: IsolatedSurfaceInput
    messages: tuple[Mapping[str, str], ...]
    raw_output: str
    candidates: tuple[IsolatedCandidateScore, ...]
    selected: IsolatedCandidateScore | None
    selected_text: str
    observation_codes: tuple[str, ...]
    score_trace: Mapping[str, Any]
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
            "score_trace": dict(self.score_trace),
            "latency_ms": int(self.latency_ms),
        }


def surface_observation_codes(selected: IsolatedCandidateScore | Mapping[str, Any] | None) -> tuple[str, ...]:
    if not selected:
        return ("NO_SELECTED",)
    if isinstance(selected, IsolatedCandidateScore):
        selected_map = selected.as_dict()
    else:
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


def _soft_quality_retry_reason_codes(selected: IsolatedCandidateScore | None) -> tuple[str, ...]:
    if not selected:
        return ()
    codes: list[str] = []
    for reason in selected.reason_codes:
        reason_text = str(reason)
        if reason_text.startswith(_SOFT_QUALITY_RETRY_PREFIXES):
            codes.append(reason_text.split(":", 1)[0])
    return tuple(dict.fromkeys(codes))


def _minimal_soft_quality_repair(
    request: IsolatedSurfaceInput,
    text: str,
    reason_codes: Sequence[str],
) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    reasons = tuple(str(item) for item in reason_codes)
    self_doubt_soft = any(item.startswith("SELF_DOUBT_") for item in reasons)
    if self_doubt_soft:
        value = re.sub(r"(?:先)?(?:把(?:手头)?这事)?放(?:一)?(?:下|放)[，,、 ]*", "", value)
        value = re.sub(r"(?:先)?搁(?:一)?(?:下|搁|着)[。.!！？，,、 ]*", "", value)
        value = re.sub(r"回头(?:再(?:看|说))?[，,、 ]*", "", value)
        value = re.sub(r"(?:换个思路)?再试[。.!！]?", "", value)
        value = re.sub(r"明天再(?:看|说)[，,、 ]*", "", value)
        if not re.search(r"(一步|一件|小事|最小|最卡|拎出来|拿出来|做完|试试|突破口)", value):
            value = value.rstrip("。.!！") + "。先把最卡的那一步拎出来。"
        elif any(item.startswith("SELF_DOUBT_LACKS_SMALL_STEP") for item in reasons):
            value = value.rstrip("。.!！") + "。先把最卡的那一步拎出来。"
    if any(item.startswith("SELF_DOUBT_DEMEANING_SMALL_STEP") for item in reasons):
        value = re.sub(r"(?:白痴|弱智|蠢货|傻子|笨蛋)都能做的", "很小的", value)
        value = re.sub(r"(?:白痴|弱智|蠢货)", "", value)
    if any(item.startswith("MISPLACED_EMOTIONAL_CONTAINMENT") for item in reasons):
        value = value.replace("接得住", "听着").replace("接住", "回应")
        value = re.sub(r"我接(?:着|住)?", "我听着", value)
    return value.strip()


def build_surface_score_trace(
    *,
    candidates: Sequence[IsolatedCandidateScore],
    selected: IsolatedCandidateScore | None,
    observation_codes: Sequence[str],
) -> dict[str, Any]:
    return {
        "selected": selected.as_dict() if selected else None,
        "candidates": [candidate.as_dict() for candidate in candidates],
        "observation_codes": list(observation_codes),
        "candidate_count": len(candidates),
    }


def _coerce_surface_request(payload: IsolatedSurfaceInput | Mapping[str, Any]) -> IsolatedSurfaceInput:
    if isinstance(payload, IsolatedSurfaceInput):
        return payload
    return build_isolated_surface_input(payload)


async def _maybe_await_text(value: str | Awaitable[str]) -> str:
    if inspect.isawaitable(value):
        value = await value
    return str(value or "").strip()


_DIRECT_PROMPT_REPLACEMENTS = (
    (
        "你是隔离的最终表层文本生成器，只负责产出可发送的中文私聊候选。",
        "你是隔离的最终表层文本生成器，只负责一次性产出一条可发送的中文私聊回复，并给出夏目蓝说出这句时自己的 EmotionTag。",
    ),
    (
        '输出必须是 JSON 对象：{"candidates":["候选1","候选2","候选3"]}。',
        '输出必须是 JSON 对象：{"reply_text":"可直接发送给用户的一条中文回复","natsume_emotion_tag":"NEUTRAL","user_emotion_tag":"NEUTRAL","emotion_confidence":0.0}。',
    ),
    (
        "给 3 到 5 个候选。每个候选必须是用户可见文本，不要 Markdown、括号动作、字段名或解释。",
        "只给 1 条用户可见回复，放在 reply_text；不要 Markdown、括号动作、字段名或解释混进 reply_text。",
    ),
    (
        "ReplyControlPacket.must_include 是逐候选字面约束；每个候选都必须包含全部 must_include 词。must_not_do 也是逐候选约束；每个候选都不能包含其中任何词。",
        "ReplyControlPacket.must_include 是字面约束；最终回复必须包含全部 must_include 词。must_not_do 也是字面约束；最终回复不能包含其中任何词。",
    ),
    (
        "候选之间要有口吻差异，可以用轻微别扭、短促顶嘴、护短、收住情绪、小得意；不要都写成“记得/好的/没忘/发我看看”的平铺句。",
        "这唯一一条回复要有口吻：可以轻微别扭、短促顶嘴、护短、收住情绪或小得意；不要写成“记得/好的/没忘/发我看看”的平铺句。",
    ),
    (
        "日常/关系候选里，至少 2 个要带 SORA 式发话动作：短促截入、轻顶嘴、撒娇耍赖、嘴硬关心、小得意、护短接手、不承认但在意。不是堆口癖，而是让句子有转身。",
        "日常/关系回复里要带一点 SORA 式发话动作：短促截入、轻顶嘴、撒娇耍赖、嘴硬关心、小得意、护短接手、不承认但在意。不是堆口癖，而是让句子有转身。",
    ),
    (
        "不要让 3 到 5 个候选都只是安全确认句；“早，今天慢慢来”“回来啦，先坐一下”“记得啊，周末看展”这类平句只能当保底，不能当最有角色感的候选。",
        "不要让回复只是安全确认句；“早，今天慢慢来”“回来啦，先坐一下”“记得啊，周末看展”这类平句只能当保底，不能当最终质感。",
    ),
    (
        "每个候选都必须完成 TurnSurfaceContract.response_move；不要只写空泛支持、空泛安慰或单纯撒娇。",
        "最终回复必须完成 TurnSurfaceContract.response_move；不要只写空泛支持、空泛安慰或单纯撒娇。",
    ),
)
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
_PROCESS_PREFACE_SPLIT_RE = re.compile(
    r"(?:所以输出(?:这条|文本)?[：:。]|只输出(?:一条)?(?:最终回复)?文本[：:。]|_move[^。！？!?]{0,24}[。！？!?])"
)
_QUOTED_REPLY_BEFORE_PROCESS_RE = re.compile(r"^[\"“「](.{2,120}?)[\"”」]\s*_move")
_TEXT_INPUT_SENSORY_PATTERNS = (
    re.compile(r"(?:你这|你的|你那|老哥这|尼尼这).{0,3}声音"),
    re.compile(r"(?:一听|听起来|听上去|听得出|听出来).{0,16}(?:你|老哥|尼尼|昨晚|没睡|困|累|声音)"),
    re.compile(r"(?:我|本小姐).{0,6}(?:看到|看见|瞧见).{0,8}(?:你|老哥|尼尼|屏幕|画面)"),
    re.compile(r"(?:你|老哥|尼尼).{0,8}(?:脸色|表情|屏幕|画面|镜头)"),
)
_INTERNAL_LEAK_PATTERNS = (
    re.compile(r"cortex_orchestrator|ReplyControlPacket|score_trace|candidate_count", re.IGNORECASE),
    re.compile(r"候选|评分|控制包|内部字段|EmotionTag", re.IGNORECASE),
    re.compile(r"^\s*[\{\[]"),
)

def _direct_surface_messages(messages: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    direct: list[dict[str, str]] = []
    for index, message in enumerate(messages):
        role = str(message.get("role") or "")
        content = str(message.get("content") or "")
        if index == 0:
            for source, replacement in _DIRECT_PROMPT_REPLACEMENTS:
                content = content.replace(source, replacement)
            content = "\n".join(
                (
                    content,
                    "",
                    "## 结构化直出要求",
                    '只输出 JSON 对象：{"reply_text":"...","natsume_emotion_tag":"...","user_emotion_tag":"...","emotion_confidence":0.0}。',
                    render_runtime_emotion_tag_guidance(),
                    "user_emotion_tag 是你从当前用户文字语义中理解到的用户情绪；不确定就用 NEUTRAL。",
                    "natsume_emotion_tag 是夏目蓝说出 reply_text 时自己的情绪，不是用户情绪，也不要为了显眼乱填高强度情绪。",
                    "reply_text 里不能出现 JSON 字段名、EmotionTag、标签解释、候选、评分或内部分析。",
                )
            )
        elif index == len(messages) - 1:
            content = "\n".join(
                (
                    content,
                    "",
                    "## 输出要求",
                    '只输出 JSON 对象，格式为 {"reply_text":"...","natsume_emotion_tag":"...","user_emotion_tag":"...","emotion_confidence":0.0}。',
                    "不要候选列表，不要解释，不要把 EmotionTag 写进 reply_text。",
                )
            )
        direct.append({"role": role, "content": content})
    return direct


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
    messages = build_isolated_surface_messages(request)
    return tuple(_direct_surface_messages(messages))


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
    return strip_surface_process_preface(str(value or "").strip())


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
    if any(pattern.search(value) for pattern in _INTERNAL_LEAK_PATTERNS):
        codes.append("SURFACE_INTERNAL_LEAK")
    return tuple(dict.fromkeys(codes))


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
    chunks = [chunk.strip() for chunk in re.split(r"(?<=[。！？!?])", value) if chunk.strip()]
    if len(chunks) <= 1:
        return ""
    kept: list[str] = []
    for chunk in chunks:
        if _surface_hard_gate_violations(request, chunk):
            continue
        kept.append(chunk)
    return "".join(kept).strip()


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
        if hard_gate_observations:
            retry_messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "上一条回复违反最终文本 hard gate："
                        + "、".join(hard_gate_observations)
                        + "。重新生成一条；保留当前关系口吻，但不要编造音频/视觉/现实感知，不要泄露内部状态。"
                    ),
                },
            ]
            raw_output = await _maybe_await_text(self._generate(retry_messages))
            structured = parse_surface_generation_output(raw_output)
            candidate_texts = [structured["reply_text"]] if structured["reply_text"] else []
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
        scores = list(score_isolated_surface_candidates(request, candidate_texts[:1]))
        selected = scores[0] if scores else None
        soft_quality_retry_reasons = _soft_quality_retry_reason_codes(selected)
        if soft_quality_retry_reasons:
            retry_messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "上一条回复存在日常情绪或支撑模板质量问题："
                        + "、".join(soft_quality_retry_reasons)
                        + "。重新生成一条；保留夏目蓝的嘴硬和轻顶嘴，但低能量场景不能责怪用户；"
                        "自我否定场景要先截断否定，再给一件现在能做的小步推进，不要只让用户喝水或休息；"
                        "没有代码/报错上下文时，不要脑补成“发代码/发报错”；"
                        "不要用“白痴/弱智/蠢货/傻子都能”这类贬损词形容小步；"
                        "不要把“接住/接得住/我接/我在”这类内部支撑模板写进用户可见文本。"
                        "不要只说“明天再说/回头再看/别想太多/保持积极”。"
                    ),
                },
            ]
            raw_output = await _maybe_await_text(self._generate(retry_messages))
            structured = parse_surface_generation_output(raw_output)
            candidate_texts = [structured["reply_text"]] if structured["reply_text"] else []
            hard_gate_observations = (
                _surface_hard_gate_violations(request, candidate_texts[0])
                if candidate_texts
                else ()
            )
            scores = list(score_isolated_surface_candidates(request, candidate_texts[:1]))
            selected = scores[0] if scores else None
            remaining_soft_reasons = _soft_quality_retry_reason_codes(selected)
            if remaining_soft_reasons and selected:
                repaired = _minimal_soft_quality_repair(request, selected.text, remaining_soft_reasons)
                if repaired and repaired != selected.text:
                    repaired_scores = list(score_isolated_surface_candidates(request, [repaired]))
                    repaired_selected = repaired_scores[0] if repaired_scores else None
                    if repaired_selected and not _soft_quality_retry_reason_codes(repaired_selected):
                        structured = {**structured, "reply_text": repaired}
                        candidate_texts = [repaired]
                        scores = repaired_scores
                        selected = repaired_selected
        observation_codes = tuple(
            dict.fromkeys([*surface_observation_codes(selected), *hard_gate_observations])
        )
        score_trace = build_surface_score_trace(
            candidates=scores,
            selected=selected,
            observation_codes=observation_codes,
        )
        return SurfaceRuntimeResult(
            request=request,
            messages=tuple(messages),
            raw_output=raw_output,
            candidates=tuple(scores),
            selected=selected,
            selected_text=selected.text if selected else "",
            observation_codes=observation_codes,
            score_trace=score_trace,
            natsume_emotion_tag=str(structured.get("natsume_emotion_tag") or "NEUTRAL"),
            user_emotion_tag=str(structured.get("user_emotion_tag") or "NEUTRAL"),
            vts_expression_id=str(structured.get("vts_expression_id") or ""),
            emotion_confidence=float(structured.get("emotion_confidence") or 0.0),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


__all__ = [
    "SurfaceRuntime",
    "SurfaceRuntimeResult",
    "build_direct_surface_messages",
    "build_surface_score_trace",
    "parse_surface_generation_output",
    "strip_surface_process_preface",
    "surface_observation_codes",
]
