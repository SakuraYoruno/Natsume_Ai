"""Physical quarantine for verified external Skill/Provider bundles.

Quarantine is a byte-copy boundary, not an activation mechanism.  Bundle code
is never imported or executed here and an existing digest directory is never
overwritten.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import os
import re
import shutil
import stat
import sys
import threading
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Iterator, Mapping

from host_brain.skills.supply_chain import (
    BundleVerificationDecision,
    SkillSupplyChainVerifier,
    SupplyChainActivationMode,
)


_DIGEST_RE = re.compile(r"\Asha256:([0-9a-f]{64})\Z")
_PROCESS_LOCK_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.Lock] = {}


@dataclass(frozen=True)
class QuarantineDecision:
    quarantined: bool
    idempotent: bool
    activation_eligible: bool
    reason_code: str
    bundle_name: str | None
    bundle_version: str | None
    bundle_digest: str | None
    quarantine_path: str | None
    audit: Mapping[str, Any]


@dataclass(frozen=True)
class _CopyStats:
    file_count: int
    directory_count: int
    total_bytes: int


class _QuarantineRejected(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class SkillBundleQuarantine:
    """Copy verified bundle bytes into a content-addressed quarantine root."""

    def __init__(
        self,
        *,
        verifier: SkillSupplyChainVerifier,
        quarantine_root: str | os.PathLike[str],
        max_files: int = 4096,
        max_directories: int = 4096,
        max_total_bytes: int = 512 * 1024 * 1024,
        max_file_bytes: int = 128 * 1024 * 1024,
    ) -> None:
        if max_files < 1 or max_directories < 1 or max_total_bytes < 1 or max_file_bytes < 1:
            raise ValueError("SUPPLY_CHAIN_QUARANTINE_LIMIT_INVALID")
        self._verifier = verifier
        self._root_config = Path(quarantine_root)
        self._max_files = int(max_files)
        self._max_directories = int(max_directories)
        self._max_total_bytes = int(max_total_bytes)
        self._max_file_bytes = int(max_file_bytes)

    def quarantine_bundle(self, source_dir: str | os.PathLike[str]) -> QuarantineDecision:
        # This branch must remain before Path(source_dir), lstat, resolve, or any
        # other source operation.
        if self._verifier.mode is SupplyChainActivationMode.BUILTINS_ONLY:
            reason = (
                "SUPPLY_CHAIN_QUARANTINE_POLICY_INVALID"
                if not self._verifier.configuration_valid
                else "SUPPLY_CHAIN_EXTERNAL_BUNDLES_DISABLED"
            )
            return self._reject(reason)

        try:
            initial = self._verify_bundle(source_dir)
        except _QuarantineRejected as exc:
            return self._reject(exc.reason_code)
        if not initial.verified or not initial.bundle_digest:
            return self._reject_from_verification(initial)
        digest_match = _DIGEST_RE.fullmatch(initial.bundle_digest)
        if digest_match is None:
            return self._reject("SUPPLY_CHAIN_QUARANTINE_DIGEST_INVALID", verification=initial)

        temp: Path | None = None
        try:
            root = self._prepare_root()
            source = Path(source_dir).resolve(strict=True)
            if _same_or_nested(source, root) or _same_or_nested(root, source):
                raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_PATH_OVERLAP")
            target_id = f"sha256-{digest_match.group(1)}"
            target = root / target_id

            with _digest_lock(root, target_id):
                if _path_exists_no_follow(target):
                    existing = self._verify_bundle(target)
                    if _same_verified_bundle(initial, existing):
                        return self._success(
                            initial,
                            target=target,
                            idempotent=True,
                            stats=None,
                        )
                    return self._reject(
                        "SUPPLY_CHAIN_QUARANTINE_EXISTING_CONFLICT",
                        verification=initial,
                        target_id=target_id,
                    )

                temp = root / f".{target_id}.{uuid.uuid4().hex}.tmp"
                os.mkdir(temp, 0o700)
                stats = self._copy_tree(source=source, destination=temp)
                _fsync_directory_tree(temp)

                copied = self._verify_bundle(temp)
                if not _same_verified_bundle(initial, copied):
                    raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_REVERIFY_MISMATCH")
                if _path_exists_no_follow(target):
                    existing = self._verify_bundle(target)
                    if _same_verified_bundle(initial, existing):
                        _remove_temp_tree(temp, root=root)
                        temp = None
                        return self._success(initial, target=target, idempotent=True, stats=stats)
                    raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_EXISTING_CONFLICT")

                # Publication must be atomic *and* no-replace.  A prior lstat
                # cannot defend against a non-cooperating writer racing an
                # empty directory into the content-addressed target.
                try:
                    _publish_directory_noreplace(temp, target)
                except FileExistsError:
                    existing = self._verify_bundle(target)
                    if _same_verified_bundle(initial, existing):
                        _remove_temp_tree(temp, root=root)
                        temp = None
                        return self._success(
                            initial,
                            target=target,
                            idempotent=True,
                            stats=stats,
                        )
                    raise _QuarantineRejected(
                        "SUPPLY_CHAIN_QUARANTINE_EXISTING_CONFLICT"
                    )
                temp = None
                _fsync_directory(root)
                return self._success(initial, target=target, idempotent=False, stats=stats)
        except _QuarantineRejected as exc:
            return self._reject(exc.reason_code, verification=initial)
        except (OSError, ValueError, TypeError):
            return self._reject("SUPPLY_CHAIN_QUARANTINE_IO_FAILED", verification=initial)
        finally:
            if temp is not None and _path_exists_no_follow(temp):
                with contextlib.suppress(OSError, _QuarantineRejected):
                    root_for_cleanup = self._root_config.resolve(strict=True)
                    _remove_temp_tree(temp, root=root_for_cleanup)

    def _verify_bundle(self, source: object) -> BundleVerificationDecision:
        try:
            return self._verifier.verify_bundle(source)
        except Exception as exc:
            raise _QuarantineRejected("SUPPLY_CHAIN_VERIFIER_FAILED") from exc

    def _prepare_root(self) -> Path:
        try:
            self._root_config.mkdir(parents=True, mode=0o700, exist_ok=True)
            metadata = self._root_config.lstat()
        except OSError as exc:
            raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_ROOT_INVALID") from exc
        if self._root_config.is_symlink() or _is_reparse_point(metadata):
            raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_ROOT_INVALID")
        if not stat.S_ISDIR(metadata.st_mode):
            raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_ROOT_INVALID")
        root = self._root_config.resolve(strict=True)
        _fsync_directory(root)
        return root

    def _copy_tree(self, *, source: Path, destination: Path) -> _CopyStats:
        root_metadata = source.lstat()
        if source.is_symlink() or _is_reparse_point(root_metadata) or not stat.S_ISDIR(root_metadata.st_mode):
            raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_SOURCE_INVALID")

        file_count = 0
        directory_count = 0
        total_bytes = 0
        stack: list[Path] = [source]
        while stack:
            source_dir = stack.pop()
            try:
                iterator = os.scandir(source_dir)
            except OSError as exc:
                raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_SOURCE_UNREADABLE") from exc
            with iterator:
                for entry in iterator:
                    source_path = Path(entry.path)
                    relative = source_path.relative_to(source).as_posix()
                    _validate_relative_path(relative)
                    try:
                        metadata = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_SOURCE_UNREADABLE") from exc
                    if entry.is_symlink() or _is_reparse_point(metadata):
                        raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_SYMLINK_FORBIDDEN")
                    destination_path = destination / Path(*PurePosixPath(relative).parts)
                    if stat.S_ISDIR(metadata.st_mode):
                        directory_count += 1
                        if directory_count > self._max_directories:
                            raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_DIRECTORY_LIMIT_EXCEEDED")
                        os.mkdir(destination_path, 0o700)
                        stack.append(source_path)
                        continue
                    if not stat.S_ISREG(metadata.st_mode):
                        raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_SPECIAL_FILE_FORBIDDEN")
                    file_count += 1
                    if file_count > self._max_files:
                        raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_FILE_LIMIT_EXCEEDED")
                    if metadata.st_size < 0 or metadata.st_size > self._max_file_bytes:
                        raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_FILE_SIZE_LIMIT_EXCEEDED")
                    total_bytes += metadata.st_size
                    if total_bytes > self._max_total_bytes:
                        raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_TOTAL_SIZE_LIMIT_EXCEEDED")
                    _copy_regular_file(
                        source_path,
                        destination_path,
                        before=metadata,
                        maximum=self._max_file_bytes,
                    )
        return _CopyStats(
            file_count=file_count,
            directory_count=directory_count,
            total_bytes=total_bytes,
        )

    def _success(
        self,
        verification: BundleVerificationDecision,
        *,
        target: Path,
        idempotent: bool,
        stats: _CopyStats | None,
    ) -> QuarantineDecision:
        reason = (
            "SUPPLY_CHAIN_QUARANTINE_ALREADY_PRESENT"
            if idempotent
            else "SUPPLY_CHAIN_QUARANTINED"
        )
        target_id = target.name
        audit: dict[str, Any] = {
            "event": "skill_supply_chain_quarantine",
            "reason_code": reason,
            "bundle_name": verification.bundle_name,
            "bundle_version": verification.bundle_version,
            "bundle_digest": verification.bundle_digest,
            "target_id": target_id,
            "idempotent": idempotent,
            "activation_eligible": verification.activation_eligible,
        }
        if stats is not None:
            audit.update(
                file_count=stats.file_count,
                directory_count=stats.directory_count,
                total_bytes=stats.total_bytes,
            )
        return QuarantineDecision(
            quarantined=True,
            idempotent=idempotent,
            activation_eligible=verification.activation_eligible,
            reason_code=reason,
            bundle_name=verification.bundle_name,
            bundle_version=verification.bundle_version,
            bundle_digest=verification.bundle_digest,
            quarantine_path=str(target),
            audit=MappingProxyType(audit),
        )

    def _reject_from_verification(self, verification: BundleVerificationDecision) -> QuarantineDecision:
        return self._reject(verification.reason_code, verification=verification)

    def _reject(
        self,
        reason_code: str,
        *,
        verification: BundleVerificationDecision | None = None,
        target_id: str | None = None,
    ) -> QuarantineDecision:
        audit: dict[str, Any] = {
            "event": "skill_supply_chain_quarantine",
            "reason_code": reason_code,
            "bundle_name": verification.bundle_name if verification else None,
            "bundle_version": verification.bundle_version if verification else None,
            "bundle_digest": verification.bundle_digest if verification else None,
        }
        if target_id is not None:
            audit["target_id"] = target_id
        return QuarantineDecision(
            quarantined=False,
            idempotent=False,
            activation_eligible=False,
            reason_code=reason_code,
            bundle_name=verification.bundle_name if verification else None,
            bundle_version=verification.bundle_version if verification else None,
            bundle_digest=verification.bundle_digest if verification else None,
            quarantine_path=None,
            audit=MappingProxyType(audit),
        )


def _copy_regular_file(source: Path, destination: Path, *, before: os.stat_result, maximum: int) -> None:
    source_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    destination_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    source_fd: int | None = None
    destination_fd: int | None = None
    try:
        source_fd = os.open(source, source_flags)
        opened = os.fstat(source_fd)
        path_state = source.lstat()
        if (
            source.is_symlink()
            or _is_reparse_point(path_state)
            or not stat.S_ISREG(opened.st_mode)
            or not _same_file_state(before, opened)
            or not _same_file_state(opened, path_state)
            or opened.st_size > maximum
        ):
            raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_SOURCE_CHANGED")
        destination_fd = os.open(destination, destination_flags, 0o600)
        remaining = opened.st_size
        while remaining:
            chunk = os.read(source_fd, min(1024 * 1024, remaining))
            if not chunk:
                raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_SOURCE_CHANGED")
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:
                    raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_IO_FAILED")
                view = view[written:]
            remaining -= len(chunk)
        if os.read(source_fd, 1):
            raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_SOURCE_CHANGED")
        after = os.fstat(source_fd)
        final_path_state = source.lstat()
        if (
            source.is_symlink()
            or _is_reparse_point(final_path_state)
            or not _same_file_state(opened, after)
            or not _same_file_state(after, final_path_state)
        ):
            raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_SOURCE_CHANGED")
        os.fsync(destination_fd)
    except _QuarantineRejected:
        raise
    except OSError as exc:
        raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_IO_FAILED") from exc
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        if source_fd is not None:
            os.close(source_fd)


@contextlib.contextmanager
def _digest_lock(root: Path, target_id: str) -> Iterator[None]:
    with _PROCESS_LOCK_GUARD:
        process_lock = _PROCESS_LOCKS.setdefault(str(root / target_id), threading.Lock())
    with process_lock:
        lock_root = root / ".locks"
        lock_root.mkdir(mode=0o700, exist_ok=True)
        lock_path = lock_root / f"{target_id}.lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
                os.fsync(descriptor)
            _lock_descriptor(descriptor)
            try:
                yield
            finally:
                _unlock_descriptor(descriptor)
        finally:
            os.close(descriptor)


def _lock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX)


def _unlock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _same_verified_bundle(
    expected: BundleVerificationDecision,
    actual: BundleVerificationDecision,
) -> bool:
    if not actual.verified:
        return False
    return (
        expected.bundle_name == actual.bundle_name
        and expected.bundle_version == actual.bundle_version
        and expected.bundle_digest == actual.bundle_digest
        and expected.audit.get("manifest_digest") == actual.audit.get("manifest_digest")
    )


def _path_exists_no_follow(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def _publish_directory_noreplace(source: Path, target: Path) -> None:
    """Atomically publish a directory without ever replacing ``target``.

    Unsupported platforms fail closed rather than falling back to POSIX
    ``rename(2)``, whose replacement semantics are unsafe for this boundary.
    """

    if os.name == "nt":
        # MoveFileEx without MOVEFILE_REPLACE_EXISTING is what os.rename uses
        # on Windows; it atomically fails when the destination exists.
        os.rename(source, target)
        return

    old = os.fsencode(source)
    new = os.fsencode(target)
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise _QuarantineRejected(
                "SUPPLY_CHAIN_QUARANTINE_NOREPLACE_UNAVAILABLE"
            )
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(-100, old, -100, new, 1)  # AT_FDCWD, RENAME_NOREPLACE
    elif sys.platform == "darwin":
        renamex_np = getattr(libc, "renamex_np", None)
        if renamex_np is None:
            raise _QuarantineRejected(
                "SUPPLY_CHAIN_QUARANTINE_NOREPLACE_UNAVAILABLE"
            )
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(old, new, 0x00000004)  # RENAME_EXCL
    else:
        raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_NOREPLACE_UNAVAILABLE")

    if result == 0:
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(error, os.strerror(error), target)
    if error in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
        raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_NOREPLACE_UNAVAILABLE")
    raise OSError(error, os.strerror(error), target)


def _same_or_nested(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_relative_path(value: str) -> None:
    if not value or len(value.encode("utf-8")) > 1024:
        raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_PATH_INVALID")
    if value != unicodedata.normalize("NFC", value):
        raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_PATH_INVALID")
    if "\\" in value or "\x00" in value or any(ord(character) < 0x20 for character in value):
        raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_PATH_INVALID")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or ":" in path.parts[0]
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_PATH_INVALID")


def _same_file_state(left: os.stat_result, right: os.stat_result) -> bool:
    identity_matches = True
    if left.st_dev and right.st_dev:
        identity_matches = left.st_dev == right.st_dev
    if left.st_ino and right.st_ino:
        identity_matches = identity_matches and left.st_ino == right.st_ino
    return identity_matches and (
        left.st_mode,
        left.st_size,
        left.st_mtime_ns,
    ) == (
        right.st_mode,
        right.st_size,
        right.st_mtime_ns,
    )


def _is_reparse_point(metadata: os.stat_result) -> bool:
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0)
    return bool(flag and attributes & flag)


def _fsync_directory_tree(root: Path) -> None:
    directories = [path for path in root.rglob("*") if path.is_dir()]
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        _fsync_directory(directory)
    _fsync_directory(root)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        # Windows has no stdlib equivalent of opening a directory with
        # FILE_FLAG_BACKUP_SEMANTICS.  File fsync remains mandatory; directory
        # fsync is performed on platforms that expose it.
        if os.name == "nt":
            return
        raise
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_temp_tree(temp: Path, *, root: Path) -> None:
    try:
        temp.relative_to(root)
    except ValueError as exc:
        raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_CLEANUP_REFUSED") from exc
    if not temp.name.startswith(".sha256-") or not temp.name.endswith(".tmp"):
        raise _QuarantineRejected("SUPPLY_CHAIN_QUARANTINE_CLEANUP_REFUSED")
    # The tree was created by this operation with a random unguessable name.
    # shutil.rmtree does not follow a root symlink and unlinks nested symlinks;
    # it never operates outside the verified quarantine root here.
    if temp.is_symlink():
        temp.unlink()
    else:
        shutil.rmtree(temp)
    _fsync_directory(root)


__all__ = ["QuarantineDecision", "SkillBundleQuarantine"]
