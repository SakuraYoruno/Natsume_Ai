"""prompt_template_defs.py — Prompt 模板定义加载器（P2-4 重构）。

原文件（272KB/4358行）已迁移到 prompts/templates/ 目录下的 YAML 文件：
  - agents.yaml    : 19 个模板（各 Agent Handler）
  - chat.yaml      : 70 个模板（Chat/Prompt Builder/Reflection）
  - executive.yaml : 8  个模板（ExecutiveRouter/InputRoutes）
  - governance.yaml: 2  个模板（ImmuneAgent/Core QA）
  - persona.yaml   : 17 个模板（Persona/Emotion/SelfModel）

本文件作为加载器，对外保持完全向后兼容：
  - PromptTemplateDefinition 数据类
  - get_prompt_template_definition(template_id) -> PromptTemplateDefinition
  - list_prompt_template_definitions() -> list[PromptTemplateDefinition]
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_logger = logging.getLogger("Natsume.prompt_template_defs")

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "prompts" / "templates"


@dataclass(frozen=True)
class PromptTemplateDefinition:
    template_id: str
    category: str
    scope: str
    locale: str
    content_type: str
    description: str
    default_content: str


_registry: dict[str, PromptTemplateDefinition] | None = None
_registry_lock = threading.Lock()


def _content_to_str(content: Any, content_type: str) -> str:
    if content is None:
        return ""
    if content_type == "json" and not isinstance(content, str):
        return json.dumps(content, ensure_ascii=False, indent=2)
    return str(content)


def _load_registry() -> dict[str, PromptTemplateDefinition]:
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        _logger.warning("PyYAML 未安装，prompt_template_defs 将返回空注册表")
        return {}

    registry: dict[str, PromptTemplateDefinition] = {}

    if not _TEMPLATES_DIR.exists():
        _logger.warning("模板目录不存在: %s", _TEMPLATES_DIR)
        return registry

    for yaml_file in sorted(_TEMPLATES_DIR.glob("*.yaml")):
        try:
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            for item in (data.get("templates") or []):
                if not isinstance(item, dict):
                    continue
                template_id = str(item.get("template_id") or "").strip()
                if not template_id:
                    continue
                content_type = str(item.get("content_type") or "text").strip()
                registry[template_id] = PromptTemplateDefinition(
                    template_id=template_id,
                    category=str(item.get("category") or "").strip(),
                    scope=str(item.get("scope") or "").strip(),
                    locale=str(item.get("locale") or "*").strip(),
                    content_type=content_type,
                    description=str(item.get("description") or "").strip(),
                    default_content=_content_to_str(item.get("default_content"), content_type),
                )
        except Exception as exc:
            _logger.warning("加载模板文件失败 %s: %s", yaml_file.name, exc)

    _logger.info("prompt_template_defs: 从 YAML 加载 %d 个模板", len(registry))
    return registry


def _get_registry() -> dict[str, PromptTemplateDefinition]:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = _load_registry()
    return _registry


def _invalidate_registry() -> None:
    """清除缓存，强制下次重新加载（主要用于测试）。"""
    global _registry
    with _registry_lock:
        _registry = None


def get_prompt_template_definition(template_id: str) -> PromptTemplateDefinition:
    key = str(template_id or "").strip()
    try:
        return _get_registry()[key]
    except KeyError as exc:
        raise KeyError(f"unknown prompt template: {template_id}") from exc


def list_prompt_template_definitions() -> list[PromptTemplateDefinition]:
    return sorted(
        _get_registry().values(),
        key=lambda item: (item.scope, item.category, item.template_id),
    )


class _LazyDict(dict):  # type: ignore[type-arg]
    """向后兼容：PROMPT_TEMPLATE_DEFINITIONS 懒加载字典。"""
    _loaded: bool = False
    _lock: threading.Lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            with self._lock:
                if not self._loaded:
                    self.update(_get_registry())
                    self._loaded = True

    def __getitem__(self, key: str) -> PromptTemplateDefinition:
        self._ensure_loaded()
        return super().__getitem__(key)

    def __contains__(self, key: object) -> bool:
        self._ensure_loaded()
        return super().__contains__(key)

    def values(self) -> Any:  # type: ignore[override]
        self._ensure_loaded()
        return super().values()

    def items(self) -> Any:  # type: ignore[override]
        self._ensure_loaded()
        return super().items()

    def keys(self) -> Any:  # type: ignore[override]
        self._ensure_loaded()
        return super().keys()


PROMPT_TEMPLATE_DEFINITIONS: dict[str, PromptTemplateDefinition] = _LazyDict()

__all__ = [
    "PromptTemplateDefinition",
    "PROMPT_TEMPLATE_DEFINITIONS",
    "get_prompt_template_definition",
    "list_prompt_template_definitions",
    "_invalidate_registry",
]
