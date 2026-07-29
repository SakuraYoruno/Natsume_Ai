"""Privacy-bounded receipt for a real Digital Life full-stack acceptance run.

The receipt is intentionally narrower than a deployment manifest.  It binds
one source commit to actual Docker, Host, OBS and Monitor evidence while
making missing remote devices or a real model explicit.  A local fixture,
port-only probe, or a synthetic response cannot be represented as a passing
component.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any


FULL_STACK_ACCEPTANCE_SCHEMA_VERSION = (
    "natsume.digital-life-full-stack-acceptance.v1"
)
FULL_STACK_ACCEPTANCE_PASS = "PASS"
FULL_STACK_ACCEPTANCE_PENDING_EXTERNAL = "ACCEPTANCE_PENDING_EXTERNAL"
FULL_STACK_ACCEPTANCE_FAIL = "FAIL"
REMOTE_CI_NOT_RUN_ACCOUNT_QUOTA = "NOT_RUN_ACCOUNT_QUOTA"

_COMPONENT_PASS = "PASS"
_COMPONENT_FAIL = "FAIL"
_COMPONENT_PENDING = "ACCEPTANCE_PENDING_EXTERNAL"
_COMPONENT_NOT_TESTED = "NOT_TESTED"
_COMPONENT_STATUSES = frozenset(
    {
        _COMPONENT_PASS,
        _COMPONENT_FAIL,
        _COMPONENT_PENDING,
        _COMPONENT_NOT_TESTED,
    }
)
_REMOTE_CI_STATUSES = frozenset(
    {"PASS", "FAIL", REMOTE_CI_NOT_RUN_ACCOUNT_QUOTA}
)
_LOCAL_COMPONENT_IDS = (
    "docker",
    "neo4j",
    "chroma",
    "redis",
    "host",
    "obs",
    "monitor",
)
_EXTERNAL_COMPONENT_IDS = (
    "gateway",
    "edge",
    "model_provider",
    "asr",
    "tts",
    "avatar",
)
_DOCKER_SERVICE_IDS = ("neo4j", "chroma", "redis")
_SHA256_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{7,159}$")
_SAFE_COMPONENT_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_DOCKER_ID_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_DOCKER_IMAGE_RE = re.compile(r"^[A-Za-z0-9./@:_-]{1,300}$")
_FORBIDDEN_FIELD_RE = re.compile(
    r"(?i)(password|passwd|secret|token|authorization|prompt|content|"
    r"message|raw_body|raw_input|audio|image|signature)"
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_ref(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _required_text(value: Any, field: str, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    return text


def _safe_ref(value: Any, field: str) -> str:
    text = _required_text(value, field, limit=96)
    if _SHA256_REF_RE.fullmatch(text) is None:
        raise ValueError(f"{field} must be a sha256 reference")
    return text


def _non_negative_int(value: Any, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if parsed < 0:
        raise ValueError(f"{field} must be non-negative")
    return parsed


def _safe_codes(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{field} must be a list")
    if len(value) > 12:
        raise ValueError(f"{field} exceeds 12 entries")
    result: list[str] = []
    for index, item in enumerate(value):
        code = _required_text(item, f"{field}[{index}]", limit=96).upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_:-]*", code):
            raise ValueError(f"{field}[{index}] must be a safe code")
        result.append(code)
    if len(set(result)) != len(result):
        raise ValueError(f"{field} must not contain duplicates")
    return tuple(result)


@dataclass(frozen=True)
class FullStackComponentV1:
    """One real component result without payloads, credentials, or user text."""

    component_id: str
    status: str
    evidence_kind: str
    evidence_digest: str
    listener: str = ""
    process_id: int = 0
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        component_id = _required_text(self.component_id, "component_id", limit=64)
        if _SAFE_COMPONENT_RE.fullmatch(component_id) is None:
            raise ValueError("component_id is invalid")
        status = _required_text(self.status, "status", limit=64).upper()
        if status not in _COMPONENT_STATUSES:
            raise ValueError("component status is invalid")
        evidence_kind = _required_text(
            self.evidence_kind,
            "evidence_kind",
            limit=96,
        ).upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_:-]*", evidence_kind):
            raise ValueError("evidence_kind is invalid")
        listener = str(self.listener or "").strip()
        if len(listener) > 200:
            raise ValueError("listener exceeds 200 characters")
        if listener and not listener.startswith("loopback://"):
            raise ValueError("listener must be loopback-scoped")
        process_id = _non_negative_int(self.process_id, "process_id")
        reasons = _safe_codes(self.reason_codes, "reason_codes")
        if status == _COMPONENT_PASS and evidence_kind.startswith(("FIXTURE", "MOCK", "SCRIPTED")):
            raise ValueError("synthetic evidence cannot pass a full-stack component")
        if status == _COMPONENT_PASS and any(
            marker in evidence_kind
            for marker in ("NOT_STARTED", "NOT_TESTED", "UNAVAILABLE", "PENDING")
        ):
            raise ValueError("absence evidence cannot pass a full-stack component")
        object.__setattr__(self, "component_id", component_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "evidence_kind", evidence_kind)
        object.__setattr__(self, "evidence_digest", _safe_ref(self.evidence_digest, "evidence_digest"))
        object.__setattr__(self, "listener", listener)
        object.__setattr__(self, "process_id", process_id)
        object.__setattr__(self, "reason_codes", reasons)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FullStackComponentV1":
        if not isinstance(value, Mapping):
            raise ValueError("component must be an object")
        expected = {
            "component_id",
            "status",
            "evidence_kind",
            "evidence_digest",
            "listener",
            "process_id",
            "reason_codes",
        }
        if set(value) != expected:
            raise ValueError("component field set is invalid")
        return cls(
            component_id=value["component_id"],
            status=value["status"],
            evidence_kind=value["evidence_kind"],
            evidence_digest=value["evidence_digest"],
            listener=value["listener"],
            process_id=value["process_id"],
            reason_codes=value["reason_codes"],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "status": self.status,
            "evidence_kind": self.evidence_kind,
            "evidence_digest": self.evidence_digest,
            "listener": self.listener,
            "process_id": self.process_id,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class DockerServiceEvidenceV1:
    """Safe container identity for one real isolated persistence service."""

    service_id: str
    status: str
    container_id: str
    image_id: str
    image_ref: str
    host_port: int
    started_at: str
    evidence_digest: str

    def __post_init__(self) -> None:
        service_id = _required_text(self.service_id, "service_id", limit=64)
        if service_id not in _DOCKER_SERVICE_IDS:
            raise ValueError("docker service_id is invalid")
        status = _required_text(self.status, "docker status", limit=64).upper()
        if status not in _COMPONENT_STATUSES:
            raise ValueError("docker service status is invalid")
        container_id = _required_text(self.container_id, "container_id", limit=80)
        image_id = _required_text(self.image_id, "image_id", limit=80)
        image_ref = _required_text(self.image_ref, "image_ref", limit=300)
        if _DOCKER_ID_RE.fullmatch(container_id) is None:
            raise ValueError("container_id is invalid")
        if _DOCKER_ID_RE.fullmatch(image_id) is None:
            raise ValueError("image_id is invalid")
        if _DOCKER_IMAGE_RE.fullmatch(image_ref) is None or "@sha256:" not in image_ref:
            raise ValueError("image_ref must be digest-pinned")
        host_port = _non_negative_int(self.host_port, "host_port")
        if not 1 <= host_port <= 65535:
            raise ValueError("host_port is invalid")
        started_at = _required_text(self.started_at, "started_at", limit=64)
        if "T" not in started_at:
            raise ValueError("started_at is invalid")
        object.__setattr__(self, "service_id", service_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "container_id", container_id)
        object.__setattr__(self, "image_id", image_id)
        object.__setattr__(self, "image_ref", image_ref)
        object.__setattr__(self, "host_port", host_port)
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "evidence_digest", _safe_ref(self.evidence_digest, "evidence_digest"))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DockerServiceEvidenceV1":
        if not isinstance(value, Mapping):
            raise ValueError("docker service evidence must be an object")
        expected = {
            "service_id",
            "status",
            "container_id",
            "image_id",
            "image_ref",
            "host_port",
            "started_at",
            "evidence_digest",
        }
        if set(value) != expected:
            raise ValueError("docker service evidence field set is invalid")
        return cls(**dict(value))

    def as_dict(self) -> dict[str, Any]:
        return {
            "service_id": self.service_id,
            "status": self.status,
            "container_id": self.container_id,
            "image_id": self.image_id,
            "image_ref": self.image_ref,
            "host_port": self.host_port,
            "started_at": self.started_at,
            "evidence_digest": self.evidence_digest,
        }


def _component_set(
    value: Any,
    *,
    field: str,
    expected_ids: tuple[str, ...],
) -> tuple[FullStackComponentV1, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{field} must be a list")
    records = tuple(FullStackComponentV1.from_mapping(item) for item in value)
    identifiers = tuple(item.component_id for item in records)
    if identifiers != expected_ids:
        raise ValueError(f"{field} component identity/order is invalid")
    return records


def _docker_service_set(value: Any) -> tuple[DockerServiceEvidenceV1, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("docker_services must be a list")
    records = tuple(DockerServiceEvidenceV1.from_mapping(item) for item in value)
    if tuple(item.service_id for item in records) != _DOCKER_SERVICE_IDS:
        raise ValueError("docker service identity/order is invalid")
    return records


@dataclass(frozen=True)
class FullStackAcceptanceReceiptV1:
    """Canonical summary of one actual DL-07 stack run.

    ``PASS`` is only possible after every local component and every externally
    required component has an actual passing receipt.  A deliberately absent
    remote Edge, provider credential, or device therefore remains
    ``ACCEPTANCE_PENDING_EXTERNAL`` instead of a synthetic success.
    """

    commit_sha: str
    run_id: str
    started_at_ms: int
    finished_at_ms: int
    configuration_digest: str
    docker_evidence_digest: str
    docker_services: tuple[DockerServiceEvidenceV1, ...]
    local_components: tuple[FullStackComponentV1, ...]
    external_components: tuple[FullStackComponentV1, ...]
    remote_ci_status: str
    outcome: str
    receipt_digest: str = ""
    schema_version: str = FULL_STACK_ACCEPTANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != FULL_STACK_ACCEPTANCE_SCHEMA_VERSION:
            raise ValueError("full-stack receipt schema version is invalid")
        commit_sha = _required_text(self.commit_sha, "commit_sha", limit=40)
        if _SHA_RE.fullmatch(commit_sha) is None:
            raise ValueError("commit_sha must be a full lowercase Git SHA")
        run_id = _required_text(self.run_id, "run_id", limit=160)
        if _RUN_ID_RE.fullmatch(run_id) is None:
            raise ValueError("run_id is invalid")
        started_at_ms = _non_negative_int(self.started_at_ms, "started_at_ms")
        finished_at_ms = _non_negative_int(self.finished_at_ms, "finished_at_ms")
        if finished_at_ms < started_at_ms:
            raise ValueError("finished_at_ms cannot precede started_at_ms")
        local = tuple(self.local_components)
        external = tuple(self.external_components)
        docker_services = tuple(self.docker_services)
        if tuple(item.service_id for item in docker_services) != _DOCKER_SERVICE_IDS:
            raise ValueError("docker service identity/order is invalid")
        if not all(isinstance(item, DockerServiceEvidenceV1) for item in docker_services):
            raise ValueError("docker services must use DockerServiceEvidenceV1")
        if tuple(item.component_id for item in local) != _LOCAL_COMPONENT_IDS:
            raise ValueError("local component identity/order is invalid")
        if tuple(item.component_id for item in external) != _EXTERNAL_COMPONENT_IDS:
            raise ValueError("external component identity/order is invalid")
        if not all(isinstance(item, FullStackComponentV1) for item in (*local, *external)):
            raise ValueError("full-stack components must use FullStackComponentV1")
        local_by_id = {item.component_id: item for item in local}
        for service in docker_services:
            if service.status != local_by_id[service.service_id].status:
                raise ValueError("docker service status differs from local component")
        remote_ci_status = _required_text(
            self.remote_ci_status,
            "remote_ci_status",
            limit=64,
        ).upper()
        if remote_ci_status not in _REMOTE_CI_STATUSES:
            raise ValueError("remote_ci_status is invalid")
        derived_outcome = derive_full_stack_outcome(
            local_components=local,
            external_components=external,
            remote_ci_status=remote_ci_status,
        )
        outcome = _required_text(self.outcome, "outcome", limit=64).upper()
        if outcome != derived_outcome:
            raise ValueError("outcome differs from component evidence")
        object.__setattr__(self, "commit_sha", commit_sha)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "started_at_ms", started_at_ms)
        object.__setattr__(self, "finished_at_ms", finished_at_ms)
        object.__setattr__(
            self,
            "configuration_digest",
            _safe_ref(self.configuration_digest, "configuration_digest"),
        )
        object.__setattr__(
            self,
            "docker_evidence_digest",
            _safe_ref(self.docker_evidence_digest, "docker_evidence_digest"),
        )
        object.__setattr__(self, "local_components", local)
        object.__setattr__(self, "external_components", external)
        object.__setattr__(self, "docker_services", docker_services)
        object.__setattr__(self, "remote_ci_status", remote_ci_status)
        object.__setattr__(self, "outcome", outcome)
        expected_digest = _sha256_ref(self._unsigned_dict())
        if self.receipt_digest and self.receipt_digest != expected_digest:
            raise ValueError("receipt_digest differs from canonical receipt")
        object.__setattr__(self, "receipt_digest", expected_digest)
        object.__setattr__(self, "schema_version", FULL_STACK_ACCEPTANCE_SCHEMA_VERSION)

    def _unsigned_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FULL_STACK_ACCEPTANCE_SCHEMA_VERSION,
            "commit_sha": self.commit_sha,
            "run_id": self.run_id,
            "started_at_ms": self.started_at_ms,
            "finished_at_ms": self.finished_at_ms,
            "configuration_digest": self.configuration_digest,
            "docker_evidence_digest": self.docker_evidence_digest,
            "docker_services": [item.as_dict() for item in self.docker_services],
            "local_components": [item.as_dict() for item in self.local_components],
            "external_components": [item.as_dict() for item in self.external_components],
            "remote_ci_status": self.remote_ci_status,
            "outcome": self.outcome,
            "privacy": {
                "contains_user_text": False,
                "contains_model_output": False,
                "contains_raw_audio_or_image": False,
                "contains_secrets": False,
            },
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FullStackAcceptanceReceiptV1":
        if not isinstance(value, Mapping):
            raise ValueError("full-stack receipt must be an object")
        _reject_forbidden_fields(value)
        expected = {
            "schema_version",
            "commit_sha",
            "run_id",
            "started_at_ms",
            "finished_at_ms",
            "configuration_digest",
            "docker_evidence_digest",
            "docker_services",
            "local_components",
            "external_components",
            "remote_ci_status",
            "outcome",
            "privacy",
            "receipt_digest",
        }
        if set(value) != expected:
            raise ValueError("full-stack receipt field set is invalid")
        if value.get("privacy") != {
            "contains_user_text": False,
            "contains_model_output": False,
            "contains_raw_audio_or_image": False,
            "contains_secrets": False,
        }:
            raise ValueError("full-stack receipt privacy declaration is invalid")
        receipt = cls(
            schema_version=value["schema_version"],
            commit_sha=value["commit_sha"],
            run_id=value["run_id"],
            started_at_ms=value["started_at_ms"],
            finished_at_ms=value["finished_at_ms"],
            configuration_digest=value["configuration_digest"],
            docker_evidence_digest=value["docker_evidence_digest"],
            docker_services=_docker_service_set(value["docker_services"]),
            local_components=_component_set(
                value["local_components"],
                field="local_components",
                expected_ids=_LOCAL_COMPONENT_IDS,
            ),
            external_components=_component_set(
                value["external_components"],
                field="external_components",
                expected_ids=_EXTERNAL_COMPONENT_IDS,
            ),
            remote_ci_status=value["remote_ci_status"],
            outcome=value["outcome"],
            receipt_digest=value["receipt_digest"],
        )
        return receipt

    def as_dict(self) -> dict[str, Any]:
        result = self._unsigned_dict()
        result["receipt_digest"] = self.receipt_digest
        return result


def derive_full_stack_outcome(
    *,
    local_components: Sequence[FullStackComponentV1],
    external_components: Sequence[FullStackComponentV1],
    remote_ci_status: str,
) -> str:
    """Derive the only three permitted end states from sealed components."""

    statuses = [item.status for item in (*local_components, *external_components)]
    if remote_ci_status == "FAIL" or _COMPONENT_FAIL in statuses:
        return FULL_STACK_ACCEPTANCE_FAIL
    if any(item.status != _COMPONENT_PASS for item in local_components):
        return FULL_STACK_ACCEPTANCE_FAIL
    if all(item.status == _COMPONENT_PASS for item in external_components):
        return FULL_STACK_ACCEPTANCE_PASS
    return FULL_STACK_ACCEPTANCE_PENDING_EXTERNAL


def _reject_forbidden_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            # Docker image identities and the explicit privacy boolean are
            # metadata, not a raw visual/audio payload.  All other sensitive
            # field names remain prohibited.
            if key_text not in {
                "image_id",
                "image_ref",
                "contains_model_output",
                "contains_raw_audio_or_image",
                "contains_secrets",
                "contains_user_text",
            } and _FORBIDDEN_FIELD_RE.search(key_text):
                raise ValueError("full-stack receipt contains forbidden field")
            _reject_forbidden_fields(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _reject_forbidden_fields(child)


__all__ = [
    "FULL_STACK_ACCEPTANCE_FAIL",
    "FULL_STACK_ACCEPTANCE_PASS",
    "FULL_STACK_ACCEPTANCE_PENDING_EXTERNAL",
    "FULL_STACK_ACCEPTANCE_SCHEMA_VERSION",
    "REMOTE_CI_NOT_RUN_ACCOUNT_QUOTA",
    "FullStackAcceptanceReceiptV1",
    "FullStackComponentV1",
    "DockerServiceEvidenceV1",
    "derive_full_stack_outcome",
]
