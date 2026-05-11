from __future__ import annotations

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
from host_brain.persona.sample_guided_text_scorer import score_sample_guided_text
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
    "happy": {"intent_tags": ("daily",), "emotion_tags": ("playful",), "style_moves": ("quick_reaction", "smug_tease")},
    "annoying_tease": {"intent_tags": ("annoyed",), "emotion_tags": ("annoyed", "angry_light"), "style_moves": ("quick_rebuttal", "familiar_pushback")},
    "praise": {"intent_tags": ("praise",), "emotion_tags": ("shy",), "style_moves": ("shy_deflection",)},
    "apology": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "help_takeover": {"intent_tags": ("help",), "emotion_tags": ("care",), "style_moves": ("take_charge", "care_behind_tease")},
    "write_takeover": {"intent_tags": ("help",), "emotion_tags": ("care",), "style_moves": ("take_charge", "care_behind_tease")},
    "advice_continue": {"intent_tags": ("help",), "emotion_tags": ("care",), "style_moves": ("take_charge", "care_behind_tease")},
    "next_step": {"intent_tags": ("help",), "emotion_tags": ("care",), "style_moves": ("take_charge", "care_behind_tease")},
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
    "short_ack": {"intent_tags": ("daily",), "style_moves": ("quick_reaction", "question_chain")},
    "followup": {"intent_tags": ("daily",), "style_moves": ("question_chain", "quick_reaction")},
    "give_up": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "complaint": {"intent_tags": ("help", "reassurance"), "emotion_tags": ("care",), "style_moves": ("take_charge", "care_behind_tease")},
    "late_night": {"intent_tags": ("reassurance",), "emotion_tags": ("soft",), "style_moves": ("soft_reassurance",)},
    "daily_chat": {"intent_tags": ("daily",), "style_moves": ("quick_reaction",)},
    "default": {"intent_tags": ("daily",), "style_moves": ("quick_reaction",)},
}


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


def build_style_reference_query(request: IsolatedSurfaceInput, *, limit: int = 5) -> StyleReferenceQuery:
    scene = _clean_text(request.control.task_mode or request.control.response_move) or "default"
    mapped = _SCENE_STYLE_QUERY.get(scene) or _SCENE_STYLE_QUERY.get(request.control.response_move) or _SCENE_STYLE_QUERY["default"]
    return StyleReferenceQuery(
        lang="zh",
        intent_tags=tuple(str(item) for item in mapped.get("intent_tags", ()) or ()),
        emotion_tags=tuple(str(item) for item in mapped.get("emotion_tags", ()) or ()),
        style_moves=tuple(str(item) for item in mapped.get("style_moves", ()) or ()),
        relationship_stage="sibling_good",
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
    control_payload = _model_dump(control)
    control_payload["facts"] = merged_facts(request)
    control_payload["must_not_do"] = merged_must_not_do(request)
    system_prompt = "\n".join(
        (
            "你是隔离的最终表层文本生成器，只负责产出可发送的中文私聊候选。",
            "唯一允许读取的输入：Soul.md、当前用户输入、ReplyControlPacket、必要事实列表、must_not_do 列表。",
            "禁止使用或推断任何未提供的历史对话、长期记忆、器官族原始输出、工具候选、RAG 文档或模块自然语言建议。",
            "ReplyControlPacket 是最终生成层的唯一运行控制面；不得要求更多 runtime state。",
            "输出必须是 JSON 对象：{\"candidates\":[\"候选1\",\"候选2\",\"候选3\"]}。",
            "给 3 到 5 个候选。每个候选必须是用户可见文本，不要 Markdown、括号动作、字段名或解释。",
            f"question_budget={int(control.question_budget)}；超过预算的候选会被扣分。",
            "",
            "## Soul.md",
            request.soul_md.strip(),
        )
    )
    user_prompt = "\n".join(
        (
            "## 当前用户输入",
            request.user_message,
            "",
            "## ReplyControlPacket",
            json.dumps(control_payload, ensure_ascii=False, indent=2, sort_keys=True),
            "",
            "## 必要事实",
            "\n".join(f"- {item}" for item in merged_facts(request)) or "- 无",
            "",
            "## must_not_do",
            "\n".join(f"- {item}" for item in merged_must_not_do(request)) or "- 无",
        )
    )
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


def parse_surface_candidates(raw_output: Any) -> list[str]:
    raw = _clean_text(raw_output)
    if not raw:
        return []
    payload: Any
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = raw
    candidates: list[str] = []
    if isinstance(payload, Mapping):
        raw_candidates = payload.get("candidates")
        if isinstance(raw_candidates, list):
            candidates.extend(_clean_text(item) for item in raw_candidates)
        reply = _clean_text(payload.get("reply") or payload.get("text"))
        if reply:
            candidates.append(reply)
    elif isinstance(payload, list):
        candidates.extend(_clean_text(item) for item in payload)
    else:
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
        )
        violations = _control_violations(request, quality.normalized_text or candidate)
        score = float(sample_score.score)
        if violations:
            score -= min(42.0, 14.0 * len(violations))
        score = max(0.0, min(100.0, score))
        hard_fail = bool(sample_score.hard_fail)
        passed = bool(score >= 80.0 and not hard_fail and not violations)
        rows.append(
            IsolatedCandidateScore(
                text=quality.normalized_text or candidate,
                score=score,
                passed=passed,
                hard_fail=hard_fail,
                reason_codes=tuple(dict.fromkeys([*sample_score.reason_codes, *quality.reason_codes])),
                control_violations=tuple(violations),
                dimensions=dict(sample_score.dimensions),
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
    "build_isolated_surface_input",
    "build_isolated_surface_messages",
    "build_style_reference_query",
    "derive_style_reference_anchor",
    "enrich_request_style_anchor",
    "find_forbidden_input_paths",
    "merged_facts",
    "merged_must_not_do",
    "parse_surface_candidates",
    "run_isolated_surface_generation",
    "score_isolated_surface_candidates",
    "select_best_isolated_candidate",
]
