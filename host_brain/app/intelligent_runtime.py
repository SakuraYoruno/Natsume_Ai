from __future__ import annotations

import json
import logging
import re
from typing import Any, Mapping

from host_brain.skills.skill_composer import SkillComposer, SkillSpec

logger = logging.getLogger("Natsume.app.intelligent_runtime")

_EMOJI_PATTERN = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
_JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)

_TTS_EMOTION_MAP: dict[str, str] = {
    "NEUTRAL": "neutral",
    "HAPPY": "happiness",
    "HAPPINESS": "happiness",
    "JOY": "happiness",
    "GRATEFUL": "happiness",
    "PROUD": "happiness",
    "SURPRISED": "happiness",
    "EXCITED": "excitement",
    "EXCITEMENT": "excitement",
    "PLAYFUL": "playful",
    "SAD": "sadness",
    "SADNESS": "sadness",
    "ANXIOUS": "sadness",
    "BORED": "sadness",
    "CALM": "comfort",
    "COMFORT": "comfort",
    "THINKING": "serious",
    "CONFUSED": "serious",
    "ANGRY": "serious",
    "ANGER": "serious",
    "FRUSTRATED": "serious",
    "SERIOUS": "serious",
    "LOVING": "coquettish",
    "EMBARRASSED": "coquettish",
    "AFFECTION": "coquettish",
    "COQUETTISH": "coquettish",
}

_CAPABILITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "translation": ("翻译", "translate", "日语", "英语"),
    "tts": ("tts", "语音", "朗读", "播报", "speak"),
    "web_search": ("搜索", "检索", "查找", "web"),
    "summarization": ("总结", "摘要", "概括"),
    "session_summary": ("回顾", "总结", "摘要", "session"),
    "emotion_status": ("情绪", "心情", "感受", "emotion", "mood"),
    "bond_status": ("关系", "亲密", "bond", "阶段"),
    "status": ("状态", "运行", "health", "status"),
    "memory": ("记忆", "回忆", "memory"),
    "calculation": ("计算", "算", "数学"),
    "creative_writing": ("写", "创作", "诗", "文案"),
    "reasoning": ("分析", "推理", "判断"),
    "image_generation": ("画", "图片", "图像", "生成图"),
    "ja_style": ("日语", "日文", "ja"),
    "en_style": ("英语", "英文", "en"),
}

_AUTO_RUNNABLE_SKILLS = frozenset({
    "emotion_query",
    "bond_query",
    "session_summary",
    "read_only_status",
    "memory_recall",
    "narrative_only",
    "noop",
})

_BUILTIN_COMPOSER_SPECS: tuple[SkillSpec, ...] = (
    SkillSpec(
        skill_id="emotion_query",
        name="emotion_query",
        input_types=["text"],
        output_types=["text"],
        capabilities=["emotion_status", "reasoning"],
        avg_latency_ms=20.0,
    ),
    SkillSpec(
        skill_id="bond_query",
        name="bond_query",
        input_types=["text"],
        output_types=["text"],
        capabilities=["bond_status", "reasoning"],
        avg_latency_ms=20.0,
    ),
    SkillSpec(
        skill_id="session_summary",
        name="session_summary",
        input_types=["text"],
        output_types=["text"],
        capabilities=["session_summary", "summarization"],
        avg_latency_ms=20.0,
    ),
    SkillSpec(
        skill_id="read_only_status",
        name="read_only_status",
        input_types=["text"],
        output_types=["text"],
        capabilities=["status", "reasoning"],
        avg_latency_ms=20.0,
    ),
    SkillSpec(
        skill_id="memory_recall",
        name="memory_recall",
        input_types=["text"],
        output_types=["text"],
        capabilities=["memory", "reasoning"],
        avg_latency_ms=20.0,
    ),
)


def build_prompt_intelligence(*, request_id: str, runtime_input: Any) -> dict[str, Any]:
    from host_brain.app.dependencies import (
        get_bond_aware_style_engine,
        get_meta_learning_loop,
        get_multimodal_processor,
        get_planning_agent,
        get_predictive_state_engine,
    )

    user_message = str(getattr(runtime_input, "user_message", "") or "").strip()
    render_lang = str(getattr(runtime_input, "render_target_lang", "") or "zh").strip().lower() or "zh"
    emotion_state = dict(getattr(runtime_input, "emotion_state", {}) or {})
    bond_summary = dict(getattr(runtime_input, "bond_summary", {}) or {})
    scene_type = str(getattr(runtime_input, "scene_type", "") or "").strip().upper()
    skill_manifest = list(getattr(runtime_input, "skill_manifest", []) or [])

    intelligence: dict[str, Any] = {}
    meta_learning = get_meta_learning_loop()
    safe_skill_runners, safe_skill_names = _build_safe_skill_runners(runtime_input=runtime_input)

    multimodal = get_multimodal_processor().process_input(text=user_message)
    multimodal_payload = multimodal.as_dict()
    multimodal_payload["items"] = [
        {
            **item.as_dict(),
            "analysis": str(item.analysis or "")[:200],
        }
        for item in multimodal.items
    ]
    intelligence["multimodal_input"] = multimodal_payload
    meta_learning.record(
        "multimodal_processor",
        success=True,
        latency_ms=0.0,
        custom={
            "items": float(len(multimodal.items)),
            "emotion_signals": float(len(multimodal.emotion_signals)),
        },
    )

    predicted_state = get_predictive_state_engine().predict_current()
    if predicted_state is not None:
        intelligence["predictive_state"] = predicted_state.as_dict()
        meta_learning.record(
            "predictive_state",
            success=True,
            latency_ms=0.0,
            custom={"confidence": float(predicted_state.confidence)},
        )

    bond_stage = str(bond_summary.get("stage") or "STRANGER").strip().upper() or "STRANGER"

    # ── 获取共同记忆叙事片段（安全降级）──────────────────────────
    _shared_memory_hints: list[str] = []
    try:
        from host_brain.bond.shared_memory_index import SharedMemoryIndex  # noqa: PLC0415
        from host_brain.bond.bond_narrative import BondNarrativeLayer  # noqa: PLC0415
        from host_brain.app.dependencies import get_bond_stage_engine  # noqa: PLC0415
        _smi: Any = None
        _mt: Any = None
        try:
            from host_brain.app.dependencies import get_shared_memory_index, get_milestone_tracker  # noqa: PLC0415
            _smi = get_shared_memory_index()
            _mt = get_milestone_tracker()
        except (ImportError, AttributeError):
            pass
        if _smi is not None:
            _bnl = BondNarrativeLayer(memory_index=_smi, milestone_tracker=_mt)
            _uid = str(getattr(runtime_input, "user_id", "") or "default")
            _bn_result = _bnl.build_narrative(user_id=_uid, bond_stage=bond_stage)
            _shared_memory_hints = [
                item.text for item in _bn_result.items if item.text.strip()
            ][:3]
    except Exception:
        pass  # 共同记忆获取失败时安全降级，不影响主流程

    bond_style = get_bond_aware_style_engine().generate(
        bond_stage=bond_stage,
        mood_state=str(emotion_state.get("mood_state") or "neutral").strip().lower(),
        rift_active=bool(bond_summary.get("rift_active", False)),
        bond_trend=str(bond_summary.get("trend") or "").strip(),
        texture_hint=str(bond_summary.get("texture_hint") or "").strip(),
        shared_memory_hints=_shared_memory_hints or None,
    )
    intelligence["bond_aware_style"] = {
        **bond_style.as_dict(),
        "prompt_injection": bond_style.prompt_injection,
        "forbidden_patterns": list(bond_style.forbidden_patterns),
    }
    meta_learning.record(
        "bond_aware_style",
        success=True,
        latency_ms=0.0,
        custom={"intimacy": float(bond_style.intimacy_level)},
    )

    planning_agent = get_planning_agent()
    needs_planning = scene_type in {"EXEC_INTENT", "WORK_CODE", "WORK_TRANSLATE"} or planning_agent.needs_planning(user_message)
    if needs_planning:
        available_skills = list(safe_skill_names)
        plan = planning_agent.create_plan(
            task_id=str(request_id or "plan"),
            request=user_message,
            available_skills=available_skills or None,
        )
        intelligence["planning_outline"] = {
            **plan.as_dict(),
            "step_descriptions": [
                str(step.description)
                for step in plan.steps[:6]
            ],
        }
        meta_learning.record(
            "planning_agent",
            success=True,
            latency_ms=0.0,
            custom={"steps": float(len(plan.steps))},
        )
        if any(str(step.skill_name or "").strip() for step in plan.steps):
            planning_execution = planning_agent.execute_plan(
                plan,
                skill_runners=safe_skill_runners,
                base_context=_build_skill_execution_context(runtime_input),
            )
            intelligence["planning_execution"] = planning_execution.as_dict()
            meta_learning.record(
                "planning_agent_execute",
                success=bool(planning_execution.success),
                latency_ms=float(planning_execution.elapsed_ms),
                custom={
                    "executed_skills": float(len(planning_execution.executed_skills)),
                    "needs_user_input": 1.0 if planning_execution.needs_user_input else 0.0,
                },
            )

    composer = SkillComposer()
    _register_builtin_composer_skills(composer=composer)
    skill_aliases = _register_manifest_skills(composer=composer, skill_manifest=skill_manifest)
    for alias, skill_name in skill_aliases.items():
        runner = safe_skill_runners.get(skill_name)
        if runner is not None and alias not in safe_skill_runners:
            safe_skill_runners[alias] = runner
    skill_dag = composer.plan(user_message) if safe_skill_runners else None
    if skill_dag is not None and skill_dag.nodes:
        intelligence["skill_dag"] = {
            **skill_dag.as_dict(),
            "node_ids": [node.node_id for node in skill_dag.nodes],
        }
        execution_result = composer.execute(
            skill_dag,
            skill_runners=safe_skill_runners,
        )
        intelligence["skill_execution"] = execution_result.as_dict()
        meta_learning.record(
            "skill_composer",
            success=bool(execution_result.success),
            latency_ms=float(execution_result.elapsed_ms),
            custom={
                "nodes": float(len(skill_dag.nodes)),
                "completed_nodes": float(execution_result.completed_nodes),
            },
        )

    return intelligence


def apply_response_intelligence(
    *,
    request_id: str,
    user_message: str,
    narrative: str,
    emotion_context: Mapping[str, Any] | None,
    bond_summary: Mapping[str, Any] | None,
    render_lang: str,
    allow_rewrite: bool = True,
) -> tuple[str, dict[str, Any]]:
    from host_brain.app.dependencies import (
        get_meta_learning_loop,
        get_multimodal_processor,
        get_reflection_chain,
    )

    reflection = get_reflection_chain()
    meta_learning = get_meta_learning_loop()
    raw_text = _normalize_narrative_payload(str(narrative or "").strip())
    if not raw_text:
        return raw_text, {
            "reflection": {"status": "SKIPPED", "reason_code": "EMPTY_NARRATIVE"},
            "multimodal_output": {"status": "SKIPPED", "reason_code": "EMPTY_NARRATIVE"},
        }

    emotion_tag = str((emotion_context or {}).get("emotion_tag") or "").strip()
    bond_stage = str((bond_summary or {}).get("stage") or "STRANGER").strip().upper() or "STRANGER"
    reflection_meta: dict[str, Any] = {"status": "SKIPPED", "reason_code": "NOT_TRIGGERED"}
    revised_text = raw_text

    if reflection.should_reflect(
        emotion_tag=emotion_tag,
        intent=str((bond_summary or {}).get("intent") or ""),
        bond_stage=bond_stage,
        turn_count=int((emotion_context or {}).get("turn_count") or 1),
    ):
        reflection_result = reflection.reflect(
            user_message=user_message,
            ai_response=raw_text,
            emotion_tag=emotion_tag,
            bond_stage=bond_stage,
        )
        reflection_meta = {
            "status": "ACTIVE",
            "before": reflection_result.as_dict(),
            "revised": False,
        }
        if reflection_result.should_revise:
            if allow_rewrite:
                revision = reflection.revise(
                    user_message=user_message,
                    ai_response=raw_text,
                    emotion_tag=emotion_tag,
                    bond_stage=bond_stage,
                )
                revised_text = _sanitize_narrative(
                    text=revision.revised_text,
                    bond_stage=bond_stage,
                    render_lang=render_lang,
                )
                if revised_text != raw_text:
                    reflection_meta["revised"] = True
                    reflection_meta["revision_mode"] = "reflection_revise"
                    reflection_meta["operations"] = list(revision.operations)
                    reflection_meta["after"] = (
                        revision.reflection_after.as_dict()
                        if revision.reflection_after is not None
                        else reflection.reflect(
                            user_message=user_message,
                            ai_response=revised_text,
                            emotion_tag=emotion_tag,
                            bond_stage=bond_stage,
                        ).as_dict()
                    )
            else:
                reflection_meta["revised"] = False
                reflection_meta["revision_mode"] = "stream_audit_only"
        meta_learning.record(
            "reflection_chain",
            success=bool(reflection_meta.get("after", {}).get("passed", not reflection_result.should_revise)),
            latency_ms=0.0,
            custom={
                "score": float(reflection_result.overall_score),
                "revised": 1.0 if reflection_meta.get("revised") else 0.0,
            },
        )

    multimodal_output = get_multimodal_processor().enhance_output(
        text=revised_text,
        emotion_tag=_normalize_emotion_tag(emotion_tag),
        bond_stage=bond_stage,
        render_lang=render_lang,
    )
    final_text = _apply_multimodal_suffix(revised_text, multimodal_output)
    multimodal_meta = {
        "status": "ACTIVE",
        "result": multimodal_output.as_dict(),
    }
    meta_learning.record(
        "multimodal_output",
        success=True,
        latency_ms=0.0,
        custom={
            "emoji": 1.0 if multimodal_output.suggested_emoji else 0.0,
            "items": float(len(multimodal_output.items)),
        },
    )
    logger.debug("response intelligence applied request_id=%s revised=%s", request_id, final_text != raw_text)
    return final_text, {
        "reflection": reflection_meta,
        "multimodal_output": multimodal_meta,
    }


def resolve_tts_route(
    *,
    text: str,
    emotion_context: Mapping[str, Any] | None,
    bond_summary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    from host_brain.app.dependencies import get_meta_learning_loop, get_tts_context_router

    emotion_tag = str((emotion_context or {}).get("emotion_tag") or "").strip().upper()
    bond_stage = str((bond_summary or {}).get("stage") or "STRANGER").strip().upper() or "STRANGER"
    vad = dict((emotion_context or {}).get("vad_vector") or {})
    valence = _coerce_optional_float(vad.get("valence"))
    arousal = _coerce_optional_float(vad.get("arousal"))
    router = get_tts_context_router()
    routed = router.route(
        text=text,
        emotion_tag=_normalize_emotion_tag(emotion_tag),
        bond_stage=bond_stage,
        valence=valence,
        arousal=arousal,
    )
    get_meta_learning_loop().record(
        "tts_context_router",
        success=True,
        latency_ms=0.0,
        custom={"speed_scale": float(routed.speed_scale)},
    )
    return {
        "speed": max(0.7, min(1.3, float(routed.speed_scale))),
        "pitch": max(-1.0, min(1.0, float(routed.pitch_shift) / 2.0)),
        "emotion_tag": str(routed.emotion_tag or _normalize_emotion_tag(emotion_tag)).strip() or "neutral",
        "style_note": str(routed.style_note or "").strip(),
        "lang_hint": str(routed.lang_hint or "zh").strip().lower() or "zh",
        "route": routed.as_dict(),
    }


def _register_builtin_composer_skills(*, composer: SkillComposer) -> None:
    for spec in _BUILTIN_COMPOSER_SPECS:
        try:
            composer.register_skill(spec)
        except Exception:
            continue


def _register_manifest_skills(
    *,
    composer: SkillComposer,
    skill_manifest: list[dict[str, Any]],
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for item in list(skill_manifest or []):
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        skill_id = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "skill"
        capabilities = _infer_capabilities(name=name, description=str(item.get("description") or ""))
        if not capabilities:
            capabilities = [skill_id]
        spec = SkillSpec(
            skill_id=skill_id,
            name=name,
            input_types=["text"],
            output_types=["text"],
            capabilities=capabilities,
            avg_latency_ms=250.0,
        )
        try:
            composer.register_skill(spec)
            aliases[skill_id] = name
        except Exception:
            continue
    return aliases


def _infer_capabilities(*, name: str, description: str) -> list[str]:
    haystack = f"{name}\n{description}".lower()
    capabilities: list[str] = []
    for capability, keywords in _CAPABILITY_KEYWORDS.items():
        if any(keyword.lower() in haystack for keyword in keywords):
            capabilities.append(capability)
    return capabilities


def _build_skill_execution_context(runtime_input: Any) -> dict[str, Any]:
    bond_summary = dict(getattr(runtime_input, "bond_summary", {}) or {})
    emotion_state = dict(getattr(runtime_input, "emotion_state", {}) or {})
    emotion_snapshot = dict(getattr(runtime_input, "emotion_snapshot", {}) or {})
    return {
        "request_id": str(getattr(runtime_input, "request_id", "") or "").strip(),
        "session_id": str(getattr(runtime_input, "session_id", "") or "").strip(),
        "thread_id": getattr(runtime_input, "thread_id", None),
        "user_message": str(getattr(runtime_input, "user_message", "") or "").strip(),
        "emotion_context": emotion_state,
        "emotion_snapshot": emotion_snapshot,
        "bond_stage": str(bond_summary.get("stage") or "STRANGER").strip().upper() or "STRANGER",
        "bond_score": bond_summary.get("score"),
        "bond_texture": bond_summary,
        "turn_count": int(emotion_state.get("turn_count") or 0),
    }


def _build_safe_skill_runners(*, runtime_input: Any) -> tuple[dict[str, Any], list[str]]:
    from host_brain.app.dependencies import get_skill_registry

    registry = get_skill_registry()
    context = _build_skill_execution_context(runtime_input)
    runners: dict[str, Any] = {}

    for meta in registry.all():
        skill_name = str(meta.skill_name or "").strip()
        if not skill_name:
            continue
        if skill_name not in _AUTO_RUNNABLE_SKILLS:
            continue
        if meta.require_confirm or int(meta.risk_level or 0) > 1:
            continue
        runners[skill_name] = _make_safe_skill_runner(meta=meta, context=context)

    return runners, sorted(runners.keys())


def _make_safe_skill_runner(*, meta: Any, context: dict[str, Any]) -> Any:
    def _runner(payload: Mapping[str, Any] | None) -> dict[str, Any]:
        raw_payload = dict(payload or {})
        args = {
            "request": raw_payload.get("request"),
            "description": raw_payload.get("description"),
            "step_id": raw_payload.get("step_id"),
            "session_id": context.get("session_id"),
            "request_id": context.get("request_id"),
            "user_message": context.get("user_message"),
        }
        result = meta.handler(args, dict(context))
        return _summarize_skill_result(skill_name=str(meta.skill_name), result=result)

    return _runner


def _summarize_skill_result(*, skill_name: str, result: Any) -> dict[str, Any]:
    if isinstance(result, Mapping):
        summary: dict[str, Any] = {
            "skill_name": skill_name,
            "status": str(result.get("status") or "OK"),
            "result": str(result.get("result") or skill_name),
        }
        for key in ("emotion", "bond", "summary", "detail", "payload"):
            if key in result and result.get(key) not in (None, "", [], {}):
                summary[key] = result.get(key)
        if "preview" not in summary:
            summary["preview"] = str(result)[:200]
        return summary
    return {
        "skill_name": skill_name,
        "status": "OK",
        "result": skill_name,
        "preview": str(result)[:200],
    }


def _sanitize_narrative(*, text: str, bond_stage: str, render_lang: str) -> str:
    sanitized = _normalize_narrative_payload(str(text or "").strip())
    replacements = (
        (r"作为(一个)?AI[^，。！？]*[，。！？]?", ""),
        (r"我是(一个)?AI[^，。！？]*[，。！？]?", ""),
        (r"您好[，,]?", ""),
        (r"如有问题[^，。！？]*", ""),
        (r"感谢您的[^，。！？]*", ""),
    )
    for pattern, replacement in replacements:
        sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if not sanitized:
        sanitized = "我在。"
    if bond_stage == "STRANGER" and render_lang == "zh":
        sanitized = sanitized.replace("呢。", "。").replace("呀。", "。")
    return sanitized


def _normalize_narrative_payload(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""

    candidates = [normalized]
    fenced = _JSON_FENCE_PATTERN.search(normalized)
    if fenced:
        fenced_payload = str(fenced.group(1) or "").strip()
        if fenced_payload:
            candidates.insert(0, fenced_payload)

    for candidate in candidates:
        extracted = _try_extract_narrative_text(candidate)
        if extracted:
            return extracted
    return normalized


def _try_extract_narrative_text(candidate: str) -> str | None:
    text = str(candidate or "").strip()
    if not text or not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except Exception:
        return None
    if not isinstance(payload, Mapping):
        return None

    narrative = payload.get("narrative")
    if isinstance(narrative, Mapping):
        extracted = str(
            narrative.get("text")
            or narrative.get("content")
            or narrative.get("reply")
            or ""
        ).strip()
        return extracted or None
    if isinstance(narrative, str):
        extracted = narrative.strip()
        return extracted or None

    direct_text = str(payload.get("text") or "").strip()
    return direct_text or None


def _apply_multimodal_suffix(text: str, multimodal_output: Any) -> str:
    final_text = str(text or "").strip()
    if not final_text:
        return final_text
    suffixes: list[str] = []
    suggested_emoji = str(getattr(multimodal_output, "suggested_emoji", "") or "").strip()
    if suggested_emoji and not _EMOJI_PATTERN.search(final_text):
        suffixes.append(suggested_emoji)
    for item in list(getattr(multimodal_output, "items", []) or []):
        content = str(getattr(item, "content", "") or "").strip()
        if content and content not in final_text:
            suffixes.append(content)
            break
    if not suffixes:
        return final_text
    if len(final_text) > 180:
        return final_text
    return f"{final_text} {' '.join(suffixes[:2])}".strip()


def _normalize_emotion_tag(emotion_tag: str) -> str:
    normalized = str(emotion_tag or "").strip().upper()
    if not normalized:
        return "neutral"
    return _TTS_EMOTION_MAP.get(normalized, normalized.lower())


def _coerce_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
