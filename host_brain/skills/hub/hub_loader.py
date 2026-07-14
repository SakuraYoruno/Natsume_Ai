"""Fail-closed discovery boundary for external Skill bundles.

BUILTINS_ONLY and QUARANTINE perform no directory scan.  STAGED may verify and
report signed declarative bundles, but it never mutates the active registries,
imports Python, changes ``sys.path``, or invokes dependency auto-repair.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from host_brain.skills.hub.manifest_parser import parse_skill_md
from host_brain.skills.registry import SkillRegistry
from host_brain.skills.supply_chain import (
    SkillSupplyChainVerifier,
    SupplyChainActivationMode,
    resolve_activation_mode,
)

logger = logging.getLogger(__name__)

# 默认 Hub Skills 目录：项目根目录的 skills/（不是 host_brain/skills/hub/，
# 因为外部安装的 skill 是用户数据，不应混在核心代码目录中）
_DEFAULT_HUB_DIR = Path(__file__).resolve().parent.parent.parent.parent / "skills"
SKILL_MD_FILENAME = "SKILL.md"
PROVIDER_FILENAME = "provider.py"
EXECUTOR_FILENAME = "executor.py"
LOCK_FILENAME = ".natsume-skills-lock.json"
_MAX_SKILL_MD_BYTES = 1024 * 1024


def _resolve_supply_chain_mode() -> tuple[SupplyChainActivationMode, bool]:
    """Resolve the central activation policy without trusting invalid config."""

    try:
        resolution = resolve_activation_mode()
    except Exception:
        logger.exception("Hub: supply-chain activation policy resolution failed")
        return SupplyChainActivationMode.BUILTINS_ONLY, False
    return resolution.mode, bool(resolution.valid)


def _resolve_hub_skills_dir() -> Path:
    """解析 Hub Skills 目录路径。

    优先级：
    1. HOST_SKILL_HUB_DIR 环境变量
    2. 默认项目根目录 skills/
    """
    env_dir = os.environ.get("HOST_SKILL_HUB_DIR", "").strip()
    if env_dir:
        return Path(env_dir).resolve()
    return _DEFAULT_HUB_DIR


@dataclass
class HubSkillInfo:
    """单个 Hub Skill 的加载信息。"""
    name: str
    version: str
    source_dir: str
    status: str  # "STAGED" / "REJECTED" / "ERROR"
    provider_type: str  # "declarative_staged" / "skipped"
    has_executor: bool
    skill_names: list[str] = field(default_factory=list)
    error: str | None = None
    reason_code: str | None = None
    activation_eligible: bool = False
    bundle_digest: str | None = None


@dataclass
class HubLoadResult:
    """Hub 加载总结果。"""
    skills_dir: str
    total_dirs_scanned: int
    registered_count: int
    skipped_count: int
    error_count: int
    skills: list[HubSkillInfo] = field(default_factory=list)
    ts_ms: int = 0
    activation_mode: str = SupplyChainActivationMode.BUILTINS_ONLY.value
    activation_eligible_count: int = 0
    reason_code: str | None = None


def _load_custom_provider(skill_dir: Path) -> Any | None:
    """Legacy Python providers are never imported into the Host process."""

    del skill_dir
    return None


def _load_custom_executor(
    skill_dir: Path,
    tool_name: str,
) -> Any | None:
    """Legacy Python executors are never imported or auto-repaired."""

    del skill_dir, tool_name
    return None


def _resolve_external_skill_dirs() -> list[Path]:
    """解析外部 Skills 目录列表（SkillHub / ClawHub / OpenClaw）。

    自动扫描以下目录（如存在）：
    1. ~/.skillhub/skills/       — SkillHub CLI 安装目录
    2. ~/.openclaw/skills/       — OpenClaw workspace skills
    3. ~/.codebuddy/skills/      — CodeBuddy/WorkBuddy skills
    4. HOST_SKILL_EXTERNAL_DIRS 环境变量指定的额外目录（逗号分隔）
    """
    home = Path.home()
    candidates = [
        home / ".skillhub" / "skills",
        home / ".openclaw" / "skills",
        home / ".codebuddy" / "skills",
    ]

    # 环境变量指定的额外目录
    extra_raw = os.environ.get("HOST_SKILL_EXTERNAL_DIRS", "").strip()
    if extra_raw:
        for part in extra_raw.replace(";", ",").split(","):
            p = part.strip()
            if p:
                candidates.append(Path(p))

    return [d for d in candidates if d.exists() and d.is_dir()]


def load_hub_skills(
    registry: SkillRegistry,
    *,
    executor_registry: Any | None = None,
    skills_dir: Path | None = None,
) -> HubLoadResult:
    """Verify staged bundles without registering or executing their content.

    扫描顺序：
    1. hub/ 目录（Natsume 本地 Skills Hub）
    2. ~/.skillhub/skills/（SkillHub CLI 安装的技能）
    3. ~/.openclaw/skills/（OpenClaw workspace 技能）
    4. ~/.codebuddy/skills/（CodeBuddy/WorkBuddy 技能）
    5. HOST_SKILL_EXTERNAL_DIRS 环境变量指定的额外目录

    BUILTINS_ONLY and QUARANTINE return before all scanning.  External staged
    roots require the explicit HOST_SKILL_SCAN_EXTERNAL opt-in.

    Args:
        registry: SkillRegistry 实例
        executor_registry: ToolExecutorRegistry 实例（可选）
        skills_dir: hub skills 目录路径（可选）

    Returns:
        HubLoadResult 加载结果汇总
    """
    # This gate intentionally runs before path resolution, directory probing,
    # scanning, imports, or sys.path changes.  Missing/invalid configuration
    # is the safe BUILTINS_ONLY state.
    mode, mode_valid = _resolve_supply_chain_mode()
    hub_dir = skills_dir or (
        _DEFAULT_HUB_DIR
        if not mode_valid or mode is not SupplyChainActivationMode.STAGED
        else _resolve_hub_skills_dir()
    )
    result = HubLoadResult(
        skills_dir=str(hub_dir),
        total_dirs_scanned=0,
        registered_count=0,
        skipped_count=0,
        error_count=0,
        ts_ms=int(time.time() * 1000),
        activation_mode=mode.value,
        reason_code=(
            "SUPPLY_CHAIN_ACTIVATION_MODE_INVALID"
            if not mode_valid
            else "SUPPLY_CHAIN_EXTERNAL_BUNDLES_DISABLED"
            if mode is SupplyChainActivationMode.BUILTINS_ONLY
            else "SUPPLY_CHAIN_QUARANTINE_NO_ACTIVE_SCAN"
            if mode is SupplyChainActivationMode.QUARANTINE
            else None
        ),
    )
    if not mode_valid or mode in {
        SupplyChainActivationMode.BUILTINS_ONLY,
        SupplyChainActivationMode.QUARANTINE,
    }:
        return result

    all_dirs: list[Path] = []

    # 主目录
    if hub_dir.exists() and hub_dir.is_dir():
        all_dirs.append(hub_dir)

    # 外部目录（SkillHub / ClawHub / OpenClaw）
    scan_external = os.environ.get("HOST_SKILL_SCAN_EXTERNAL", "false").strip().lower()
    if scan_external in {"1", "true", "yes", "on"}:
        all_dirs.extend(_resolve_external_skill_dirs())

    if not all_dirs:
        logger.info("Hub: 无可扫描的 skills 目录")
        return result

    try:
        verifier = SkillSupplyChainVerifier.from_environment()
    except Exception as exc:
        logger.warning("Hub: supply-chain verifier unavailable: %s", exc)
        result.reason_code = "SUPPLY_CHAIN_VERIFIER_UNAVAILABLE"
        return result

    for scan_dir in all_dirs:
        _scan_single_dir(
            scan_dir=scan_dir,
            registry=registry,
            executor_registry=executor_registry,
            result=result,
            verifier=verifier,
        )

    if result.registered_count > 0:
        logger.info(
            "Hub: 加载完成 dirs=%d scanned=%d registered=%d skipped=%d errors=%d",
            len(all_dirs),
            result.total_dirs_scanned,
            result.registered_count,
            result.skipped_count,
            result.error_count,
        )

    return result


def _scan_single_dir(
    *,
    scan_dir: Path,
    registry: SkillRegistry,
    executor_registry: Any | None,
    result: HubLoadResult,
    verifier: SkillSupplyChainVerifier,
) -> None:
    """Statically verify staged bundles without registering or importing."""

    del executor_registry
    try:
        root_metadata = scan_dir.lstat()
        root_attributes = int(getattr(root_metadata, "st_file_attributes", 0) or 0)
        reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0)
        if scan_dir.is_symlink() or bool(reparse_flag and root_attributes & reparse_flag):
            raise ValueError("SUPPLY_CHAIN_SCAN_ROOT_SYMLINK_FORBIDDEN")
        items = sorted(scan_dir.iterdir())
    except (OSError, ValueError) as exc:
        result.error_count += 1
        result.reason_code = (
            str(exc)
            if isinstance(exc, ValueError) and str(exc).startswith("SUPPLY_CHAIN_")
            else "SUPPLY_CHAIN_SCAN_DIRECTORY_UNREADABLE"
        )
        logger.warning("Hub: staged scan directory unreadable (%s): %s", scan_dir, exc)
        return
    for item in items:
        try:
            item_metadata = item.lstat()
        except OSError as exc:
            result.total_dirs_scanned += 1
            result.error_count += 1
            result.skills.append(HubSkillInfo(
                name=item.name,
                version="",
                source_dir=str(item),
                status="REJECTED",
                provider_type="skipped",
                has_executor=False,
                error=str(exc),
                reason_code="SUPPLY_CHAIN_BUNDLE_UNREADABLE",
            ))
            continue
        item_attributes = int(getattr(item_metadata, "st_file_attributes", 0) or 0)
        if stat.S_ISLNK(item_metadata.st_mode) or bool(
            reparse_flag and item_attributes & reparse_flag
        ):
            result.total_dirs_scanned += 1
            result.error_count += 1
            result.skills.append(HubSkillInfo(
                name=item.name,
                version="",
                source_dir=str(item),
                status="REJECTED",
                provider_type="skipped",
                has_executor=False,
                error="symlink/reparse bundle roots are forbidden",
                reason_code="SUPPLY_CHAIN_SYMLINK_FORBIDDEN",
            ))
            continue
        if not stat.S_ISDIR(item_metadata.st_mode):
            continue
        if item.name.startswith(("_", ".")):
            continue

        skill_md = item / SKILL_MD_FILENAME
        try:
            skill_metadata = skill_md.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            result.total_dirs_scanned += 1
            result.error_count += 1
            result.skills.append(HubSkillInfo(
                name=item.name,
                version="",
                source_dir=str(item),
                status="REJECTED",
                provider_type="skipped",
                has_executor=False,
                error=str(exc),
                reason_code="SUPPLY_CHAIN_SKILL_MANIFEST_UNREADABLE",
            ))
            continue
        skill_attributes = int(getattr(skill_metadata, "st_file_attributes", 0) or 0)
        if stat.S_ISLNK(skill_metadata.st_mode) or bool(
            reparse_flag and skill_attributes & reparse_flag
        ):
            result.total_dirs_scanned += 1
            result.error_count += 1
            result.skills.append(HubSkillInfo(
                name=item.name,
                version="",
                source_dir=str(item),
                status="REJECTED",
                provider_type="skipped",
                has_executor=False,
                error="symlink/reparse SKILL.md is forbidden",
                reason_code="SUPPLY_CHAIN_SYMLINK_FORBIDDEN",
            ))
            continue
        if not stat.S_ISREG(skill_metadata.st_mode):
            continue

        result.total_dirs_scanned += 1
        info = _load_single_skill(
            skill_dir=item,
            skill_md=skill_md,
            registry=registry,
            executor_registry=None,
            verifier=verifier,
        )
        result.skills.append(info)

        if info.activation_eligible:
            result.activation_eligible_count += 1
            result.skipped_count += 1
        elif info.status == "ERROR":
            result.error_count += 1
        elif info.status == "REJECTED":
            result.error_count += 1
        else:
            result.skipped_count += 1


def _load_single_skill(
    *,
    skill_dir: Path,
    skill_md: Path,
    registry: SkillRegistry,
    executor_registry: Any | None,
    verifier: SkillSupplyChainVerifier | None = None,
) -> HubSkillInfo:
    """Verify and report a staged bundle without activating any code."""

    del registry, executor_registry
    if verifier is None:
        return HubSkillInfo(
            name=skill_dir.name,
            version="",
            source_dir=str(skill_dir),
            status="REJECTED",
            provider_type="skipped",
            has_executor=False,
            error="supply-chain verifier is required",
            reason_code="SUPPLY_CHAIN_VERIFIER_REQUIRED",
        )

    try:
        decision = verifier.verify_bundle(skill_dir)
    except Exception:
        return HubSkillInfo(
            name=skill_dir.name,
            version="",
            source_dir=str(skill_dir),
            status="REJECTED",
            provider_type="skipped",
            has_executor=False,
            error="SUPPLY_CHAIN_VERIFIER_FAILED",
            reason_code="SUPPLY_CHAIN_VERIFIER_FAILED",
            activation_eligible=False,
        )
    if not decision.activation_eligible:
        return HubSkillInfo(
            name=decision.bundle_name or skill_dir.name,
            version=decision.bundle_version or "",
            source_dir=str(skill_dir),
            status="REJECTED",
            provider_type="skipped",
            has_executor=False,
            error=decision.reason_code,
            reason_code=decision.reason_code,
            activation_eligible=False,
            bundle_digest=decision.bundle_digest,
        )

    # Python extensions remain forbidden even for a cryptographically valid
    # bundle.  TASK-0490 has no sandbox worker handoff that could execute them.
    if (skill_dir / PROVIDER_FILENAME).exists() or (skill_dir / EXECUTOR_FILENAME).exists():
        return HubSkillInfo(
            name=decision.bundle_name or skill_dir.name,
            version=decision.bundle_version or "",
            source_dir=str(skill_dir),
            status="REJECTED",
            provider_type="skipped",
            has_executor=False,
            error="provider.py/executor.py require a future sandbox worker",
            reason_code="SUPPLY_CHAIN_PYTHON_EXTENSION_FORBIDDEN",
            activation_eligible=False,
            bundle_digest=decision.bundle_digest,
        )

    try:
        metadata = skill_md.lstat()
        attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
        reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0)
        if (
            skill_md.is_symlink()
            or bool(reparse_flag and attributes & reparse_flag)
            or not stat.S_ISREG(metadata.st_mode)
        ):
            raise ValueError("SUPPLY_CHAIN_SKILL_MANIFEST_NOT_REGULAR")
        if metadata.st_size <= 0 or metadata.st_size > _MAX_SKILL_MD_BYTES:
            raise ValueError("SUPPLY_CHAIN_SKILL_MANIFEST_SIZE_INVALID")
    except (OSError, ValueError) as exc:
        return HubSkillInfo(
            name=decision.bundle_name or skill_dir.name,
            version=decision.bundle_version or "",
            source_dir=str(skill_dir),
            status="ERROR",
            provider_type="skipped",
            has_executor=False,
            error=str(exc),
            reason_code=(
                str(exc)
                if isinstance(exc, ValueError) and str(exc).startswith("SUPPLY_CHAIN_")
                else "SUPPLY_CHAIN_SKILL_MANIFEST_UNREADABLE"
            ),
            bundle_digest=decision.bundle_digest,
        )

    try:
        manifest = parse_skill_md(skill_md)
    except Exception as exc:
        return HubSkillInfo(
            name=skill_dir.name,
            version="",
            source_dir=str(skill_dir),
            status="ERROR",
            provider_type="skipped",
            has_executor=False,
            error=f"SKILL.md parse error: {exc}",
            reason_code="SUPPLY_CHAIN_SKILL_MANIFEST_INVALID",
            bundle_digest=decision.bundle_digest,
        )

    errors = manifest.validate()
    if errors:
        return HubSkillInfo(
            name=manifest.name,
            version=manifest.version,
            source_dir=str(skill_dir),
            status="ERROR",
            provider_type="skipped",
            has_executor=False,
            error=f"validation errors: {'; '.join(errors)}",
            reason_code="SUPPLY_CHAIN_SKILL_MANIFEST_INVALID",
            bundle_digest=decision.bundle_digest,
        )
    if manifest.name != decision.bundle_name:
        return HubSkillInfo(
            name=manifest.name,
            version=manifest.version,
            source_dir=str(skill_dir),
            status="REJECTED",
            provider_type="skipped",
            has_executor=False,
            error="SKILL.md name does not match signed bundle identity",
            reason_code="SUPPLY_CHAIN_BUNDLE_IDENTITY_MISMATCH",
            bundle_digest=decision.bundle_digest,
        )

    return HubSkillInfo(
        name=manifest.name,
        version=manifest.version,
        source_dir=str(skill_dir),
        status="STAGED",
        provider_type="declarative_staged",
        has_executor=False,
        skill_names=[manifest.name],
        reason_code=decision.reason_code,
        activation_eligible=True,
        bundle_digest=decision.bundle_digest,
    )


# ── Lock File 管理 ────────────────────────────────────────────────────────

def read_lock_file(skills_dir: Path | None = None) -> dict[str, Any]:
    """读取 skills lock 文件。"""
    hub_dir = skills_dir or _resolve_hub_skills_dir()
    lock_path = hub_dir / LOCK_FILENAME
    if not lock_path.exists():
        return {}
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_lock_file(data: dict[str, Any], skills_dir: Path | None = None) -> None:
    """写入 skills lock 文件。"""
    hub_dir = skills_dir or _resolve_hub_skills_dir()
    hub_dir.mkdir(parents=True, exist_ok=True)
    lock_path = hub_dir / LOCK_FILENAME
    lock_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def update_lock_entry(
    name: str,
    version: str,
    source: str,
    *,
    skills_dir: Path | None = None,
) -> None:
    """更新 lock 文件中的单个条目。"""
    lock = read_lock_file(skills_dir)
    lock[name] = {
        "version": version,
        "source": source,
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    write_lock_file(lock, skills_dir)


def remove_lock_entry(name: str, *, skills_dir: Path | None = None) -> bool:
    """从 lock 文件中移除条目。"""
    lock = read_lock_file(skills_dir)
    if name not in lock:
        return False
    del lock[name]
    write_lock_file(lock, skills_dir)
    return True
