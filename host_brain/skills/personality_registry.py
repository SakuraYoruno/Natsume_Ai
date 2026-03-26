"""人格 / 调度 Skill 注册。

声明式 Provider：新增人格或宿主侧行为技能时，只需补一个
`DeclarativeSkillSpec` 和对应的参数归一化函数。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec
from host_brain.skills.registry import SkillRegistry


def _normalize_diary_writer(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    base_dir = str((args or {}).get("base_dir") or "").strip()
    date = str((args or {}).get("date") or "").strip()
    return {
        "base_dir": base_dir,
        "date": date,
        "manifest_ref": f"diary_writer::{date or 'today'}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_dream_generator(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    try:
        max_entries = max(1, min(100, int((args or {}).get("max_episodic_entries") or 20)))
    except (TypeError, ValueError):
        max_entries = 20

    return {
        "max_episodic_entries": max_entries,
        "manifest_ref": "dream_generator::today",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_desktop_note(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    action = str((args or {}).get("action") or "").strip().lower()
    filename = str((args or {}).get("filename") or "").strip()
    content = (args or {}).get("content")
    base_dir = str((args or {}).get("base_dir") or "").strip()

    return {
        "action": action,
        "filename": filename,
        "content": content,
        "base_dir": base_dir,
        "manifest_ref": f"desktop_note::{action}::{filename}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_focus_mode(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    action = str((args or {}).get("action") or "").strip().lower()
    try:
        duration_ms = max(0, int((args or {}).get("duration_ms") or 90 * 60 * 1000))
    except (TypeError, ValueError):
        duration_ms = 90 * 60 * 1000
    reason = str((args or {}).get("reason") or "").strip() or None

    return {
        "action": action,
        "duration_ms": duration_ms,
        "reason": reason,
        "manifest_ref": f"focus_mode::{action}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_proactive_talk(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    try:
        idle_threshold_ms = max(0, int((args or {}).get("idle_threshold_ms") or 4 * 60 * 60 * 1000))
    except (TypeError, ValueError):
        idle_threshold_ms = 4 * 60 * 60 * 1000
    topic_hint = str((args or {}).get("topic_hint") or "").strip() or None

    return {
        "idle_threshold_ms": idle_threshold_ms,
        "topic_hint": topic_hint,
        "manifest_ref": "proactive_talk::send",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "_context": ctx or {},
    }


class PersonalitySkillProvider(DeclarativeSkillProvider):
    skill_specs = (
        DeclarativeSkillSpec(
            skill_name="diary_writer",
            clearance="MEDIUM",
            risk_level=2,
            scope="host",
            reversible=True,
            description="每日日记生成：汇总当日对话 → LLM 生成日记叙事 → 双写文件 + Episodic Memory。",
            tool="diary_writer",
            action="generate",
            normalize_args=_normalize_diary_writer,
        ),
        DeclarativeSkillSpec(
            skill_name="dream_generator",
            clearance="LOW",
            risk_level=1,
            scope="memory",
            reversible=True,
            description="梦境生成：读取当日 Episodic Memory → LLM 生成梦境叙事 → 写入 Episodic（dream 标签）。",
            tool="dream_generator",
            action="generate",
            normalize_args=_normalize_dream_generator,
        ),
        DeclarativeSkillSpec(
            skill_name="desktop_note",
            clearance="MEDIUM",
            risk_level=2,
            scope="system",
            reversible=True,
            description="桌面留言：在安全基目录下创建/更新/删除文本文件（路径穿越拒绝，内容经 QA 校验）。",
            tool="desktop_note",
            action=None,
            normalize_args=_normalize_desktop_note,
        ),
        DeclarativeSkillSpec(
            skill_name="focus_mode",
            clearance="LOW",
            risk_level=1,
            scope="system",
            reversible=True,
            description="专注模式切换：进入/退出专注模式，抑制低优先级调度任务和主动对话。",
            tool="focus_mode",
            action=None,
            normalize_args=_normalize_focus_mode,
        ),
        DeclarativeSkillSpec(
            skill_name="proactive_talk",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=False,
            description="主动对话：空闲超过阈值时由调度器触发，LLM 生成问候语并推送到 UI 队列。",
            tool="proactive_talk",
            action="send",
            normalize_args=_normalize_proactive_talk,
        ),
    )


def build_personality_skill_registry(provider: PersonalitySkillProvider | None = None) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_decorated(provider or PersonalitySkillProvider())
    return registry
