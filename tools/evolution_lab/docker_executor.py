"""Fail-closed Docker executor for promotion-eligible NE-04 experiments.

This boundary intentionally does not reuse the generic Airlock executor: that
path may degrade to a host subprocess, while trusted Evolution Lab evidence
must come from one concrete, digest-pinned Docker container.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Callable, Mapping, Protocol, Sequence

from host_brain.contracts.evolution_lab import (
    EffectAuditReceiptV1,
    ExperimentSpecV2,
    LabDatasetV1,
    PolicyVariantV1,
    canonical_evolution_lab_digest,
)
from host_brain.contracts.evolution_lab_trusted import (
    DockerSandboxAttestationV1,
    docker_effect_audit_evidence_digest,
)
from host_brain.core.ids import now_ms
from host_brain.core.subprocess_utils import run as silent_run
from host_brain.persistence.evolution_lab_trusted_repository import (
    TrustedExecutionClaim,
)


_PINNED_IMAGE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
_MAX_OUTPUT_BYTES = 1_048_576


class TrustedDockerExecutorError(RuntimeError):
    """Docker evidence is missing, ambiguous, or weaker than the frozen spec."""


@dataclass(frozen=True, slots=True)
class DockerCommandResult:
    returncode: int
    stdout: str
    stderr: str


class DockerCli(Protocol):
    def run(
        self,
        args: Sequence[str],
        *,
        timeout_s: int,
    ) -> DockerCommandResult: ...


class SubprocessDockerCli:
    """Production Docker CLI port with bounded, hidden subprocesses."""

    def run(
        self,
        args: Sequence[str],
        *,
        timeout_s: int,
    ) -> DockerCommandResult:
        try:
            completed = silent_run(
                list(args),
                capture_output=True,
                timeout=float(timeout_s),
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
            raise TrustedDockerExecutorError(
                "Docker CLI is unavailable or timed out"
            ) from exc
        return DockerCommandResult(
            returncode=int(completed.returncode),
            stdout=str(completed.stdout or ""),
            stderr=str(completed.stderr or ""),
        )


@dataclass(frozen=True, slots=True)
class TrustedDockerExecution:
    execution_id: str
    model_identity: str
    usage: Mapping[str, int]
    cost: Mapping[str, int]
    latency_ms: int
    output: Mapping[str, Any]
    sandbox_attestation: DockerSandboxAttestationV1


class TrustedDockerSandboxExecutor:
    """Execute one reserved arm and independently reopen Docker inspect facts."""

    def __init__(
        self,
        *,
        runtime_root: str | Path,
        image_reference: str,
        docker_cli: DockerCli | None = None,
        worker_source: str | Path | None = None,
        clock: Callable[[], int] = now_ms,
        memory_limit_bytes: int = 268_435_456,
        pids_limit: int = 64,
        nano_cpus: int = 1_000_000_000,
        non_root_user: str = "65534:65534",
    ) -> None:
        root = Path(runtime_root).expanduser().resolve()
        image = str(image_reference or "").strip().lower()
        if _PINNED_IMAGE.fullmatch(image) is None:
            raise TrustedDockerExecutorError(
                "trusted Docker image must be pinned by sha256 digest"
            )
        worker = Path(
            worker_source
            or Path(__file__).resolve().with_name("golden_worker.py")
        ).resolve()
        if not worker.is_file():
            raise TrustedDockerExecutorError(
                "trusted golden worker source is missing"
            )
        for field, value in (
            ("memory_limit_bytes", memory_limit_bytes),
            ("pids_limit", pids_limit),
            ("nano_cpus", nano_cpus),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise TrustedDockerExecutorError(f"{field} must be positive")
        user = str(non_root_user or "").strip()
        if not user or user in {"0", "0:0", "root", "root:root"}:
            raise TrustedDockerExecutorError(
                "trusted Docker worker must use a non-root user"
            )
        self.runtime_root = root
        self.image_reference = image
        self.worker_source = worker
        self.memory_limit_bytes = memory_limit_bytes
        self.pids_limit = pids_limit
        self.nano_cpus = nano_cpus
        self.non_root_user = user
        self._docker = docker_cli or SubprocessDockerCli()
        self._clock = clock

    @property
    def runtime_source_digest(self) -> str:
        return hashlib.sha256(self.worker_source.read_bytes()).hexdigest()

    @property
    def sandbox_profile_digest(self) -> str:
        return canonical_evolution_lab_digest(
            {
                "schema": "natsume.evolution-lab.docker-profile.v1",
                "image_reference": self.image_reference,
                "runtime_source_digest": self.runtime_source_digest,
                "network_mode": "none",
                "readonly_rootfs": True,
                "readonly_dataset": True,
                "readonly_runtime": True,
                "output_mount_type": "isolated-temporary-bind",
                "no_new_privileges": True,
                "cap_drop": ["ALL"],
                "non_root_user": self.non_root_user,
                "memory_limit_bytes": self.memory_limit_bytes,
                "pids_limit": self.pids_limit,
                "nano_cpus": self.nano_cpus,
            }
        )

    def execute(
        self,
        *,
        claim: TrustedExecutionClaim,
        experiment_kind: str,
        spec: ExperimentSpecV2,
        variant: PolicyVariantV1,
        dataset: LabDatasetV1,
    ) -> TrustedDockerExecution:
        self._validate_inputs(
            claim=claim,
            spec=spec,
            variant=variant,
            dataset=dataset,
        )
        execution_dir = self._execution_dir(claim.execution_id)
        if execution_dir.exists():
            raise TrustedDockerExecutorError(
                "trusted execution directory already exists; reconciliation is required"
            )
        dataset_dir = execution_dir / "dataset"
        output_dir = execution_dir / "output"
        reopen_dir = execution_dir / "reopen"
        dataset_dir.mkdir(parents=True, exist_ok=False)
        output_dir.mkdir()
        reopen_dir.mkdir()
        dataset_path = dataset_dir / "input.json"
        copied_output_path = reopen_dir / "result.json"
        dataset_path.write_text(
            _canonical(dataset.to_dict()),
            encoding="utf-8",
        )
        if _sha256_file(dataset_path) != dataset.dataset_digest:
            raise TrustedDockerExecutorError(
                "materialized dataset digest drifts from the frozen dataset"
            )

        engine_identity = self._engine_identity()
        image_id = self._reopen_image_id()
        command = self._worker_command(
            claim=claim,
            experiment_kind=experiment_kind,
            spec=spec,
            variant=variant,
        )
        create_args = self._create_args(
            claim=claim,
            dataset_path=dataset_path,
            output_dir=output_dir,
            command=command,
        )
        created = self._checked(create_args, timeout_s=30, action="create")
        created_id = str(created.stdout or "").strip().lower()
        if not created_id:
            raise TrustedDockerExecutorError(
                "Docker create returned no container identity"
            )

        started_at_ms = int(self._clock())
        started = self._checked(
            ["docker", "start", "-a", claim.container_name],
            timeout_s=max(
                30,
                int(spec.budget.get("max_wall_time_ms", 60_000) / 1_000)
                + 15,
            ),
            action="start",
        )
        if len(started.stdout.encode("utf-8")) > 65_536:
            raise TrustedDockerExecutorError(
                "trusted Docker worker stdout exceeds its evidence bound"
            )
        inspect = self._container_inspect(claim.container_name)
        normalized, container_id = self._normalize_inspect(
            inspect=inspect,
            claim=claim,
            command=command,
            dataset_path=dataset_path,
            output_dir=output_dir,
            expected_image_id=image_id,
        )
        if not container_id.startswith(created_id) and not created_id.startswith(
            container_id
        ):
            raise TrustedDockerExecutorError(
                "Docker create and inspect container identities drift"
            )
        copied = self._checked(
            [
                "docker",
                "cp",
                f"{claim.container_name}:/output/result.json",
                str(copied_output_path),
            ],
            timeout_s=15,
            action="copy output",
        )
        if copied.stdout.strip():
            raise TrustedDockerExecutorError(
                "Docker copy emitted unexpected stdout"
            )
        if (
            not copied_output_path.is_file()
            or copied_output_path.stat().st_size <= 0
            or copied_output_path.stat().st_size > _MAX_OUTPUT_BYTES
        ):
            raise TrustedDockerExecutorError(
                "trusted Docker output is missing or exceeds its evidence bound"
            )
        try:
            output = json.loads(copied_output_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TrustedDockerExecutorError(
                "trusted Docker output is not valid JSON"
            ) from exc
        if not isinstance(output, Mapping):
            raise TrustedDockerExecutorError(
                "trusted Docker output must be a JSON object"
            )
        output_digest = canonical_evolution_lab_digest({"output": output})
        finished_at_ms = int(self._clock())
        output_source_digest = _path_digest(output_dir)
        normalized["output_source_digest"] = output_source_digest
        normalized["runtime_source_digest"] = self.runtime_source_digest
        audit_digest = docker_effect_audit_evidence_digest(
            execution_id=claim.execution_id,
            command=command,
            inspect_evidence=normalized,
            sandbox_profile_digest=self.sandbox_profile_digest,
            dataset_digest=dataset.dataset_digest,
            output_digest=output_digest,
        )
        effect_audit = EffectAuditReceiptV1(
            execution_id=claim.execution_id,
            audit_id=f"docker-effect-audit-{claim.execution_id}",
            auditor_identity="docker-inspect://natsume/ne04/v1",
            audit_evidence_digest=audit_digest,
            effect_count=0,
            denied_effect_count=0,
            production_side_effects=False,
        )
        attestation = DockerSandboxAttestationV1(
            execution_id=claim.execution_id,
            engine_identity=engine_identity,
            image_reference=self.image_reference,
            image_id=image_id,
            container_id=container_id,
            container_name=claim.container_name,
            command=command,
            inspect_evidence=normalized,
            sandbox_profile_digest=self.sandbox_profile_digest,
            runtime_source_digest=self.runtime_source_digest,
            output_source_digest=output_source_digest,
            dataset_digest=dataset.dataset_digest,
            output_digest=output_digest,
            output_mount_type="bind",
            network_mode="none",
            readonly_rootfs=True,
            readonly_dataset=True,
            readonly_runtime=True,
            temporary_output=True,
            no_new_privileges=True,
            cap_drop_all=True,
            non_root_user=self.non_root_user,
            pids_limit=self.pids_limit,
            memory_limit_bytes=self.memory_limit_bytes,
            nano_cpus=self.nano_cpus,
            exit_code=0,
            started_at_ms=started_at_ms,
            finished_at_ms=finished_at_ms,
            effect_audit=effect_audit,
        )
        return TrustedDockerExecution(
            execution_id=claim.execution_id,
            model_identity=(
                "deterministic-golden-policy://natsume/ne04/"
                f"{experiment_kind}/{variant.variant_id}"
            ),
            usage={"records": len(dataset.records), "container_runs": 1},
            cost={"microusd": 0},
            latency_ms=max(0, finished_at_ms - started_at_ms),
            output=dict(output),
            sandbox_attestation=attestation,
        )

    def cleanup(self, *, claim: TrustedExecutionClaim) -> bool:
        """Remove only this claim's container and isolated execution directory."""

        removed = self._docker.run(
            ["docker", "rm", "-f", claim.container_name],
            timeout_s=15,
        )
        if removed.returncode != 0:
            detail = str(removed.stderr or "").lower()
            if "no such container" not in detail:
                return False
        execution_dir = self._execution_dir(claim.execution_id)
        if execution_dir.exists():
            shutil.rmtree(execution_dir)
        return not execution_dir.exists()

    def _validate_inputs(
        self,
        *,
        claim: TrustedExecutionClaim,
        spec: ExperimentSpecV2,
        variant: PolicyVariantV1,
        dataset: LabDatasetV1,
    ) -> None:
        if not isinstance(claim, TrustedExecutionClaim):
            raise TrustedDockerExecutorError(
                "trusted Docker execution requires a durable claim"
            )
        if not claim.execution_authorized:
            raise TrustedDockerExecutorError(
                "replayed or non-running claim cannot start a container"
            )
        if not isinstance(spec, ExperimentSpecV2) or not isinstance(
            variant, PolicyVariantV1
        ):
            raise TrustedDockerExecutorError(
                "trusted Docker spec or variant is invalid"
            )
        if (
            claim.spec_digest != spec.spec_digest
            or claim.workspace_id != spec.workspace_id
            or claim.dataset_manifest_digest
            != spec.dataset_manifest.manifest_digest
            or claim.variant_id != variant.variant_id
            or spec.isolation_profile_digest != self.sandbox_profile_digest
        ):
            raise TrustedDockerExecutorError(
                "trusted Docker claim/spec/profile identity drift"
            )
        if (
            not isinstance(dataset, LabDatasetV1)
            or dataset.dataset_id != spec.dataset_manifest.dataset_id
            or dataset.dataset_digest != spec.dataset_manifest.dataset_digest
            or len(dataset.records) != spec.dataset_manifest.record_count
        ):
            raise TrustedDockerExecutorError(
                "trusted Docker dataset drifts from the frozen manifest"
            )

    def _execution_dir(self, execution_id: str) -> Path:
        digest = hashlib.sha256(execution_id.encode("utf-8")).hexdigest()
        return (self.runtime_root / digest).resolve()

    def _engine_identity(self) -> str:
        result = self._checked(
            ["docker", "version", "--format", "{{json .Server}}"],
            timeout_s=15,
            action="reopen engine identity",
        )
        try:
            payload = json.loads(result.stdout)
        except ValueError as exc:
            raise TrustedDockerExecutorError(
                "Docker engine identity is not JSON"
            ) from exc
        if not isinstance(payload, Mapping):
            raise TrustedDockerExecutorError(
                "Docker engine identity payload is invalid"
            )
        version = str(payload.get("Version") or "").strip()
        os_name = str(payload.get("Os") or "").strip().lower()
        arch = str(payload.get("Arch") or "").strip().lower()
        if not version or not os_name or not arch:
            raise TrustedDockerExecutorError(
                "Docker engine identity is incomplete"
            )
        identity_digest = canonical_evolution_lab_digest(
            {
                "version": version,
                "os": os_name,
                "arch": arch,
                "components": payload.get("Components", []),
            }
        )
        return (
            f"docker://engine/{version}/{os_name}/{arch}"
            f"@sha256:{identity_digest}"
        )

    def _reopen_image_id(self) -> str:
        result = self._checked(
            ["docker", "image", "inspect", self.image_reference],
            timeout_s=20,
            action="reopen image",
        )
        try:
            payload = json.loads(result.stdout)
            row = payload[0]
        except (ValueError, IndexError, KeyError, TypeError) as exc:
            raise TrustedDockerExecutorError(
                "Docker image inspect payload is invalid"
            ) from exc
        if not isinstance(row, Mapping):
            raise TrustedDockerExecutorError(
                "Docker image inspect row is invalid"
            )
        image_id = str(row.get("Id") or "").strip().lower()
        if image_id.startswith("sha256:"):
            image_id = image_id[7:]
        if re.fullmatch(r"[0-9a-f]{64}", image_id) is None:
            raise TrustedDockerExecutorError(
                "Docker image inspect identity is invalid"
            )
        return image_id

    def _container_inspect(self, container_name: str) -> Mapping[str, Any]:
        result = self._checked(
            ["docker", "inspect", container_name],
            timeout_s=20,
            action="reopen container",
        )
        try:
            payload = json.loads(result.stdout)
            row = payload[0]
        except (ValueError, IndexError, KeyError, TypeError) as exc:
            raise TrustedDockerExecutorError(
                "Docker container inspect payload is invalid"
            ) from exc
        if not isinstance(row, Mapping):
            raise TrustedDockerExecutorError(
                "Docker container inspect row is invalid"
            )
        return row

    def _normalize_inspect(
        self,
        *,
        inspect: Mapping[str, Any],
        claim: TrustedExecutionClaim,
        command: tuple[str, ...],
        dataset_path: Path,
        output_dir: Path,
        expected_image_id: str,
    ) -> tuple[dict[str, Any], str]:
        config = _mapping(inspect.get("Config"), "Config")
        host = _mapping(inspect.get("HostConfig"), "HostConfig")
        state = _mapping(inspect.get("State"), "State")
        container_id = str(inspect.get("Id") or "").strip().lower()
        image_id = str(inspect.get("Image") or "").strip().lower()
        if image_id.startswith("sha256:"):
            image_id = image_id[7:]
        name = str(inspect.get("Name") or "").strip().removeprefix("/")
        actual_command = tuple(str(item) for item in config.get("Cmd") or ())
        cap_drop = sorted(
            {str(item).upper() for item in host.get("CapDrop") or ()}
        )
        security = {
            str(item).lower() for item in host.get("SecurityOpt") or ()
        }
        mounts = inspect.get("Mounts")
        if not isinstance(mounts, list):
            raise TrustedDockerExecutorError(
                "Docker inspect mounts are missing"
            )
        by_destination: dict[str, Mapping[str, Any]] = {}
        for raw_mount in mounts:
            if isinstance(raw_mount, Mapping):
                destination = str(raw_mount.get("Destination") or "")
                by_destination[destination] = raw_mount
        dataset_mount = by_destination.get("/dataset/input.json")
        runtime_mount = by_destination.get("/runtime/golden_worker.py")
        output_mount = by_destination.get("/output")
        if not all(
            isinstance(value, Mapping)
            for value in (dataset_mount, runtime_mount, output_mount)
        ):
            raise TrustedDockerExecutorError(
                "Docker inspect lacks the exact dataset/runtime/output mounts"
            )
        assert dataset_mount is not None
        assert runtime_mount is not None
        assert output_mount is not None
        actual_output_source = str(output_mount.get("Source") or "")
        actual_runtime_source = str(runtime_mount.get("Source") or "")
        actual_dataset_source = str(dataset_mount.get("Source") or "")
        expected = {
            "container": container_id,
            "name": claim.container_name,
            "image": expected_image_id,
            "command": command,
            "network": "none",
            "readonly_rootfs": True,
            "user": self.non_root_user,
            "pids": self.pids_limit,
            "memory": self.memory_limit_bytes,
            "nano_cpus": self.nano_cpus,
        }
        actual = {
            "container": container_id,
            "name": name,
            "image": image_id,
            "command": actual_command,
            "network": str(host.get("NetworkMode") or "").lower(),
            "readonly_rootfs": host.get("ReadonlyRootfs"),
            "user": str(config.get("User") or ""),
            "pids": host.get("PidsLimit"),
            "memory": host.get("Memory"),
            "nano_cpus": host.get("NanoCpus"),
        }
        if actual != expected:
            raise TrustedDockerExecutorError(
                "Docker inspect identity or sandbox resource facts drift"
            )
        if (
            cap_drop != ["ALL"]
            or not any(
                item == "no-new-privileges"
                or item.startswith("no-new-privileges:")
                for item in security
            )
            or bool(dataset_mount.get("RW"))
            or bool(runtime_mount.get("RW"))
            or not bool(output_mount.get("RW"))
            or str(output_mount.get("Type") or "").lower() != "bind"
            or not _same_host_path(actual_dataset_source, dataset_path)
            or not _same_host_path(actual_runtime_source, self.worker_source)
            or not _same_host_path(actual_output_source, output_dir)
            or int(state.get("ExitCode", -1)) != 0
        ):
            raise TrustedDockerExecutorError(
                "Docker inspect isolation or mount facts drift"
            )
        normalized = {
            "cap_drop": ["ALL"],
            "command": list(command),
            "container_id": container_id,
            "dataset_mount_readonly": True,
            "exit_code": 0,
            "image_id": image_id,
            "image_reference": self.image_reference,
            "memory_limit_bytes": self.memory_limit_bytes,
            "name": name,
            "nano_cpus": self.nano_cpus,
            "network_mode": "none",
            "no_new_privileges": True,
            "output_mount_temporary": True,
            "output_mount_type": "bind",
            "output_source_digest": "",
            "pids_limit": self.pids_limit,
            "readonly_rootfs": True,
            "runtime_mount_readonly": True,
            "runtime_source_digest": "",
            "user": self.non_root_user,
        }
        return normalized, container_id

    def _worker_command(
        self,
        *,
        claim: TrustedExecutionClaim,
        experiment_kind: str,
        spec: ExperimentSpecV2,
        variant: PolicyVariantV1,
    ) -> tuple[str, ...]:
        return (
            "python",
            "/runtime/golden_worker.py",
            "--dataset",
            "/dataset/input.json",
            "--output",
            "/output/result.json",
            "--kind",
            str(experiment_kind),
            "--variant",
            variant.variant_id,
            "--execution-id",
            claim.execution_id,
            "--seed",
            str(spec.seed),
            "--dataset-digest",
            spec.dataset_manifest.dataset_digest,
        )

    def _create_args(
        self,
        *,
        claim: TrustedExecutionClaim,
        dataset_path: Path,
        output_dir: Path,
        command: tuple[str, ...],
    ) -> list[str]:
        for path in (dataset_path, output_dir, self.worker_source):
            if "," in str(path):
                raise TrustedDockerExecutorError(
                    "trusted Docker mount paths cannot contain commas"
                )
        cpus = _format_cpus(self.nano_cpus)
        return [
            "docker",
            "create",
            "--name",
            claim.container_name,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.pids_limit),
            "--memory",
            str(self.memory_limit_bytes),
            "--cpus",
            cpus,
            "--user",
            self.non_root_user,
            "--mount",
            (
                f"type=bind,src={dataset_path},"
                "dst=/dataset/input.json,readonly"
            ),
            "--mount",
            (
                f"type=bind,src={self.worker_source},"
                "dst=/runtime/golden_worker.py,readonly"
            ),
            "--mount",
            f"type=bind,src={output_dir},dst=/output",
            self.image_reference,
            *command,
        ]

    def _checked(
        self,
        args: Sequence[str],
        *,
        timeout_s: int,
        action: str,
    ) -> DockerCommandResult:
        result = self._docker.run(args, timeout_s=timeout_s)
        if result.returncode != 0:
            detail = str(result.stderr or "").strip().replace("\r", " ").replace(
                "\n", " "
            )
            raise TrustedDockerExecutorError(
                f"Docker {action} failed: {detail[-500:] or 'unknown error'}"
            )
        return result


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TrustedDockerExecutorError(
            f"Docker inspect {field} payload is invalid"
        )
    return value


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _path_digest(path: Path) -> str:
    return hashlib.sha256(_normalized_host_path(path).encode("utf-8")).hexdigest()


def _same_host_path(actual: str, expected: Path) -> bool:
    return _normalized_host_path(actual) == _normalized_host_path(expected)


def _normalized_host_path(value: str | Path) -> str:
    raw = str(value).strip().replace("\\", "/")
    if raw.startswith("/run/desktop/mnt/host/") and len(raw) > 25:
        suffix = raw.removeprefix("/run/desktop/mnt/host/")
        drive, separator, rest = suffix.partition("/")
        if separator and len(drive) == 1:
            raw = f"{drive}:/{rest}"
    return os.path.normcase(os.path.abspath(raw)).replace("\\", "/").lower()


def _format_cpus(nano_cpus: int) -> str:
    whole = nano_cpus // 1_000_000_000
    fraction = nano_cpus % 1_000_000_000
    if fraction == 0:
        return str(whole)
    return f"{whole}.{fraction:09d}".rstrip("0")


__all__ = [
    "DockerCli",
    "DockerCommandResult",
    "SubprocessDockerCli",
    "TrustedDockerExecution",
    "TrustedDockerExecutorError",
    "TrustedDockerSandboxExecutor",
]
