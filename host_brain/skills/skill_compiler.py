"""Deterministic, proposal-only SkillDraft compiler for NE-05."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_DURABLE_REF_RE = re.compile(r"^[a-z][a-z0-9+.-]{1,31}://\S+$")
_ACCEPTED_TRACE_ALLOWED_KEYS = {
    "receipt_ref",
    "receipt_digest",
    "artifact_ref",
    "artifact_digest",
    "handler_adapter_id",
    "input_schema_ref",
    "output_schema_ref",
    "tool_ids",
    "verification_ref",
}
_SENSITIVE_TRACE_KEYS = {
    "raw_thought",
    "thought",
    "messages",
    "conversation",
    "body",
    "content",
    "secret",
    "token",
    "credential",
    "private_data",
}


class SkillCompilerError(ValueError):
    """A SkillDraft is incomplete or contains untrusted material."""


def _text(value: Any, field: str, limit: int = 240) -> str:
    result = str(value or "").strip()
    if not result or len(result) > limit:
        raise SkillCompilerError(f"{field} must be non-empty and bounded")
    return result


def _digest(value: Any, field: str) -> str:
    result = _text(value, field, 64).lower()
    if not _DIGEST_RE.fullmatch(result):
        raise SkillCompilerError(f"{field} must be a SHA-256 digest")
    return result


def _durable_ref(value: Any, field: str, limit: int = 1_024) -> str:
    result = _text(value, field, limit)
    if not _DURABLE_REF_RE.fullmatch(result):
        raise SkillCompilerError(f"{field} must be a durable reference")
    return result


def _copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise SkillCompilerError("SkillDraft payload must be JSON-safe") from exc


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SkillDraftV1:
    source_kind: str
    source_digest: str
    input_contract: Mapping[str, Any]
    output_contract: Mapping[str, Any]
    risk_level: int
    capability_dependencies: tuple[str, ...]
    tool_dependencies: tuple[str, ...]
    verifier_ref: str
    test_vectors: tuple[Mapping[str, Any], ...]
    budget_profile: Mapping[str, int]
    rollback_target: str
    source_ref: str = ""
    accepted_receipt_ref: str | None = None
    accepted_artifact_digest: str | None = None
    compiler_version: str = "skill-compiler-v1"
    draft_digest: str = ""

    def __post_init__(self) -> None:
        kind = _text(self.source_kind, "source_kind", 32).lower()
        if kind not in {"markdown", "api", "script", "accepted_trace"}:
            raise SkillCompilerError("unsupported SkillDraft source_kind")
        object.__setattr__(self, "source_kind", kind)
        object.__setattr__(self, "source_digest", _digest(self.source_digest, "source_digest"))
        for name in ("input_contract", "output_contract"):
            value = getattr(self, name)
            if not isinstance(value, Mapping) or not value:
                raise SkillCompilerError(f"{name} must be a non-empty mapping")
            object.__setattr__(self, name, _copy(dict(value)))
        if isinstance(self.risk_level, bool) or not isinstance(self.risk_level, int) or not 1 <= self.risk_level <= 5:
            raise SkillCompilerError("risk_level must be an integer within [1, 5]")
        for name in ("capability_dependencies", "tool_dependencies"):
            values = tuple(sorted({_text(item, f"{name}[]", 160) for item in getattr(self, name)}))
            object.__setattr__(self, name, values)
        object.__setattr__(
            self,
            "verifier_ref",
            _durable_ref(self.verifier_ref, "verifier_ref"),
        )
        vectors = tuple(_copy(dict(item)) for item in self.test_vectors if isinstance(item, Mapping))
        if not vectors:
            raise SkillCompilerError("SkillDraft requires at least one test vector")
        object.__setattr__(self, "test_vectors", vectors)
        if not isinstance(self.budget_profile, Mapping) or not self.budget_profile:
            raise SkillCompilerError("budget_profile must be non-empty")
        budget = {str(key): value for key, value in self.budget_profile.items()}
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in budget.values()):
            raise SkillCompilerError("budget_profile values must be positive integers")
        object.__setattr__(self, "budget_profile", budget)
        object.__setattr__(
            self,
            "rollback_target",
            _durable_ref(self.rollback_target, "rollback_target", 240),
        )
        source_ref = (
            _durable_ref(self.source_ref, "source_ref")
            if str(self.source_ref or "").strip()
            else f"source-digest://sha256/{self.source_digest}"
        )
        object.__setattr__(self, "source_ref", source_ref)
        accepted_ref = (
            _durable_ref(
                self.accepted_receipt_ref,
                "accepted_receipt_ref",
            )
            if self.accepted_receipt_ref is not None
            else None
        )
        artifact_digest = (
            _digest(
                self.accepted_artifact_digest,
                "accepted_artifact_digest",
            )
            if self.accepted_artifact_digest is not None
            else None
        )
        if kind == "accepted_trace" and (
            accepted_ref is None or artifact_digest is None
        ):
            raise SkillCompilerError(
                "accepted_trace requires accepted receipt and artifact digest"
            )
        if kind != "accepted_trace" and (
            accepted_ref is not None or artifact_digest is not None
        ):
            raise SkillCompilerError(
                "accepted receipt fields are exclusive to accepted_trace"
            )
        object.__setattr__(self, "accepted_receipt_ref", accepted_ref)
        object.__setattr__(
            self,
            "accepted_artifact_digest",
            artifact_digest,
        )
        object.__setattr__(self, "compiler_version", _text(self.compiler_version, "compiler_version", 80))
        identity = self.to_dict(include_digest=False)
        computed = _hash(identity)
        supplied = str(self.draft_digest or "").strip().lower()
        if supplied and supplied != computed:
            raise SkillCompilerError("draft_digest drift")
        object.__setattr__(self, "draft_digest", computed)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload = {
            "schema": "natsume.skill-draft.v1",
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "source_digest": self.source_digest,
            "input_contract": _copy(self.input_contract),
            "output_contract": _copy(self.output_contract),
            "risk_level": self.risk_level,
            "capability_dependencies": list(self.capability_dependencies),
            "tool_dependencies": list(self.tool_dependencies),
            "verifier_ref": self.verifier_ref,
            "test_vectors": [_copy(item) for item in self.test_vectors],
            "budget_profile": _copy(self.budget_profile),
            "rollback_target": self.rollback_target,
            "accepted_receipt_ref": self.accepted_receipt_ref,
            "accepted_artifact_digest": self.accepted_artifact_digest,
            "compiler_version": self.compiler_version,
        }
        if include_digest:
            payload["draft_digest"] = self.draft_digest
        return payload


def compile_skill_draft(**kwargs: Any) -> SkillDraftV1:
    """Compile only typed metadata; never imports or activates source code."""

    return SkillDraftV1(**kwargs)


@dataclass(frozen=True, slots=True)
class SkillBundleCompilationV1:
    """Deterministic unsigned bundle material; it never contains a private key."""

    skill_name: str
    bundle_version: str
    draft: SkillDraftV1
    source_descriptor: Mapping[str, Any]
    bundle_inventory: tuple[Mapping[str, Any], ...]
    sbom: Mapping[str, Any]
    signature_request: Mapping[str, Any]
    inventory_digest: str
    sbom_digest: str
    signature_request_digest: str
    bundle_digest: str
    compilation_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.skill-bundle-compilation.v1",
            "skill_name": self.skill_name,
            "bundle_version": self.bundle_version,
            "draft": self.draft.to_dict(),
            "source_descriptor": _copy(self.source_descriptor),
            "bundle_inventory": [
                _copy(item) for item in self.bundle_inventory
            ],
            "sbom": _copy(self.sbom),
            "signature_request": _copy(self.signature_request),
            "inventory_digest": self.inventory_digest,
            "sbom_digest": self.sbom_digest,
            "signature_request_digest": self.signature_request_digest,
            "bundle_digest": self.bundle_digest,
            "compilation_digest": self.compilation_digest,
            "contains_private_key": False,
        }


def compile_skill_bundle(
    *,
    skill_name: str,
    bundle_version: str,
    source_kind: str,
    source_ref: str,
    source_material: str | bytes | Mapping[str, Any],
    input_contract: Mapping[str, Any],
    output_contract: Mapping[str, Any],
    risk_level: int,
    capability_dependencies: Sequence[str] = (),
    tool_dependencies: Sequence[str] = (),
    verifier_ref: str,
    test_vectors: Sequence[Mapping[str, Any]],
    budget_profile: Mapping[str, int],
    rollback_target: str,
    accepted_receipt: Mapping[str, Any] | None = None,
    signing_key_purpose: str = "natsume-skill-bundle",
) -> SkillBundleCompilationV1:
    """Compile source metadata into an unsigned, content-addressed bundle.

    The source body is hashed and discarded.  Accepted traces are restricted
    to receipt/artifact metadata so thoughts, conversation bodies and secrets
    cannot enter a draft.
    """

    name = _text(skill_name, "skill_name", 160)
    version = _text(bundle_version, "bundle_version", 128)
    kind = _text(source_kind, "source_kind", 32).lower()
    durable_source_ref = _durable_ref(source_ref, "source_ref")
    material = _source_material(kind, source_material)
    source_digest = _hash(material)
    accepted_ref: str | None = None
    accepted_artifact_digest: str | None = None
    if kind == "accepted_trace":
        accepted = _accepted_trace_receipt(
            accepted_receipt,
            source_material=material,
        )
        accepted_ref = accepted["receipt_ref"]
        accepted_artifact_digest = accepted["artifact_digest"]
    elif accepted_receipt is not None:
        raise SkillCompilerError(
            "accepted_receipt is exclusive to accepted_trace"
        )

    draft = SkillDraftV1(
        source_kind=kind,
        source_ref=durable_source_ref,
        source_digest=source_digest,
        input_contract=input_contract,
        output_contract=output_contract,
        risk_level=risk_level,
        capability_dependencies=tuple(capability_dependencies),
        tool_dependencies=tuple(tool_dependencies),
        verifier_ref=verifier_ref,
        test_vectors=tuple(test_vectors),
        budget_profile=budget_profile,
        rollback_target=rollback_target,
        accepted_receipt_ref=accepted_ref,
        accepted_artifact_digest=accepted_artifact_digest,
    )
    source_descriptor = {
        "schema": "natsume.skill-source-descriptor.v1",
        "source_kind": kind,
        "source_ref": durable_source_ref,
        "source_digest": source_digest,
        "accepted_receipt_ref": accepted_ref,
        "accepted_artifact_digest": accepted_artifact_digest,
        "source_body_retained": False,
    }
    draft_json = _canonical_bytes(draft.to_dict())
    source_json = _canonical_bytes(source_descriptor)
    preliminary_inventory = (
        _inventory_entry("skill-draft.json", draft_json),
        _inventory_entry("source-descriptor.json", source_json),
    )
    dependencies = sorted(
        {
            *draft.capability_dependencies,
            *draft.tool_dependencies,
        }
    )
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": name,
                "version": version,
                "bom-ref": f"skill:{name}@{version}",
            },
            "properties": [
                {
                    "name": "natsume:draft-digest",
                    "value": draft.draft_digest,
                },
                {
                    "name": "natsume:source-digest",
                    "value": source_digest,
                },
            ],
        },
        "components": [
            {
                "type": "library",
                "name": dependency,
                "version": "authority-resolved",
                "bom-ref": f"dependency:{dependency}",
            }
            for dependency in dependencies
        ],
    }
    sbom_json = _canonical_bytes(sbom)
    inventory = tuple(
        sorted(
            (
                *preliminary_inventory,
                _inventory_entry("sbom.cdx.json", sbom_json),
            ),
            key=lambda item: str(item["path"]),
        )
    )
    inventory_digest = _hash(list(inventory))
    sbom_digest = _hash(sbom)
    signature_request = {
        "schema": "natsume.skill-signature-request.v1",
        "skill_name": name,
        "bundle_version": version,
        "draft_digest": draft.draft_digest,
        "inventory_digest": inventory_digest,
        "sbom_digest": sbom_digest,
        "algorithm": "ED25519",
        "key_purpose": _text(
            signing_key_purpose,
            "signing_key_purpose",
            128,
        ),
        "private_key_included": False,
    }
    signature_request_digest = _hash(signature_request)
    bundle_identity = {
        "skill_name": name,
        "bundle_version": version,
        "draft_digest": draft.draft_digest,
        "inventory_digest": inventory_digest,
        "sbom_digest": sbom_digest,
        "signature_request_digest": signature_request_digest,
    }
    bundle_digest = f"sha256:{_hash(bundle_identity)}"
    compilation_digest = _hash(
        {
            "schema": "natsume.skill-bundle-compilation.v1",
            **bundle_identity,
            "bundle_digest": bundle_digest,
        }
    )
    return SkillBundleCompilationV1(
        skill_name=name,
        bundle_version=version,
        draft=draft,
        source_descriptor=source_descriptor,
        bundle_inventory=inventory,
        sbom=sbom,
        signature_request=signature_request,
        inventory_digest=inventory_digest,
        sbom_digest=sbom_digest,
        signature_request_digest=signature_request_digest,
        bundle_digest=bundle_digest,
        compilation_digest=compilation_digest,
    )


def _source_material(
    kind: str,
    source: str | bytes | Mapping[str, Any],
) -> str | Mapping[str, Any]:
    if kind not in {"markdown", "api", "script", "accepted_trace"}:
        raise SkillCompilerError("unsupported SkillDraft source_kind")
    if kind == "accepted_trace":
        if not isinstance(source, Mapping) or not source:
            raise SkillCompilerError(
                "accepted_trace source must be receipt metadata"
            )
        material = _copy(dict(source))
        keys = {str(key) for key in material}
        if keys - _ACCEPTED_TRACE_ALLOWED_KEYS:
            raise SkillCompilerError(
                "accepted_trace contains non-reference fields"
            )
        if keys & _SENSITIVE_TRACE_KEYS:
            raise SkillCompilerError(
                "accepted_trace contains private or thought material"
            )
        return material
    if isinstance(source, Mapping):
        if kind != "api":
            raise SkillCompilerError(
                f"{kind} source must be text or bytes"
            )
        return _copy(dict(source))
    if isinstance(source, bytes):
        try:
            text = source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillCompilerError(
                "source material must be UTF-8"
            ) from exc
    else:
        text = str(source or "")
    if not text:
        raise SkillCompilerError("source material must be non-empty")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _accepted_trace_receipt(
    value: Mapping[str, Any] | None,
    *,
    source_material: str | Mapping[str, Any],
) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise SkillCompilerError(
            "accepted_trace requires an authoritative receipt"
        )
    status = str(
        value.get("acceptance_status") or value.get("status") or ""
    ).strip().upper()
    receipt_ref = _durable_ref(
        value.get("receipt_ref"),
        "accepted_receipt.receipt_ref",
    )
    receipt_digest = _digest(
        value.get("receipt_digest"),
        "accepted_receipt.receipt_digest",
    )
    artifact_digest = _digest(
        value.get("artifact_digest"),
        "accepted_receipt.artifact_digest",
    )
    if status not in {"ACCEPTED", "COMMITTED"}:
        raise SkillCompilerError("accepted trace receipt is not accepted")
    material = dict(source_material)
    if (
        str(material.get("receipt_ref") or "") != receipt_ref
        or str(material.get("receipt_digest") or "").lower()
        != receipt_digest
        or str(material.get("artifact_digest") or "").lower()
        != artifact_digest
    ):
        raise SkillCompilerError(
            "accepted trace metadata drifts from receipt"
        )
    return {
        "receipt_ref": receipt_ref,
        "receipt_digest": receipt_digest,
        "artifact_digest": artifact_digest,
    }


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _inventory_entry(path: str, content: bytes) -> dict[str, Any]:
    return {
        "path": path,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }


__all__ = [
    "SkillBundleCompilationV1",
    "SkillCompilerError",
    "SkillDraftV1",
    "compile_skill_bundle",
    "compile_skill_draft",
]
