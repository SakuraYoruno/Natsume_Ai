from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Protocol

from host_brain.security_immune.models import AssetSnapshot, now_ms


def _stable_asset_id(hostname: str) -> str:
    digest = hashlib.sha256(hostname.lower().encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"host-{digest}"


@dataclass(frozen=True)
class ProviderResult:
    provider_id: str
    status: str
    assets: list[AssetSnapshot] = field(default_factory=list)
    detail: str = ""
    metadata: dict[str, object] = field(default_factory=dict)
    ts_ms: int = field(default_factory=now_ms)

    def as_dict(self) -> dict[str, object]:
        signal_count = sum(len(asset.signals) for asset in self.assets)
        return {
            "provider_id": self.provider_id,
            "status": self.status,
            "asset_count": len(self.assets),
            "signal_count": signal_count,
            "detail": self.detail,
            "metadata": dict(self.metadata),
            "ts_ms": int(self.ts_ms),
        }


class SecuritySignalProvider(Protocol):
    provider_id: str

    def collect_assets(self) -> ProviderResult:
        """Collect authorized endpoint evidence and return normalized assets."""
