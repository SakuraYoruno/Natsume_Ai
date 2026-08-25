"""Fail-closed supply-chain policy for external Skill/Provider bundles.

This module deliberately stops at verification and activation eligibility.  It
does not import, execute, install, copy, or otherwise activate bundle content.
Callers must re-run verification against the quarantined bytes immediately
before a later activation step.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from host_brain.core.runtime_helpers import same_file_state


ACTIVATION_MODE_ENV = "HOST_SUPPLY_CHAIN_ACTIVATION_MODE"
SIGNING_KEYS_ENV = "HOST_SUPPLY_CHAIN_HMAC_KEYS_JSON"
MANIFEST_FILENAME = "bundle-manifest.json"
SIGNATURE_FILENAME = "bundle-manifest.sig"
MANIFEST_SCHEMA_VERSION = "natsume.skill-provider-bundle.v1"

_ALGORITHM = "hmac-sha256"
_DIGEST_RE = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_SIGNATURE_RE = re.compile(r"\Ahmac-sha256:([0-9a-f]{64})\Z")
_NAME_RE = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,127}\Z")
_VERSION_RE = re.compile(r"\A[0-9A-Za-z][0-9A-Za-z.+_-]{0,127}\Z")
_KEY_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SECRET_REF_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:/-]{0,191}\Z")
_PUBLISHER_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}\Z")
_BUNDLE_KINDS = frozenset({"SKILL", "PROVIDER"})
_APPROVED_PROFILES = frozenset({"python-restricted-v1", "provider-restricted-v1"})
_JSON_MAX_BYTES = 4 * 1024 * 1024
_JSON_MAX_DEPTH = 64


class SupplyChainActivationMode(str, Enum):
    """The only supported external-bundle activation modes."""

    BUILTINS_ONLY = "BUILTINS_ONLY"
    QUARANTINE = "QUARANTINE"
    STAGED = "STAGED"


class BundleDisposition(str, Enum):
    REJECTED = "REJECTED"
    QUARANTINE_VERIFIED = "QUARANTINE_VERIFIED"
    STAGED_ELIGIBLE = "STAGED_ELIGIBLE"


class SupplyChainConfigurationError(ValueError):
    """Raised for invalid dedicated supply-chain signing-key configuration."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class TrustedSigningKey:
    """Dedicated signer trust statement, including its identity authority."""

    secret: bytes
    publisher_id: str
    allowed_bundle_kinds: frozenset[str]
    allowed_bundle_names: frozenset[str]


class _BundleRejected(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True)
class ActivationModeResolution:
    mode: SupplyChainActivationMode
    valid: bool
    explicitly_configured: bool


@dataclass(frozen=True)
class BundleVerificationDecision:
    """A non-executable verification result safe to pass across composition.

    ``allowed`` and ``activation_eligible`` are true only in STAGED mode after
    every check passes.  QUARANTINE success is represented by ``verified`` and
    ``must_quarantine`` while remaining non-activatable.
    """

    allowed: bool
    verified: bool
    must_quarantine: bool
    activation_eligible: bool
    disposition: BundleDisposition
    reason_code: str
    bundle_name: str | None
    bundle_version: str | None
    bundle_digest: str | None
    audit: Mapping[str, Any]


@dataclass(frozen=True)
class _ObservedFile:
    path: str
    size: int
    digest: str


def resolve_activation_mode(
    raw: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> ActivationModeResolution:
    """Resolve the feature flag; missing and invalid values fail to built-ins."""

    configured = raw
    if configured is None:
        source = os.environ if environ is None else environ
        configured = source.get(ACTIVATION_MODE_ENV)
    explicitly_configured = configured is not None and bool(str(configured).strip())
    value = str(configured or "").strip().upper()
    if not value:
        return ActivationModeResolution(
            mode=SupplyChainActivationMode.BUILTINS_ONLY,
            valid=True,
            explicitly_configured=False,
        )
    try:
        mode = SupplyChainActivationMode(value)
    except ValueError:
        return ActivationModeResolution(
            mode=SupplyChainActivationMode.BUILTINS_ONLY,
            valid=False,
            explicitly_configured=True,
        )
    return ActivationModeResolution(
        mode=mode,
        valid=True,
        explicitly_configured=explicitly_configured,
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic JSON bytes, rejecting non-finite numbers."""

    _reject_nonfinite(value)
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("SUPPLY_CHAIN_JSON_NOT_CANONICAL") from exc
    return text.encode("utf-8")


def compute_inventory_digest(files: Sequence[Mapping[str, Any]]) -> str:
    """Compute the v1 canonical full-file inventory digest."""

    normalized: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, Mapping):
            raise ValueError("SUPPLY_CHAIN_FILE_ENTRY_INVALID")
        path = _validate_relative_path(item.get("path"))
        size = item.get("size")
        digest = item.get("digest")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("SUPPLY_CHAIN_FILE_SIZE_INVALID")
        if not isinstance(digest, str) or _DIGEST_RE.fullmatch(digest) is None:
            raise ValueError("SUPPLY_CHAIN_FILE_DIGEST_INVALID")
        normalized.append({"path": path, "size": size, "digest": digest})
    normalized.sort(key=lambda entry: entry["path"].encode("utf-8"))
    return _sha256(canonical_json_bytes(normalized))


def load_trusted_signing_keys(
    raw: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Mapping[str, TrustedSigningKey]:
    """Load only the dedicated supply-chain key map.

    No SecureIngress, Gateway, session, or application secret is consulted.
    Every value is a strict trust statement containing a UTF-8 HMAC secret,
    one publisher identity, and non-empty kind/name authority allowlists.
    """

    configured = raw
    if configured is None:
        source = os.environ if environ is None else environ
        configured = source.get(SIGNING_KEYS_ENV)
    if configured is None or not str(configured).strip():
        return MappingProxyType({})
    try:
        payload = _strict_json_loads(str(configured), max_bytes=_JSON_MAX_BYTES)
    except _BundleRejected as exc:
        raise SupplyChainConfigurationError("SUPPLY_CHAIN_SIGNING_KEYS_JSON_INVALID") from exc
    if not isinstance(payload, dict) or not payload:
        raise SupplyChainConfigurationError("SUPPLY_CHAIN_SIGNING_KEYS_INVALID")

    return _normalize_explicit_keys(payload)


class SkillSupplyChainVerifier:
    """Verify a bundle without importing or executing any bundle content."""

    def __init__(
        self,
        *,
        mode: SupplyChainActivationMode | ActivationModeResolution | str | None = None,
        trusted_keys: Mapping[str, TrustedSigningKey | Mapping[str, Any]] | None = None,
        approved_sandbox_profiles: Sequence[str] = tuple(_APPROVED_PROFILES),
        max_files: int = 4096,
        max_directories: int = 4096,
        max_total_bytes: int = 512 * 1024 * 1024,
        max_file_bytes: int = 128 * 1024 * 1024,
    ) -> None:
        if isinstance(mode, ActivationModeResolution):
            self._mode_resolution = mode
        elif isinstance(mode, SupplyChainActivationMode):
            self._mode_resolution = ActivationModeResolution(mode, True, True)
        else:
            self._mode_resolution = resolve_activation_mode(mode)

        self._trusted_keys = _normalize_explicit_keys(trusted_keys or {})
        profiles = frozenset(str(item) for item in approved_sandbox_profiles)
        if not profiles or any(not _safe_token(item, maximum=128) for item in profiles):
            raise ValueError("SUPPLY_CHAIN_SANDBOX_PROFILE_CONFIG_INVALID")
        self._approved_profiles = profiles
        if (
            max_files < 1
            or max_directories < 1
            or max_total_bytes < 1
            or max_file_bytes < 1
        ):
            raise ValueError("SUPPLY_CHAIN_LIMIT_INVALID")
        self._max_files = int(max_files)
        self._max_directories = int(max_directories)
        self._max_total_bytes = int(max_total_bytes)
        self._max_file_bytes = int(max_file_bytes)

    @classmethod
    def from_environment(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        approved_sandbox_profiles: Sequence[str] = tuple(_APPROVED_PROFILES),
    ) -> "SkillSupplyChainVerifier":
        source = os.environ if environ is None else environ
        return cls(
            mode=resolve_activation_mode(environ=source),
            trusted_keys=load_trusted_signing_keys(environ=source),
            approved_sandbox_profiles=approved_sandbox_profiles,
        )

    @property
    def mode(self) -> SupplyChainActivationMode:
        return self._mode_resolution.mode

    @property
    def configuration_valid(self) -> bool:
        return self._mode_resolution.valid

    def verify_bundle(self, bundle_dir: str | os.PathLike[str]) -> BundleVerificationDecision:
        if not self._mode_resolution.valid:
            return self._reject("SUPPLY_CHAIN_ACTIVATION_MODE_INVALID")
        if self.mode is SupplyChainActivationMode.BUILTINS_ONLY:
            return self._reject("SUPPLY_CHAIN_EXTERNAL_BUNDLES_DISABLED")
        if not self._trusted_keys:
            return self._reject("SUPPLY_CHAIN_SIGNING_KEYS_UNAVAILABLE")

        manifest: dict[str, Any] | None = None
        observed_manifest_digest: str | None = None
        try:
            root = Path(bundle_dir)
            _validate_bundle_root(root)
            manifest_bytes = _read_bounded_regular_file(
                root / MANIFEST_FILENAME,
                root=root,
                maximum=_JSON_MAX_BYTES,
            )
            observed_manifest_digest = _sha256(manifest_bytes)
            manifest_value = _strict_json_loads_bytes(manifest_bytes, max_bytes=_JSON_MAX_BYTES)
            if not isinstance(manifest_value, dict):
                raise _BundleRejected("SUPPLY_CHAIN_MANIFEST_INVALID")
            manifest = manifest_value
            metadata = self._validate_manifest_shape(manifest)
            self._verify_signature(root=root, manifest=manifest, metadata=metadata)
            observed = self._observe_payload_files(root)
            declared = self._validate_declared_files(manifest["files"])
            if set(observed) != set(declared):
                raise _BundleRejected("SUPPLY_CHAIN_FILE_INVENTORY_MISMATCH")
            for relative_path, actual in observed.items():
                expected = declared[relative_path]
                if actual.size != expected.size or not hmac.compare_digest(actual.digest, expected.digest):
                    raise _BundleRejected("SUPPLY_CHAIN_FILE_DIGEST_MISMATCH")

            declared_digest = str(manifest["bundle_digest"])
            computed_digest = compute_inventory_digest(
                [
                    {"path": item.path, "size": item.size, "digest": item.digest}
                    for item in observed.values()
                ]
            )
            if not hmac.compare_digest(declared_digest, computed_digest):
                raise _BundleRejected("SUPPLY_CHAIN_BUNDLE_DIGEST_MISMATCH")
            self._validate_sbom(root=root, manifest=manifest, observed=observed)
            sandbox_summary = self._validate_sandbox(manifest["sandbox"])

            audit = _safe_audit(
                reason_code=(
                    "SUPPLY_CHAIN_QUARANTINE_VERIFIED"
                    if self.mode is SupplyChainActivationMode.QUARANTINE
                    else "SUPPLY_CHAIN_STAGED_ELIGIBLE"
                ),
                mode=self.mode,
                bundle_name=metadata["name"],
                bundle_version=metadata["version"],
                bundle_digest=declared_digest,
                manifest_digest=observed_manifest_digest,
                bundle_kind=metadata["bundle_kind"],
                publisher_id=metadata["publisher_id"],
                key_id=metadata["key_id"],
                signature_algorithm=_ALGORITHM,
                sbom_digest=str(manifest["sbom"]["digest"]),
                sandbox_profile_id=str(manifest["sandbox"]["profile"]),
                sandbox_profile_digest=_sha256(canonical_json_bytes(manifest["sandbox"])),
                file_count=len(observed),
                total_bytes=sum(item.size for item in observed.values()),
                sandbox_summary=sandbox_summary,
            )
            if self.mode is SupplyChainActivationMode.QUARANTINE:
                return BundleVerificationDecision(
                    allowed=False,
                    verified=True,
                    must_quarantine=True,
                    activation_eligible=False,
                    disposition=BundleDisposition.QUARANTINE_VERIFIED,
                    reason_code="SUPPLY_CHAIN_QUARANTINE_VERIFIED",
                    bundle_name=metadata["name"],
                    bundle_version=metadata["version"],
                    bundle_digest=declared_digest,
                    audit=MappingProxyType(audit),
                )
            return BundleVerificationDecision(
                allowed=True,
                verified=True,
                must_quarantine=True,
                activation_eligible=True,
                disposition=BundleDisposition.STAGED_ELIGIBLE,
                reason_code="SUPPLY_CHAIN_STAGED_ELIGIBLE",
                bundle_name=metadata["name"],
                bundle_version=metadata["version"],
                bundle_digest=declared_digest,
                audit=MappingProxyType(audit),
            )
        except _BundleRejected as exc:
            return self._reject(
                exc.reason_code,
                manifest=manifest,
                manifest_digest=observed_manifest_digest,
            )
        except (OSError, UnicodeError, ValueError, TypeError):
            return self._reject(
                "SUPPLY_CHAIN_BUNDLE_UNREADABLE",
                manifest=manifest,
                manifest_digest=observed_manifest_digest,
            )

    def _validate_manifest_shape(self, manifest: Mapping[str, Any]) -> dict[str, str]:
        required = {
            "schema_version",
            "name",
            "version",
            "bundle_kind",
            "publisher_id",
            "bundle_digest",
            "files",
            "sbom",
            "sandbox",
            "signing",
        }
        if set(manifest) != required:
            raise _BundleRejected("SUPPLY_CHAIN_MANIFEST_FIELDS_INVALID")
        if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise _BundleRejected("SUPPLY_CHAIN_MANIFEST_VERSION_UNSUPPORTED")
        name = manifest.get("name")
        version = manifest.get("version")
        bundle_kind = manifest.get("bundle_kind")
        publisher_id = manifest.get("publisher_id")
        digest = manifest.get("bundle_digest")
        if not isinstance(name, str) or _NAME_RE.fullmatch(name) is None:
            raise _BundleRejected("SUPPLY_CHAIN_BUNDLE_NAME_INVALID")
        if not isinstance(version, str) or _VERSION_RE.fullmatch(version) is None:
            raise _BundleRejected("SUPPLY_CHAIN_BUNDLE_VERSION_INVALID")
        if not isinstance(bundle_kind, str) or bundle_kind not in _BUNDLE_KINDS:
            raise _BundleRejected("SUPPLY_CHAIN_BUNDLE_KIND_INVALID")
        if (
            not isinstance(publisher_id, str)
            or _PUBLISHER_ID_RE.fullmatch(publisher_id) is None
        ):
            raise _BundleRejected("SUPPLY_CHAIN_PUBLISHER_ID_INVALID")
        if not isinstance(digest, str) or _DIGEST_RE.fullmatch(digest) is None:
            raise _BundleRejected("SUPPLY_CHAIN_BUNDLE_DIGEST_INVALID")
        if not isinstance(manifest.get("files"), list) or not manifest["files"]:
            raise _BundleRejected("SUPPLY_CHAIN_FILE_INVENTORY_INVALID")
        if not isinstance(manifest.get("sbom"), dict):
            raise _BundleRejected("SUPPLY_CHAIN_SBOM_INVALID")
        if not isinstance(manifest.get("sandbox"), dict):
            raise _BundleRejected("SUPPLY_CHAIN_SANDBOX_INVALID")
        signing = manifest.get("signing")
        if not isinstance(signing, dict) or set(signing) != {"algorithm", "key_id"}:
            raise _BundleRejected("SUPPLY_CHAIN_SIGNATURE_METADATA_INVALID")
        if signing.get("algorithm") != _ALGORITHM:
            raise _BundleRejected("SUPPLY_CHAIN_SIGNATURE_ALGORITHM_UNSUPPORTED")
        key_id = signing.get("key_id")
        if not isinstance(key_id, str) or _KEY_ID_RE.fullmatch(key_id) is None:
            raise _BundleRejected("SUPPLY_CHAIN_SIGNING_KEY_ID_INVALID")
        return {
            "name": name,
            "version": version,
            "bundle_kind": bundle_kind,
            "publisher_id": publisher_id,
            "key_id": key_id,
        }

    def _verify_signature(
        self,
        *,
        root: Path,
        manifest: Mapping[str, Any],
        metadata: Mapping[str, str],
    ) -> None:
        trust = self._trusted_keys.get(metadata["key_id"])
        if trust is None:
            raise _BundleRejected("SUPPLY_CHAIN_SIGNING_KEY_UNTRUSTED")
        signature_bytes = _read_bounded_regular_file(
            root / SIGNATURE_FILENAME,
            root=root,
            maximum=256,
        )
        try:
            signature_text = signature_bytes.decode("ascii")
        except UnicodeDecodeError as exc:
            raise _BundleRejected("SUPPLY_CHAIN_SIGNATURE_FORMAT_INVALID") from exc
        match = _SIGNATURE_RE.fullmatch(signature_text)
        if match is None:
            raise _BundleRejected("SUPPLY_CHAIN_SIGNATURE_FORMAT_INVALID")
        expected = hmac.new(
            trust.secret,
            canonical_json_bytes(manifest),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(match.group(1), expected):
            raise _BundleRejected("SUPPLY_CHAIN_SIGNATURE_INVALID")
        if (
            not hmac.compare_digest(trust.publisher_id, metadata["publisher_id"])
            or metadata["bundle_kind"] not in trust.allowed_bundle_kinds
            or metadata["name"] not in trust.allowed_bundle_names
        ):
            raise _BundleRejected("SUPPLY_CHAIN_SIGNER_SCOPE_FORBIDDEN")

    def _observe_payload_files(self, root: Path) -> dict[str, _ObservedFile]:
        files: dict[str, _ObservedFile] = {}
        collision_keys: set[str] = set()
        total_bytes = 0
        directory_count = 0
        stack = [root]
        while stack:
            directory = stack.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError as exc:
                raise _BundleRejected("SUPPLY_CHAIN_BUNDLE_UNREADABLE") from exc
            for entry in entries:
                path = Path(entry.path)
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise _BundleRejected("SUPPLY_CHAIN_BUNDLE_UNREADABLE") from exc
                if entry.is_symlink() or _is_reparse_point(metadata):
                    raise _BundleRejected("SUPPLY_CHAIN_SYMLINK_FORBIDDEN")
                if stat.S_ISDIR(metadata.st_mode):
                    directory_count += 1
                    if directory_count > self._max_directories:
                        raise _BundleRejected(
                            "SUPPLY_CHAIN_DIRECTORY_LIMIT_EXCEEDED"
                        )
                    stack.append(path)
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    raise _BundleRejected("SUPPLY_CHAIN_SPECIAL_FILE_FORBIDDEN")
                relative_path = path.relative_to(root).as_posix()
                relative_path = _validate_relative_path(relative_path)
                if relative_path in {MANIFEST_FILENAME, SIGNATURE_FILENAME}:
                    continue
                collision_key = _path_collision_key(relative_path)
                if collision_key in collision_keys:
                    raise _BundleRejected("SUPPLY_CHAIN_FILE_PATH_COLLISION")
                collision_keys.add(collision_key)
                if len(files) >= self._max_files:
                    raise _BundleRejected("SUPPLY_CHAIN_FILE_LIMIT_EXCEEDED")
                if metadata.st_size < 0 or metadata.st_size > self._max_file_bytes:
                    raise _BundleRejected("SUPPLY_CHAIN_FILE_SIZE_LIMIT_EXCEEDED")
                total_bytes += metadata.st_size
                if total_bytes > self._max_total_bytes:
                    raise _BundleRejected("SUPPLY_CHAIN_TOTAL_SIZE_LIMIT_EXCEEDED")
                raw, after = _read_stable_regular_file(path, before=metadata)
                if path.suffix.casefold() == ".json":
                    _strict_json_loads_bytes(raw, max_bytes=self._max_file_bytes)
                files[relative_path] = _ObservedFile(
                    path=relative_path,
                    size=after.st_size,
                    digest=_sha256(raw),
                )
        return files

    def _validate_declared_files(self, value: Any) -> dict[str, _ObservedFile]:
        if not isinstance(value, list) or not value or len(value) > self._max_files:
            raise _BundleRejected("SUPPLY_CHAIN_FILE_INVENTORY_INVALID")
        declared: dict[str, _ObservedFile] = {}
        collision_keys: set[str] = set()
        previous_sort_key: bytes | None = None
        for item in value:
            if not isinstance(item, dict) or set(item) != {"path", "size", "digest"}:
                raise _BundleRejected("SUPPLY_CHAIN_FILE_ENTRY_INVALID")
            path = _validate_relative_path(item.get("path"))
            if path in {MANIFEST_FILENAME, SIGNATURE_FILENAME}:
                raise _BundleRejected("SUPPLY_CHAIN_RESERVED_FILE_DECLARED")
            collision_key = _path_collision_key(path)
            if collision_key in collision_keys:
                raise _BundleRejected("SUPPLY_CHAIN_FILE_PATH_COLLISION")
            collision_keys.add(collision_key)
            size = item.get("size")
            digest = item.get("digest")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise _BundleRejected("SUPPLY_CHAIN_FILE_SIZE_INVALID")
            if not isinstance(digest, str) or _DIGEST_RE.fullmatch(digest) is None:
                raise _BundleRejected("SUPPLY_CHAIN_FILE_DIGEST_INVALID")
            sort_key = path.encode("utf-8")
            if previous_sort_key is not None and sort_key <= previous_sort_key:
                raise _BundleRejected("SUPPLY_CHAIN_FILE_INVENTORY_NOT_CANONICAL")
            previous_sort_key = sort_key
            declared[path] = _ObservedFile(path=path, size=size, digest=digest)
        return declared

    def _validate_sbom(
        self,
        *,
        root: Path,
        manifest: Mapping[str, Any],
        observed: Mapping[str, _ObservedFile],
    ) -> None:
        sbom = manifest["sbom"]
        if set(sbom) != {"format", "path", "digest"}:
            raise _BundleRejected("SUPPLY_CHAIN_SBOM_INVALID")
        sbom_format = sbom.get("format")
        if sbom_format not in {"cyclonedx-json", "spdx-json"}:
            raise _BundleRejected("SUPPLY_CHAIN_SBOM_FORMAT_UNSUPPORTED")
        sbom_path = _validate_relative_path(sbom.get("path"))
        if Path(sbom_path).suffix.casefold() != ".json" or sbom_path not in observed:
            raise _BundleRejected("SUPPLY_CHAIN_SBOM_MISSING")
        sbom_digest = sbom.get("digest")
        if not isinstance(sbom_digest, str) or _DIGEST_RE.fullmatch(sbom_digest) is None:
            raise _BundleRejected("SUPPLY_CHAIN_SBOM_DIGEST_INVALID")
        if not hmac.compare_digest(sbom_digest, observed[sbom_path].digest):
            raise _BundleRejected("SUPPLY_CHAIN_SBOM_DIGEST_MISMATCH")
        raw = _read_bounded_regular_file(
            root / Path(*PurePosixPath(sbom_path).parts),
            root=root,
            maximum=self._max_file_bytes,
        )
        document = _strict_json_loads_bytes(raw, max_bytes=self._max_file_bytes)
        if not isinstance(document, dict):
            raise _BundleRejected("SUPPLY_CHAIN_SBOM_INVALID")
        if sbom_format == "cyclonedx-json":
            if document.get("bomFormat") != "CycloneDX" or not isinstance(document.get("components"), list):
                raise _BundleRejected("SUPPLY_CHAIN_SBOM_INVALID")
        elif not isinstance(document.get("spdxVersion"), str) or not isinstance(document.get("packages"), list):
            raise _BundleRejected("SUPPLY_CHAIN_SBOM_INVALID")

    def _validate_sandbox(self, value: Any) -> dict[str, Any]:
        required = {"profile", "secrets", "network", "process", "workspace"}
        if not isinstance(value, dict) or set(value) != required:
            raise _BundleRejected("SUPPLY_CHAIN_SANDBOX_INVALID")
        profile = value.get("profile")
        if not isinstance(profile, str) or profile not in self._approved_profiles:
            raise _BundleRejected("SUPPLY_CHAIN_SANDBOX_PROFILE_UNAPPROVED")
        secrets = value.get("secrets")
        if not isinstance(secrets, list):
            raise _BundleRejected("SUPPLY_CHAIN_SECRET_SCOPE_INVALID")
        secret_ids: set[str] = set()
        for item in secrets:
            if not isinstance(item, str) or _SECRET_REF_RE.fullmatch(item) is None:
                raise _BundleRejected("SUPPLY_CHAIN_SECRET_SCOPE_INVALID")
            key = unicodedata.normalize("NFC", item).casefold()
            if key in secret_ids or key in {"*", "all", "host", "inherit"}:
                raise _BundleRejected("SUPPLY_CHAIN_SECRET_SCOPE_INVALID")
            secret_ids.add(key)
        network = value.get("network")
        if not isinstance(network, list):
            raise _BundleRejected("SUPPLY_CHAIN_NETWORK_SCOPE_INVALID")
        normalized_origins: set[str] = set()
        for origin in network:
            normalized = _validate_network_origin(origin)
            if normalized in normalized_origins:
                raise _BundleRejected("SUPPLY_CHAIN_NETWORK_SCOPE_INVALID")
            normalized_origins.add(normalized)
        if value.get("process") != "deny":
            raise _BundleRejected("SUPPLY_CHAIN_PROCESS_SCOPE_INVALID")
        workspace = value.get("workspace")
        if workspace not in {"none", "read_only"}:
            raise _BundleRejected("SUPPLY_CHAIN_WORKSPACE_SCOPE_INVALID")
        return {
            "profile": profile,
            "secret_ref_count": len(secret_ids),
            "network_origin_count": len(normalized_origins),
            "process": "deny",
            "workspace": workspace,
        }

    def _reject(
        self,
        reason_code: str,
        *,
        manifest: Mapping[str, Any] | None = None,
        manifest_digest: str | None = None,
    ) -> BundleVerificationDecision:
        name = _safe_manifest_text(manifest, "name", _NAME_RE)
        version = _safe_manifest_text(manifest, "version", _VERSION_RE)
        digest = _safe_manifest_text(manifest, "bundle_digest", _DIGEST_RE)
        audit = _safe_audit(
            reason_code=reason_code,
            mode=self.mode,
            bundle_name=name,
            bundle_version=version,
            bundle_digest=digest,
            manifest_digest=manifest_digest,
        )
        return BundleVerificationDecision(
            allowed=False,
            verified=False,
            must_quarantine=self.mode is not SupplyChainActivationMode.BUILTINS_ONLY,
            activation_eligible=False,
            disposition=BundleDisposition.REJECTED,
            reason_code=reason_code,
            bundle_name=name,
            bundle_version=version,
            bundle_digest=digest,
            audit=MappingProxyType(audit),
        )


def _strict_json_loads_bytes(raw: bytes, *, max_bytes: int) -> Any:
    if len(raw) > max_bytes:
        raise _BundleRejected("SUPPLY_CHAIN_JSON_SIZE_LIMIT_EXCEEDED")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _BundleRejected("SUPPLY_CHAIN_JSON_ENCODING_INVALID") from exc
    return _strict_json_loads(text, max_bytes=max_bytes)


def _strict_json_loads(text: str, *, max_bytes: int) -> Any:
    if len(text.encode("utf-8")) > max_bytes:
        raise _BundleRejected("SUPPLY_CHAIN_JSON_SIZE_LIMIT_EXCEEDED")

    def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        normalized: set[str] = set()
        for key, value in pairs:
            collision_key = unicodedata.normalize("NFC", key).casefold()
            if collision_key in normalized:
                raise _DuplicateJsonKey(key)
            normalized.add(collision_key)
            result[key] = value
        return result

    def _nonfinite(_: str) -> Any:
        raise ValueError("non-finite JSON number")

    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_nonfinite)
        _validate_json_depth(value)
        return value
    except RecursionError as exc:
        raise _BundleRejected("SUPPLY_CHAIN_JSON_DEPTH_EXCEEDED") from exc
    except (_DuplicateJsonKey, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise _BundleRejected("SUPPLY_CHAIN_JSON_INVALID") from exc


def _validate_json_depth(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > _JSON_MAX_DEPTH:
            raise _BundleRejected("SUPPLY_CHAIN_JSON_DEPTH_EXCEEDED")
        if isinstance(current, Mapping):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)


def _validate_bundle_root(root: Path) -> None:
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise _BundleRejected("SUPPLY_CHAIN_BUNDLE_UNREADABLE") from exc
    if root.is_symlink() or _is_reparse_point(metadata):
        raise _BundleRejected("SUPPLY_CHAIN_SYMLINK_FORBIDDEN")
    if not stat.S_ISDIR(metadata.st_mode):
        raise _BundleRejected("SUPPLY_CHAIN_BUNDLE_NOT_DIRECTORY")


def _read_bounded_regular_file(path: Path, *, root: Path, maximum: int) -> bytes:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise _BundleRejected("SUPPLY_CHAIN_PATH_ESCAPE") from exc
    _validate_relative_path(relative)
    try:
        before = path.lstat()
    except OSError as exc:
        raise _BundleRejected("SUPPLY_CHAIN_REQUIRED_FILE_MISSING") from exc
    if path.is_symlink() or _is_reparse_point(before):
        raise _BundleRejected("SUPPLY_CHAIN_SYMLINK_FORBIDDEN")
    if not stat.S_ISREG(before.st_mode):
        raise _BundleRejected("SUPPLY_CHAIN_SPECIAL_FILE_FORBIDDEN")
    if before.st_size < 0 or before.st_size > maximum:
        raise _BundleRejected("SUPPLY_CHAIN_FILE_SIZE_LIMIT_EXCEEDED")
    raw, _ = _read_stable_regular_file(path, before=before)
    if len(raw) > maximum:
        raise _BundleRejected("SUPPLY_CHAIN_FILE_SIZE_LIMIT_EXCEEDED")
    return raw


def _read_stable_regular_file(path: Path, *, before: os.stat_result) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or not same_file_state(before, opened):
                raise _BundleRejected("SUPPLY_CHAIN_FILE_CHANGED_DURING_VALIDATION")
            chunks: list[bytes] = []
            remaining = opened.st_size
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise _BundleRejected("SUPPLY_CHAIN_FILE_CHANGED_DURING_VALIDATION")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise _BundleRejected("SUPPLY_CHAIN_FILE_CHANGED_DURING_VALIDATION")
            after = os.fstat(descriptor)
            if not same_file_state(opened, after):
                raise _BundleRejected("SUPPLY_CHAIN_FILE_CHANGED_DURING_VALIDATION")
            return b"".join(chunks), after
        finally:
            os.close(descriptor)
    except _BundleRejected:
        raise
    except OSError as exc:
        raise _BundleRejected("SUPPLY_CHAIN_BUNDLE_UNREADABLE") from exc


def _is_reparse_point(metadata: os.stat_result) -> bool:
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0)
    return bool(flag and attributes & flag)


def _validate_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 1024:
        raise _BundleRejected("SUPPLY_CHAIN_FILE_PATH_INVALID")
    if value != unicodedata.normalize("NFC", value):
        raise _BundleRejected("SUPPLY_CHAIN_FILE_PATH_NOT_CANONICAL")
    if "\\" in value or "\x00" in value or any(ord(char) < 0x20 for char in value):
        raise _BundleRejected("SUPPLY_CHAIN_FILE_PATH_INVALID")
    pure = PurePosixPath(value)
    if pure.is_absolute() or value.startswith("/") or ":" in pure.parts[0]:
        raise _BundleRejected("SUPPLY_CHAIN_PATH_ESCAPE")
    if any(part in {"", ".", ".."} for part in pure.parts) or pure.as_posix() != value:
        raise _BundleRejected("SUPPLY_CHAIN_PATH_ESCAPE")
    return value


def _path_collision_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def _validate_network_origin(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 512 or value != value.strip():
        raise _BundleRejected("SUPPLY_CHAIN_NETWORK_SCOPE_INVALID")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise _BundleRejected("SUPPLY_CHAIN_NETWORK_SCOPE_INVALID") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise _BundleRejected("SUPPLY_CHAIN_NETWORK_SCOPE_INVALID")
    host = parsed.hostname.rstrip(".").casefold()
    if not host or host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise _BundleRejected("SUPPLY_CHAIN_NETWORK_SCOPE_INVALID")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "*" in host or "." not in host or any(not label for label in host.split(".")):
            raise _BundleRejected("SUPPLY_CHAIN_NETWORK_SCOPE_INVALID")
        if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
            raise _BundleRejected("SUPPLY_CHAIN_NETWORK_SCOPE_INVALID")
    else:
        if not address.is_global:
            raise _BundleRejected("SUPPLY_CHAIN_NETWORK_SCOPE_INVALID")
    return f"https://{host}{f':{port}' if port is not None else ''}"


def _normalize_explicit_keys(
    value: Mapping[str, TrustedSigningKey | Mapping[str, Any]],
) -> Mapping[str, TrustedSigningKey]:
    keys: dict[str, TrustedSigningKey] = {}
    normalized_ids: set[str] = set()
    fingerprints: set[str] = set()
    for key_id, trust_value in value.items():
        if not isinstance(key_id, str) or _KEY_ID_RE.fullmatch(key_id) is None:
            raise SupplyChainConfigurationError("SUPPLY_CHAIN_SIGNING_KEY_ID_INVALID")
        normalized = unicodedata.normalize("NFC", key_id).casefold()
        if normalized in normalized_ids:
            raise SupplyChainConfigurationError("SUPPLY_CHAIN_SIGNING_KEY_ID_COLLISION")
        trust = _normalize_trust_statement(trust_value)
        secret = trust.secret
        if not 32 <= len(secret) <= 4096:
            raise SupplyChainConfigurationError("SUPPLY_CHAIN_SIGNING_SECRET_INVALID")
        fingerprint = hashlib.sha256(secret).hexdigest()
        if fingerprint in fingerprints:
            raise SupplyChainConfigurationError("SUPPLY_CHAIN_SIGNING_SECRET_REUSED")
        keys[key_id] = trust
        normalized_ids.add(normalized)
        fingerprints.add(fingerprint)
    return MappingProxyType(keys)


def _normalize_trust_statement(
    value: TrustedSigningKey | Mapping[str, Any],
) -> TrustedSigningKey:
    if isinstance(value, TrustedSigningKey):
        raw_secret: Any = value.secret
        raw_publisher: Any = value.publisher_id
        raw_kinds: Any = value.allowed_bundle_kinds
        raw_names: Any = value.allowed_bundle_names
    elif isinstance(value, Mapping) and set(value) == {
        "secret",
        "publisher_id",
        "allowed_bundle_kinds",
        "allowed_bundle_names",
    }:
        raw_secret = value.get("secret")
        raw_publisher = value.get("publisher_id")
        raw_kinds = value.get("allowed_bundle_kinds")
        raw_names = value.get("allowed_bundle_names")
    else:
        raise SupplyChainConfigurationError("SUPPLY_CHAIN_SIGNING_TRUST_INVALID")

    if isinstance(raw_secret, str):
        if raw_secret != raw_secret.strip() or any(ord(char) < 0x20 for char in raw_secret):
            raise SupplyChainConfigurationError("SUPPLY_CHAIN_SIGNING_SECRET_INVALID")
        secret = raw_secret.encode("utf-8")
    elif isinstance(raw_secret, bytes):
        secret = bytes(raw_secret)
    else:
        raise SupplyChainConfigurationError("SUPPLY_CHAIN_SIGNING_SECRET_INVALID")
    if not 32 <= len(secret) <= 4096:
        raise SupplyChainConfigurationError("SUPPLY_CHAIN_SIGNING_SECRET_INVALID")

    if (
        not isinstance(raw_publisher, str)
        or _PUBLISHER_ID_RE.fullmatch(raw_publisher) is None
    ):
        raise SupplyChainConfigurationError("SUPPLY_CHAIN_SIGNING_PUBLISHER_INVALID")
    if (
        not isinstance(raw_kinds, (list, tuple, frozenset))
        or not raw_kinds
        or any(not isinstance(item, str) or item not in _BUNDLE_KINDS for item in raw_kinds)
    ):
        raise SupplyChainConfigurationError("SUPPLY_CHAIN_SIGNING_KIND_SCOPE_INVALID")
    kinds = frozenset(raw_kinds)
    if len(kinds) != len(raw_kinds):
        raise SupplyChainConfigurationError("SUPPLY_CHAIN_SIGNING_KIND_SCOPE_INVALID")
    if (
        not isinstance(raw_names, (list, tuple, frozenset))
        or not raw_names
        or any(
            not isinstance(item, str) or _NAME_RE.fullmatch(item) is None
            for item in raw_names
        )
    ):
        raise SupplyChainConfigurationError("SUPPLY_CHAIN_SIGNING_NAME_SCOPE_INVALID")
    names = frozenset(raw_names)
    if len(names) != len(raw_names):
        raise SupplyChainConfigurationError("SUPPLY_CHAIN_SIGNING_NAME_SCOPE_INVALID")
    return TrustedSigningKey(
        secret=secret,
        publisher_id=raw_publisher,
        allowed_bundle_kinds=kinds,
        allowed_bundle_names=names,
    )


def _safe_manifest_text(
    manifest: Mapping[str, Any] | None,
    key: str,
    pattern: re.Pattern[str],
) -> str | None:
    if not isinstance(manifest, Mapping):
        return None
    value = manifest.get(key)
    return value if isinstance(value, str) and pattern.fullmatch(value) is not None else None


def _safe_audit(
    *,
    reason_code: str,
    mode: SupplyChainActivationMode,
    bundle_name: str | None = None,
    bundle_version: str | None = None,
    bundle_digest: str | None = None,
    manifest_digest: str | None = None,
    bundle_kind: str | None = None,
    publisher_id: str | None = None,
    key_id: str | None = None,
    signature_algorithm: str | None = None,
    sbom_digest: str | None = None,
    sandbox_profile_id: str | None = None,
    sandbox_profile_digest: str | None = None,
    file_count: int | None = None,
    total_bytes: int | None = None,
    sandbox_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    audit: dict[str, Any] = {
        "event": "skill_supply_chain_verification",
        "mode": mode.value,
        "reason_code": reason_code,
        "bundle_name": bundle_name,
        "bundle_version": bundle_version,
        "bundle_digest": bundle_digest,
        "manifest_digest": manifest_digest,
    }
    if key_id is not None:
        audit["signing_key_id"] = key_id
    if bundle_kind is not None:
        audit["bundle_kind"] = bundle_kind
    if publisher_id is not None:
        audit["publisher_id"] = publisher_id
    if signature_algorithm is not None:
        audit["signature_algorithm"] = signature_algorithm
    if sbom_digest is not None:
        audit["sbom_digest"] = sbom_digest
    if sandbox_profile_id is not None:
        audit["sandbox_profile_id"] = sandbox_profile_id
    if sandbox_profile_digest is not None:
        audit["sandbox_profile_digest"] = sandbox_profile_digest
    if file_count is not None:
        audit["file_count"] = int(file_count)
    if total_bytes is not None:
        audit["total_bytes"] = int(total_bytes)
    if sandbox_summary is not None:
        audit["sandbox"] = dict(sandbox_summary)
    return audit


def _reject_nonfinite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("SUPPLY_CHAIN_JSON_NONFINITE")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("SUPPLY_CHAIN_JSON_KEY_INVALID")
            _reject_nonfinite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_nonfinite(item)


def _safe_token(value: str, *, maximum: int) -> bool:
    return bool(value) and len(value) <= maximum and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) is not None


def _sha256(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


__all__ = [
    "ACTIVATION_MODE_ENV",
    "SIGNING_KEYS_ENV",
    "MANIFEST_FILENAME",
    "SIGNATURE_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "SupplyChainActivationMode",
    "BundleDisposition",
    "SupplyChainConfigurationError",
    "TrustedSigningKey",
    "ActivationModeResolution",
    "BundleVerificationDecision",
    "SkillSupplyChainVerifier",
    "resolve_activation_mode",
    "load_trusted_signing_keys",
    "canonical_json_bytes",
    "compute_inventory_digest",
]
