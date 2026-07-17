"""Contracts for isolated historical Code benchmark evidence.

These values deliberately contain only benchmark identifiers, opaque evidence
references, digests and repository-relative policy.  Host filesystem paths
belong exclusively to the server-side sandbox adapter and never appear in an
arm receipt.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any

from host_brain.planning_search.benchmarking import (
    BenchmarkArm,
    BenchmarkArmOutcome,
    DomainBenchmarkReport,
    DomainBenchmarkWorkload,
)


CODE_HISTORICAL_BENCHMARK_SCHEMA = "natsume.code-historical-benchmark.v1"
# This mode is deliberately explicit: the adapter gives trusted repository code
# disposable Git worktrees; it is not a process, network or filesystem sandbox.
CODE_HISTORICAL_BENCHMARK_MODE = "PRIVATE_BARE_WORKTREE_TRUSTED_PROCESS_ONLY"
CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST = "TRUSTED_IN_PROCESS_ONLY"
CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION = "NOT_ENFORCED"
CODE_BENCHMARK_CLEANUP_STATUS = "VERIFIED_CLEAN"
CODE_BENCHMARK_CLEANUP_ADAPTER_REVISION = "code-private-bare-cleanup-v1"

_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,119}$")
_PATH_COMPONENT_RE = re.compile(r"^[a-z0-9_][a-z0-9._-]{0,119}$")
_GOVERNED_REF_RE = re.compile(
    r"^[a-z][a-z0-9+.-]{0,63}://[a-z0-9][a-z0-9._/-]{0,1018}$"
)
_WINDOWS_RESERVED_PATH_STEMS = frozenset(
    {
        "aux",
        "clock$",
        "con",
        "nul",
        "prn",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)


class CodeBenchmarkContractError(ValueError):
    """Raised when Code benchmark evidence cannot remain isolated or replayable."""


@dataclass(frozen=True, slots=True)
class CodeBenchmarkPathPolicy:
    """Server-owned repository-relative allow/deny policy for one workload.

    The values are repository-relative paths, never host paths.  They are
    intentionally retained only as a digest in durable-facing receipts.  D17
    accepts a deliberately small, lowercase portable path grammar so Windows
    case aliases, trailing-dot aliases and Git metadata aliases cannot give
    the trusted adapter a second filesystem interpretation.
    """

    allowed_paths: Sequence[str]
    forbidden_paths: Sequence[str] = ()
    max_changed_files: int = 1

    def __post_init__(self) -> None:
        allowed = _relative_paths(
            self.allowed_paths,
            field_name="allowed_paths",
            allow_empty=False,
        )
        forbidden = _relative_paths(
            self.forbidden_paths,
            field_name="forbidden_paths",
            allow_empty=True,
        )
        if any(
            _is_under(allowed_path, forbidden_path)
            for allowed_path in allowed
            for forbidden_path in forbidden
        ):
            raise CodeBenchmarkContractError(
                "an allowed path cannot be contained by a forbidden path"
            )
        if (
            isinstance(self.max_changed_files, bool)
            or not isinstance(self.max_changed_files, int)
            or self.max_changed_files < 1
            or self.max_changed_files > 10_000
        ):
            raise CodeBenchmarkContractError(
                "max_changed_files must be an integer between 1 and 10000"
            )
        object.__setattr__(self, "allowed_paths", allowed)
        object.__setattr__(self, "forbidden_paths", forbidden)
        object.__setattr__(self, "max_changed_files", int(self.max_changed_files))

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def canonicalize(self, relative_path: str) -> str:
        """Return the one accepted spelling for a policy-controlled path."""

        return _relative_path(relative_path, field_name="relative_path")

    def permits(self, relative_path: str) -> bool:
        candidate = self.canonicalize(relative_path)
        return any(_is_under(candidate, item) for item in self.allowed_paths) and not any(
            _is_under(candidate, item) for item in self.forbidden_paths
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_paths": list(self.allowed_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "max_changed_files": self.max_changed_files,
        }


@dataclass(frozen=True, slots=True)
class CodeVerifierResult:
    """Bounded outcome returned by a trusted verifier adapter."""

    success: bool
    receipt_ref: str
    cost: Mapping[str, int]
    evidence_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.success, bool):
            raise CodeBenchmarkContractError("success must be boolean")
        object.__setattr__(
            self,
            "receipt_ref",
            _governed_ref(self.receipt_ref, field_name="receipt_ref"),
        )
        object.__setattr__(self, "cost", _freeze_json(_counters(self.cost)))
        object.__setattr__(
            self,
            "evidence_digest",
            _digest_text(self.evidence_digest, field_name="evidence_digest"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "receipt_ref": self.receipt_ref,
            "cost": _thaw_json(self.cost),
            "evidence_digest": self.evidence_digest,
        }


@dataclass(frozen=True, slots=True)
class CodeBenchmarkArmEvidence:
    """One arm's redacted isolation, diff and verifier evidence."""

    arm_id: str
    source_ref: str
    source_commit: str
    path_policy_digest: str
    changed_file_count: int
    changed_path_digest: str
    workspace_diff_digest: str
    verifier: CodeVerifierResult
    receipt_ref: str = ""

    def __post_init__(self) -> None:
        arm_id = _identifier(self.arm_id, field_name="arm_id")
        source_ref = _governed_ref(self.source_ref, field_name="source_ref")
        source_commit = _commit(self.source_commit, field_name="source_commit")
        policy_digest = _digest_text(
            self.path_policy_digest,
            field_name="path_policy_digest",
        )
        if (
            isinstance(self.changed_file_count, bool)
            or not isinstance(self.changed_file_count, int)
            or self.changed_file_count < 0
        ):
            raise CodeBenchmarkContractError(
                "changed_file_count must be a non-negative integer"
            )
        changed_path_digest = _digest_text(
            self.changed_path_digest,
            field_name="changed_path_digest",
        )
        workspace_diff_digest = _digest_text(
            self.workspace_diff_digest,
            field_name="workspace_diff_digest",
        )
        if not isinstance(self.verifier, CodeVerifierResult):
            raise CodeBenchmarkContractError("verifier must be CodeVerifierResult")
        identity = {
            "schema": CODE_HISTORICAL_BENCHMARK_SCHEMA,
            "arm_id": arm_id,
            "source_ref": source_ref,
            "source_commit": source_commit,
            "path_policy_digest": policy_digest,
            "changed_file_count": self.changed_file_count,
            "changed_path_digest": changed_path_digest,
            "workspace_diff_digest": workspace_diff_digest,
            "verifier": self.verifier.to_dict(),
        }
        digest = _digest(identity)
        supplied = str(self.receipt_ref or "").strip()
        expected_ref = f"benchmark-arm://sha256/{digest}"
        if supplied and supplied != expected_ref:
            raise CodeBenchmarkContractError("receipt_ref does not bind arm evidence")
        object.__setattr__(self, "arm_id", arm_id)
        object.__setattr__(self, "source_ref", source_ref)
        object.__setattr__(self, "source_commit", source_commit)
        object.__setattr__(self, "path_policy_digest", policy_digest)
        object.__setattr__(self, "changed_file_count", int(self.changed_file_count))
        object.__setattr__(self, "changed_path_digest", changed_path_digest)
        object.__setattr__(self, "workspace_diff_digest", workspace_diff_digest)
        object.__setattr__(self, "receipt_ref", expected_ref)

    @property
    def evidence_digest(self) -> str:
        return self.receipt_ref.removeprefix("benchmark-arm://sha256/")

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm_id": self.arm_id,
            "source_ref": self.source_ref,
            "source_commit": self.source_commit,
            "path_policy_digest": self.path_policy_digest,
            "changed_file_count": self.changed_file_count,
            "changed_path_digest": self.changed_path_digest,
            "workspace_diff_digest": self.workspace_diff_digest,
            "verifier": self.verifier.to_dict(),
            "receipt_ref": self.receipt_ref,
        }


@dataclass(frozen=True, slots=True)
class CodeBenchmarkCleanupReceipt:
    """Path-free proof that a private benchmark run finished cleanly.

    The receipt deliberately records only facts that the trusted D17 adapter
    can establish after its finally-block completes.  It is *not* evidence of
    a container, OS, tenant or network sandbox.  In particular, the source
    fingerprints prove that the protected Git checkout did not change during
    this one run; they do not authenticate the source or grant promotion.
    """

    run_id: str
    source_before_digest: str
    source_after_digest: str
    private_bare_removed: bool
    worktrees_removed: bool
    run_root_removed: bool
    cleanup_adapter_revision: str = CODE_BENCHMARK_CLEANUP_ADAPTER_REVISION
    status: str = CODE_BENCHMARK_CLEANUP_STATUS
    receipt_ref: str = ""

    def __post_init__(self) -> None:
        run_id = _identifier(self.run_id, field_name="run_id")
        before = _digest_text(
            self.source_before_digest,
            field_name="source_before_digest",
        )
        after = _digest_text(
            self.source_after_digest,
            field_name="source_after_digest",
        )
        if before != after:
            raise CodeBenchmarkContractError(
                "verified cleanup requires an unchanged protected source fingerprint"
            )
        for field_name in (
            "private_bare_removed",
            "worktrees_removed",
            "run_root_removed",
        ):
            if getattr(self, field_name) is not True:
                raise CodeBenchmarkContractError(
                    f"verified cleanup requires {field_name} to be true"
                )
        revision = _identifier(
            self.cleanup_adapter_revision,
            field_name="cleanup_adapter_revision",
        )
        status = str(self.status or "").strip().upper()
        if status != CODE_BENCHMARK_CLEANUP_STATUS:
            raise CodeBenchmarkContractError(
                "cleanup receipt status must be VERIFIED_CLEAN"
            )
        identity = {
            "schema": CODE_HISTORICAL_BENCHMARK_SCHEMA,
            "run_id": run_id,
            "source_before_digest": before,
            "source_after_digest": after,
            "private_bare_removed": True,
            "worktrees_removed": True,
            "run_root_removed": True,
            "cleanup_adapter_revision": revision,
            "status": CODE_BENCHMARK_CLEANUP_STATUS,
        }
        digest = _digest(identity)
        supplied = str(self.receipt_ref or "").strip()
        expected_ref = f"benchmark-cleanup://sha256/{digest}"
        if supplied and supplied != expected_ref:
            raise CodeBenchmarkContractError("receipt_ref does not bind cleanup evidence")
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "source_before_digest", before)
        object.__setattr__(self, "source_after_digest", after)
        object.__setattr__(self, "private_bare_removed", True)
        object.__setattr__(self, "worktrees_removed", True)
        object.__setattr__(self, "run_root_removed", True)
        object.__setattr__(self, "cleanup_adapter_revision", revision)
        object.__setattr__(self, "status", CODE_BENCHMARK_CLEANUP_STATUS)
        object.__setattr__(self, "receipt_ref", expected_ref)

    @property
    def receipt_digest(self) -> str:
        return self.receipt_ref.removeprefix("benchmark-cleanup://sha256/")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "source_before_digest": self.source_before_digest,
            "source_after_digest": self.source_after_digest,
            "private_bare_removed": True,
            "worktrees_removed": True,
            "run_root_removed": True,
            "cleanup_adapter_revision": self.cleanup_adapter_revision,
            "status": CODE_BENCHMARK_CLEANUP_STATUS,
            "receipt_ref": self.receipt_ref,
        }


@dataclass(frozen=True, slots=True)
class CodeHistoricalBenchmarkRun:
    """One paired private-bare run; never a production promotion authority."""

    run_id: str
    report: DomainBenchmarkReport
    baseline_evidence: CodeBenchmarkArmEvidence
    selected_evidence: CodeBenchmarkArmEvidence
    cleanup_receipt: CodeBenchmarkCleanupReceipt | None = None
    mode: str = CODE_HISTORICAL_BENCHMARK_MODE
    production_influence_enabled: bool = False

    def __post_init__(self) -> None:
        run_id = _identifier(self.run_id, field_name="run_id")
        if not isinstance(self.report, DomainBenchmarkReport):
            raise CodeBenchmarkContractError("report must be DomainBenchmarkReport")
        if self.report.workload.domain != "code":
            raise CodeBenchmarkContractError("historical Code benchmark requires code domain")
        if not isinstance(self.baseline_evidence, CodeBenchmarkArmEvidence):
            raise CodeBenchmarkContractError("baseline_evidence is invalid")
        if not isinstance(self.selected_evidence, CodeBenchmarkArmEvidence):
            raise CodeBenchmarkContractError("selected_evidence is invalid")
        if self.baseline_evidence.arm_id != self.report.baseline.arm_id:
            raise CodeBenchmarkContractError("baseline evidence does not bind baseline arm")
        if self.selected_evidence.arm_id != self.report.selected.arm_id:
            raise CodeBenchmarkContractError("selected evidence does not bind selected arm")
        if self.baseline_evidence.source_commit != self.selected_evidence.source_commit:
            raise CodeBenchmarkContractError("benchmark arms must use the same frozen commit")
        if self.baseline_evidence.source_ref != self.selected_evidence.source_ref:
            raise CodeBenchmarkContractError("benchmark arms must use the same frozen source ref")
        if self.baseline_evidence.path_policy_digest != self.selected_evidence.path_policy_digest:
            raise CodeBenchmarkContractError("benchmark arms must use the same path policy")
        cleanup = self.cleanup_receipt
        if cleanup is not None:
            if not isinstance(cleanup, CodeBenchmarkCleanupReceipt):
                raise CodeBenchmarkContractError("cleanup_receipt is invalid")
            if cleanup.run_id != run_id:
                raise CodeBenchmarkContractError("cleanup receipt does not bind run_id")
        if str(self.mode or "").strip().upper() != CODE_HISTORICAL_BENCHMARK_MODE:
            raise CodeBenchmarkContractError(
                "historical Code benchmark must remain trusted-process-only"
            )
        if self.production_influence_enabled:
            raise CodeBenchmarkContractError(
                "historical Code benchmark cannot enable production influence"
            )
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "mode", CODE_HISTORICAL_BENCHMARK_MODE)
        object.__setattr__(self, "production_influence_enabled", False)

    @property
    def run_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CODE_HISTORICAL_BENCHMARK_SCHEMA,
            "run_id": self.run_id,
            "report": self.report.to_dict(),
            "baseline_evidence": self.baseline_evidence.to_dict(),
            "selected_evidence": self.selected_evidence.to_dict(),
            "cleanup_receipt": (
                self.cleanup_receipt.to_dict() if self.cleanup_receipt else None
            ),
            "mode": CODE_HISTORICAL_BENCHMARK_MODE,
            "production_influence_enabled": False,
        }


def benchmark_outcome(
    *,
    arm: BenchmarkArm,
    verifier: CodeVerifierResult,
    latency_ms: int,
    workspace_diff_digest: str,
) -> BenchmarkArmOutcome:
    """Construct the generic paired-outcome contract from Code verifier evidence."""

    if not isinstance(arm, BenchmarkArm):
        raise CodeBenchmarkContractError("arm must be BenchmarkArm")
    if not isinstance(verifier, CodeVerifierResult):
        raise CodeBenchmarkContractError("verifier must be CodeVerifierResult")
    return BenchmarkArmOutcome(
        arm_id=arm.arm_id,
        verifier_receipt_refs=(verifier.receipt_ref,),
        success=verifier.success,
        latency_ms=latency_ms,
        cost=_thaw_json(verifier.cost),
        workspace_diff_digest=workspace_diff_digest,
        executed=True,
    )


def _identifier(value: Any, *, field_name: str) -> str:
    text = _required_text(value, field_name=field_name, limit=120).lower()
    if not _ID_RE.fullmatch(text):
        raise CodeBenchmarkContractError(
            f"{field_name} must be a lowercase opaque identifier"
        )
    return text


def _governed_ref(value: Any, *, field_name: str) -> str:
    text = _required_text(value, field_name=field_name, limit=1_024)
    if (
        not _GOVERNED_REF_RE.fullmatch(text)
        or text.lower().startswith("file://")
        or "/../" in text
        or text.endswith("/..")
    ):
        raise CodeBenchmarkContractError(
            f"{field_name} must be an opaque governed ref, not a path"
        )
    return text


def _commit(value: Any, *, field_name: str) -> str:
    text = _required_text(value, field_name=field_name, limit=64).lower()
    if not _COMMIT_RE.fullmatch(text):
        raise CodeBenchmarkContractError(f"{field_name} must be a full Git commit id")
    return text


def _digest_text(value: Any, *, field_name: str) -> str:
    text = _required_text(value, field_name=field_name, limit=64).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise CodeBenchmarkContractError(f"{field_name} must be a lowercase SHA-256")
    return text


def _relative_paths(
    values: Sequence[str],
    *,
    field_name: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise CodeBenchmarkContractError(f"{field_name} must be an explicit sequence")
    output: list[str] = []
    for index, value in enumerate(values):
        path = _relative_path(value, field_name=f"{field_name}[{index}]")
        if path not in output:
            output.append(path)
    if not output and not allow_empty:
        raise CodeBenchmarkContractError(f"{field_name} must not be empty")
    return tuple(output)


def _relative_path(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise CodeBenchmarkContractError(f"{field_name} must be text")
    if value != value.strip() or not value or len(value) > 512:
        raise CodeBenchmarkContractError(
            f"{field_name} must be non-empty, unpadded and at most 512 characters"
        )
    text = value
    if "\\" in text or ":" in text or text.startswith("/"):
        raise CodeBenchmarkContractError(f"{field_name} must be a repository-relative path")
    parts = text.split("/")
    for part in parts:
        if part in {"", ".", ".."}:
            raise CodeBenchmarkContractError(
                f"{field_name} cannot contain empty or traversal components"
            )
        if part != part.rstrip(" ."):
            raise CodeBenchmarkContractError(
                f"{field_name} cannot contain trailing space or dot components"
            )
        if part.casefold() == ".git":
            raise CodeBenchmarkContractError(
                f"{field_name} cannot contain Git metadata components"
            )
        if not _PATH_COMPONENT_RE.fullmatch(part):
            raise CodeBenchmarkContractError(
                f"{field_name} must use lowercase portable path components"
            )
        if part.split(".", 1)[0] in _WINDOWS_RESERVED_PATH_STEMS:
            raise CodeBenchmarkContractError(
                f"{field_name} cannot contain Windows reserved device components"
            )
    return "/".join(parts)


def _is_under(candidate: str, parent: str) -> bool:
    return candidate == parent or candidate.startswith(parent + "/")


def _counters(value: Mapping[str, int]) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise CodeBenchmarkContractError("cost must be an object")
    output: dict[str, int] = {}
    for raw_name, raw_value in value.items():
        name = _identifier(str(raw_name), field_name="cost key")
        if isinstance(raw_value, bool) or not isinstance(raw_value, int) or raw_value < 0:
            raise CodeBenchmarkContractError(
                f"cost[{name}] must be a non-negative integer"
            )
        output[name] = int(raw_value)
    return dict(sorted(output.items()))


def _required_text(value: Any, *, field_name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise CodeBenchmarkContractError(f"{field_name} must be text")
    text = value.strip()
    if not text or len(text) > limit:
        raise CodeBenchmarkContractError(
            f"{field_name} must be non-empty and at most {limit} characters"
        )
    return text


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_json(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(child) for child in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


__all__ = (
    "CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST",
    "CODE_BENCHMARK_CLEANUP_ADAPTER_REVISION",
    "CODE_BENCHMARK_CLEANUP_STATUS",
    "CODE_HISTORICAL_BENCHMARK_MODE",
    "CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION",
    "CODE_HISTORICAL_BENCHMARK_SCHEMA",
    "CodeBenchmarkArmEvidence",
    "CodeBenchmarkCleanupReceipt",
    "CodeBenchmarkContractError",
    "CodeBenchmarkPathPolicy",
    "CodeHistoricalBenchmarkRun",
    "CodeVerifierResult",
    "benchmark_outcome",
)
