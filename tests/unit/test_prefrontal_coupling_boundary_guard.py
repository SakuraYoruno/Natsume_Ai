from __future__ import annotations

from pathlib import Path


FORBIDDEN_MARKERS = (
    "from host_brain.persona.drift_detector import PersonaDriftDetector",
    "from host_brain.persona.drift_detector import get_drift_detector",
    "from host_brain.persona.growth_feedback_collector import GrowthFeedbackCollector",
    "from host_brain.persona.persona_growth import PersonaGrowthTracker",
    "from host_brain.persona.persona_engine import PersonaEngine",
    "from host_brain.persona.evolution_orchestrator import EvolutionOrchestrator",
    "from host_brain.persona.expression_adapter import PersonaExpressionAdapter",
    "from host_brain.persona.self_reflection import",
    "from host_brain.persona.bond_aware_style import BondAwareStyleEngine",
    "host_brain.persona.drift_detector.PersonaDriftDetector",
    "host_brain.persona.growth_feedback_collector.GrowthFeedbackCollector",
    "host_brain.persona.persona_growth.PersonaGrowthTracker",
    "host_brain.persona.persona_engine.PersonaEngine",
    "host_brain.persona.evolution_orchestrator.EvolutionOrchestrator",
    "host_brain.persona.expression_adapter.PersonaExpressionAdapter",
    "host_brain.persona.self_reflection.",
    "host_brain.persona.bond_aware_style.BondAwareStyleEngine",
)


def test_prefrontal_coupling_stays_in_tribe_layer() -> None:
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
        "Prefrontal drift/growth direct-coupling markers must stay in tribe wrappers.\n"
        + "\n".join(violations)
    )
