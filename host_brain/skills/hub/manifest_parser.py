"""SKILL.md 解析器。

将 SKILL.md 的 YAML front matter 解析为标准化的 SkillManifest 数据结构，
可直接映射到 DeclarativeSkillSpec / SkillMeta。

SKILL.md 格式：
```yaml
---
name: "weather-query"
version: "1.0.0"
description: "查询天气信息"
author: "koinosakura"
tags: ["weather", "天气"]
trigger_words: ["天气", "温度", "weather"]

clearance: LOW          # LOW / MEDIUM / HIGH
risk_level: 1           # 1-5
scope: system           # host / memory / system
reversible: true
require_confirm: false
scheduler_allowed: true

tool: weather           # executor 路由 key
action: query           # executor 内部分发 action

dependencies: []        # pip 依赖（可选）
---

# Weather Query Skill

详细说明...
```
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# YAML front matter 正则：匹配 --- 之间的内容
_FRONT_MATTER_RE = re.compile(
    r"\A\s*---\s*\n(.*?)\n---",
    re.DOTALL,
)

# 允许的字段集合
_ALLOWED_CLEARANCE = {"LOW", "MEDIUM", "HIGH"}
_ALLOWED_SCOPE = {"host", "memory", "system"}


@dataclass
class SkillManifest:
    """SKILL.md 解析后的标准化数据结构。"""

    # 基本信息
    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)
    trigger_words: list[str] = field(default_factory=list)

    # SkillMeta 映射字段
    clearance: str = "LOW"
    risk_level: int = 1
    scope: str = "host"
    reversible: bool = True
    require_confirm: bool = False
    scheduler_allowed: bool = True

    # 执行配置
    tool: str = ""           # executor 路由 key
    action: str | None = None  # executor 内部分发 action

    # 依赖
    dependencies: list[str] = field(default_factory=list)

    # 源文件路径
    source_dir: str = ""
    skill_md_path: str = ""

    # 正文内容（--- 之后的 markdown）
    body: str = ""

    def validate(self) -> list[str]:
        """校验 manifest 有效性，返回错误列表。"""
        errors: list[str] = []
        if not self.name or not isinstance(self.name, str):
            errors.append("name is required and must be a non-empty string")
        if self.name and not re.match(r"^[a-z0-9][a-z0-9_-]*$", self.name):
            errors.append(
                f"name must be lowercase alphanumeric with hyphens/underscores: {self.name!r}"
            )
        if self.clearance.upper() not in _ALLOWED_CLEARANCE:
            errors.append(f"clearance must be one of {sorted(_ALLOWED_CLEARANCE)}")
        if not isinstance(self.risk_level, int) or self.risk_level < 1 or self.risk_level > 5:
            errors.append("risk_level must be int in [1, 5]")
        if self.scope not in _ALLOWED_SCOPE:
            errors.append(f"scope must be one of {sorted(_ALLOWED_SCOPE)}")
        if self.risk_level >= 4 and not self.require_confirm:
            errors.append("risk_level >= 4 requires require_confirm=True")
        if not self.tool:
            errors.append("tool is required (executor routing key)")
        return errors

    def to_dict(self) -> dict[str, Any]:
        """转为可序列化的 dict。"""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "tags": list(self.tags),
            "trigger_words": list(self.trigger_words),
            "clearance": self.clearance,
            "risk_level": self.risk_level,
            "scope": self.scope,
            "reversible": self.reversible,
            "require_confirm": self.require_confirm,
            "scheduler_allowed": self.scheduler_allowed,
            "tool": self.tool,
            "action": self.action,
            "dependencies": list(self.dependencies),
            "source_dir": self.source_dir,
        }


def _parse_yaml_simple(text: str) -> dict[str, Any]:
    """简易 YAML 解析器（不依赖 PyYAML）。

    支持：
    - key: value（字符串、数字、布尔）
    - key: [item1, item2]（内联列表）
    - key:
      - item1
      - item2
    """
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # 列表项 "- value"
        if stripped.startswith("- ") and current_key is not None and current_list is not None:
            val = stripped[2:].strip().strip("\"'")
            current_list.append(val)
            continue

        # key: value
        match = re.match(r"^([a-z_][a-z0-9_]*)\s*:\s*(.*)", stripped, re.IGNORECASE)
        if not match:
            continue

        # 如果之前有列表在累积，先保存
        if current_key is not None and current_list is not None:
            result[current_key] = current_list
            current_list = None
            current_key = None

        key = match.group(1).strip()
        val_raw = match.group(2).strip()

        # 空值 → 下一行可能是列表
        if not val_raw:
            current_key = key
            current_list = []
            continue

        # 内联列表 [a, b, c]
        if val_raw.startswith("[") and val_raw.endswith("]"):
            items = val_raw[1:-1].split(",")
            result[key] = [item.strip().strip("\"'") for item in items if item.strip()]
            continue

        # 布尔值
        if val_raw.lower() in ("true", "yes", "on"):
            result[key] = True
            continue
        if val_raw.lower() in ("false", "no", "off"):
            result[key] = False
            continue

        # 数字
        try:
            result[key] = int(val_raw)
            continue
        except ValueError:
            pass
        try:
            result[key] = float(val_raw)
            continue
        except ValueError:
            pass

        # 字符串（去引号）
        result[key] = val_raw.strip("\"'")

    # 处理末尾列表
    if current_key is not None and current_list is not None:
        result[current_key] = current_list

    return result


def parse_skill_md(path: Path) -> SkillManifest:
    """解析 SKILL.md 文件，返回 SkillManifest。

    Args:
        path: SKILL.md 文件路径

    Returns:
        解析后的 SkillManifest

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 解析失败
    """
    if not path.exists():
        raise FileNotFoundError(f"SKILL.md not found: {path}")

    content = path.read_text(encoding="utf-8")
    match = _FRONT_MATTER_RE.search(content)
    if not match:
        raise ValueError(f"SKILL.md missing YAML front matter (---): {path}")

    front_matter_text = match.group(1)
    body = content[match.end():].strip()

    data = _parse_yaml_simple(front_matter_text)
    if not data.get("name"):
        # 从目录名推断
        data["name"] = path.parent.name.replace(" ", "-").lower()

    manifest = SkillManifest(
        name=str(data.get("name") or "").strip(),
        version=str(data.get("version") or "0.0.0").strip(),
        description=str(data.get("description") or "").strip(),
        author=str(data.get("author") or "").strip(),
        tags=list(data.get("tags") or []),
        trigger_words=list(data.get("trigger_words") or []),
        clearance=str(data.get("clearance") or "LOW").strip().upper(),
        risk_level=int(data.get("risk_level") or 1),
        scope=str(data.get("scope") or "host").strip().lower(),
        reversible=bool(data.get("reversible", True)),
        require_confirm=bool(data.get("require_confirm", False)),
        scheduler_allowed=bool(data.get("scheduler_allowed", True)),
        tool=str(data.get("tool") or "").strip(),
        action=str(data.get("action") or "").strip() or None,
        dependencies=list(data.get("dependencies") or []),
        source_dir=str(path.parent),
        skill_md_path=str(path),
        body=body,
    )

    errors = manifest.validate()
    if errors:
        logger.warning("SKILL.md validation warnings (%s): %s", path, errors)

    return manifest
