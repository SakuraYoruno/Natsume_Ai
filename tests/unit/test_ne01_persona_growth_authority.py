from __future__ import annotations

from pathlib import Path

import pytest

from host_brain.persona.persona_engine import (
    PERSONA_GROWTH_AUTHORITY_CLOSED,
    PersonaEngine,
    PersonaGrowthAuthorityError,
    PersonaGrowthMigrationAuthority,
)
from host_brain.persona.persona_growth import (
    PERSONA_GROWTH_PROPOSAL_REQUIRED,
    PersonaGrowthTracker,
)
from host_brain.persona.personality_traits import TraitsDelta
from tools.static_scan.persona_growth_authority_scan import scan_file, scan_repo


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _reset_persona_engine() -> None:
    PersonaEngine.reset_instance()
    yield
    PersonaEngine.reset_instance()


def test_t_persona_fence_01_direct_growth_write_fails_closed() -> None:
    engine = PersonaEngine.get_instance()
    before = engine.get_traits("user-fence").as_dict()

    with pytest.raises(PersonaGrowthAuthorityError) as caught:
        engine.apply_growth_delta(
            "user-fence",
            TraitsDelta(warmth=0.03),
            reason="bond_stage_promoted",
        )

    assert caught.value.code == PERSONA_GROWTH_AUTHORITY_CLOSED
    assert engine.get_traits("user-fence").as_dict() == before
    assert engine.get_change_log("user-fence") == []


def test_t_persona_fence_01_migration_requires_complete_audit_authority() -> None:
    engine = PersonaEngine.get_instance()

    with pytest.raises(PersonaGrowthAuthorityError):
        engine.apply_migration_growth_delta(
            "user-migration",
            TraitsDelta(curiosity=0.02),
            authority=PersonaGrowthMigrationAuthority(
                migration_id="bad-id",
                actor_id="operator:test",
                evidence_ref="receipt:test",
            ),
            reason="backfill legacy persona state",
        )


def test_t_persona_fence_01_explicit_migration_is_audited() -> None:
    engine = PersonaEngine.get_instance()
    authority = PersonaGrowthMigrationAuthority(
        migration_id="persona-migration:test-fixture",
        actor_id="operator:test",
        evidence_ref="receipt:test-persona-migration",
    )

    result = engine.apply_migration_growth_delta(
        "user-migration",
        TraitsDelta(curiosity=0.02),
        authority=authority,
        reason="backfill legacy persona state",
        ts_ms=123,
    )

    assert result.curiosity > engine.baseline.curiosity
    record = engine.get_change_log("user-migration")[-1].as_dict()
    assert record["authority_kind"] == "MIGRATION"
    assert "persona-migration:test-fixture" in record["authority_ref"]
    assert record["ts_ms"] == 123


def test_t_persona_fence_01_stage_candidate_does_not_mutate_traits() -> None:
    engine = PersonaEngine.get_instance()
    tracker = PersonaGrowthTracker(engine=engine)
    before = engine.get_traits("user-stage").as_dict()

    result = tracker.on_stage_promoted(
        "user-stage",
        "FRIEND",
        "CLOSE_FRIEND",
    )

    assert result.applied is False
    assert PERSONA_GROWTH_PROPOSAL_REQUIRED in result.reason
    assert result.guard_result is not None
    assert result.guard_result["authority_gate"] == PERSONA_GROWTH_PROPOSAL_REQUIRED
    assert result.guard_result["candidate_delta"]["playfulness"] == pytest.approx(0.05)
    assert engine.get_traits("user-stage").as_dict() == before
    assert tracker.get_triggered_rules("user-stage") == set()
    assert tracker.get_change_history("user-stage") == []
    assert tracker.get_weekly_cumulative("user-stage") == {}


def test_t_persona_fence_01_static_scan_rejects_new_runtime_writer(
    tmp_path: Path,
) -> None:
    bad = tmp_path / "host_brain" / "persona" / "bad_writer.py"
    bad.parent.mkdir(parents=True)
    bad.write_text(
        "def mutate(engine, delta):\n"
        "    return engine.apply_growth_delta('u', delta, reason='bad')\n",
        encoding="utf-8",
    )

    violations = scan_file(bad, root=tmp_path)

    assert [row["rule_id"] for row in violations] == ["T-PERSONA-FENCE-01"]
    assert "Legacy persona growth write" in violations[0]["message"]


def test_t_persona_fence_01_repository_has_no_runtime_growth_writer() -> None:
    assert scan_repo(ROOT) == []
