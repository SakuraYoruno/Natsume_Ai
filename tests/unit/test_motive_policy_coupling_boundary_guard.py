from __future__ import annotations

from pathlib import Path


_MOTIVE_RUNTIME_IMPORT = "from " + "host_brain.persona.motive_runtime" + " import compute_runtime_motive_state"
_MOTIVE_ENGINE_IMPORT = "from " + "host_brain.persona.motive_engine" + " import"
_LEGACY_POLICY_MODULE = "host_brain.persona." + "response" + "_policy"

FORBIDDEN_MARKERS = (
    _MOTIVE_RUNTIME_IMPORT,
    _MOTIVE_ENGINE_IMPORT,
    "from " + _LEGACY_POLICY_MODULE + " import",
    "host_brain.persona.motive_runtime.compute_runtime_motive_state",
    "host_brain.persona.motive_engine.compute_motive_state",
    _LEGACY_POLICY_MODULE + "." + "decide_" + "response" + "_policy",
)


def test_motive_policy_coupling_stays_in_tribe_layer() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    scan_root = repo_root / "host_brain" / "app"
    violations: list[str] = []

    for file_path in scan_root.rglob("*.py"):
        rel = file_path.relative_to(repo_root)
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        for marker in FORBIDDEN_MARKERS:
            if marker in text:
                violations.append(f"{rel}: {marker}")

    assert not violations, (
        "Motive/response direct-coupling markers must stay in limbic wrappers.\n"
        + "\n".join(violations)
    )
