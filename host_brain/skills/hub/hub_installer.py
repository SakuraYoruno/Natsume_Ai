"""Sealed compatibility facade for the legacy Skills Hub installer.

Legacy local/GitHub/ClawHub install and remote-search requests do not carry a
signed bundle inventory, SBOM, or sandbox envelope.  They are therefore
rejected before filesystem, network, package-manager, or subprocess access.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from host_brain.skills.supply_chain import SupplyChainActivationMode, resolve_activation_mode

logger = logging.getLogger(__name__)


OFFICIAL_CLAWHUB_HOME_URL = os.environ.get("HOST_SKILL_CLAWHUB_HOME_URL", "https://clawhub.ai").strip() or "https://clawhub.ai"
OFFICIAL_CLAWHUB_DOCS_URL = os.environ.get("HOST_SKILL_CLAWHUB_DOCS_URL", "https://docs.openclaw.ai/tools/clawdhub").strip() or "https://docs.openclaw.ai/tools/clawdhub"
OFFICIAL_SKILLSHUB_HOME_URL = os.environ.get("HOST_SKILL_SKILLSHUB_HOME_URL", "https://www.skillshubco.com").strip() or "https://www.skillshubco.com"
OFFICIAL_SKILLSHUB_GITHUB_URL = os.environ.get("HOST_SKILL_SKILLSHUB_GITHUB_URL", "https://github.com/EYH0602/skillshub").strip() or "https://github.com/EYH0602/skillshub"
OFFICIAL_SKILLSHUB_INSTALL_EXAMPLE = os.environ.get("HOST_SKILL_SKILLSHUB_INSTALL_EXAMPLE", "skillshub:anthropics/skills/frontend-design").strip() or "skillshub:anthropics/skills/frontend-design"


_SAFE_SKILL_COMPONENT_RE = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,127}\Z")
_SAFE_GITHUB_COMPONENT_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_GITHUB_BRANCH_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._/-]{0,254}\Z")


def _activation_mode() -> tuple[SupplyChainActivationMode, bool]:
    try:
        resolution = resolve_activation_mode()
    except Exception:
        logger.exception("Hub installer: activation policy resolution failed")
        return SupplyChainActivationMode.BUILTINS_ONLY, False
    return resolution.mode, bool(resolution.valid)


def _legacy_mutation_rejection() -> str:
    mode, valid = _activation_mode()
    if not valid:
        return "SUPPLY_CHAIN_ACTIVATION_MODE_INVALID"
    if mode is SupplyChainActivationMode.BUILTINS_ONLY:
        return "SUPPLY_CHAIN_EXTERNAL_BUNDLES_DISABLED"
    return "SUPPLY_CHAIN_SIGNED_BUNDLE_REQUIRED"


def get_official_skill_hub_sources() -> list[dict[str, str]]:
    return [
        {"name": "ClawHub", "url": OFFICIAL_CLAWHUB_HOME_URL},
        {"name": "ClawHub Docs", "url": OFFICIAL_CLAWHUB_DOCS_URL},
        {"name": "Skillshub", "url": OFFICIAL_SKILLSHUB_HOME_URL},
        {"name": "Skillshub GitHub", "url": OFFICIAL_SKILLSHUB_GITHUB_URL},
    ]


def _run_clawhub_cli(
    command_args: list[str],
    *,
    workdir: Path,
    skills_dir: Path,
    timeout_s: float | None = None,
    retry_limit: int | None = None,
) -> Any:
    del command_args, workdir, skills_dir, timeout_s, retry_limit
    raise RuntimeError(_legacy_mutation_rejection())



def _parse_clawhub_search_output(stdout: str) -> list[SearchResult]:
    import re as _re

    results: list[SearchResult] = []
    for raw_line in str(stdout or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("-"):
            continue
        match = _re.match(r"^(?P<slug>\S+)\s{2,}(?P<title>.+?)\s+\((?P<score>[-+]?\d+(?:\.\d+)?)\)$", line)
        if not match:
            continue
        slug = str(match.group("slug") or "").strip()
        title = str(match.group("title") or slug).strip() or slug
        if not slug:
            continue
        results.append(SearchResult(
            name=slug,
            version="",
            description=title,
            author="ClawHub",
            tags=["clawhub"],
        ))
    return results



def _search_skills_via_clawhub_cli(query: str, *, skills_dir: Path | None = None) -> SearchResponse:
    del query, skills_dir
    return SearchResponse(
        results=[],
        registry_url=OFFICIAL_CLAWHUB_HOME_URL,
        error=_legacy_mutation_rejection(),
        discovery_sources=get_official_skill_hub_sources(),
    )



def _install_from_clawhub(
    *,
    skill_slug: str,
    hub_dir: Path,
    source_str: str,
    force: bool,
) -> InstallResult:
    del hub_dir, force
    return InstallResult(
        success=False,
        name=skill_slug if _is_safe_skill_component(skill_slug) else "",
        version="",
        source=source_str,
        install_dir="",
        detail="",
        error=_legacy_mutation_rejection(),
    )


@dataclass
class InstallResult:
    """安装结果。"""
    success: bool
    name: str
    version: str
    source: str
    install_dir: str
    detail: str
    error: str | None = None


@dataclass
class RemoveResult:
    """卸载结果。"""
    success: bool
    name: str
    detail: str
    error: str | None = None


@dataclass
class SearchResult:
    """搜索结果（单条）。"""
    name: str
    version: str
    description: str
    author: str
    tags: list[str]

@dataclass
class SearchResponse:
    """远程技能搜索结果。"""
    results: list[SearchResult]
    registry_url: str
    error: str | None = None
    discovery_sources: list[dict[str, str]] | None = None


def _is_safe_skill_component(value: str) -> bool:
    return bool(_SAFE_SKILL_COMPONENT_RE.fullmatch(str(value or "")))


def _is_safe_github_branch(value: str) -> bool:
    text = str(value or "")
    if not _SAFE_GITHUB_BRANCH_RE.fullmatch(text):
        return False
    return all(part not in {"", ".", ".."} for part in text.split("/"))


def _validated_github_parts(
    owner: str,
    repo: str,
    branch: str,
    slug: str,
) -> tuple[str, str, str] | None:
    if not _SAFE_GITHUB_COMPONENT_RE.fullmatch(owner):
        return None
    if not _SAFE_GITHUB_COMPONENT_RE.fullmatch(repo):
        return None
    if not _is_safe_github_branch(branch) or not _is_safe_skill_component(slug):
        return None
    return f"{owner}/{repo}", branch, slug



def _parse_github_source(source: str) -> tuple[str, str, str] | None:
    """解析 GitHub 来源字符串。

    支持格式：
    - github:owner/repo/skill-slug
    - github:owner/repo/skill-slug@branch
    - skillshub:owner/repo/skill-slug
    - skillshub:owner/repo/skill-slug@branch
    - https://github.com/owner/repo/tree/branch/skill-slug

    Returns:
        (owner/repo, branch, skill_slug) 或 None
    """
    # github:owner/repo/slug[@branch] / skillshub:owner/repo/slug[@branch]
    normalized_source = str(source or "").strip()
    if normalized_source.startswith("github:") or normalized_source.startswith("skillshub:"):
        prefix = "github:" if normalized_source.startswith("github:") else "skillshub:"
        rest = normalized_source[len(prefix):]
        parts = rest.split("/", 2)
        if len(parts) < 3:
            return None
        slug_and_branch = parts[2]
        if "@" in slug_and_branch:
            slug, branch = slug_and_branch.rsplit("@", 1)
        else:
            slug, branch = slug_and_branch, "main"
        return _validated_github_parts(parts[0], parts[1], branch, slug)

    # https://github.com/owner/repo/tree/branch/slug
    if normalized_source.lower().startswith("https://"):
        # 提取路径部分
        from urllib.parse import urlparse

        parsed = urlparse(normalized_source)
        if (
            parsed.scheme.lower() != "https"
            or (parsed.hostname or "").lower() != "github.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or parsed.query
            or parsed.fragment
        ):
            return None
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]
        if len(path_parts) < 4:
            return None
        # tree/branch/slug 或直接 slug
        if path_parts[2] == "tree" and len(path_parts) >= 5:
            branch = path_parts[3]
            slug = "/".join(path_parts[4:])
        else:
            branch = "main"
            slug = "/".join(path_parts[2:])
        return _validated_github_parts(path_parts[0], path_parts[1], branch, slug)

    return None


def _download_github_skill(
    owner_repo: str,
    branch: str,
    skill_slug: str,
    *,
    dest_dir: Path,
    timeout_s: float = 30.0,
) -> Path:
    """The legacy ZIP path is sealed; signed intake owns all future I/O."""

    del owner_repo, branch, skill_slug, dest_dir, timeout_s
    raise RuntimeError(_legacy_mutation_rejection())


def install_skill(
    source: str,
    *,
    skills_dir: Path | None = None,
    force: bool = False,
) -> InstallResult:
    """安装 Skill。

    Args:
        source: 来源（github:owner/repo/slug、本地路径）
        skills_dir: 目标 skills 目录
        force: 是否强制覆盖已安装的 skill

    Returns:
        InstallResult

    支持来源：
        - github:owner/repo/skill-slug
        - skillshub:owner/repo/skill-slug（Skillshub git-native tap 别名）
        - https://github.com/owner/repo/tree/branch/skill-slug
        - 本地目录路径
    """
    # Gate before Path(source), mkdir, DNS, URL handling, subprocess lookup, or
    # any filesystem mutation.  The legacy request does not carry the signed
    # bundle, SBOM and sandbox envelope required by the quarantine intake API.
    del skills_dir, force
    source_text = str(source or "").strip()
    return InstallResult(
        success=False,
        name="",
        version="",
        source=source_text,
        install_dir="",
        detail="",
        error=_legacy_mutation_rejection(),
    )


def _install_from_local(
    source_dir: Path,
    *,
    hub_dir: Path,
    force: bool,
) -> InstallResult:
    """Reject raw local directories before following symlinks or UNC paths."""

    del hub_dir, force
    return InstallResult(
        success=False,
        name=source_dir.name if _is_safe_skill_component(source_dir.name) else "",
        version="",
        source=str(source_dir),
        install_dir="",
        detail="",
        error=_legacy_mutation_rejection(),
    )


def _install_from_github(
    *,
    owner_repo: str,
    branch: str,
    skill_slug: str,
    hub_dir: Path,
    source_str: str,
    force: bool,
) -> InstallResult:
    """Reject raw GitHub sources; signed bundle intake is the only path."""

    del owner_repo, branch, hub_dir, force
    return InstallResult(
        success=False,
        name=skill_slug if _is_safe_skill_component(skill_slug) else "",
        version="",
        source=source_str,
        install_dir="",
        detail="",
        error=_legacy_mutation_rejection(),
    )


def remove_skill(
    name: str,
    *,
    skills_dir: Path | None = None,
) -> RemoveResult:
    """Reject legacy deletion; state transitions belong to the durable store."""

    del skills_dir
    normalized = str(name or "").strip()
    if not _is_safe_skill_component(normalized):
        return RemoveResult(
            success=False,
            name="",
            detail="",
            error="SUPPLY_CHAIN_SKILL_NAME_INVALID",
        )
    return RemoveResult(
        success=False,
        name=normalized,
        detail="",
        error="SUPPLY_CHAIN_MANAGED_REMOVAL_REQUIRED",
    )


def list_installed_skills(
    *,
    skills_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Legacy live directories are not an authoritative installed catalog."""

    del skills_dir
    return []


def search_remote_skills_with_status(
    query: str,
    *,
    registry_url: str | None = None,
    timeout_s: float = 10.0,
) -> SearchResponse:
    """Remote legacy discovery is sealed because it bypasses signed intake."""

    del query, timeout_s
    url = str(registry_url or "").strip()
    return SearchResponse(
        results=[],
        registry_url=url or OFFICIAL_CLAWHUB_HOME_URL,
        error=_legacy_mutation_rejection(),
        discovery_sources=get_official_skill_hub_sources(),
    )


def search_remote_skills(
    query: str,
    *,
    registry_url: str | None = None,
    timeout_s: float = 10.0,
) -> list[SearchResult]:
    return search_remote_skills_with_status(
        query,
        registry_url=registry_url,
        timeout_s=timeout_s,
    ).results
