"""智能文件处理引擎（Phase XII 阶段 2）。

职责：magic bytes 后缀检测修复、批量解压（含密码尝试）、递归解压。
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_logger = logging.getLogger("Natsume.tools.file_forensics")

# ---------------------------------------------------------------------------
# Magic bytes → 真实文件类型
# ---------------------------------------------------------------------------
MAGIC_SIGNATURES: list[tuple[bytes, str, str]] = [
    # (magic_bytes, extension, description)
    (b"PK\x03\x04", ".zip", "ZIP archive"),
    (b"PK\x05\x06", ".zip", "ZIP archive (empty)"),
    (b"PK\x07\x08", ".zip", "ZIP archive (spanned)"),
    (b"Rar!\x1a\x07\x00", ".rar", "RAR archive v4"),
    (b"Rar!\x1a\x07\x01\x00", ".rar", "RAR archive v5"),
    (b"7z\xbc\xaf\x27\x1c", ".7z", "7-Zip archive"),
    (b"\x1f\x8b", ".gz", "GZIP archive"),
    (b"BZh", ".bz2", "BZIP2 archive"),
    (b"\xfd7zXZ\x00", ".xz", "XZ archive"),
    (b"%PDF", ".pdf", "PDF document"),
    (b"\x89PNG\r\n\x1a\n", ".png", "PNG image"),
    (b"\xff\xd8\xff", ".jpg", "JPEG image"),
    (b"GIF87a", ".gif", "GIF image"),
    (b"GIF89a", ".gif", "GIF image"),
    (b"RIFF", ".avi", "AVI/WAV (RIFF)"),
    (b"\x00\x00\x00\x1cftyp", ".mp4", "MP4 video"),
    (b"\x00\x00\x00\x18ftyp", ".mp4", "MP4 video"),
    (b"\x00\x00\x00\x20ftyp", ".mp4", "MP4 video"),
    (b"ID3", ".mp3", "MP3 audio"),
    (b"\xff\xfb", ".mp3", "MP3 audio"),
    (b"fLaC", ".flac", "FLAC audio"),
    (b"MZ", ".exe", "Windows executable"),
    (b"\x50\x4b\x03\x04", ".docx", "Office XML (docx/xlsx/pptx)"),
]

ARCHIVE_EXTENSIONS = frozenset({".zip", ".rar", ".7z", ".gz", ".bz2", ".xz", ".tar"})

# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class FileIdentResult:
    """文件识别结果。"""
    path: str
    current_ext: str
    detected_ext: str
    detected_type: str
    needs_rename: bool
    is_archive: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "current_ext": self.current_ext,
            "detected_ext": self.detected_ext,
            "detected_type": self.detected_type,
            "needs_rename": self.needs_rename,
            "is_archive": self.is_archive,
        }


@dataclass
class ExtractResult:
    """解压结果。"""
    source: str
    success: bool
    output_dir: str = ""
    password_used: str | None = None
    files_extracted: int = 0
    error: str = ""
    nested_archives: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": Path(self.source).name,
            "success": self.success,
            "output_dir": self.output_dir,
            "password_used": "***" if self.password_used else None,
            "files_extracted": self.files_extracted,
            "error": self.error[:200],
            "nested_archives_count": len(self.nested_archives),
        }


@dataclass
class BatchResult:
    """批量处理结果。"""
    total: int
    success: int
    failed: int
    skipped: int
    results: list[ExtractResult]
    elapsed_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "success": self.success,
            "failed": self.failed,
            "skipped": self.skipped,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


# ---------------------------------------------------------------------------
# FileForensics
# ---------------------------------------------------------------------------
class FileForensics:
    """智能文件处理引擎。

    用法::

        forensics = FileForensics()

        # 检测文件真实类型
        result = forensics.identify("suspicious_file.mp4")
        # result.detected_ext → ".rar"

        # 修复后缀
        new_path = forensics.fix_extension("suspicious_file.mp4")
        # new_path → "suspicious_file.rar"

        # 批量解压（带密码尝试）
        batch = forensics.batch_extract(
            directory="D:\\\\Downloads\\\\mods",
            passwords=["abc123", "sakura2026"],
            recursive=True,
        )
    """

    def __init__(
        self,
        *,
        seven_zip_path: str = "7z",
        max_recursive_depth: int = 3,
        max_file_size_mb: int = 4096,
    ) -> None:
        self._7z = seven_zip_path
        self._max_depth = int(max_recursive_depth)
        self._max_size = int(max_file_size_mb) * 1024 * 1024

    # ── 文件识别 ──────────────────────────────────────────────────────────

    def identify(self, file_path: str | Path) -> FileIdentResult:
        """通过 magic bytes 识别文件真实类型。"""
        fp = Path(file_path)
        current_ext = fp.suffix.lower()

        if not fp.exists() or not fp.is_file():
            return FileIdentResult(
                path=str(fp), current_ext=current_ext,
                detected_ext="", detected_type="not_found",
                needs_rename=False, is_archive=False,
            )

        # 读取前 32 bytes
        try:
            with open(fp, "rb") as f:
                header = f.read(32)
        except OSError:
            return FileIdentResult(
                path=str(fp), current_ext=current_ext,
                detected_ext="", detected_type="read_error",
                needs_rename=False, is_archive=False,
            )

        # 匹配 magic bytes
        for magic, ext, desc in MAGIC_SIGNATURES:
            if header[:len(magic)] == magic:
                needs_rename = current_ext != ext and current_ext != ext.replace(".", "")
                return FileIdentResult(
                    path=str(fp), current_ext=current_ext,
                    detected_ext=ext, detected_type=desc,
                    needs_rename=needs_rename,
                    is_archive=ext in ARCHIVE_EXTENSIONS,
                )

        return FileIdentResult(
            path=str(fp), current_ext=current_ext,
            detected_ext=current_ext, detected_type="unknown",
            needs_rename=False,
            is_archive=current_ext in ARCHIVE_EXTENSIONS,
        )

    def fix_extension(self, file_path: str | Path) -> Path | None:
        """检测并修复文件后缀。返回新路径，未修改返回 None。"""
        ident = self.identify(file_path)
        if not ident.needs_rename or not ident.detected_ext:
            return None

        fp = Path(file_path)
        new_path = fp.with_suffix(ident.detected_ext)
        # 避免覆盖已有文件
        counter = 1
        while new_path.exists():
            new_path = fp.with_name(f"{fp.stem}_{counter}{ident.detected_ext}")
            counter += 1

        try:
            fp.rename(new_path)
            _logger.info("FileForensics: 修复后缀 %s → %s", fp.name, new_path.name)
            return new_path
        except OSError as exc:
            _logger.warning("FileForensics: 重命名失败 %s: %s", fp, exc)
            return None

    # ── 解压 ──────────────────────────────────────────────────────────────

    def extract(
        self,
        file_path: str | Path,
        *,
        output_dir: str | Path | None = None,
        passwords: list[str] | None = None,
        recursive: bool = False,
        _depth: int = 0,
    ) -> ExtractResult:
        """解压单个文件。支持密码尝试和递归解压。"""
        fp = Path(file_path).resolve()
        if not fp.exists():
            return ExtractResult(source=str(fp), success=False, error="文件不存在")

        if fp.stat().st_size > self._max_size:
            return ExtractResult(source=str(fp), success=False, error=f"文件过大（>{self._max_size // 1024 // 1024}MB）")

        # 先检测/修复后缀
        ident = self.identify(fp)
        if ident.needs_rename:
            new_fp = self.fix_extension(fp)
            if new_fp:
                fp = new_fp

        # 确定输出目录
        if output_dir:
            out_dir = Path(output_dir).resolve()
        else:
            out_dir = fp.parent / fp.stem
        out_dir.mkdir(parents=True, exist_ok=True)

        # 尝试解压（无密码 + 密码列表）
        all_passwords = [None] + list(passwords or [])
        for pwd in all_passwords:
            result = self._try_extract(fp, out_dir, password=pwd)
            if result.success:
                result.password_used = pwd
                # 统计解压文件数
                result.files_extracted = sum(1 for _ in out_dir.rglob("*") if _.is_file())

                # 递归解压
                if recursive and _depth < self._max_depth:
                    nested = self._find_nested_archives(out_dir)
                    result.nested_archives = [str(n) for n in nested]
                    for nested_file in nested:
                        self.extract(
                            nested_file,
                            passwords=passwords,
                            recursive=True,
                            _depth=_depth + 1,
                        )

                return result

        return ExtractResult(
            source=str(fp), success=False,
            output_dir=str(out_dir),
            error=f"所有密码尝试失败（共 {len(all_passwords)} 个）",
        )

    def _try_extract(
        self,
        file_path: Path,
        output_dir: Path,
        *,
        password: str | None = None,
    ) -> ExtractResult:
        """用 7z 尝试解压。"""
        cmd = [self._7z, "x", str(file_path), f"-o{output_dir}", "-y", "-aoa"]
        if password:
            cmd.append(f"-p{password}")
        else:
            cmd.append("-p")  # 无密码

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=300, encoding="utf-8", errors="replace",
            )
            if proc.returncode == 0:
                return ExtractResult(
                    source=str(file_path), success=True,
                    output_dir=str(output_dir),
                )
            return ExtractResult(
                source=str(file_path), success=False,
                output_dir=str(output_dir),
                error=proc.stderr[:200] if proc.stderr else f"exit_code={proc.returncode}",
            )
        except subprocess.TimeoutExpired:
            return ExtractResult(
                source=str(file_path), success=False, error="解压超时（300s）",
            )
        except FileNotFoundError:
            return ExtractResult(
                source=str(file_path), success=False,
                error=f"7z 未找到（路径: {self._7z}）",
            )
        except Exception as exc:
            return ExtractResult(
                source=str(file_path), success=False, error=str(exc)[:200],
            )

    def _find_nested_archives(self, directory: Path) -> list[Path]:
        """在目录中查找嵌套的压缩文件。"""
        nested: list[Path] = []
        for f in directory.rglob("*"):
            if not f.is_file():
                continue
            ident = self.identify(f)
            if ident.is_archive:
                nested.append(f)
        return nested

    # ── 批量操作 ──────────────────────────────────────────────────────────

    def batch_extract(
        self,
        directory: str | Path,
        *,
        passwords: list[str] | None = None,
        recursive: bool = True,
        pattern: str = "*",
    ) -> BatchResult:
        """批量解压目录下的所有压缩文件。"""
        t0 = time.monotonic()
        dir_path = Path(directory).resolve()
        if not dir_path.is_dir():
            return BatchResult(total=0, success=0, failed=0, skipped=0, results=[])

        # 扫描所有文件（不只看后缀，也用 magic bytes 检测）
        archive_files: list[Path] = []
        for f in sorted(dir_path.glob(pattern)):
            if not f.is_file():
                continue
            ident = self.identify(f)
            if ident.is_archive:
                archive_files.append(f)

        results: list[ExtractResult] = []
        success = 0
        failed = 0

        for i, archive in enumerate(archive_files):
            _logger.info("BatchExtract [%d/%d]: %s", i + 1, len(archive_files), archive.name)
            result = self.extract(archive, passwords=passwords, recursive=recursive)
            results.append(result)
            if result.success:
                success += 1
            else:
                failed += 1

        elapsed = (time.monotonic() - t0) * 1000
        _logger.info(
            "BatchExtract 完成: total=%d success=%d failed=%d elapsed=%.1fs",
            len(archive_files), success, failed, elapsed / 1000,
        )

        return BatchResult(
            total=len(archive_files),
            success=success,
            failed=failed,
            skipped=0,
            results=results,
            elapsed_ms=elapsed,
        )

    def scan_and_fix_extensions(
        self, directory: str | Path, *, pattern: str = "*",
    ) -> list[FileIdentResult]:
        """扫描目录，检测并修复所有错误后缀。"""
        dir_path = Path(directory).resolve()
        results: list[FileIdentResult] = []
        for f in sorted(dir_path.glob(pattern)):
            if not f.is_file():
                continue
            ident = self.identify(f)
            if ident.needs_rename:
                results.append(ident)
                self.fix_extension(f)
        _logger.info("ScanFixExt: 扫描 %s，修复 %d 个文件后缀", dir_path, len(results))
        return results
