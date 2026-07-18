"""Single production owner for ToolCatalog metadata and ToolBroker handlers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable

from host_brain.lifecycle_runtime.life_jobs import (
    run_life_memory_sleep_replay_handler,
    run_long_term_distillation_handler,
)
from host_brain.skills.personality_registry import build_personality_skill_registry
from host_brain.skills.registry import SkillRegistry
from host_brain.skills.scheduler_registry import build_scheduler_skill_registry
from host_brain.tools.broker import ToolBroker
from host_brain.tools.catalog import ToolCatalog, ToolMeta


@dataclass(frozen=True, slots=True)
class ToolRuntimeSeedReceipt:
    registered_tool_ids: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.registered_tool_ids)


_BUILTIN_TOOL_DEFS = (
    ("file_list", "READ", "NONE", ("file",), "列出目录内容（只读）"),
    ("file_read", "READ", "NONE", ("file",), "读取文件内容（只读）"),
    ("memory_search", "READ", "NONE", ("memory",), "语义检索记忆库"),
    ("system_info", "READ", "NONE", ("system",), "查询系统信息"),
    ("system_resource_snapshot", "READ", "NONE", ("system",), "查询整机资源快照"),
    ("process_resource_top", "READ", "NONE", ("system",), "查询资源热点进程"),
    ("resource_pressure_analysis", "READ", "NONE", ("system",), "分析整机资源压力根因"),
    ("resource_manager_status", "READ", "NONE", ("system",), "查询自动资源管家状态"),
    ("network_status", "READ", "NONE", ("system",), "查询网络状态"),
    ("web_search", "READ", "NONE", ("web",), "执行网络搜索"),
)
_PERSONALITY_TOOL_DEFS = (
    ("diary_writer", "WRITE", "LOW", ("memory", "file"), "每日日记生成"),
    ("dream_generator", "WRITE", "LOW", ("memory",), "梦境生成"),
    ("desktop_note", "WRITE", "LOW", ("system", "file"), "桌面留言"),
    ("focus_mode", "WRITE", "LOW", ("system",), "专注模式切换"),
    ("proactive_talk", "WRITE", "LOW", ("chat",), "主动对话"),
)
_SCHEDULER_TOOL_DEFS = (
    ("scheduler_add", "WRITE", "MEDIUM", ("system",), "添加定时任务"),
    ("scheduler_cancel", "WRITE", "MEDIUM", ("system",), "取消定时任务"),
    ("scheduler_list", "READ", "NONE", ("system",), "列出定时任务"),
)
# Registry handlers currently return declarative executor payloads; they are
# not the workspace-bound production I/O adapters themselves.  None may enter
# SAFE_LOCAL_REPLAY until a canonical executor adapter plus reconciliation
# contract is wired.  Treating the payload as a completed effect would create
# a false SUCCESS/ACCEPTED receipt.
_SAFE_LOCAL_REPLAY_TOOL_IDS: frozenset[str] = frozenset()


def seed_production_tool_runtime(
    *,
    catalog: ToolCatalog,
    broker: ToolBroker,
    skill_registry: SkillRegistry,
    lifecycle_ports: Any,
) -> ToolRuntimeSeedReceipt:
    """Seed the one process Tool runtime; callers must be the composition lifecycle."""

    registered: list[str] = []
    _seed_from_registry(
        catalog=catalog,
        broker=broker,
        registry=skill_registry,
        definitions=_BUILTIN_TOOL_DEFS,
        registered=registered,
    )
    _seed_from_registry(
        catalog=catalog,
        broker=broker,
        registry=build_personality_skill_registry(),
        definitions=_PERSONALITY_TOOL_DEFS,
        registered=registered,
    )
    _seed_from_registry(
        catalog=catalog,
        broker=broker,
        registry=build_scheduler_skill_registry(),
        definitions=_SCHEDULER_TOOL_DEFS,
        registered=registered,
    )
    for tool_id, description, handler in (
        (
            "long_term_memory_distillation",
            "长期记忆蒸馏",
            lambda args, ctx: run_long_term_distillation_handler(
                args or {}, ctx or {}, ports=lifecycle_ports
            ),
        ),
        (
            "life_memory_sleep_replay",
            "生命记忆睡眠回放",
            lambda args, ctx: run_life_memory_sleep_replay_handler(
                args or {}, ctx or {}, ports=lifecycle_ports
            ),
        ),
    ):
        _register(
            catalog=catalog,
            broker=broker,
            tool_id=tool_id,
            side_effect_level="WRITE",
            auth_level="LOW",
            domain_tags=("memory",),
            description=description,
            handler=handler,
        )
        registered.append(tool_id)
    return ToolRuntimeSeedReceipt(tuple(sorted(registered)))


def _seed_from_registry(
    *,
    catalog: ToolCatalog,
    broker: ToolBroker,
    registry: Any,
    definitions: tuple[tuple[str, str, str, tuple[str, ...], str], ...],
    registered: list[str],
) -> None:
    for tool_id, side_effect_level, auth_level, domain_tags, description in definitions:
        skill = registry.get(tool_id)
        if skill is None:
            continue
        _register(
            catalog=catalog,
            broker=broker,
            tool_id=tool_id,
            side_effect_level=side_effect_level,
            auth_level=auth_level,
            domain_tags=domain_tags,
            description=description,
            handler=lambda args, ctx, _handler=skill.handler: _handler(args or {}, ctx or {}),
        )
        registered.append(tool_id)


def _register(
    *,
    catalog: ToolCatalog,
    broker: ToolBroker,
    tool_id: str,
    side_effect_level: str,
    auth_level: str,
    domain_tags: tuple[str, ...],
    description: str,
    handler: Callable[[dict[str, Any], dict[str, Any] | None], Any],
) -> None:
    schema = {"type": "object", "properties": {}}
    schema_hash = "sha256:" + hashlib.sha256(
        json.dumps(schema, sort_keys=True).encode("utf-8")
    ).hexdigest()
    catalog.register(
        ToolMeta(
            id=tool_id,
            name=tool_id,
            desc=description,
            schema_hash=schema_hash,
            side_effect_level=side_effect_level,
            auth_level=auth_level,
            domain_tags=domain_tags,
            version="1.0",
        )
    )
    broker.register_handler(
        tool_id,
        handler,
        replay_safe_local=tool_id in _SAFE_LOCAL_REPLAY_TOOL_IDS,
        adapter_version_id=f"host-brain-tool-adapter:{tool_id}:v1:{schema_hash}",
    )


__all__ = ["ToolRuntimeSeedReceipt", "seed_production_tool_runtime"]
