"""Registry for Bio-Ops proposal-only capabilities."""

from __future__ import annotations

from host_brain.bio_ops.capabilities import BioOpsCapabilitySpec, list_capability_specs


class BioOpsRegistry:
    def __init__(self, specs: list[BioOpsCapabilitySpec] | tuple[BioOpsCapabilitySpec, ...]) -> None:
        self._specs = {spec.capability: spec for spec in specs}

    def list_capabilities(self) -> list[dict[str, str]]:
        return [
            {
                "capability": spec.capability,
                "group": spec.group,
                "owner_organ": spec.owner_organ,
                "output_kind": spec.output_kind,
            }
            for spec in sorted(self._specs.values(), key=lambda item: item.capability)
        ]

    def get(self, capability: str) -> BioOpsCapabilitySpec | None:
        return self._specs.get(str(capability or "").strip().lower())

    def require(self, capability: str) -> BioOpsCapabilitySpec:
        spec = self.get(capability)
        if spec is None:
            raise KeyError(f"unknown BioOps capability: {capability}")
        return spec


def build_default_bio_ops_registry() -> BioOpsRegistry:
    return BioOpsRegistry(list_capability_specs())


__all__ = ["BioOpsRegistry", "build_default_bio_ops_registry"]
