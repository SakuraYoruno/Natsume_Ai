"""Helpers for managing custom skill providers."""

from __future__ import annotations

import ast
import importlib
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path

from host_brain.skills.provider_catalog import (
    CUSTOM_PROVIDER_CLASS_ATTR,
    CUSTOM_PROVIDER_DIR,
    CUSTOM_PROVIDER_PACKAGE,
)


@dataclass(frozen=True)
class CustomProviderInfo:
    filename: str
    module_name: str
    class_name: str | None
    path: str
    size_bytes: int
    updated_at_ms: int
    declared_skill_names: tuple[str, ...]
    active_skill_names: tuple[str, ...]
    status: str
    error: str | None = None


def normalize_provider_filename(filename: str) -> str:
    raw = str(filename or "").strip()
    if not raw:
        raise ValueError("filename is required")
    name = Path(raw).name.strip()
    if not name:
        raise ValueError("filename is required")
    if name in {"__init__.py", "README.md"}:
        raise ValueError("reserved filename")
    if name.endswith(".example"):
        raise ValueError("example file cannot be overwritten")
    if name.endswith(".py"):
        stem = name[:-3]
    else:
        stem = name
    stem = stem.strip().replace("-", "_")
    if not stem:
        raise ValueError("filename is required")
    if not stem.endswith("_provider"):
        stem = f"{stem}_provider"
    if not stem.replace("_", "").isalnum() or not stem[0].isalpha():
        raise ValueError("filename must contain only letters, digits, and underscores")
    return f"{stem}.py"


def build_provider_module_name(filename: str) -> str:
    normalized = normalize_provider_filename(filename)
    return f"{CUSTOM_PROVIDER_PACKAGE}.{Path(normalized).stem}"


def invalidate_provider_module(filename: str) -> str:
    module_name = build_provider_module_name(filename)
    importlib.invalidate_caches()
    sys.modules.pop(module_name, None)
    return module_name


def validate_provider_source(source_code: str, *, filename: str) -> None:
    source = str(source_code or "")
    if not source.strip():
        raise ValueError("source_code is required")
    try:
        tree = ast.parse(source, filename=filename)
        compile(tree, filename, "exec")
    except SyntaxError as exc:
        raise ValueError(f"provider source syntax error: {exc.msg} (line {exc.lineno})") from exc

    has_provider_class_attr = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == CUSTOM_PROVIDER_CLASS_ATTR:
                    has_provider_class_attr = True
                    break
        if has_provider_class_attr:
            break
    if not has_provider_class_attr:
        raise ValueError(f"provider source must define {CUSTOM_PROVIDER_CLASS_ATTR}")


def get_provider_path(filename: str) -> Path:
    return CUSTOM_PROVIDER_DIR / normalize_provider_filename(filename)


def write_provider_source(filename: str, source_code: str) -> Path:
    normalized = normalize_provider_filename(filename)
    validate_provider_source(source_code, filename=normalized)
    CUSTOM_PROVIDER_DIR.mkdir(parents=True, exist_ok=True)
    path = CUSTOM_PROVIDER_DIR / normalized
    path.write_text(str(source_code).rstrip() + "\n", encoding="utf-8")
    return path


def load_provider_template() -> str:
    template_path = CUSTOM_PROVIDER_DIR / "template_provider.py.example"
    return template_path.read_text(encoding="utf-8")


def _extract_skill_names(provider_cls: type) -> tuple[str, ...]:
    provider = provider_cls()
    declarative_entries = getattr(provider, "__skill_meta_entries__", None)
    if callable(declarative_entries):
        return tuple(entry.skill_name for entry in declarative_entries())

    names: list[str] = []
    for attr_name in sorted(dir(provider)):
        candidate = getattr(provider, attr_name)
        cfg = getattr(candidate, "__skill_meta_config__", None)
        if isinstance(cfg, dict):
            names.append(str(cfg.get("skill_name") or attr_name).strip())
    return tuple(names)


def load_provider_info(path: Path, *, active_skill_names: set[str] | None = None) -> CustomProviderInfo:
    module_name = invalidate_provider_module(path.name)

    class_name: str | None = None
    declared_skill_names: tuple[str, ...] = ()
    status = "STAGED"
    error: str | None = None
    try:
        module = importlib.import_module(module_name)
        provider_cls = getattr(module, CUSTOM_PROVIDER_CLASS_ATTR, None)
        if not inspect.isclass(provider_cls):
            raise TypeError(f"{CUSTOM_PROVIDER_CLASS_ATTR} is missing or not a class")
        class_name = provider_cls.__name__
        declared_skill_names = _extract_skill_names(provider_cls)
        if declared_skill_names and active_skill_names is not None:
            active = tuple(name for name in declared_skill_names if name in active_skill_names)
            status = "ACTIVE" if active and len(active) == len(declared_skill_names) else "STAGED"
        else:
            active = ()
    except Exception as exc:
        active = ()
        status = "ERROR"
        error = str(exc)

    return CustomProviderInfo(
        filename=path.name,
        module_name=module_name,
        class_name=class_name,
        path=str(path),
        size_bytes=path.stat().st_size,
        updated_at_ms=int(path.stat().st_mtime * 1000),
        declared_skill_names=declared_skill_names,
        active_skill_names=active,
        status=status,
        error=error,
    )


def list_custom_provider_infos(*, active_skill_names: set[str] | None = None) -> list[CustomProviderInfo]:
    if not CUSTOM_PROVIDER_DIR.exists() or not CUSTOM_PROVIDER_DIR.is_dir():
        return []
    infos: list[CustomProviderInfo] = []
    for path in sorted(CUSTOM_PROVIDER_DIR.glob("*_provider.py")):
        if path.name.startswith("_"):
            continue
        infos.append(load_provider_info(path, active_skill_names=active_skill_names))
    return infos
