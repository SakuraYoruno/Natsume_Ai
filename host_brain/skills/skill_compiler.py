"""Deterministic, proposal-only SkillDraft compiler for NE-05."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


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
        if isinstance(self.risk_level, bool) or not isinstance(self.risk_level, int) or not 0 <= self.risk_level <= 5:
            raise SkillCompilerError("risk_level must be an integer within [0, 5]")
        for name in ("capability_dependencies", "tool_dependencies"):
            values = tuple(sorted({_text(item, f"{name}[]", 160) for item in getattr(self, name)}))
            object.__setattr__(self, name, values)
        object.__setattr__(self, "verifier_ref", _text(self.verifier_ref, "verifier_ref", 1_024))
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
        object.__setattr__(self, "rollback_target", _text(self.rollback_target, "rollback_target", 240))
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
            "compiler_version": self.compiler_version,
        }
        if include_digest:
            payload["draft_digest"] = self.draft_digest
        return payload


def compile_skill_draft(**kwargs: Any) -> SkillDraftV1:
    """Compile only typed metadata; never imports or activates source code."""

    return SkillDraftV1(**kwargs)


__all__ = ["SkillCompilerError", "SkillDraftV1", "compile_skill_draft"]
