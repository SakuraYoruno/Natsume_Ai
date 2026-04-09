from __future__ import annotations

from pathlib import Path


FORBIDDEN_MARKERS = (
    "get_bond_stage_engine",
    "get_bond_update_queue",
    "BondDynamicsWriter",
)

ALLOWED_PATH_SUFFIXES = {
    Path("host_brain/app/dependencies.py"),
}


def test_bond_coupling_stays_in_tribe_layer() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    scan_roots = [
        repo_root / "host_brain" / "app",
        repo_root / "host_brain" / "persona",
    ]
    violations: list[str] = []

    for root in scan_roots:
        for file_path in root.rglob("*.py"):
            rel = file_path.relative_to(repo_root)
            if rel in ALLOWED_PATH_SUFFIXES:
                continue
            text = file_path.read_text(encoding="utf-8", errors="ignore")
            for marker in FORBIDDEN_MARKERS:
                if marker in text:
                    violations.append(f"{rel}: {marker}")

    assert not violations, (
        "Bond direct-coupling markers must stay inside tribe wrappers.\n"
        + "\n".join(violations)
    )
