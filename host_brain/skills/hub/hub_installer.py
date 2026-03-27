"""Skills Hub 远程安装器。

支持从 GitHub 仓库安装 Skill：
  natsume skills install github:koinosakura/natsume-skills-hub/weather-query
  natsume skills install https://github.com/.../weather-query
  natsume skills install ./local/path/to/skill-dir

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
import shutil
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


def _parse_github_source(source: str) -> tuple[str, str, str] | None:
    """解析 GitHub 来源字符串。

    支持格式：
    - github:owner/repo/skill-slug
    - github:owner/repo/skill-slug@branch
    - https://github.com/owner/repo/tree/branch/skill-slug

    Returns:
        (owner/repo, branch, skill_slug) 或 None
    """
    # github:owner/repo/slug[@branch]
    if source.startswith("github:"):
        rest = source[len("github:"):]
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
    """
    hub_dir = skills_dir or _resolve_hub_skills_dir()
    hub_dir.mkdir(parents=True, exist_ok=True)

    # 本地路径安装
    local_path = Path(source)
    if local_path.exists() and local_path.is_dir():
        return _install_from_local(local_path, hub_dir=hub_dir, force=force)

    # GitHub 安装
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
        error=f"Unsupported source format: {source}. "
              f"Supported: github:owner/repo/slug, local directory path",
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


def search_remote_skills(
    query: str,
    *,
    registry_url: str | None = None,
    timeout_s: float = 10.0,
) -> list[SearchResult]:
    """从远程 GitHub Hub 搜索可用 Skills。

    远程仓库约定：根目录有 registry.json：
    [
      {"name": "weather-query", "version": "1.0.0", "description": "...", "author": "...", "tags": ["天气"]},
      ...
    ]
    """
    url = registry_url or os.environ.get(
        "HOST_SKILL_HUB_REGISTRY_URL",
        "https://raw.githubusercontent.com/koinosakura/natsume-skills-hub/main/registry.json",
    )

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Natsume-Ai-Hub/1.0"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("Hub search: registry fetch failed (%s): %s", url, exc)
        return []

    if not isinstance(data, list):
        return []

    query_lower = query.lower()
    results: list[SearchResult] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "")
        desc = str(entry.get("description") or "")
        tags = list(entry.get("tags") or [])
        # 简易匹配：名称、描述、标签中包含查询词
        searchable = f"{name} {desc} {' '.join(tags)}".lower()
        if query_lower in searchable:
            results.append(SearchResult(
                name=name,
                version=str(entry.get("version") or ""),
                description=desc,
                author=str(entry.get("author") or ""),
                tags=tags,
            ))

    return results
