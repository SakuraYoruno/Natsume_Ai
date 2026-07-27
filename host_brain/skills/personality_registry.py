"""人格 / 调度 Skill 注册。

声明式 Provider：新增人格或宿主侧行为技能时，只需补一个
`DeclarativeSkillSpec` 和对应的参数归一化函数。
"""

from __future__ import annotations

from typing import Any

from host_brain.contracts.skill_execution import SkillBudgetPolicy
from host_brain.skills.declarative import (
    DeclarativeSkillProvider,
    DeclarativeSkillSpec,
)
from host_brain.skills.registry import SkillMeta, SkillRegistry


def _cron_profile(
    *,
    task_id: str,
    hour: int,
    minute: int,
    description: str,
    mode_allowlist: tuple[str, ...] = ("normal", "sleep"),
    args: dict[str, Any] | None = None,
    priority: str = "LOW",
    resource_class: str = "background_llm",
    uses_llm: bool | None = None,
    uses_gpu: bool | None = None,
    catch_up_window_minutes: int | None = None,
    trigger_env: dict[str, dict[str, Any]] | None = None,
    arg_env: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    trigger_config = {"hour": int(hour), "minute": int(minute)}
    if catch_up_window_minutes is not None:
        trigger_config["catch_up_window_minutes"] = int(catch_up_window_minutes)
    return {
        "auto_schedule": True,
        "task_id": task_id,
        "trigger_mode": "cron",
        "trigger_config": trigger_config,
        "trigger_env": dict(trigger_env or {}),
        "priority": priority,
        "mode_allowlist": list(mode_allowlist),
        "args": dict(args or {}),
        "arg_env": dict(arg_env or {}),
        "resource_class": resource_class,
        "uses_llm": uses_llm,
        "uses_gpu": uses_gpu,
        "advisory_policy": "rule",
        "description": description,
    }


def _interval_profile(
    *,
    task_id: str,
    seconds: int,
    description: str,
    mode_allowlist: tuple[str, ...] = ("normal", "focus", "gaming", "sleep"),
    args: dict[str, Any] | None = None,
    priority: str = "LOW",
    resource_class: str = "light",
    uses_llm: bool | None = None,
    uses_gpu: bool | None = None,
    trigger_env: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "auto_schedule": True,
        "task_id": task_id,
        "trigger_mode": "interval",
        "trigger_config": {"seconds": int(seconds)},
        "trigger_env": dict(trigger_env or {}),
        "priority": priority,
        "mode_allowlist": list(mode_allowlist),
        "args": dict(args or {}),
        "resource_class": resource_class,
        "uses_llm": uses_llm,
        "uses_gpu": uses_gpu,
        "advisory_policy": "rule",
        "description": description,
    }


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
            scheduler_profile=_cron_profile(
                task_id="diary_writer_daily",
                hour=23,
                minute=50,
                args={"base_dir": "runtime/diary"},
                arg_env={
                    "base_dir": {
                        "name": "HOST_DIARY_WRITER_BASE_DIR",
                        "default": "runtime/diary",
                    },
                },
                trigger_env={
                    "hour": {"name": "HOST_DIARY_WRITER_HOUR", "default": 23, "low": 0, "high": 23},
                    "minute": {"name": "HOST_DIARY_WRITER_MINUTE", "default": 50, "low": 0, "high": 59},
                },
                description="每日日记：汇总当天对话、审计和 episodic 片段，沉淀为文件与 Episodic Memory。",
            ),
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
            scheduler_profile=_cron_profile(
                task_id="dream_generator_sleep",
                hour=4,
                minute=20,
                args={"max_episodic_entries": 20, "idle_threshold_ms": 90 * 60 * 1000},
                arg_env={
                    "max_episodic_entries": {
                        "name": "HOST_DREAM_GENERATOR_MAX_EPISODIC_ENTRIES",
                        "default": 20,
                        "low": 1,
                        "high": 100,
                        "type": "int",
                    },
                    "idle_threshold_ms": {
                        "name": "HOST_DREAM_GENERATOR_IDLE_THRESHOLD_MINUTES",
                        "default": 90,
                        "low": 1,
                        "high": 24 * 60,
                        "type": "int",
                        "unit": "minutes_to_ms",
                    },
                },
                trigger_env={
                    "hour": {"name": "HOST_DREAM_GENERATOR_HOUR", "default": 4, "low": 0, "high": 23},
                    "minute": {"name": "HOST_DREAM_GENERATOR_MINUTE", "default": 20, "low": 0, "high": 59},
                },
                description="睡眠/空闲梦境：在夜间回放 Episodic Memory，生成 narrative-only 梦境并写回记忆。",
                catch_up_window_minutes=720,
            ),
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
            scheduler_profile=_interval_profile(
                task_id="proactive_talk_periodic",
                seconds=180,
                mode_allowlist=("normal", "sleep"),
                args={"idle_threshold_ms": 2_700_000},
                description="主动对话：每 3 分钟检查一次，空闲 45 分钟后尝试发起问候。",
                resource_class="social_light_llm",
            ),
        ),
    )


def build_personality_skill_registry(provider: PersonalitySkillProvider | None = None) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_decorated(provider or PersonalitySkillProvider())
    _register_inner_world_skills(registry)
    _register_silence_skills(registry)
    _register_gaming_companion_skills(registry)
    _register_body_sense_skills(registry)
    _register_emergent_growth_skills(registry)
    return registry


def _register_inner_world_skills(registry: SkillRegistry) -> None:
    """注册离线内心世界 Skills（Phase XVI）。

    使用直接 handler 注册方式（非 DeclarativeSkillSpec），
    因为这些 skill 不走 tool 路由，直接调用 inner_world 状态机。
    """
    from host_brain.skills.impl.inner_world_skill import (  # noqa: PLC0415
        execute_inner_world_monologue_tick,
        execute_inner_world_numeric_tick,
        execute_inner_world_awaken,
        execute_inner_world_session_end,
    )

    _inner_world_skills = (
        SkillMeta(
            skill_name="inner_world_numeric_tick",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=False,
            handler=execute_inner_world_numeric_tick,
            description="离线内心世界数值 tick：推进数值状态，不写 inner_stream。",
            scheduler_allowed=True,
            scheduler_profile=_interval_profile(
                task_id="inner_world_numeric_tick",
                seconds=60,
                args={"user_id": "natsume_ai"},
                trigger_env={
                    "seconds": {
                        "name": "HOST_INNER_WORLD_NUMERIC_TICK_INTERVAL_SECONDS",
                        "default": 60,
                        "low": 10,
                        "high": 3600,
                    },
                },
                description="Core organ tick: advances numeric inner-world continuity without writing inner_stream.",
                resource_class="light",
            ),
        ),
        SkillMeta(
            skill_name="inner_world_monologue_tick",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=False,
            handler=execute_inner_world_monologue_tick,
            description="离线内心世界独白 tick：按较慢节奏生成 inner_stream 独白。",
            scheduler_allowed=True,
            scheduler_profile=_interval_profile(
                task_id="inner_world_monologue_tick",
                seconds=300,
                args={"user_id": "natsume_ai"},
                trigger_env={
                    "seconds": {
                        "name": "HOST_INNER_WORLD_MONOLOGUE_TICK_INTERVAL_SECONDS",
                        "default": 300,
                        "low": 30,
                        "high": 7200,
                    },
                },
                description="Core organ tick: slow inner-world stream update; LLM note generation remains gated separately.",
                resource_class="background_llm",
            ),
        ),
        SkillMeta(
            skill_name="inner_world_awaken",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=False,
            handler=execute_inner_world_awaken,
            description="离线内心世界唤醒：用户开始对话时调用，生成离线状态快照注入 prompt。",
            scheduler_allowed=False,
        ),
        SkillMeta(
            skill_name="inner_world_session_end",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=False,
            handler=execute_inner_world_session_end,
            description="离线内心世界会话结束：更新情绪基准和期待话题。",
            scheduler_allowed=False,
        ),
    )
    for meta in _inner_world_skills:
        try:
            registry.register(meta)
        except ValueError:
            pass  # 已注册时跳过（重启时可能重复注册）


def _register_silence_skills(registry: SkillRegistry) -> None:
    """注册情绪驱动的沉默能力 Skills（Phase XVIII）。

    使用直接 handler 注册方式（非 DeclarativeSkillSpec），
    因为这些 skill 直接调用 EmotionalSilenceEngine 状态机。
    """
    from host_brain.skills.impl.silence_skill import (  # noqa: PLC0415
        execute_silence_tick,
        execute_silence_update_emotion,
        execute_silence_add_resistance,
    )

    _silence_skills = (
        SkillMeta(
            skill_name="silence_tick",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=False,
            handler=execute_silence_tick,
            description="沉默引擎 tick：每分钟由调度器调用，驱动回复意愿自然恢复和话题抗拒衰减。",
            scheduler_allowed=True,
            scheduler_profile=_interval_profile(
                task_id="silence_tick",
                seconds=60,
                args={"user_id": "natsume_ai"},
                description="Core organ tick: recovers reply willingness and decays topic resistance.",
                resource_class="light",
            ),
        ),
        SkillMeta(
            skill_name="silence_update_emotion",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=False,
            handler=execute_silence_update_emotion,
            description="沉默引擎情绪更新：情绪变化时调用，重新计算回复意愿值。",
            scheduler_allowed=False,
        ),
        SkillMeta(
            skill_name="silence_add_resistance",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=False,
            handler=execute_silence_add_resistance,
            description="沉默引擎话题抗拒：标记某话题触发真实抗拒（非礼貌转移）。",
            scheduler_allowed=False,
        ),
    )
    for meta in _silence_skills:
        try:
            registry.register(meta)
        except ValueError:
            pass  # 已注册时跳过（重启时可能重复注册）


def _register_gaming_companion_skills(registry: SkillRegistry) -> None:
    """注册 gaming 陪玩识图 Skill（GAP_ANALYSIS 14.2 节）。

    串联 desktop_executor.screenshot + 多模态 LLM + ui_pusher，
    实现 gaming 模式下的陪玩识图能力。
    """
    from host_brain.skills.impl.gaming_companion_executor import (  # noqa: PLC0415
        execute_gaming_companion,
    )

    _gaming_skills = (
        SkillMeta(
            skill_name="gaming_companion_tick",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=False,
            handler=execute_gaming_companion,
            description=(
                "gaming 陪玩识图 tick：截取当前屏幕 → 多模态 LLM 分析 → 推送陪玩评论/攻略提示。"
                "仅在 gaming 模式下运行，由 NatsumeScheduler 每 5 分钟触发一次。"
            ),
            scheduler_allowed=True,
            scheduler_profile=_interval_profile(
                task_id="gaming_companion_tick",
                seconds=300,
                mode_allowlist=("gaming",),
                args={"user_id": "natsume_ai", "monitor": 1},
                description="gaming 陪玩识图 tick：每 5 分钟截图分析并尝试陪玩反馈。",
                resource_class="gpu_heavy",
                uses_llm=True,
                uses_gpu=True,
            ),
        ),
    )
    for meta in _gaming_skills:
        try:
            registry.register(meta)
        except ValueError:
            pass  # 已注册时跳过（重启时可能重复注册）


def _register_body_sense_skills(registry: SkillRegistry) -> None:
    """注册身体感知 Skills（v1.12 低优先级债务修复）。

    使用直接 handler 注册方式（非 DeclarativeSkillSpec），
    因为这些 skill 直接调用 BodySenseModel 状态机。
    """
    from host_brain.skills.impl.body_sense_skill import (  # noqa: PLC0415
        execute_body_sense_tick,
        execute_body_sense_conversation_start,
        execute_body_sense_conversation_end,
        execute_body_sense_update_loneliness,
    )

    _body_sense_skills = (
        SkillMeta(
            skill_name="body_sense_tick",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=False,
            handler=execute_body_sense_tick,
            description="身体感知 tick：每分钟由调度器调用，更新困倦感（基于时段）和疲劳自然恢复。",
            scheduler_allowed=True,
            scheduler_profile=_interval_profile(
                task_id="body_sense_tick",
                seconds=60,
                args={"user_id": "natsume_ai"},
                description="Core organ tick: updates fatigue, sleepiness, and body-sense baseline.",
                resource_class="light",
            ),
        ),
        SkillMeta(
            skill_name="body_sense_conversation_start",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=False,
            handler=execute_body_sense_conversation_start,
            description="身体感知对话开始：启动疲劳计时，在每次对话开始时调用。",
            scheduler_allowed=False,
        ),
        SkillMeta(
            skill_name="body_sense_conversation_end",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=False,
            handler=execute_body_sense_conversation_end,
            description="身体感知对话结束：根据对话时长积累疲劳度，在每次对话结束时调用。",
            scheduler_allowed=False,
        ),
        SkillMeta(
            skill_name="body_sense_update_loneliness",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=False,
            handler=execute_body_sense_update_loneliness,
            description="身体感知孤独感注入：将 canonical Life affect snapshot 的孤独值同步到 BodySenseModel。",
            scheduler_allowed=False,
        ),
    )
    for meta in _body_sense_skills:
        try:
            registry.register(meta)
        except ValueError:
            pass  # 已注册时跳过（重启时可能重复注册）


def _register_emergent_growth_skills(registry: SkillRegistry) -> None:
    """注册涌现式成长 Skills（Phase XIX）。

    使用直接 handler 注册方式（非 DeclarativeSkillSpec），
    因为这些 skill 直接调用 InteractionPatternAnalyzer + EmergentGrowthEngine。
    """
    from host_brain.skills.impl.emergent_growth_skill import (  # noqa: PLC0415
        execute_emergent_growth_weekly,
        execute_emergent_growth_status,
    )

    _emergent_growth_skills = (
        SkillMeta(
            skill_name="emergent_growth_weekly",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=False,
            handler=execute_emergent_growth_weekly,
            model_budget_required=True,
            budget_policy=SkillBudgetPolicy.TASK_PRECHARGE_DECLARED_MODEL,
            description=(
                "涌现式成长周期（Phase XIX）：每周日 03:00 由调度器调用，"
                "分析近期对话模式 → LLM/关键词归纳特质变化建议 → 安全门控 → 应用特质微调。"
            ),
            scheduler_allowed=True,
            scheduler_profile=_cron_profile(
                task_id="emergent_growth_weekly",
                hour=3,
                minute=50,
                args={"user_id": "natsume_ai", "lookback_days": 7},
                mode_allowlist=("normal", "sleep"),
                resource_class="background_llm",
                description="涌现式成长：每周日 03:50 分析近期对话并沉淀人格微调。",
                catch_up_window_minutes=1440,
            )
            | {"trigger_config": {"day_of_week": 6, "hour": 3, "minute": 50, "catch_up_window_minutes": 1440}},
        ),
        SkillMeta(
            skill_name="emergent_growth_status",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=False,
            handler=execute_emergent_growth_status,
            description="涌现式成长状态查询：返回成长日志摘要（总周期数/最近主题/最新成长叙事）。",
            scheduler_allowed=False,
        ),
    )
    for meta in _emergent_growth_skills:
        try:
            registry.register(meta)
        except ValueError:
            pass  # 已注册时跳过（重启时可能重复注册）
