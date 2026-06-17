"""Central registry for tool executors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from host_brain.skills.impl.browser_executor import execute_browser_skill
from host_brain.agent_runtime import execute_agent_production_payload, execute_agent_runtime_payload
from host_brain.skills.impl.desktop_executor import execute_desktop_skill
from host_brain.skills.impl.desktop_note import execute_desktop_note
from host_brain.skills.impl.diary_writer import execute_diary_writer
from host_brain.skills.impl.dream_generator import execute_dream_generator
from host_brain.skills.impl.emotion_executor import execute_emotion_skill
from host_brain.skills.impl.file_executor import execute_file_skill
from host_brain.skills.impl.focus_mode import execute_focus_mode
from host_brain.skills.impl.frontend_executor import execute_frontend_skill
from host_brain.skills.impl.ide_executor import execute_ide_skill
from host_brain.skills.impl.memory_executor import execute_memory_skill
from host_brain.skills.impl.notification_executor import execute_notification_skill
from host_brain.skills.impl.proactive_talk import execute_proactive_talk
from host_brain.skills.impl.repo_executor import execute_repo_skill
from host_brain.skills.impl.scheduler_executor import execute_scheduler_skill
from host_brain.skills.impl.shell_executor import execute_shell_skill
from host_brain.skills.impl.system_executor import execute_system_skill
from host_brain.skills.impl.web_executor import execute_web_skill
from host_brain.skills.impl.hub_skill_executor import execute_hub_skill


ExecutorFn = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class ToolExecutorSpec:
    tool_name: str
    handler: ExecutorFn
    aliases: tuple[str, ...] = field(default_factory=tuple)


class ToolExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, ExecutorFn] = {}

    def register(self, spec: ToolExecutorSpec) -> None:
        names = (spec.tool_name, *spec.aliases)
        for name in names:
            normalized = str(name).strip().lower()
            if not normalized:
                raise ValueError("tool executor name must be non-empty")
            if normalized in self._executors:
                raise ValueError(f"executor already registered: {normalized}")
            self._executors[normalized] = spec.handler

    def get(self, tool_name: str) -> ExecutorFn | None:
        return self._executors.get(str(tool_name or "").strip().lower())

    def dispatch(self, payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        executor = self.get(str(payload.get("tool") or ""))
        if executor is None:
            return payload
        return executor(payload)


def _with_context(handler: Callable[[dict[str, Any], dict[str, Any] | None], Any]) -> ExecutorFn:
    return lambda payload: handler(payload, payload.get("_context"))


def _focus_mode_with_mode_changer(payload: dict[str, Any]) -> Any:
    """focus_mode 的特殊包装器——自动注入 mode_changer 回调。

    focus_mode skill 期望 context 中有 mode_changer 回调来实际切换 sys_mode。
    如果调用方未提供 mode_changer，则自动注入 dependencies.set_sys_mode。
    """
    ctx = dict(payload.get("_context") or {})
    if ctx.get("mode_changer") is None:
        try:
            from host_brain.app.dependencies import set_sys_mode  # noqa: PLC0415
            ctx["mode_changer"] = set_sys_mode
        except ImportError:
            pass  # 降级：不注入，focus_mode 仍返回 EXECUTED 但不切换模式
    return execute_focus_mode(payload, ctx)


def build_builtin_tool_executor_registry() -> ToolExecutorRegistry:
    registry = ToolExecutorRegistry()
    specs = (
        ToolExecutorSpec("file", execute_file_skill),
        ToolExecutorSpec("notification", execute_notification_skill),
        ToolExecutorSpec("shell", execute_shell_skill),
        ToolExecutorSpec("browser", execute_browser_skill),
        ToolExecutorSpec("cursor", execute_ide_skill),
        ToolExecutorSpec("pycharm", execute_ide_skill),
        ToolExecutorSpec("codex_runtime", execute_agent_runtime_payload),
        ToolExecutorSpec("codex_production", execute_agent_production_payload),
        ToolExecutorSpec("open_file_in_editor", execute_ide_skill),
        ToolExecutorSpec("diary_writer", _with_context(execute_diary_writer)),
        ToolExecutorSpec("dream_generator", _with_context(execute_dream_generator)),
        ToolExecutorSpec("desktop_note", _with_context(execute_desktop_note)),
        ToolExecutorSpec("focus_mode", _focus_mode_with_mode_changer),
        ToolExecutorSpec("proactive_talk", _with_context(execute_proactive_talk)),
        ToolExecutorSpec("desktop", _with_context(execute_desktop_skill)),
        ToolExecutorSpec("memory", _with_context(execute_memory_skill)),
        ToolExecutorSpec("emotion", _with_context(execute_emotion_skill)),
        ToolExecutorSpec("repo", _with_context(execute_repo_skill)),
        ToolExecutorSpec("frontend", _with_context(execute_frontend_skill)),
        ToolExecutorSpec("scheduler", _with_context(execute_scheduler_skill)),
        ToolExecutorSpec("system", _with_context(execute_system_skill)),
        ToolExecutorSpec("web", _with_context(execute_web_skill)),
        ToolExecutorSpec("hub_skill", _with_context(execute_hub_skill)),
    )
    for spec in specs:
        registry.register(spec)
    return registry


_BUILTIN_EXECUTOR_REGISTRY = build_builtin_tool_executor_registry()


def get_builtin_tool_executor_registry() -> ToolExecutorRegistry:
    return _BUILTIN_EXECUTOR_REGISTRY


def dispatch_executor_payload(payload: Any) -> Any:
    return _BUILTIN_EXECUTOR_REGISTRY.dispatch(payload)
