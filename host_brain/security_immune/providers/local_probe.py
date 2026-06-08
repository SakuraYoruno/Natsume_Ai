from __future__ import annotations

from host_brain.security_immune.local_sensor import LocalHostSensor
from host_brain.security_immune.policy import SecurityImmunePolicy
from host_brain.security_immune.providers.base import ProviderResult


class LocalProbeProvider:
    provider_id = "local_probe"

    def __init__(
        self,
        policy: SecurityImmunePolicy,
        *,
        sensor: LocalHostSensor | None = None,
    ) -> None:
        self.policy = policy
        self.sensor = sensor or LocalHostSensor(policy)

    def collect_assets(self) -> ProviderResult:
        asset = self.sensor.collect()
        return ProviderResult(
            provider_id=self.provider_id,
            status="ok",
            assets=[asset],
            detail="local host probe completed",
            metadata={"sensor": type(self.sensor).__name__},
        )
