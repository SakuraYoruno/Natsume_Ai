"""Tool metadata SSOT and visibility hash contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any


TOOL_META_REQUIRED_KEYS = (
    "id",
    "name",
    "desc",
    "schema_hash",
    "side_effect_level",
    "auth_level",
    "domain_tags",
    "version",
)

TOOL_META_ALLOWED_KEYS = set(TOOL_META_REQUIRED_KEYS)
SIDE_EFFECT_LEVELS = {"READ", "WRITE"}
AUTH_LEVELS = {"NONE", "APPROVAL", "CONFIRM"}
# 兼容 MCP 等来源的别名：LOW/MEDIUM/HIGH -> NONE/APPROVAL/CONFIRM
AUTH_LEVEL_ALIASES = {"LOW": "NONE", "MEDIUM": "APPROVAL", "HIGH": "CONFIRM"}


@dataclass(frozen=True)
class ToolMeta:
    """Stable tool metadata contract exposed to the model."""

    id: str
    name: str
    desc: str
    schema_hash: str
    side_effect_level: str
    auth_level: str
    domain_tags: tuple[str, ...]
    version: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ToolMeta":
        normalized = normalize_tool_meta(payload)
        return cls(
            id=normalized["id"],
            name=normalized["name"],
            desc=normalized["desc"],
            schema_hash=normalized["schema_hash"],
            side_effect_level=normalized["side_effect_level"],
            auth_level=normalized["auth_level"],
            domain_tags=tuple(normalized["domain_tags"]),
            version=normalized["version"],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "desc": self.desc,
            "schema_hash": self.schema_hash,
            "side_effect_level": self.side_effect_level,
            "auth_level": self.auth_level,
            "domain_tags": list(self.domain_tags),
            "version": self.version,
        }


class ToolCatalog:
    """In-memory metadata registry used by context/tool selector contracts."""

    def __init__(self, tools: list[ToolMeta] | None = None) -> None:
        self._tools: dict[str, ToolMeta] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: ToolMeta | dict[str, Any]) -> None:
        meta = tool if isinstance(tool, ToolMeta) else ToolMeta.from_dict(tool)
        self._tools[meta.id] = meta

    def get(self, tool_id: str) -> ToolMeta:
        if tool_id not in self._tools:
            raise KeyError(f"tool not found: {tool_id}")
        return self._tools[tool_id]

    def list_all(self) -> list[ToolMeta]:
        return [self._tools[tool_id] for tool_id in sorted(self._tools.keys())]

    def export_meta(self, *, include_write: bool = True) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for meta in self.list_all():
            if not include_write and meta.side_effect_level == "WRITE":
                continue
            result.append(meta.as_dict())
        return result


def normalize_tool_meta(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a metadata object."""

    if not isinstance(payload, dict):
        raise ValueError("tool meta must be object")
    missing = [key for key in TOOL_META_REQUIRED_KEYS if key not in payload]
    if missing:
        raise ValueError(f"tool meta missing required keys: {missing}")
    unknown = sorted(set(payload.keys()) - TOOL_META_ALLOWED_KEYS)
    if unknown:
        raise ValueError(f"tool meta contains unknown keys: {unknown}")

    normalized = {
        "id": str(payload["id"]).strip(),
        "name": str(payload["name"]).strip(),
        "desc": str(payload["desc"]).strip(),
        "schema_hash": str(payload["schema_hash"]).strip(),
        "side_effect_level": str(payload["side_effect_level"]).strip().upper(),
        "auth_level": str(payload["auth_level"]).strip().upper(),
        "domain_tags": _normalize_tags(payload["domain_tags"]),
        "version": str(payload["version"]).strip(),
    }

    for key in ("id", "name", "desc", "schema_hash", "version"):
        if not normalized[key]:
            raise ValueError(f"tool meta {key} must be non-empty")
    if not normalized["schema_hash"].startswith("sha256:"):
        raise ValueError("tool meta schema_hash must start with sha256:")
    if normalized["side_effect_level"] not in SIDE_EFFECT_LEVELS:
        raise ValueError(f"unsupported side_effect_level: {normalized['side_effect_level']}")
    raw_auth = normalized["auth_level"]
    if raw_auth in AUTH_LEVEL_ALIASES:
        normalized["auth_level"] = AUTH_LEVEL_ALIASES[raw_auth]
    if normalized["auth_level"] not in AUTH_LEVELS:
        raise ValueError(f"unsupported auth_level: {raw_auth}")
    if not normalized["domain_tags"]:
        raise ValueError("tool meta domain_tags must be non-empty list[str]")
    return normalized


def compute_tool_visibility_hash(tool_meta_pack: list[dict[str, Any]]) -> str:
    """Compute stable visibility hash for selected tool metadata subset."""

    canonical_pack: list[dict[str, Any]] = []
    for item in tool_meta_pack:
        canonical_pack.append(normalize_tool_meta(item))
    canonical_pack.sort(key=lambda row: (row["id"], row["version"]))
    canonical_json = json.dumps(
        canonical_pack,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()}"


def _normalize_tags(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError("tool meta domain_tags must be list")
    tags: list[str] = []
    for item in raw:
        tag = str(item).strip().lower()
        if tag and tag not in tags:
            tags.append(tag)
    return tags
