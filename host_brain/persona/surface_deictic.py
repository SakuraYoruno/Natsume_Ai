"""Temporal-deictic policy helpers for the persona surface.

The helpers are pure presentation checks.  Keeping them outside the runtime
orchestrator prevents time-of-day wording policy from inflating the surface
execution module.
"""

from __future__ import annotations

import re
from typing import Any

from host_brain.persona.isolated_surface_harness import (
    IsolatedSurfaceInput,
    merged_must_not_do,
    surface_prompt_facts,
)


_PHASE_FAMILY_ALIASES = {
    "dawn": "morning",
    "early_morning": "morning",
    "morning": "morning",
    "late_morning": "late_morning",
    "noon": "noon",
    "lunch_window": "noon",
    "afternoon": "afternoon",
    "dusk": "evening",
    "evening": "evening",
    "dinner_window": "evening",
    "night": "night",
    "late_night": "night",
    "late_snack_window": "night",
}
_MORNING_DEICTIC_START_RE = re.compile(
    r"^(?:早安|早上好|早晨好|上午好|早啊|早呀|早哦|早)(?:[。.!！?？~～]|$)"
)
_MORNING_DEICTIC_MENTION_RE = re.compile(r"(?:早安|早上好|早晨好|上午好|早啊|早呀|早哦)")
_TEMPORAL_DEICTIC_CORRECTION_RE = re.compile(
    r"(?:现在|当前|都|几点|什么时候|下午|晚上|夜|晚了|不是早上|还早上|说早|怎么还|怎么又)"
)


def _surface_morning_deictic_user_match(user: str) -> bool:
    value = re.sub(r"\s+", "", str(user or "")).strip()
    if not value:
        return False
    if _MORNING_DEICTIC_START_RE.search(value):
        return True
    return bool(
        _MORNING_DEICTIC_MENTION_RE.search(value)
        and _TEMPORAL_DEICTIC_CORRECTION_RE.search(value)
    )


_DEICTIC_PHASE_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "kind": "morning",
        "label": "早晨/早安",
        "valid_families": {"morning"},
        "user_re": re.compile(r"^(?:早|早啊|早呀|早安|早上好|早晨好|上午好)(?:[。.!！?？~～]*)?$"),
        "forbidden": (
            "早啊", "早呀", "早安", "早上好", "早晨", "上午", "一大早", "大清早",
            "这么精神", "刚醒", "刚睡醒", "睡醒", "没醒", "没睡醒", "睡迷糊", "迷糊",
            "糊涂", "脑子", "烧糊", "盯屏幕", "屏幕",
        ),
    },
    {
        "kind": "noon",
        "label": "中午/午安",
        "valid_families": {"noon"},
        "user_re": re.compile(r"^(?:午安|中午好|午好)(?:[。.!！?？~～]*)?$"),
        "forbidden": ("午安", "中午好", "午好", "午饭", "午休", "大中午"),
    },
    {
        "kind": "afternoon",
        "label": "下午",
        "valid_families": {"afternoon"},
        "user_re": re.compile(r"^(?:下午好|下午安)(?:[。.!！?？~～]*)?$"),
        "forbidden": ("下午好", "下午安", "大下午"),
    },
    {
        "kind": "evening",
        "label": "傍晚/晚上",
        "valid_families": {"evening", "night"},
        "user_re": re.compile(r"^(?:晚上好|傍晚好|晚好)(?:[。.!！?？~～]*)?$"),
        "forbidden": ("晚上好", "傍晚好", "晚好", "大晚上"),
    },
    {
        "kind": "night",
        "label": "晚安/夜间",
        "valid_families": {"night"},
        "user_re": re.compile(r"^(?:晚安|夜安)(?:[。.!！?？~～]*)?$"),
        "forbidden": ("晚安", "夜安", "睡前", "该睡", "睡觉", "夜里"),
    },
)
_PHASE_FACT_RE = re.compile(
    r"(?:阶段|day_phase|当前时段)\s*(?:是|=|：|:)\s*([A-Za-z_]+)",
    re.IGNORECASE,
)


def _current_phase_family_from_facts(request: IsolatedSurfaceInput) -> tuple[str, str]:
    facts_text = "\n".join(str(item or "") for item in surface_prompt_facts(request))
    for match in _PHASE_FACT_RE.finditer(facts_text):
        phase = str(match.group(1) or "").strip().lower()
        if phase:
            return phase, _PHASE_FAMILY_ALIASES.get(phase, phase)
    try:
        from host_brain.runtime.environment_state import build_environment_snapshot  # noqa: PLC0415

        environment = build_environment_snapshot()
        phase = str(
            getattr(environment, "get", lambda *_args, **_kwargs: "")("day_phase") or ""
        ).strip().lower()
        if phase:
            return phase, _PHASE_FAMILY_ALIASES.get(phase, phase)
    except Exception:
        pass
    return "", ""


def _surface_deictic_phase_profile(request: IsolatedSurfaceInput) -> dict[str, Any]:
    user = re.sub(r"\s+", "", str(getattr(request, "user_message", "") or "")).strip()
    if not user:
        return {}
    phase, family = _current_phase_family_from_facts(request)
    if not family:
        return {}
    for profile in _DEICTIC_PHASE_PROFILES:
        user_re = profile.get("user_re")
        matched = bool(hasattr(user_re, "search") and user_re.search(user))
        if not matched and str(profile.get("kind") or "") == "morning":
            matched = _surface_morning_deictic_user_match(user)
        if matched:
            valid = set(profile.get("valid_families") or ())
            return {
                **profile,
                "current_phase": phase,
                "current_family": family,
                "mismatch": bool(valid and family not in valid),
            }
    return {}


def _surface_deictic_consistency_facts(request: IsolatedSurfaceInput) -> tuple[str, ...]:
    profile = _surface_deictic_phase_profile(request)
    if not profile or not profile.get("mismatch"):
        return ()
    forbidden = "、".join(
        str(item) for item in profile.get("forbidden") or () if str(item).strip()
    )
    return (
        f"时间/场景指向一致性：用户消息带有{profile.get('label')}指向，但当前阶段是 {profile.get('current_phase')}；回复必须以真实当前阶段为准，不能顺承错误时段。",
        f"时间/场景错位时可以轻短指出错位，但不要评价用户状态或动机；不要输出这些顺承词或推断词：{forbidden}。",
    )


def _surface_runtime_prompt_facts(request: IsolatedSurfaceInput) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            [*surface_prompt_facts(request), *_surface_deictic_consistency_facts(request)]
        )
    )


def _surface_runtime_must_not_do(request: IsolatedSurfaceInput) -> tuple[str, ...]:
    profile = _surface_deictic_phase_profile(request)
    extra: list[str] = []
    if profile.get("mismatch"):
        forbidden = "、".join(
            str(item) for item in profile.get("forbidden") or () if str(item).strip()
        )
        extra.extend(
            [
                f"当前用户消息的时间/场景指向与真实阶段 {profile.get('current_phase')} 冲突；不要顺着错误时段回复。",
                f"不要输出这些错位顺承词或状态推断：{forbidden}。",
            ]
        )
    return tuple(dict.fromkeys([*merged_must_not_do(request), *extra]))
