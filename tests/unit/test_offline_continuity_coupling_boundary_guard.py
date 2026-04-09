from __future__ import annotations

from pathlib import Path


FORBIDDEN_MARKERS = (
    "from host_brain.persona.offline_continuity import",
    "host_brain.persona.offline_continuity.",
)


def test_offline_continuity_coupling_stays_in_tribe_layer() -> None:
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
        "Offline-continuity direct coupling markers must stay in limbic wrappers.\n"
        + "\n".join(violations)
    )
