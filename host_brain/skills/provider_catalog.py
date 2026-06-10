"""Central catalog for pluggable skill providers."""

from __future__ import annotations

import importlib
import inspect
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from host_brain.skills.registry import SkillRegistry


@dataclass(frozen=True)
class SkillProviderSpec:
    env_flag: str
    provider_path: str
    log_label: str
    log_summary: str
    default_enabled: bool = True

    def instantiate(self):
        module_name, class_name = self.provider_path.split(":", 1)
        module = importlib.import_module(module_name)
        provider_cls = getattr(module, class_name)
        return provider_cls()


BUILTIN_SKILL_PROVIDER_SPECS: tuple[SkillProviderSpec, ...] = (
    SkillProviderSpec("HOST_SKILL_FILE_ENABLED", "host_brain.skills.file_registry:FileSkillProvider", "File Skills", "file_read / file_write / file_append / file_delete / file_list"),
    SkillProviderSpec("HOST_SKILL_SHELL_ENABLED", "host_brain.skills.shell_registry:ShellSkillProvider", "Shell Skills", "shell_run / shell_read_log / shell_run_test"),
    SkillProviderSpec("HOST_SKILL_NOTIFICATION_ENABLED", "host_brain.skills.notification_registry:NotificationSkillProvider", "Notification Skills", "notify_desktop / notify_tts_speak / notify_schedule_reminder"),
    SkillProviderSpec("HOST_SKILL_IDE_ENABLED", "host_brain.skills.ide_registry:IDESkillProvider", "IDE Skills", "open_cursor / run_pycharm_action / open_file_in_editor"),
    SkillProviderSpec("HOST_CODEX_WORKPLANE_ENABLED", "host_brain.skills.agent_runtime_registry:AgentRuntimeSkillProvider", "Agent Runtime Skills", "agent_inspect / agent_worker / agent_self_heal / agent_self_evolve / agent_sql_audit / agent_translate_project / agent_skill_forge / agent_research / agent_*_production"),
    SkillProviderSpec("HOST_SKILL_BROWSER_ENABLED", "host_brain.skills.browser_registry:BrowserSkillProvider", "Browser Skills", "browser_navigate / browser_click / browser_type / browser_screenshot / browser_execute_script / browser_wait_for / browser_scrape / browser_close"),
    SkillProviderSpec("HOST_SKILL_PERSONALITY_ENABLED", "host_brain.skills.personality_registry:PersonalitySkillProvider", "Personality Skills", "diary_writer / dream_generator / desktop_note / focus_mode / proactive_talk"),
    SkillProviderSpec("HOST_SKILL_DESKTOP_ENABLED", "host_brain.skills.desktop_registry:DesktopSkillProvider", "Desktop Skills", "desktop_screenshot / desktop_click / desktop_type / desktop_hotkey / clipboard_read / clipboard_write"),
    SkillProviderSpec("HOST_SKILL_MEMORY_ENABLED", "host_brain.skills.memory_registry:MemorySkillProvider", "Memory Skills", "memory_search / memory_store / memory_preference_extract / memory_consolidate / memory_forget"),
    SkillProviderSpec("HOST_SKILL_REPO_ENABLED", "host_brain.skills.repo_registry:RepoSkillProvider", "Repo Doctor Skills", "repo_health_scan / test_failure_triage"),
    SkillProviderSpec("HOST_SKILL_FRONTEND_ENABLED", "host_brain.skills.frontend_registry:FrontendSkillProvider", "Frontend Audit Skills", "frontend_route_audit / frontend_component_value_audit"),
    SkillProviderSpec("HOST_SKILL_EMOTION_ENABLED", "host_brain.skills.emotion_registry:EmotionSkillProvider", "Emotion Skills", "emotion_update / bond_score_update / emotion_get / bond_score_get"),
    SkillProviderSpec("HOST_SKILL_SCHEDULER_ENABLED", "host_brain.skills.scheduler_registry:SchedulerSkillProvider", "Scheduler Skills", "scheduler_add / scheduler_cancel / scheduler_list"),
    SkillProviderSpec("HOST_SKILL_SYSTEM_ENABLED", "host_brain.skills.system_registry:SystemSkillProvider", "System Skills", "system_info / system_resource_snapshot / process_resource_top / resource_manager_status / resource_release / process_list / process_kill / network_status / open_app"),
    SkillProviderSpec("HOST_SKILL_TOKEN_USAGE_ENABLED", "host_brain.skills.token_usage_registry:TokenUsageSkillProvider", "Token Usage Skills", "llm_token_usage_report"),
    SkillProviderSpec("HOST_SKILL_WEB_ENABLED", "host_brain.skills.web_registry:WebSkillProvider", "Web Skills", "web_search / http_request"),
    SkillProviderSpec("HOST_SKILL_HUB_ENABLED", "host_brain.skills.hub_skill_registry:HubSkillProvider", "Hub Skills", "hub_skill_install / hub_skill_search / hub_skill_list / hub_skill_remove"),
)

CUSTOM_PROVIDER_DIR = Path(__file__).with_name("custom_providers")
CUSTOM_PROVIDER_PACKAGE = "host_brain.skills.custom_providers"
CUSTOM_PROVIDER_GLOB = "*_provider.py"
CUSTOM_PROVIDER_CLASS_ATTR = "SKILL_PROVIDER_CLASS"
CUSTOM_PROVIDERS_ENABLED_ENV = "HOST_SKILL_CUSTOM_PROVIDERS_ENABLED"
EXTRA_PROVIDERS_ENV = "HOST_SKILL_EXTRA_PROVIDERS"


def _split_provider_paths(raw: str) -> tuple[str, ...]:
    items: list[str] = []
    for chunk in str(raw or "").replace(";", "\n").replace(",", "\n").splitlines():
        value = chunk.strip()
        if value:
            items.append(value)
    return tuple(items)


def _discover_custom_provider_paths(
    *,
    logger: logging.Logger,
    custom_dir: Path = CUSTOM_PROVIDER_DIR,
    package_name: str = CUSTOM_PROVIDER_PACKAGE,
) -> tuple[str, ...]:
    if not custom_dir.exists() or not custom_dir.is_dir():
        return ()

    discovered: list[str] = []
    for path in sorted(custom_dir.glob(CUSTOM_PROVIDER_GLOB)):
        if path.name.startswith("_"):
            continue
        module_name = f"{package_name}.{path.stem}"
        try:
            importlib.invalidate_caches()
            sys.modules.pop(module_name, None)
            module = importlib.import_module(module_name)
        except Exception as exc:
            logger.warning("自定义 Skill Provider 导入失败，跳过: %s (%s)", module_name, exc)
            continue

        provider_cls = getattr(module, CUSTOM_PROVIDER_CLASS_ATTR, None)
        if not inspect.isclass(provider_cls):
            logger.warning(
                "自定义 Skill Provider 缺少 %s，跳过: %s",
                CUSTOM_PROVIDER_CLASS_ATTR,
                module_name,
            )
            continue
        discovered.append(f"{module_name}:{provider_cls.__name__}")
    return tuple(discovered)


def _register_provider_path(
    registry: SkillRegistry,
    provider_path: str,
    *,
    logger: logging.Logger,
    label: str,
) -> int:
    try:
        spec = SkillProviderSpec(
            env_flag="",
            provider_path=provider_path,
            log_label=label,
            log_summary=provider_path,
        )
        registry.register_decorated(spec.instantiate())
        logger.info("%s 已注册（%s）", label, provider_path)
        return 1
    except Exception as exc:
        logger.warning("%s 注册失败，跳过: %s", label, exc)
        return 0


def register_configured_skill_providers(
    registry: SkillRegistry,
    *,
    parse_bool_env: Callable[..., bool],
    logger: logging.Logger,
    specs: tuple[SkillProviderSpec, ...] = BUILTIN_SKILL_PROVIDER_SPECS,
) -> int:
    count = 0
    for spec in specs:
        if not parse_bool_env(spec.env_flag, default=spec.default_enabled):
            continue
        try:
            registry.register_decorated(spec.instantiate())
            logger.info("%s 已注册（%s）", spec.log_label, spec.log_summary)
            count += 1
        except Exception as exc:
            logger.warning("%s 注册失败，跳过: %s", spec.log_label, exc)

    if parse_bool_env(CUSTOM_PROVIDERS_ENABLED_ENV, default=True):
        for provider_path in _discover_custom_provider_paths(logger=logger):
            count += _register_provider_path(
                registry,
                provider_path,
                logger=logger,
                label="Custom Skill Provider",
            )

    for provider_path in _split_provider_paths(os.environ.get(EXTRA_PROVIDERS_ENV) or ""):
        count += _register_provider_path(
            registry,
            provider_path,
            logger=logger,
            label="Extra Skill Provider",
        )

    return count
