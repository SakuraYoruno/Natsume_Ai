"""Frozen readonly corpus loader for the D20 Research shadow experiment.

This adapter maps a manifest-owned source identifier to one repository-relative
file and verifies the file boundary before and after every read.  It has no
network client, write capability, Host route, TaskGraph integration or
production authority.  In particular, it is *not* a filesystem/tenant
sandbox: the repository and the manifest are trusted experiment inputs.

The before/open/after checks detect ordinary drift in a stable trusted input;
they are not an atomic defense against hostile concurrent replacement on
platforms that cannot open with a no-follow guarantee.  A later experiment
runner must bind a planning snapshot to this manifest exactly and must not
treat this loader alone as a source-independence proof.

Only governed source metadata is returned in :meth:`ResearchCorpusDocument.to_public_receipt`.
The private repository root and manifest locator are never part of that
receipt, so a durable D20 result can cite source provenance without leaking a
host path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any
import unicodedata


RESEARCH_BENCHMARK_CORPUS_MODE = "READONLY_FROZEN_CORPUS_SHADOW_ONLY"
"""The only supported mode; this loader cannot influence production work."""

_MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
_SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GOVERNED_REF_RE = re.compile(
    r"^[a-z][a-z0-9+.-]{0,63}://[a-z0-9][a-z0-9._/-]{0,1018}$"
)
_AUTHORITY_TIERS = frozenset({"primary", "independent", "secondary"})


class ResearchBenchmarkCorpusError(ValueError):
    """Raised when a frozen D20 corpus cannot be represented or read safely."""


@dataclass(frozen=True, slots=True)
class ResearchCorpusManifestEntry:
    """Private path mapping plus public provenance for one frozen source.

    ``relative_path`` is intentionally absent from public receipts.  It is
    accepted only as canonical POSIX, repository-relative syntax; callers
    cannot use it to name an arbitrary host file.
    """

    source_id: str
    source_ref: str
    relative_path: str
    content_digest: str
    byte_count: int
    authority_tier: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _source_id(self.source_id))
        object.__setattr__(self, "source_ref", _governed_ref(self.source_ref))
        object.__setattr__(self, "relative_path", _repository_relative_path(self.relative_path))
        object.__setattr__(self, "content_digest", _sha256(self.content_digest))
        object.__setattr__(self, "byte_count", _byte_count(self.byte_count))
        object.__setattr__(self, "authority_tier", _authority_tier(self.authority_tier))

    def to_public_receipt(self) -> dict[str, Any]:
        """Return auditable provenance without a filesystem locator or bytes."""

        return {
            "source_id": self.source_id,
            "source_ref": self.source_ref,
            "content_digest": self.content_digest,
            "byte_count": self.byte_count,
            "authority_tier": self.authority_tier,
        }


@dataclass(frozen=True, slots=True)
class FrozenResearchCorpusManifest:
    """Canonical, immutable source mapping for one D20 readonly corpus."""

    corpus_ref: str
    entries: Sequence[ResearchCorpusManifestEntry]

    def __post_init__(self) -> None:
        corpus_ref = _governed_ref(self.corpus_ref)
        entries = tuple(self.entries or ())
        if not entries or len(entries) > 64:
            raise ResearchBenchmarkCorpusError("corpus manifest must contain 1 through 64 entries")
        if not all(isinstance(entry, ResearchCorpusManifestEntry) for entry in entries):
            raise ResearchBenchmarkCorpusError("corpus manifest entries must be ResearchCorpusManifestEntry")
        ordered = tuple(sorted(entries, key=lambda entry: entry.source_id))
        if len({entry.source_id for entry in ordered}) != len(ordered):
            raise ResearchBenchmarkCorpusError("corpus manifest source_id values must be unique")
        if len({entry.source_ref for entry in ordered}) != len(ordered):
            raise ResearchBenchmarkCorpusError("corpus manifest source_ref values must be unique")
        if len({entry.relative_path.casefold() for entry in ordered}) != len(ordered):
            raise ResearchBenchmarkCorpusError(
                "corpus manifest relative_path values must be unique across case aliases"
            )
        object.__setattr__(self, "corpus_ref", corpus_ref)
        object.__setattr__(self, "entries", ordered)

    def entry_for(self, source_id: str) -> ResearchCorpusManifestEntry:
        """Resolve a listed source ID without revealing any host path on error."""

        identifier = _source_id(source_id)
        for entry in self.entries:
            if entry.source_id == identifier:
                return entry
        raise ResearchBenchmarkCorpusError("source is not listed in the frozen corpus manifest")

    @property
    def manifest_digest(self) -> str:
        """Digest all public provenance fields without exposing local paths."""

        body = json.dumps(
            self.identity_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(body).hexdigest()

    def identity_dict(self) -> dict[str, Any]:
        return {
            "mode": RESEARCH_BENCHMARK_CORPUS_MODE,
            "corpus_ref": self.corpus_ref,
            "sources": [entry.to_public_receipt() for entry in self.entries],
        }

    def to_public_receipt(self) -> dict[str, Any]:
        """Return the provenance-only manifest projection for durable evidence."""

        return {"manifest_digest": self.manifest_digest, **self.identity_dict()}


@dataclass(frozen=True, slots=True)
class ResearchCorpusDocument:
    """One digest-verified source body returned only to a trusted experiment adapter."""

    source_id: str
    source_ref: str
    content_digest: str
    byte_count: int
    authority_tier: str
    content: bytes

    def __post_init__(self) -> None:
        source_id = _source_id(self.source_id)
        source_ref = _governed_ref(self.source_ref)
        content_digest = _sha256(self.content_digest)
        byte_count = _byte_count(self.byte_count)
        authority_tier = _authority_tier(self.authority_tier)
        if not isinstance(self.content, bytes):
            raise ResearchBenchmarkCorpusError("research corpus document content must be bytes")
        if len(self.content) != byte_count:
            raise ResearchBenchmarkCorpusError("research corpus document byte_count does not match content")
        if hashlib.sha256(self.content).hexdigest() != content_digest:
            raise ResearchBenchmarkCorpusError("research corpus document digest does not match content")
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "source_ref", source_ref)
        object.__setattr__(self, "content_digest", content_digest)
        object.__setattr__(self, "byte_count", byte_count)
        object.__setattr__(self, "authority_tier", authority_tier)

    def to_public_receipt(self) -> dict[str, Any]:
        """Return provenance metadata while keeping bytes and host paths private."""

        return {
            "source_id": self.source_id,
            "source_ref": self.source_ref,
            "content_digest": self.content_digest,
            "byte_count": self.byte_count,
            "authority_tier": self.authority_tier,
        }


class FrozenResearchCorpusLoader:
    """Read only manifest-listed, regular files below one trusted repository root.

    The loader detects absolute/traversal paths, stable symlink or reparse-point
    components, special files, mapping ambiguity, and content or metadata
    drift.  It validates the configured path before opening it, checks the
    opened descriptor, then validates the same path again after the read.  It
    is a trusted-input integrity check, not a hostile concurrent-TOCTOU barrier.
    """

    def __init__(self, *, repository_root: Path, manifest: FrozenResearchCorpusManifest) -> None:
        if not isinstance(manifest, FrozenResearchCorpusManifest):
            raise TypeError("manifest must be FrozenResearchCorpusManifest")
        self._repository_root = _repository_root(repository_root)
        self._manifest = manifest

    @property
    def corpus_ref(self) -> str:
        """Return the opaque corpus provenance reference, never a local path."""

        return self._manifest.corpus_ref

    def public_manifest_receipt(self) -> dict[str, Any]:
        """Return a provenance projection with no local locators."""

        return self._manifest.to_public_receipt()

    def load(self, source_id: str) -> ResearchCorpusDocument:
        """Load one listed source after a before/after immutable-file check."""

        entry = self._manifest.entry_for(source_id)
        content = self._read_verified_entry(entry)
        return ResearchCorpusDocument(
            source_id=entry.source_id,
            source_ref=entry.source_ref,
            content_digest=entry.content_digest,
            byte_count=entry.byte_count,
            authority_tier=entry.authority_tier,
            content=content,
        )

    def load_all(self) -> tuple[ResearchCorpusDocument, ...]:
        """Load the manifest's stable source-ID order using the same checks."""

        return tuple(self.load(entry.source_id) for entry in self._manifest.entries)

    def _read_verified_entry(self, entry: ResearchCorpusManifestEntry) -> bytes:
        target, before = self._safe_existing_regular_file(entry)
        descriptor: int | None = None
        try:
            descriptor = os.open(
                target,
                os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            opened = os.fstat(descriptor)
            path_after_open = target.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or _is_link_or_reparse_point(target, path_after_open)
                or not _same_file_state(before, opened)
                or not _same_file_state(opened, path_after_open)
                or opened.st_size != entry.byte_count
            ):
                raise ResearchBenchmarkCorpusError("frozen corpus source changed before read")
            content = _read_exact(descriptor, entry.byte_count)
            after_opened = os.fstat(descriptor)
            _target_after, after = self._safe_existing_regular_file(entry)
            if (
                not _same_file_state(opened, after_opened)
                or not _same_file_state(after_opened, after)
            ):
                raise ResearchBenchmarkCorpusError("frozen corpus source changed during read")
        except ResearchBenchmarkCorpusError:
            raise
        except OSError as exc:
            raise ResearchBenchmarkCorpusError("frozen corpus source cannot be read") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if hashlib.sha256(content).hexdigest() != entry.content_digest:
            raise ResearchBenchmarkCorpusError("frozen corpus source digest drifted")
        return content

    def _safe_existing_regular_file(
        self, entry: ResearchCorpusManifestEntry
    ) -> tuple[Path, os.stat_result]:
        _ensure_directory_no_link(self._repository_root)
        target = self._repository_root.joinpath(*PurePosixPath(entry.relative_path).parts)
        current = self._repository_root
        for component in PurePosixPath(entry.relative_path).parts:
            current = current / component
            metadata = _lstat(current, "frozen corpus source is unavailable")
            if _is_link_or_reparse_point(current, metadata):
                raise ResearchBenchmarkCorpusError("frozen corpus source contains a forbidden link")
            if current != target:
                if not stat.S_ISDIR(metadata.st_mode):
                    raise ResearchBenchmarkCorpusError("frozen corpus source parent is not a directory")
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != entry.byte_count:
                raise ResearchBenchmarkCorpusError("frozen corpus source size or type drifted")
        try:
            resolved = target.resolve(strict=True)
            resolved.relative_to(self._repository_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ResearchBenchmarkCorpusError("frozen corpus source escaped repository root") from exc
        return target, _lstat(target, "frozen corpus source is unavailable")


def _source_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not _SOURCE_ID_RE.fullmatch(text):
        raise ResearchBenchmarkCorpusError("source_id must be a lower-case stable identifier")
    return text


def _governed_ref(value: Any) -> str:
    text = str(value or "").strip()
    if text.split("://", 1)[0].lower() == "file":
        raise ResearchBenchmarkCorpusError("source_ref must not expose a file locator")
    if not _GOVERNED_REF_RE.fullmatch(text):
        raise ResearchBenchmarkCorpusError("source_ref must be a governed opaque reference")
    return text


def _repository_relative_path(value: Any) -> str:
    if not isinstance(value, str):
        raise ResearchBenchmarkCorpusError("relative_path must be text")
    if not value or len(value.encode("utf-8")) > 1_024:
        raise ResearchBenchmarkCorpusError("relative_path must be non-empty and bounded")
    if value != unicodedata.normalize("NFC", value):
        raise ResearchBenchmarkCorpusError("relative_path must use NFC Unicode")
    if "\\" in value or "\x00" in value or any(ord(character) < 0x20 for character in value):
        raise ResearchBenchmarkCorpusError("relative_path must use safe POSIX syntax")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(":" in part for part in path.parts)
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ResearchBenchmarkCorpusError("relative_path must be canonical and repository-relative")
    return value


def _sha256(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(text):
        raise ResearchBenchmarkCorpusError("content_digest must be a lower-case SHA-256 digest")
    return text


def _byte_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ResearchBenchmarkCorpusError("byte_count must be an integer")
    if value < 0 or value > _MAX_DOCUMENT_BYTES:
        raise ResearchBenchmarkCorpusError("byte_count is outside the D20 corpus size limit")
    return value


def _authority_tier(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text not in _AUTHORITY_TIERS:
        raise ResearchBenchmarkCorpusError(
            "authority_tier must be primary, independent, or secondary"
        )
    return text


def _repository_root(value: Path) -> Path:
    try:
        root = Path(value)
    except TypeError as exc:
        raise ResearchBenchmarkCorpusError("frozen corpus repository root is invalid") from exc
    metadata = _lstat(root, "frozen corpus repository root is unavailable")
    if _is_link_or_reparse_point(root, metadata) or not stat.S_ISDIR(metadata.st_mode):
        raise ResearchBenchmarkCorpusError("frozen corpus repository root is invalid")
    try:
        return root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ResearchBenchmarkCorpusError("frozen corpus repository root is unavailable") from exc


def _ensure_directory_no_link(path: Path) -> None:
    metadata = _lstat(path, "frozen corpus repository root is unavailable")
    if _is_link_or_reparse_point(path, metadata) or not stat.S_ISDIR(metadata.st_mode):
        raise ResearchBenchmarkCorpusError("frozen corpus repository root changed")


def _lstat(path: Path, detail: str) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise ResearchBenchmarkCorpusError(detail) from exc


def _is_link_or_reparse_point(path: Path, metadata: os.stat_result | None = None) -> bool:
    if path.is_symlink():
        return True
    state = metadata if metadata is not None else _lstat(path, "frozen corpus path is unavailable")
    attributes = int(getattr(state, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0)
    return bool(reparse_flag and attributes & reparse_flag)


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


def _read_exact(descriptor: int, byte_count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = byte_count
    while remaining:
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            raise ResearchBenchmarkCorpusError("frozen corpus source changed during read")
        chunks.append(chunk)
        remaining -= len(chunk)
    if os.read(descriptor, 1):
        raise ResearchBenchmarkCorpusError("frozen corpus source changed during read")
    return b"".join(chunks)


__all__ = [
    "RESEARCH_BENCHMARK_CORPUS_MODE",
    "FrozenResearchCorpusLoader",
    "FrozenResearchCorpusManifest",
    "ResearchBenchmarkCorpusError",
    "ResearchCorpusDocument",
    "ResearchCorpusManifestEntry",
]
