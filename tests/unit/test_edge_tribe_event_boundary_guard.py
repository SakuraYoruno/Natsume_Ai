from __future__ import annotations

from pathlib import Path


FORBIDDEN_MARKERS = (
    "publish_event(",
    "_publish_tribe_event(",
)


def test_edge_event_publish_stays_in_tribe_agents() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    scan_roots = [
        repo_root / "edge_client" / "gateway",
        repo_root / "edge_client" / "runtime",
    ]
    violations: list[str] = []

    for root in scan_roots:
        for file_path in root.rglob("*.py"):
            rel = file_path.relative_to(repo_root)
            text = file_path.read_text(encoding="utf-8", errors="ignore")
            for marker in FORBIDDEN_MARKERS:
                if marker in text:
                    violations.append(f"{rel}: {marker}")

    assert not violations, (
        "Edge event publishing should stay inside edge tribe agents.\n"
        + "\n".join(violations)
    )
