"""Read-only organ wiring audit for tribe agent files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from host_brain.organ_ops.organ_health_profile import OrganHealthProfile
from host_brain.organ_ops.organ_registry import OrganRegistry, build_default_organ_registry


def build_organ_health_profiles(registry: OrganRegistry | None = None) -> list[dict[str, Any]]:
    active_registry = registry or build_default_organ_registry()
    profiles: list[dict[str, Any]] = []
    for organ in active_registry.list_organs():
        path = Path(str(organ.get("file_path") or ""))
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        profile = OrganHealthProfile(
            organ_id=str(organ.get("organ_id") or ""),
            role=str(organ.get("module") or ""),
            inputs=_detect_inputs(text),
            outputs=_detect_outputs(text),
            state_keys=_detect_state_keys(text),
            failure_modes=_detect_failure_modes(text),
            health_metrics={
                "has_event_subscription": "subscribe(" in text,
                "has_state_write": "state_registry" in text or ".set_state(" in text,
                "has_trace_or_ledger": "ledger" in text or "trace" in text,
                "has_prompt_reference": "prompt" in text.lower(),
            },
        )
        profiles.append(profile.to_dict())
    return profiles


def run_organ_wiring_audit(registry: OrganRegistry | None = None) -> dict[str, Any]:
    profiles = build_organ_health_profiles(registry)
    findings: list[dict[str, Any]] = []
    for profile in profiles:
        metrics = dict(profile.get("health_metrics") or {})
        missing = [key for key, ok in metrics.items() if not bool(ok)]
        if missing:
            findings.append(
                {
                    "organ_id": profile.get("organ_id"),
                    "issue": "incomplete_wiring_surface",
                    "missing_metrics": missing,
                }
            )
    return {
        "status": "AUDITED",
        "organ_count": len(profiles),
        "profiles": profiles,
        "findings": findings,
    }


def _detect_inputs(text: str) -> list[str]:
    inputs: list[str] = []
    if "subscribe(" in text:
        inputs.append("tribe_event_bus")
    if "resolve_" in text:
        inputs.append("runtime_state")
    return inputs


def _detect_outputs(text: str) -> list[str]:
    outputs: list[str] = []
    if "emit_" in text or ".publish(" in text:
        outputs.append("events")
    if "state_registry" in text or ".set_state(" in text:
        outputs.append("state_registry")
    return outputs


def _detect_state_keys(text: str) -> list[str]:
    keys: list[str] = []
    for marker in ('key="', "key='"):
        start = 0
        quote = marker[-1]
        while True:
            pos = text.find(marker, start)
            if pos < 0:
                break
            pos += len(marker)
            end = text.find(quote, pos)
            if end < 0:
                break
            keys.append(text[pos:end])
            start = end + 1
    return sorted(set(keys))


def _detect_failure_modes(text: str) -> list[str]:
    modes: list[str] = []
    if "except Exception" in text:
        modes.append("degraded_exception_path")
    if "timeout" in text.lower():
        modes.append("timeout")
    return modes


__all__ = ["build_organ_health_profiles", "run_organ_wiring_audit"]
