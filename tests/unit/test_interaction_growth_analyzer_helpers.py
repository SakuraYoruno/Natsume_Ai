from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import host_brain.persona.interaction_growth_analyzer as iga
from host_brain.persona.interaction_growth_analyzer import (
    InteractionGrowthAnalyzer,
    InteractionGrowthResult,
    InteractionQualityEvaluator,
    _load_canon_growth_raw,
    make_interaction_growth_task_config,
)
from host_brain.persona.personality_traits import PersonalityTraits, TraitsDelta


def test_interaction_quality_evaluator_covers_boundary_buckets() -> None:
    evaluator = InteractionQualityEvaluator()

    cases = [
        (0, 0.0, False, False, 1.0, 1.0),
        (1, 10.0, False, False, 0.5, 0.6),
        (2, 30.0, True, False, 0.65, 0.7333333333),
        (5, 80.0, False, True, 1.0, 1.12),
        (12, 180.0, True, True, 1.5, 1.4),
    ]

    for msg_count, avg_len, has_depth, has_followup, depth_score, length_score in cases:
        score = evaluator.evaluate(
            interaction_type="INTERACTION_DEEP",
            msg_count=msg_count,
            avg_msg_length=avg_len,
            has_emotion_depth=has_depth,
            has_followup=has_followup,
        )
        expected = round(
            depth_score * evaluator._WEIGHT_MSG_COUNT
            + length_score * evaluator._WEIGHT_MSG_LENGTH
            + (1.3 if has_depth else 0.8) * evaluator._WEIGHT_EMOTION
            + (1.3 if has_followup else 0.8) * evaluator._WEIGHT_FOLLOWUP,
            3,
        )
        assert score.quality == expected
        assert score.avg_msg_length == round(avg_len, 1)


def test_interaction_growth_result_as_dict_rounds_quality_multipliers() -> None:
    result = InteractionGrowthResult(
        delta=TraitsDelta(warmth=0.01234),
        interaction_stats={"INTERACTION_DEEP": 3},
        trait_contributions={"warmth": 0.123456},
        total_interactions=3,
        dominant_interaction="INTERACTION_DEEP",
        analysis_period_days=7,
        ts_ms=123456789,
        quality_multipliers={"INTERACTION_DEEP": 1.23456},
    )

    data = result.as_dict()

    assert data["trait_contributions"]["warmth"] == 0.1235
    assert data["quality_multipliers"]["INTERACTION_DEEP"] == 1.235


def test_from_canon_loads_override_map_and_params(monkeypatch) -> None:
    monkeypatch.setattr(
        iga,
        "_load_canon_growth_raw",
        lambda: {
            "interaction_trait_map": {"custom_kind": {"warmth": 0.7, "curiosity": 0.3}},
            "growth_params": {
                "base_delta": 0.02,
                "min_interaction_ratio": 0.22,
                "diminishing_threshold": 0.91,
            },
        },
    )

    analyzer = InteractionGrowthAnalyzer.from_canon(None)

    assert analyzer._trait_map["CUSTOM_KIND"] == {"warmth": 0.7, "curiosity": 0.3}
    assert analyzer._base_delta == 0.02
    assert analyzer._min_ratio == 0.22
    assert analyzer._diminishing_threshold == 0.91


def test_from_canon_falls_back_when_loader_raises(monkeypatch) -> None:
    def _raise() -> None:
        raise RuntimeError("broken canon")

    monkeypatch.setattr(iga, "_load_canon_growth_raw", _raise)

    analyzer = InteractionGrowthAnalyzer.from_canon(None)

    assert analyzer._base_delta == iga.BASE_DELTA
    assert analyzer._min_ratio == iga.MIN_INTERACTION_RATIO
    assert analyzer._diminishing_threshold == iga.DIMINISHING_THRESHOLD
    assert analyzer._trait_map["INTERACTION_DEEP"]["curiosity"] == 0.40


def test_analyze_returns_none_delta_when_no_interactions() -> None:
    analyzer = InteractionGrowthAnalyzer()
    result = analyzer.analyze(
        interaction_stats={},
        current_traits=PersonalityTraits.from_baseline(),
        analysis_period_days=14,
        ts_ms=99,
    )

    assert result.total_interactions == 0
    assert result.dominant_interaction == "NONE"
    assert result.delta.as_dict()["warmth"] == 0.0


def test_analyze_clamps_quality_and_applies_diminishing() -> None:
    analyzer = InteractionGrowthAnalyzer(
        interaction_trait_map={"FOO": {"warmth": 1.0}, "BAR": {"curiosity": 1.0}},
    )

    result = analyzer.analyze(
        interaction_stats={"FOO": 8, "BAR": 1, "BAZ": 1},
        current_traits=PersonalityTraits(warmth=0.95, curiosity=0.5),
        interaction_quality_scores={"FOO": 5.0, "BAR": 0.0},
    )

    assert result.dominant_interaction == "FOO"
    assert result.quality_multipliers == {"FOO": 2.0, "BAR": 0.1, "BAZ": 1.0}
    assert result.trait_contributions["warmth"] == 1.6
    assert result.delta.as_dict()["warmth"] == 0.0053
    assert result.delta.as_dict()["curiosity"] == 0.0


def test_collect_interaction_stats_from_ledger_queries_sqlite(tmp_path) -> None:
    db_path = tmp_path / "bond.db"
    conn = sqlite3.connect(db_path)
    now_ms = int(time.time() * 1000)
    conn.execute(
        "CREATE TABLE bond_update_log (update_kind TEXT, status TEXT, user_id TEXT, ts_ms INTEGER)"
    )
    conn.executemany(
        "INSERT INTO bond_update_log VALUES (?, ?, ?, ?)",
        [
            ("INTERACTION_DEEP", "OK", "u1", now_ms),
            ("INTERACTION_DEEP", "SUCCESS", "u1", now_ms),
            ("EMOTIONAL_SUPPORT", "SUCCESS", "u1", now_ms),
            ("INIT", "SUCCESS", "u1", now_ms),
            ("INTERACTION_DEEP", "FAIL", "u1", now_ms),
            ("INTERACTION_DEEP", "SUCCESS", "u2", now_ms),
            ("INTERACTION_DEEP", "SUCCESS", "u1", now_ms - 20 * 86400 * 1000),
        ],
    )
    conn.commit()
    conn.close()

    stats = InteractionGrowthAnalyzer.collect_interaction_stats_from_ledger(
        SimpleNamespace(db_path=str(db_path)),
        user_id="u1",
        lookback_days=7,
    )

    assert stats == {"INTERACTION_DEEP": 2, "EMOTIONAL_SUPPORT": 1}


def test_collect_interaction_stats_returns_empty_without_db_path() -> None:
    stats = InteractionGrowthAnalyzer.collect_interaction_stats_from_ledger(
        SimpleNamespace(),
        user_id="u1",
        lookback_days=7,
    )

    assert stats == {}


def test_collect_interaction_stats_returns_empty_when_query_fails(tmp_path) -> None:
    db_path = tmp_path / "broken.db"
    sqlite3.connect(db_path).close()

    stats = InteractionGrowthAnalyzer.collect_interaction_stats_from_ledger(
        SimpleNamespace(db_path=str(db_path)),
        user_id="u1",
        lookback_days=7,
    )

    assert stats == {}


def test_load_canon_growth_raw_reads_project_default_file() -> None:
    raw = _load_canon_growth_raw()

    assert isinstance(raw, dict)
    assert "growth_params" in raw


def test_load_canon_growth_raw_returns_none_on_read_error(monkeypatch) -> None:
    original_read_text = Path.read_text

    def _boom(self: Path, *args, **kwargs):
        if self.name == "wc-004-persona-growth-rules.json":
            raise OSError("unreadable")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _boom)

    assert _load_canon_growth_raw() is None


def test_make_interaction_growth_task_config_matches_weekly_schedule() -> None:
    config = make_interaction_growth_task_config()

    assert config.task_id == "interaction_driven_persona_growth"
    assert config.trigger_mode.value == "cron"
    assert config.trigger_config == {"day_of_week": 0, "hour": 4, "minute": 0}
    assert config.intent_template["skill_name"] == "interaction_persona_growth"
    assert config.priority.value == "LOW"
    assert config.mode_allowlist == ["normal", "sleep"]
