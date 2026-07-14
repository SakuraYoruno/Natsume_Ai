"""Helpers for managing custom skill providers."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from host_brain.skills.provider_catalog import (
    CUSTOM_PROVIDER_CLASS_ATTR,
    CUSTOM_PROVIDER_DIR,
    CUSTOM_PROVIDER_PACKAGE,
)


_MAX_LEGACY_PROVIDER_BYTES = 256 * 1024


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
    if name != raw or "/" in raw or "\\" in raw:
        raise ValueError("filename must not contain a path")
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
    """Return the retired module identity without mutating Python import state."""

    return build_provider_module_name(filename)


def validate_provider_source(source_code: str, *, filename: str) -> None:
    source = str(source_code or "")
    if not source.strip():
        raise ValueError("source_code is required")
    try:
        tree = ast.parse(source, filename=filename)
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
    """Reject the legacy raw-source installer before it can reach import paths.

    A single Python source string cannot carry the TASK-0490 full inventory,
    signature, SBOM and sandbox contract.  Signed bundles use the supply-chain
    quarantine adapter; keeping this function as an explicit rejection lets
    the existing HTTP route fail closed without writing into the active Python
    package or silently preserving the old hot-import behavior.
    """

    normalized = normalize_provider_filename(filename)
    validate_provider_source(source_code, filename=normalized)
    raise ValueError("SKILL_SUPPLY_CHAIN_LEGACY_SOURCE_REJECTED")


def load_provider_template() -> str:
    template_path = CUSTOM_PROVIDER_DIR / "template_provider.py.example"
    return template_path.read_text(encoding="utf-8")


def _static_provider_metadata(
    source: str,
    *,
    filename: str,
) -> tuple[str, tuple[str, ...]]:
    """Read provider metadata from AST without importing attacker code."""

    tree = ast.parse(source, filename=filename)
    exported_class: str | None = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name)
            and target.id == CUSTOM_PROVIDER_CLASS_ATTR
            for target in node.targets
        ):
            continue
        if isinstance(node.value, ast.Name):
            exported_class = node.value.id
        break
    if not exported_class:
        raise ValueError(f"provider source must define {CUSTOM_PROVIDER_CLASS_ATTR}")
    if not any(
        isinstance(node, ast.ClassDef) and node.name == exported_class
        for node in tree.body
    ):
        raise ValueError("exported provider class is missing")

    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function_name = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            else ""
        )
        if function_name != "DeclarativeSkillSpec":
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "skill_name"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
                and keyword.value.value.strip()
            ):
                names.add(keyword.value.value.strip())
    return exported_class, tuple(sorted(names))


def load_provider_info(path: Path, *, active_skill_names: set[str] | None = None) -> CustomProviderInfo:
    module_name = build_provider_module_name(path.name)

    class_name: str | None = None
    declared_skill_names: tuple[str, ...] = ()
    status = "STAGED"
    error: str | None = None
    try:
        if path.is_symlink():
            raise ValueError("SKILL_SUPPLY_CHAIN_SYMLINK_REJECTED")
        size_bytes = path.stat().st_size
        if size_bytes <= 0 or size_bytes > _MAX_LEGACY_PROVIDER_BYTES:
            raise ValueError("SKILL_SUPPLY_CHAIN_FILE_SIZE_INVALID")
        source = path.read_text(encoding="utf-8")
        class_name, declared_skill_names = _static_provider_metadata(
            source,
            filename=path.name,
        )
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
