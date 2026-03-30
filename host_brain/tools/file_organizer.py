"""高级文件整理器（Phase XII 阶段 4）。

职责：按类型分类、去重、归档、清理空文件夹。
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_logger = logging.getLogger("Natsume.tools.file_organizer")

# ---------------------------------------------------------------------------
# 文件类型分类规则
# ---------------------------------------------------------------------------
FILE_CATEGORIES: dict[str, list[str]] = {
    "视频": [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".rmvb"],
    "音频": [".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".wma", ".ape"],
    "图片": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".tiff", ".psd"],
    "文档": [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".csv", ".rtf"],
    "压缩包": [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"],
    "程序": [".exe", ".msi", ".bat", ".cmd", ".ps1", ".sh", ".apk", ".dmg", ".iso"],
    "代码": [".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".go", ".rs", ".html", ".css", ".json", ".xml", ".yaml", ".yml"],
    "字体": [".ttf", ".otf", ".woff", ".woff2", ".eot"],
    "模型": [".pth", ".pt", ".onnx", ".safetensors", ".ckpt", ".bin"],
}

# 构建反向索引
_EXT_TO_CATEGORY: dict[str, str] = {}
for _cat, _exts in FILE_CATEGORIES.items():
    for _ext in _exts:
        _EXT_TO_CATEGORY[_ext.lower()] = _cat


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class FileInfo:
    """文件信息。"""
    path: str
    name: str
    ext: str
    size_bytes: int
    category: str
    hash_md5: str = ""
    modified_ts: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ext": self.ext,
            "size_mb": round(self.size_bytes / 1024 / 1024, 2),
            "category": self.category,
        }


@dataclass
class DuplicateGroup:
    """重复文件组。"""
    hash_md5: str
    size_bytes: int
    files: list[str]  # 文件路径列表

    @property
    def wasted_bytes(self) -> int:
        return self.size_bytes * (len(self.files) - 1)


@dataclass
class OrganizeResult:
    """整理结果。"""
    total_files: int
    moved_files: int
    duplicates_found: int
    duplicates_size_mb: float
    empty_dirs_cleaned: int
    categories: dict[str, int]
    elapsed_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_files": self.total_files,
            "moved_files": self.moved_files,
            "duplicates_found": self.duplicates_found,
            "duplicates_size_mb": round(self.duplicates_size_mb, 2),
            "empty_dirs_cleaned": self.empty_dirs_cleaned,
            "categories": dict(self.categories),
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


# ---------------------------------------------------------------------------
# FileOrganizer
# ---------------------------------------------------------------------------
class FileOrganizer:
    """高级文件整理器。

    用法::

        organizer = FileOrganizer()

        # 扫描分析（不移动）
        report = organizer.scan("D:\\\\Downloads")

        # 按类型分类整理
        result = organizer.organize_by_type("D:\\\\Downloads")

        # 查找重复文件
        dupes = organizer.find_duplicates("D:\\\\Downloads")

        # 清理空文件夹
        cleaned = organizer.clean_empty_dirs("D:\\\\Downloads")
    """

    def __init__(
        self,
        *,
        custom_categories: dict[str, list[str]] | None = None,
        hash_chunk_size: int = 8192,
    ) -> None:
        self._categories = dict(custom_categories or FILE_CATEGORIES)
        self._chunk_size = int(hash_chunk_size)
        # 重建反向索引
        self._ext_map: dict[str, str] = {}
        for cat, exts in self._categories.items():
            for ext in exts:
                self._ext_map[ext.lower()] = cat

    def scan(self, directory: str | Path) -> list[FileInfo]:
        """扫描目录，返回所有文件信息。"""
        dir_path = Path(directory).resolve()
        files: list[FileInfo] = []
        for f in dir_path.rglob("*"):
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            category = self._ext_map.get(ext, "其他")
            try:
                stat = f.stat()
                files.append(FileInfo(
                    path=str(f),
                    name=f.name,
                    ext=ext,
                    size_bytes=stat.st_size,
                    category=category,
                    modified_ts=stat.st_mtime,
                ))
            except OSError:
                continue
        return files

    def organize_by_type(
        self,
        directory: str | Path,
        *,
        dry_run: bool = False,
    ) -> OrganizeResult:
        """按文件类型分类整理。"""
        t0 = time.monotonic()
        dir_path = Path(directory).resolve()
        files = self.scan(dir_path)
        categories: dict[str, int] = defaultdict(int)
        moved = 0

        for fi in files:
            # 跳过已在分类目录中的文件
            if fi.category != "其他" and Path(fi.path).parent.name == fi.category:
                categories[fi.category] += 1
                continue

            target_dir = dir_path / fi.category
            target_path = target_dir / Path(fi.path).name

            if not dry_run:
                target_dir.mkdir(exist_ok=True)
                # 避免覆盖
                if target_path.exists():
                    stem = Path(fi.path).stem
                    suffix = Path(fi.path).suffix
                    counter = 1
                    while target_path.exists():
                        target_path = target_dir / f"{stem}_{counter}{suffix}"
                        counter += 1
                try:
                    shutil.move(fi.path, str(target_path))
                    moved += 1
                except OSError as exc:
                    _logger.warning("移动失败 %s: %s", fi.name, exc)

            categories[fi.category] += 1

        elapsed = (time.monotonic() - t0) * 1000
        return OrganizeResult(
            total_files=len(files),
            moved_files=moved,
            duplicates_found=0,
            duplicates_size_mb=0.0,
            empty_dirs_cleaned=0,
            categories=dict(categories),
            elapsed_ms=elapsed,
        )

    def find_duplicates(
        self,
        directory: str | Path,
        *,
        min_size_bytes: int = 1024,
    ) -> list[DuplicateGroup]:
        """查找重复文件（基于 MD5 hash）。"""
        dir_path = Path(directory).resolve()
        # 第一步：按大小分组
        size_groups: dict[int, list[Path]] = defaultdict(list)
        for f in dir_path.rglob("*"):
            if not f.is_file():
                continue
            try:
                size = f.stat().st_size
                if size >= min_size_bytes:
                    size_groups[size].append(f)
            except OSError:
                continue

        # 第二步：同大小文件做 hash
        duplicates: list[DuplicateGroup] = []
        for size, paths in size_groups.items():
            if len(paths) < 2:
                continue
            hash_groups: dict[str, list[str]] = defaultdict(list)
            for p in paths:
                h = self._file_hash(p)
                if h:
                    hash_groups[h].append(str(p))
            for h, file_list in hash_groups.items():
                if len(file_list) >= 2:
                    duplicates.append(DuplicateGroup(
                        hash_md5=h, size_bytes=size, files=file_list,
                    ))

        return sorted(duplicates, key=lambda d: -d.wasted_bytes)

    def clean_empty_dirs(self, directory: str | Path) -> int:
        """清理空文件夹（自底向上）。"""
        dir_path = Path(directory).resolve()
        cleaned = 0
        # 自底向上遍历
        for dirpath in sorted(dir_path.rglob("*"), reverse=True):
            if not dirpath.is_dir():
                continue
            try:
                if not any(dirpath.iterdir()):
                    dirpath.rmdir()
                    cleaned += 1
            except OSError:
                continue
        return cleaned

    def organize_by_date(
        self,
        directory: str | Path,
        *,
        date_format: str = "%Y-%m",
        dry_run: bool = False,
    ) -> OrganizeResult:
        """按修改日期归档。"""
        t0 = time.monotonic()
        dir_path = Path(directory).resolve()
        files = self.scan(dir_path)
        moved = 0

        for fi in files:
            date_str = time.strftime(date_format, time.localtime(fi.modified_ts))
            target_dir = dir_path / date_str
            target_path = target_dir / Path(fi.path).name

            if not dry_run:
                target_dir.mkdir(exist_ok=True)
                if not target_path.exists():
                    try:
                        shutil.move(fi.path, str(target_path))
                        moved += 1
                    except OSError:
                        pass

        elapsed = (time.monotonic() - t0) * 1000
        return OrganizeResult(
            total_files=len(files), moved_files=moved,
            duplicates_found=0, duplicates_size_mb=0.0,
            empty_dirs_cleaned=0, categories={},
            elapsed_ms=elapsed,
        )

    def _file_hash(self, path: Path) -> str | None:
        """计算文件 MD5 hash。"""
        try:
            h = hashlib.md5()
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(self._chunk_size)
                    if not chunk:
                        break
                    h.update(chunk)
            return h.hexdigest()
        except OSError:
            return None
