from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from host_brain.persona.response_form_contract import (
    is_pure_media_response_form,
    normalize_response_form,
)


@dataclass(frozen=True)
class ReactionAsset:
    reaction_id: str
    emotion: str = "unknown"
    response_form: str = "image"
    media_ref: str = ""
    content: str = ""
    platforms: tuple[str, ...] = ()
    intensity_min: float = 0.0
    intensity_max: float = 1.0
    soul_id: str = ""
    raw_extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reaction_id": self.reaction_id,
            "emotion": self.emotion,
            "response_form": self.response_form,
            "media_ref": self.media_ref,
            "content": self.content,
            "platforms": list(self.platforms),
            "intensity_min": float(self.intensity_min),
            "intensity_max": float(self.intensity_max),
            "soul_id": self.soul_id,
            "raw_extra": dict(self.raw_extra or {}),
        }


def _normalize_text(raw: Any) -> str:
    return str(raw or "").strip()


def _normalize_platforms(raw: Any) -> tuple[str, ...]:
    values: list[str] = []
    for item in list(raw or []):
        value = _normalize_text(item).lower()
        if value and value not in values:
            values.append(value)
    return tuple(values)


def _to_float(raw: Any, default: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    return max(0.0, min(value, 1.0))


def _asset_from_mapping(raw: Mapping[str, Any]) -> ReactionAsset | None:
    reaction_id = _normalize_text(raw.get("reaction_id") or raw.get("id"))
    media_ref = _normalize_text(
        raw.get("media_ref")
        or raw.get("media_path")
        or raw.get("url")
        or raw.get("sticker")
        or raw.get("gif")
        or raw.get("image")
    )
    content = _normalize_text(raw.get("content"))
    response_form = normalize_response_form(_normalize_text(raw.get("response_form") or raw.get("form") or "image"))
    if not reaction_id:
        return None
    if not is_pure_media_response_form(response_form):
        return None
    if not media_ref:
        return None
    return ReactionAsset(
        reaction_id=reaction_id,
        emotion=_normalize_text(raw.get("emotion") or "unknown").lower() or "unknown",
        response_form=response_form,
        media_ref=media_ref,
        content=content,
        platforms=_normalize_platforms(raw.get("platforms") or raw.get("platform")),
        intensity_min=_to_float(raw.get("intensity_min"), 0.0),
        intensity_max=_to_float(raw.get("intensity_max"), 1.0),
        soul_id=_normalize_text(raw.get("soul_id")),
        raw_extra=dict(raw.get("raw_extra") or {}),
    )


class ReactionCatalog:
    """Selects sendable reaction assets for media-first social replies.

    Missing catalog or no matching asset intentionally returns None. The caller
    should emit no_reply rather than synthesize fallback text.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        default_path = Path(__file__).resolve().parents[2] / "config" / "reaction_catalog.json"
        self.path = Path(
            path
            or os.environ.get("HOST_REACTION_CATALOG_PATH")
            or default_path
        )
        self.assets = self._load_assets(self.path)

    @staticmethod
    def _load_assets(path: Path) -> tuple[ReactionAsset, ...]:
        try:
            if not path.exists():
                return ()
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return ()
        raw_assets = payload.get("assets") if isinstance(payload, Mapping) else payload
        assets: list[ReactionAsset] = []
        for item in list(raw_assets or []):
            if isinstance(item, Mapping):
                asset = _asset_from_mapping(item)
                if asset is not None:
                    assets.append(asset)
        return tuple(assets)

    def select(
        self,
        *,
        platform: str,
        emotion: str,
        intensity: float = 0.0,
        soul_id: str = "",
    ) -> ReactionAsset | None:
        normalized_platform = _normalize_text(platform).lower()
        normalized_emotion = _normalize_text(emotion or "unknown").lower() or "unknown"
        normalized_soul_id = _normalize_text(soul_id)
        bounded_intensity = _to_float(intensity, 0.0)
        candidates: list[ReactionAsset] = []
        fallback_candidates: list[ReactionAsset] = []
        for asset in self.assets:
            if asset.platforms and normalized_platform not in asset.platforms:
                continue
            if asset.soul_id and normalized_soul_id and asset.soul_id != normalized_soul_id:
                continue
            if not (asset.intensity_min <= bounded_intensity <= asset.intensity_max):
                continue
            if asset.emotion == normalized_emotion:
                candidates.append(asset)
            elif asset.emotion == "unknown":
                fallback_candidates.append(asset)
        return (candidates or fallback_candidates or [None])[0]


_CATALOG: ReactionCatalog | None = None


def get_reaction_catalog() -> ReactionCatalog:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = ReactionCatalog()
    return _CATALOG


def reset_reaction_catalog_for_tests() -> None:
    global _CATALOG
    _CATALOG = None


__all__ = [
    "ReactionAsset",
    "ReactionCatalog",
    "get_reaction_catalog",
    "reset_reaction_catalog_for_tests",
]
