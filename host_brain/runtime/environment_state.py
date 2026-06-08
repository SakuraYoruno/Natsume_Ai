from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "Asia/Shanghai"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _timezone_name(value: str | None = None) -> str:
    return str(value or os.getenv("HOST_LOCAL_TIMEZONE") or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE


def _local_dt(*, now_ms: int | None = None, timezone_name: str | None = None) -> datetime:
    tz_name = _timezone_name(timezone_name)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        if tz_name in {DEFAULT_TIMEZONE, "Asia/Chongqing", "Asia/Shanghai"}:
            tz = timezone(timedelta(hours=8), name=DEFAULT_TIMEZONE)
        else:
            tz = datetime.now().astimezone().tzinfo or timezone(timedelta(hours=8), name=DEFAULT_TIMEZONE)
    return datetime.fromtimestamp(int(now_ms or _now_ms()) / 1000.0, tz=tz)


def day_phase_for_hour(hour: int) -> str:
    h = int(hour)
    if 5 <= h < 7:
        return "dawn"
    if 7 <= h < 10:
        return "morning"
    if 10 <= h < 12:
        return "late_morning"
    if 12 <= h < 14:
        return "noon"
    if 14 <= h < 18:
        return "afternoon"
    if 18 <= h < 21:
        return "evening"
    if 21 <= h < 24:
        return "night"
    return "late_night"


def meal_phase_for_time(hour: int, minute: int = 0) -> str:
    value = int(hour) * 60 + int(minute)
    if 6 * 60 <= value < 10 * 60:
        return "breakfast_window"
    if 11 * 60 <= value < 14 * 60:
        return "lunch_window"
    if 17 * 60 + 30 <= value < 20 * 60 + 30:
        return "dinner_window"
    if 23 * 60 <= value or value < 2 * 60:
        return "late_snack_window"
    return "none"


def _routine_phase(day_phase: str, meal_phase: str) -> str:
    if meal_phase != "none":
        return meal_phase
    if day_phase in {"dawn", "morning"}:
        return "morning_wake"
    if day_phase in {"late_morning", "noon", "afternoon"}:
        return "day_focus"
    if day_phase == "evening":
        return "evening_settle"
    if day_phase == "night":
        return "night_soft"
    return "late_night_rest"


def _routine_affordances(day_phase: str, meal_phase: str) -> list[str]:
    values: list[str] = [day_phase]
    if meal_phase != "none":
        values.append(meal_phase)
    if day_phase in {"late_morning", "noon", "afternoon"}:
        values.extend(["not_morning", "daytime"])
    if day_phase in {"night", "late_night"}:
        values.extend(["low_energy", "soft_presence"])
    if day_phase == "afternoon":
        values.append("afternoon_focus")
    return list(dict.fromkeys(values))


def build_environment_snapshot(*, now_ms: int | None = None, timezone_name: str | None = None) -> dict[str, Any]:
    now = int(now_ms or _now_ms())
    dt = _local_dt(now_ms=now, timezone_name=timezone_name)
    tz_name = _timezone_name(timezone_name)
    hour = int(dt.hour)
    minute = int(dt.minute)
    day_phase = day_phase_for_hour(hour)
    meal_phase = meal_phase_for_time(hour, minute)
    facts = [f"当前本地时间是 {hour:02d}:{minute:02d}，阶段是 {day_phase}。"]
    if day_phase not in {"dawn", "morning"}:
        facts.append("普通 hello/你好 不是早安、刚醒或早餐语境。")
    if meal_phase == "none":
        facts.append("当前不在早餐、午餐或晚餐窗口；不要编造餐食事实。")
    elif meal_phase == "breakfast_window":
        facts.append("当前处于早餐时间窗口，但不能据此断言用户已经准备或没吃早餐。")
    elif meal_phase == "lunch_window":
        facts.append("当前处于午餐时间窗口，但不能据此断言用户已经准备或没吃午餐。")
    elif meal_phase == "dinner_window":
        facts.append("当前处于晚餐时间窗口，但不能据此断言用户已经准备或没吃晚餐。")
    return {
        "timezone": tz_name,
        "local_iso": dt.isoformat(),
        "now_ms": now,
        "hour": hour,
        "minute": minute,
        "day_phase": day_phase,
        "meal_phase": meal_phase,
        "routine_phase": _routine_phase(day_phase, meal_phase),
        "routine_affordances": _routine_affordances(day_phase, meal_phase),
        "surface_facts": facts,
    }


def private_mind_summary(inner_world: dict[str, Any] | None) -> dict[str, Any]:
    inner = dict(inner_world or {})
    preoccupation = str(inner.get("current_preoccupation") or "").strip()
    attention_target = str(inner.get("attention_target") or "").strip()
    felt_tendency = str(inner.get("felt_tendency") or "").strip()
    unresolved_loop = str(inner.get("unresolved_loop") or "").strip()
    return {
        "attention_target": attention_target or ("current_preoccupation" if preoccupation else ""),
        "current_preoccupation": preoccupation[:160],
        "unresolved_loop": unresolved_loop[:120],
        "felt_tendency": felt_tendency[:120],
        "dialogue_bias": str(inner.get("dialogue_bias") or "").strip()[:160],
        "must_not_expose_raw_inner_text": True,
    }


__all__ = [
    "DEFAULT_TIMEZONE",
    "build_environment_snapshot",
    "day_phase_for_hour",
    "meal_phase_for_time",
    "private_mind_summary",
]
