"""Key profile contracts for secret-ref based runtime key resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from host_brain.core.errors import (
    KEY_PROFILE_EXPIRED,
    KEY_PROFILE_SCOPE_INVALID,
    KEY_PROFILE_SECRET_REF_MISSING,
)


KEY_PROFILE_STATUS_ACTIVE = "active"
ALLOWED_KEY_PROFILE_STATUS = {KEY_PROFILE_STATUS_ACTIVE, "disabled", "expired"}


@dataclass(frozen=True)
class KeyProfile:
    provider: str
    node_id: str
    scope: str
    secret_ref: str
    rotation_epoch: int
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "node_id": self.node_id,
            "scope": self.scope,
            "secret_ref": self.secret_ref,
            "rotation_epoch": self.rotation_epoch,
            "status": self.status,
        }


@dataclass(frozen=True)
class KeyProfileResolveResult:
    success: bool
    reason_code: str | None
    detail: str
    secret_value: str | None
    secret_ref: str | None
    profile: KeyProfile | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "secret_value_set": bool(self.secret_value),
            "secret_ref": self.secret_ref,
            "profile": (self.profile.as_dict() if self.profile is not None else None),
        }


def normalize_key_profiles(
    key_profiles: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for profile in _to_profiles(key_profiles):
        normalized.append(profile.as_dict())
    normalized.sort(
        key=lambda row: (
            str(row["provider"]),
            str(row["node_id"]),
            str(row["scope"]),
            str(row["secret_ref"]),
        )
    )
    return normalized


def resolve_secret_from_profiles(
    *,
    provider: str,
    node_id: str,
    scope: str,
    key_profiles: Sequence[Mapping[str, Any]] | None,
    secret_store: Mapping[str, Any] | None,
    current_epoch: int,
) -> KeyProfileResolveResult:
    provider_value = str(provider or "").strip().lower()
    node_value = str(node_id or "").strip().lower()
    scope_value = str(scope or "").strip().lower()
    profiles = _to_profiles(key_profiles)

    provider_matches = [
        profile
        for profile in profiles
        if profile.provider.lower() == provider_value and profile.node_id.lower() == node_value
    ]
    if not provider_matches:
        return KeyProfileResolveResult(
            success=False,
            reason_code=KEY_PROFILE_SECRET_REF_MISSING,
            detail=f"key profile not found for provider={provider_value}, node_id={node_value}",
            secret_value=None,
            secret_ref=None,
            profile=None,
        )

    scope_matches = [profile for profile in provider_matches if profile.scope.lower() == scope_value]
    if not scope_matches:
        return KeyProfileResolveResult(
            success=False,
            reason_code=KEY_PROFILE_SCOPE_INVALID,
            detail=(
                f"scope mismatch for provider={provider_value}, node_id={node_value}, "
                f"expected_scope={scope_value}"
            ),
            secret_value=None,
            secret_ref=None,
            profile=None,
        )

    profile = _pick_latest(scope_matches)
    if profile.status.lower() != KEY_PROFILE_STATUS_ACTIVE:
        return KeyProfileResolveResult(
            success=False,
            reason_code=KEY_PROFILE_EXPIRED,
            detail=f"key profile inactive status={profile.status}",
            secret_value=None,
            secret_ref=profile.secret_ref,
            profile=profile,
        )
    if int(profile.rotation_epoch) < int(current_epoch):
        return KeyProfileResolveResult(
            success=False,
            reason_code=KEY_PROFILE_EXPIRED,
            detail=(
                "key profile rotation expired: "
                f"rotation_epoch={profile.rotation_epoch}, current_epoch={int(current_epoch)}"
            ),
            secret_value=None,
            secret_ref=profile.secret_ref,
            profile=profile,
        )
    if not profile.secret_ref:
        return KeyProfileResolveResult(
            success=False,
            reason_code=KEY_PROFILE_SECRET_REF_MISSING,
            detail="key profile secret_ref missing",
            secret_value=None,
            secret_ref=None,
            profile=profile,
        )

    store = dict(secret_store or {})
    secret_raw = store.get(profile.secret_ref)
    secret_value = str(secret_raw or "").strip()
    if not secret_value:
        return KeyProfileResolveResult(
            success=False,
            reason_code=KEY_PROFILE_SECRET_REF_MISSING,
            detail=f"secret ref not found in runtime store: {profile.secret_ref}",
            secret_value=None,
            secret_ref=profile.secret_ref,
            profile=profile,
        )

    return KeyProfileResolveResult(
        success=True,
        reason_code=None,
        detail="secret resolved from key profile",
        secret_value=secret_value,
        secret_ref=profile.secret_ref,
        profile=profile,
    )


def _to_profiles(key_profiles: Sequence[Mapping[str, Any]] | None) -> list[KeyProfile]:
    profiles: list[KeyProfile] = []
    for raw in key_profiles or ():
        profiles.append(
            KeyProfile(
                provider=str(raw.get("provider") or "").strip(),
                node_id=str(raw.get("node_id") or "").strip(),
                scope=str(raw.get("scope") or "").strip(),
                secret_ref=str(raw.get("secret_ref") or "").strip(),
                rotation_epoch=_to_int(raw.get("rotation_epoch"), default=0),
                status=_normalize_status(raw.get("status")),
            )
        )
    return profiles


def _pick_latest(profiles: list[KeyProfile]) -> KeyProfile:
    ordered = sorted(profiles, key=lambda profile: int(profile.rotation_epoch), reverse=True)
    return ordered[0]


def _to_int(raw: Any, *, default: int) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return int(default)


def _normalize_status(raw: Any) -> str:
    status = str(raw or KEY_PROFILE_STATUS_ACTIVE).strip().lower()
    if status not in ALLOWED_KEY_PROFILE_STATUS:
        return KEY_PROFILE_STATUS_ACTIVE
    return status
