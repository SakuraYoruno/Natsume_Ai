"""Safety policy for advanced Bio-Ops proposals."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any


BIO_OPS_FORBIDDEN_TARGETS: tuple[str, ...] = (
    ".env",
    "bond",
    "canon",
    "personality_core",
    "secret",
    "secrets",
)

BIO_OPS_OWNER_ORGANS: tuple[str, ...] = (
    "hippocampus_agent",
    "glymphatic_agent",
    "immune_agent",
    "social_brain_agent",
    "prefrontal_agent",
    "default_mode_agent",
    "thalamus_agent",
    "surface_reply",
    "limbic_agent",
    "somatic_agent",
)


def is_bio_ops_sensitive_path(path: object) -> bool:
    normalized = str(path or "").replace("\\", "/").strip().lower()
    if not normalized:
        return False
    parts = PurePosixPath(normalized).parts
    return any(target in parts or target in normalized for target in BIO_OPS_FORBIDDEN_TARGETS)


def _as_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _task_attr(task: object, key: str, default: Any = None) -> Any:
    if isinstance(task, dict):
        return task.get(key, default)
    return getattr(task, key, default)


def _task_mapping(task: object, key: str) -> dict[str, Any]:
    value = _task_attr(task, key, {})
    return dict(value) if isinstance(value, dict) else {}


def bio_ops_task_target_paths(task: object) -> list[str]:
    inputs = _task_mapping(task, "inputs")
    scope = _task_mapping(task, "scope")
    paths: list[str] = []
    paths.extend(_as_text_list(inputs.get("target_paths")))
    paths.extend(_as_text_list(scope.get("target_paths")))
    for key in ("target_path", "path"):
        if inputs.get(key):
            paths.append(str(inputs.get(key)))
        if scope.get(key):
            paths.append(str(scope.get(key)))
    return paths


def bio_ops_task_is_write_candidate(task: object) -> bool:
    direct = bool(_task_attr(task, "is_write_candidate", False))
    mode = str(_task_attr(task, "mode", "") or "").strip().lower()
    inputs = _task_mapping(task, "inputs")
    scope = _task_mapping(task, "scope")
    actions = set(_as_text_list(inputs.get("proposed_actions")))
    actions.update(_as_text_list(scope.get("proposed_actions")))
    return bool(
        direct
        or mode == "candidate"
        or inputs.get("write_candidate")
        or inputs.get("proposes_long_term_write")
        or "write_files" in actions
        or "write_memory_candidate" in actions
    )


def validate_bio_ops_task_safety(task: object) -> tuple[bool, str | None, str | None]:
    target_paths = bio_ops_task_target_paths(task)
    blocked_paths = [path for path in target_paths if is_bio_ops_sensitive_path(path)]
    if blocked_paths:
        return False, "BIO_OPS_SENSITIVE_TARGET_BLOCKED", ", ".join(blocked_paths)

    inputs = _task_mapping(task, "inputs")
    scope = _task_mapping(task, "scope")
    actions = set(_as_text_list(inputs.get("proposed_actions")))
    actions.update(_as_text_list(scope.get("proposed_actions")))
    if "write_files" in actions:
        return False, "BIO_OPS_WRITE_FILES_FORBIDDEN", "BioOps tasks are proposal/report only."

    if bio_ops_task_is_write_candidate(task):
        owner = str(_task_attr(task, "owner_organ", "") or "").strip()
        if not owner:
            return False, "BIO_OPS_OWNER_REQUIRED", "Write candidates must name an owner organ."
        if owner not in BIO_OPS_OWNER_ORGANS:
            return False, "BIO_OPS_OWNER_UNKNOWN", owner

    return True, None, None


def build_proposal_only_policy() -> dict[str, Any]:
    return {
        "allowed_actions": ["read_files", "generate_report"],
        "forbidden_actions": [
            "write_files",
            "modify_personality_core",
            "modify_bond",
            "modify_canon",
            "modify_secret_files",
            "read_env_secrets",
        ],
        "review_required": False,
        "output": "proposal_or_report_only",
    }
