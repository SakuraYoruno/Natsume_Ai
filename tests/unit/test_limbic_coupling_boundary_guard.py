from __future__ import annotations

from pathlib import Path


FORBIDDEN_MARKERS = (
    "from host_brain.persona.body_sense import get_body_sense_model",
    "from host_brain.persona.emotional_silence import get_silence_engine",
    "from host_brain.persona.emotion_persistence import EmotionPersistence",
    "get_body_sense_model(",
    "get_silence_engine(",
)

ALLOWED_PATH_SUFFIXES = {
    Path("host_brain/persona/body_sense.py"),
    Path("host_brain/persona/emotional_silence.py"),
    Path("host_brain/persona/emotion_persistence.py"),
}


def test_limbic_coupling_stays_in_tribe_layer() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    scan_roots = [
        repo_root / "host_brain" / "app",
        repo_root / "host_brain" / "skills" / "impl",
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
        "Silence/body-sense direct coupling markers must stay in limbic wrappers.\n"
        + "\n".join(violations)
    )
