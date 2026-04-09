from __future__ import annotations

from pathlib import Path


FORBIDDEN_MARKERS = (
    "from host_brain.persona.inner_world import get_inner_world",
    "get_inner_world(",
    "from host_brain.persona.motive_runtime import build_offline_state_from_inner_state",
    "build_offline_state_from_inner_state(",
)


def test_inner_world_coupling_stays_in_tribe_layer() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    scan_roots = [
        repo_root / "host_brain" / "app",
        repo_root / "host_brain" / "skills" / "impl",
    ]
    violations: list[str] = []

    for scan_root in scan_roots:
        for file_path in scan_root.rglob("*.py"):
            rel = file_path.relative_to(repo_root)
            text = file_path.read_text(encoding="utf-8", errors="ignore")
            for marker in FORBIDDEN_MARKERS:
                if marker in text:
                    violations.append(f"{rel}: {marker}")

    assert not violations, (
        "Inner-world direct-coupling markers must stay inside tribe wrappers.\n"
        + "\n".join(violations)
    )
