from __future__ import annotations

from host_brain.security_immune.local_sensor import LocalHostSensor
from host_brain.security_immune.policy import SecurityImmunePolicy
from host_brain.security_immune.providers.base import SecuritySignalProvider
from host_brain.security_immune.providers.defender_provider import DefenderProvider
from host_brain.security_immune.providers.local_probe import LocalProbeProvider
from host_brain.security_immune.providers.osquery_provider import OsqueryProvider
from host_brain.security_immune.providers.wazuh_provider import WazuhProvider


def build_provider_chain(
    policy: SecurityImmunePolicy,
    *,
    sensor: LocalHostSensor | None = None,
) -> list[SecuritySignalProvider]:
    providers: list[SecuritySignalProvider] = []
    requested = tuple(item.strip().lower() for item in policy.provider_names if item.strip())
    for name in requested:
        if name in {"local", "local_probe", "local-probe"}:
            providers.append(LocalProbeProvider(policy, sensor=sensor))
        elif name in {"osquery", "osquery_provider", "osquery-provider"}:
            providers.append(OsqueryProvider(policy))
        elif name in {"wazuh", "wazuh_provider", "wazuh-provider"}:
            providers.append(WazuhProvider(policy))
        elif name in {"defender", "microsoft_defender", "microsoft-defender"}:
            providers.append(DefenderProvider(policy))
    if not providers:
        providers.append(LocalProbeProvider(policy, sensor=sensor))
    return providers
