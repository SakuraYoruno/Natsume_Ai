"""Skills Hub 远程安装器。

支持从官方兼容来源安装 Skill：
  natsume skills install github:owner/repo/skill-slug
  natsume skills install skillshub:owner/repo/skill-slug
  natsume skills install https://github.com/owner/repo/tree/main/skill-slug
  natsume skills install ./local/path/to/skill-dir

官方发现入口：
  - ClawHub: https://clawhub.ai
  - Skillshub: https://www.skillshubco.com

安装流程：
  1. 解析来源（GitHub URL / 本地路径）
  2. 下载/复制 skill 目录到 hub/
  3. 校验 SKILL.md 有效性
  4. 更新 lock 文件
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from host_brain.skills.hub.hub_loader import (
    LOCK_FILENAME,
    SKILL_MD_FILENAME,
    _resolve_hub_skills_dir,
    read_lock_file,
    remove_lock_entry,
    update_lock_entry,
)
from host_brain.skills.hub.manifest_parser import parse_skill_md

logger = logging.getLogger(__name__)


OFFICIAL_CLAWHUB_HOME_URL = os.environ.get("HOST_SKILL_CLAWHUB_HOME_URL", "https://clawhub.ai").strip() or "https://clawhub.ai"
OFFICIAL_CLAWHUB_DOCS_URL = os.environ.get("HOST_SKILL_CLAWHUB_DOCS_URL", "https://docs.openclaw.ai/tools/clawdhub").strip() or "https://docs.openclaw.ai/tools/clawdhub"
OFFICIAL_SKILLSHUB_HOME_URL = os.environ.get("HOST_SKILL_SKILLSHUB_HOME_URL", "https://www.skillshubco.com").strip() or "https://www.skillshubco.com"
OFFICIAL_SKILLSHUB_GITHUB_URL = os.environ.get("HOST_SKILL_SKILLSHUB_GITHUB_URL", "https://github.com/EYH0602/skillshub").strip() or "https://github.com/EYH0602/skillshub"
OFFICIAL_SKILLSHUB_INSTALL_EXAMPLE = os.environ.get("HOST_SKILL_SKILLSHUB_INSTALL_EXAMPLE", "skillshub:anthropics/skills/frontend-design").strip() or "skillshub:anthropics/skills/frontend-design"


OFFICIAL_CLAWHUB_NPX_PACKAGE = os.environ.get("HOST_SKILL_CLAWHUB_NPX_PACKAGE", "clawhub").strip() or "clawhub"
HOST_SKILL_CLAWHUB_TIMEOUT_S = float(os.environ.get("HOST_SKILL_CLAWHUB_TIMEOUT_S", "120") or "120")
HOST_SKILL_CLAWHUB_RETRY_LIMIT = int(os.environ.get("HOST_SKILL_CLAWHUB_RETRY_LIMIT", "2") or "2")
HOST_SKILL_CLAWHUB_SEARCH_LIMIT = int(os.environ.get("HOST_SKILL_CLAWHUB_SEARCH_LIMIT", "8") or "8")


def get_official_skill_hub_sources() -> list[dict[str, str]]:
    return [
        {"name": "ClawHub", "url": OFFICIAL_CLAWHUB_HOME_URL},
        {"name": "ClawHub Docs", "url": OFFICIAL_CLAWHUB_DOCS_URL},
        {"name": "Skillshub", "url": OFFICIAL_SKILLSHUB_HOME_URL},
        {"name": "Skillshub GitHub", "url": OFFICIAL_SKILLSHUB_GITHUB_URL},
    ]


def _normalize_cli_output(*parts: str) -> str:
    chunks: list[str] = []
    for part in parts:
        text = str(part or "").strip()
        if text:
            chunks.append(text)
    combined = "\n".join(chunks).strip()
    combined = combined.replace("\r", "")
    return re.sub(r"\x1b\[[0-9;]*m", "", combined)



def _extract_retry_after_seconds(message: str) -> float:
    import re as _re

    match = _re.search(r"retry in\s+(\d+(?:\.\d+)?)s", str(message or ""), _re.IGNORECASE)
    if not match:
        return 1.0
    try:
        return max(0.5, float(match.group(1)))
    except ValueError:
        return 1.0



def _resolve_npx_command() -> str:
    candidates = ["npx.cmd", "npx"] if os.name == "nt" else ["npx"]
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return candidates[0]


def _build_clawhub_base_command(*, workdir: Path, skills_dir: Path) -> list[str]:
    return [
        _resolve_npx_command(),
        "-y",
        OFFICIAL_CLAWHUB_NPX_PACKAGE,
        "--no-input",
        "--workdir",
        str(workdir),
        "--dir",
        str(skills_dir.name),
    ]



def _run_clawhub_cli(
    command_args: list[str],
    *,
    workdir: Path,
    skills_dir: Path,
    timeout_s: float | None = None,
    retry_limit: int | None = None,
) -> subprocess.CompletedProcess[str]:
    timeout_value = float(timeout_s or HOST_SKILL_CLAWHUB_TIMEOUT_S)
    max_retries = max(0, int(HOST_SKILL_CLAWHUB_RETRY_LIMIT if retry_limit is None else retry_limit))
    base_cmd = _build_clawhub_base_command(workdir=workdir, skills_dir=skills_dir)
    last_result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(max_retries + 1):
        result = subprocess.run(
            [*base_cmd, *command_args],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_value,
            check=False,
        )
        last_result = result
        output_text = _normalize_cli_output(result.stdout, result.stderr)
        if result.returncode == 0:
            return result
        if "Rate limit exceeded" in output_text and attempt < max_retries:
            time.sleep(_extract_retry_after_seconds(output_text))
            continue
        return result
    assert last_result is not None
    return last_result



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
    hub_dir = skills_dir or _resolve_hub_skills_dir()
    workdir = hub_dir.parent
    result = _run_clawhub_cli(
        ["search", str(query or "").strip(), "--limit", str(HOST_SKILL_CLAWHUB_SEARCH_LIMIT)],
        workdir=workdir,
        skills_dir=hub_dir,
    )
    output_text = _normalize_cli_output(result.stdout, result.stderr)
    parsed = _parse_clawhub_search_output(result.stdout)
    if result.returncode != 0:
        return SearchResponse(
            results=[],
            registry_url=OFFICIAL_CLAWHUB_HOME_URL,
            error=output_text or "clawhub search failed",
            discovery_sources=get_official_skill_hub_sources(),
        )
    return SearchResponse(
        results=parsed,
        registry_url=OFFICIAL_CLAWHUB_HOME_URL,
        error=None,
        discovery_sources=get_official_skill_hub_sources(),
    )



def _install_from_clawhub(
    *,
    skill_slug: str,
    hub_dir: Path,
    source_str: str,
    force: bool,
) -> InstallResult:
    workdir = hub_dir.parent
    command = ["install", str(skill_slug or "").strip()]
    if force:
        command.append("--force")
    result = _run_clawhub_cli(command, workdir=workdir, skills_dir=hub_dir)
    output_text = _normalize_cli_output(result.stdout, result.stderr)
    install_dir = hub_dir / skill_slug
    if result.returncode != 0:
        return InstallResult(
            success=False,
            name=skill_slug,
            version="",
            source=source_str,
            install_dir=str(install_dir),
            detail="",
            error=output_text or "clawhub install failed",
        )
    skill_md = install_dir / SKILL_MD_FILENAME
    if not skill_md.exists():
        return InstallResult(
            success=False,
            name=skill_slug,
            version="",
            source=source_str,
            install_dir=str(install_dir),
            detail=output_text,
            error=f"ClawHub install finished but SKILL.md was not found in {install_dir}",
        )
    try:
        manifest = parse_skill_md(skill_md)
    except Exception as exc:
        return InstallResult(
            success=False,
            name=skill_slug,
            version="",
            source=source_str,
            install_dir=str(install_dir),
            detail=output_text,
            error=f"SKILL.md parse error: {exc}",
        )
    update_lock_entry(
        manifest.name,
        manifest.version,
        source_str,
        skills_dir=hub_dir,
    )
    return InstallResult(
        success=True,
        name=manifest.name,
        version=manifest.version,
        source=source_str,
        install_dir=str(install_dir),
        detail=output_text or f"Installed {manifest.name} from ClawHub",
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
        owner_repo = f"{parts[0]}/{parts[1]}"
        slug_and_branch = parts[2]
        if "@" in slug_and_branch:
            slug, branch = slug_and_branch.rsplit("@", 1)
        else:
            slug, branch = slug_and_branch, "main"
        return owner_repo, branch, slug

    # https://github.com/owner/repo/tree/branch/slug
    if "github.com" in source:
        # 提取路径部分
        from urllib.parse import urlparse

        parsed = urlparse(source)
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]
        if len(path_parts) < 4:
            return None
        owner_repo = f"{path_parts[0]}/{path_parts[1]}"
        # tree/branch/slug 或直接 slug
        if path_parts[2] == "tree" and len(path_parts) >= 5:
            branch = path_parts[3]
            slug = "/".join(path_parts[4:])
        else:
            branch = "main"
            slug = "/".join(path_parts[2:])
        return owner_repo, branch, slug

    return None


def _download_github_skill(
    owner_repo: str,
    branch: str,
    skill_slug: str,
    *,
    dest_dir: Path,
    timeout_s: float = 30.0,
) -> Path:
    """从 GitHub 下载单个 skill 目录。

    策略：下载整个仓库 ZIP → 解压 → 提取目标 skill 目录
    """
    zip_url = f"https://github.com/{owner_repo}/archive/refs/heads/{branch}.zip"
    logger.info("Hub install: downloading %s", zip_url)

    with tempfile.TemporaryDirectory(prefix="natsume_hub_") as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "repo.zip"

        # 下载 ZIP
        req = urllib.request.Request(zip_url, headers={"User-Agent": "Natsume-Ai-Hub/1.0"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            zip_path.write_bytes(resp.read())

        # 解压
        extract_dir = tmp_path / "extracted"
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)

        # GitHub ZIP 解压后有一个顶层目录：repo-branch/
        top_dirs = [d for d in extract_dir.iterdir() if d.is_dir()]
        if not top_dirs:
            raise FileNotFoundError("Empty ZIP archive")
        repo_root = top_dirs[0]

        # 找到目标 skill 目录
        skill_src = repo_root / skill_slug
        if not skill_src.exists() or not skill_src.is_dir():
            # 尝试子目录搜索
            for sub in repo_root.rglob(skill_slug):
                if sub.is_dir() and (sub / SKILL_MD_FILENAME).exists():
                    skill_src = sub
                    break
            else:
                raise FileNotFoundError(
                    f"Skill '{skill_slug}' not found in {owner_repo}@{branch}"
                )

        # 校验 SKILL.md 存在
        if not (skill_src / SKILL_MD_FILENAME).exists():
            raise FileNotFoundError(
                f"SKILL.md not found in {skill_slug}/ of {owner_repo}@{branch}"
            )

        # 复制到目标目录
        skill_dest = dest_dir / skill_slug
        if skill_dest.exists():
            shutil.rmtree(skill_dest)
        shutil.copytree(skill_src, skill_dest)

        return skill_dest


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
    hub_dir = skills_dir or _resolve_hub_skills_dir()
    hub_dir.mkdir(parents=True, exist_ok=True)

    # 本地路径安装
    local_path = Path(source)
    if local_path.exists() and local_path.is_dir():
        return _install_from_local(local_path, hub_dir=hub_dir, force=force)

    if str(source or "").strip().startswith("clawhub:"):
        skill_slug = str(source or "").strip().split(":", 1)[-1].strip() or "unknown-skill"
        return _install_from_clawhub(
            skill_slug=skill_slug,
            hub_dir=hub_dir,
            source_str=str(source),
            force=force,
        )

    # GitHub / Skillshub tap 安装
    github_info = _parse_github_source(source)
    if github_info:
        owner_repo, branch, slug = github_info
        return _install_from_github(
            owner_repo=owner_repo,
            branch=branch,
            skill_slug=slug,
            hub_dir=hub_dir,
            source_str=source,
            force=force,
        )

    return InstallResult(
        success=False,
        name="",
        version="",
        source=source,
        install_dir="",
        detail="",
        error=(
            f"不支持的来源格式：{source}。支持 github:owner/repo/slug、"
            f"skillshub:owner/repo/slug 和本地目录路径。"
            f"你可以先去 {OFFICIAL_CLAWHUB_HOME_URL} 或 {OFFICIAL_SKILLSHUB_HOME_URL} 查找技能。"
        ),
    )


def _install_from_local(
    source_dir: Path,
    *,
    hub_dir: Path,
    force: bool,
) -> InstallResult:
    """从本地目录安装 skill。"""
    skill_md = source_dir / SKILL_MD_FILENAME
    if not skill_md.exists():
        return InstallResult(
            success=False,
            name=source_dir.name,
            version="",
            source=str(source_dir),
            install_dir="",
            detail="",
            error=f"SKILL.md not found in {source_dir}",
        )

    try:
        manifest = parse_skill_md(skill_md)
    except Exception as exc:
        return InstallResult(
            success=False,
            name=source_dir.name,
            version="",
            source=str(source_dir),
            install_dir="",
            detail="",
            error=f"SKILL.md parse error: {exc}",
        )

    dest_dir = hub_dir / manifest.name
    if dest_dir.exists() and not force:
        return InstallResult(
            success=False,
            name=manifest.name,
            version=manifest.version,
            source=str(source_dir),
            install_dir=str(dest_dir),
            detail="",
            error=f"Skill already installed: {manifest.name}. Use --force to overwrite.",
        )

    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    shutil.copytree(source_dir, dest_dir)

    update_lock_entry(
        manifest.name,
        manifest.version,
        f"local:{source_dir}",
        skills_dir=hub_dir,
    )

    return InstallResult(
        success=True,
        name=manifest.name,
        version=manifest.version,
        source=str(source_dir),
        install_dir=str(dest_dir),
        detail=f"Installed {manifest.name}@{manifest.version} from local path",
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
    """从 GitHub 安装 skill。"""
    dest_dir = hub_dir / skill_slug
    if dest_dir.exists() and not force:
        # 读取已安装版本
        lock = read_lock_file(hub_dir)
        existing = lock.get(skill_slug, {})
        return InstallResult(
            success=False,
            name=skill_slug,
            version=existing.get("version", ""),
            source=source_str,
            install_dir=str(dest_dir),
            detail="",
            error=f"Skill already installed: {skill_slug}. Use --force to overwrite.",
        )

    try:
        installed_dir = _download_github_skill(
            owner_repo, branch, skill_slug, dest_dir=hub_dir,
        )
    except Exception as exc:
        return InstallResult(
            success=False,
            name=skill_slug,
            version="",
            source=source_str,
            install_dir="",
            detail="",
            error=f"Download failed: {exc}",
        )

    # 解析已安装的 SKILL.md
    try:
        manifest = parse_skill_md(installed_dir / SKILL_MD_FILENAME)
    except Exception as exc:
        # 安装成功但解析失败
        return InstallResult(
            success=True,
            name=skill_slug,
            version="unknown",
            source=source_str,
            install_dir=str(installed_dir),
            detail=f"Installed but SKILL.md parse warning: {exc}",
        )

    update_lock_entry(
        manifest.name,
        manifest.version,
        f"github:{owner_repo}/{skill_slug}@{branch}",
        skills_dir=hub_dir,
    )

    return InstallResult(
        success=True,
        name=manifest.name,
        version=manifest.version,
        source=source_str,
        install_dir=str(installed_dir),
        detail=f"Installed {manifest.name}@{manifest.version} from {owner_repo}@{branch}",
    )


def remove_skill(
    name: str,
    *,
    skills_dir: Path | None = None,
) -> RemoveResult:
    """卸载 Skill（删除目录 + 清除 lock 条目）。"""
    hub_dir = skills_dir or _resolve_hub_skills_dir()
    skill_dir = hub_dir / name

    if not skill_dir.exists():
        return RemoveResult(
            success=False,
            name=name,
            detail="",
            error=f"Skill not found: {name}",
        )

    try:
        shutil.rmtree(skill_dir)
        remove_lock_entry(name, skills_dir=hub_dir)
        return RemoveResult(
            success=True,
            name=name,
            detail=f"Removed {name} from {hub_dir}",
        )
    except Exception as exc:
        return RemoveResult(
            success=False,
            name=name,
            detail="",
            error=f"Remove failed: {exc}",
        )


def list_installed_skills(
    *,
    skills_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """列出所有已安装的 Hub Skills。"""
    hub_dir = skills_dir or _resolve_hub_skills_dir()
    lock = read_lock_file(hub_dir)
    result: list[dict[str, Any]] = []

    if not hub_dir.exists():
        return result

    for item in sorted(hub_dir.iterdir()):
        if not item.is_dir() or item.name.startswith(("_", ".")):
            continue
        skill_md = item / SKILL_MD_FILENAME
        if not skill_md.exists():
            continue

        try:
            manifest = parse_skill_md(skill_md)
            lock_entry = lock.get(manifest.name, {})
            result.append({
                "name": manifest.name,
                "version": manifest.version,
                "description": manifest.description,
                "author": manifest.author,
                "tags": manifest.tags,
                "source": lock_entry.get("source", "local"),
                "installed_at": lock_entry.get("installed_at", ""),
                "dir": str(item),
            })
        except Exception:
            result.append({
                "name": item.name,
                "version": "?",
                "description": "parse error",
                "dir": str(item),
            })

    return result


def search_remote_skills_with_status(
    query: str,
    *,
    registry_url: str | None = None,
    timeout_s: float = 10.0,
) -> SearchResponse:
    """从远程 registry 搜索可用 Skills，并在需要时回退到 ClawHub CLI。"""
    env_registry_url = os.environ.get("HOST_SKILL_HUB_REGISTRY_URL", "").strip()
    url = str(registry_url or env_registry_url).strip()
    discovery_sources = get_official_skill_hub_sources()
    registry_error: str | None = None

    if url:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Natsume-Ai-Hub/1.0"})
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.warning("Hub search: registry fetch failed (%s): %s", url, exc)
            registry_error = str(exc)
        else:
            if isinstance(data, list):
                query_lower = query.lower()
                results: list[SearchResult] = []
                for entry in data:
                    if not isinstance(entry, dict):
                        continue
                    name = str(entry.get("name") or "")
                    desc = str(entry.get("description") or "")
                    tags = list(entry.get("tags") or [])
                    searchable = f"{name} {desc} {' '.join(tags)}".lower()
                    if query_lower in searchable:
                        results.append(SearchResult(
                            name=name,
                            version=str(entry.get("version") or ""),
                            description=desc,
                            author=str(entry.get("author") or ""),
                            tags=tags,
                        ))
                return SearchResponse(results=results, registry_url=url, error=None, discovery_sources=discovery_sources)
            registry_error = "registry payload is not a list"
    else:
        registry_error = (
            f"当前还没有配置机器可读的技能 registry。"
            f"你可以先去 ClawHub（{OFFICIAL_CLAWHUB_HOME_URL}）或 Skillshub（{OFFICIAL_SKILLSHUB_HOME_URL}）查找。"
            f"安装时可使用 github:owner/repo/skill 或 {OFFICIAL_SKILLSHUB_INSTALL_EXAMPLE}。"
        )

    fallback = _search_skills_via_clawhub_cli(query)
    if fallback.results:
        return fallback
    combined_error = _normalize_cli_output(registry_error or "", fallback.error or "") or None
    return SearchResponse(
        results=[],
        registry_url=url or fallback.registry_url,
        error=combined_error,
        discovery_sources=discovery_sources,
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

