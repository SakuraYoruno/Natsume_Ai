from __future__ import annotations

import json
from pathlib import Path

import pytest

import host_brain.persona.emotion_persistence as ep
from host_brain.persona.emotion_persistence import (
    EmotionPersistence,
    PersistedEmotionState,
    RestoredEmotionState,
    _classify_valence_direction,
    compute_momentum_decay_multiplier,
)


def test_persisted_and_restored_state_optional_fields_and_properties() -> None:
    persisted = PersistedEmotionState(user_id="u1", valence=0.12345)
    assert "momentum_days" not in persisted.as_dict()

    persisted.momentum_days = 3
    persisted.momentum_direction = "positive"
    assert persisted.as_dict()["momentum_days"] == 3

    restored = RestoredEmotionState(
        valence=0.1,
        arousal=0.2,
        dominance=0.3,
        decay_factor=0.4,
        hours_elapsed=5.0,
        from_baseline=False,
        reunion_type="reunion",
        anchor_active=True,
        momentum_active=True,
        momentum_days=4,
    )
    data = restored.as_dict()
    assert data["reunion_type"] == "reunion"
    assert data["anchor_active"] is True
    assert data["momentum_active"] is True
    assert data["momentum_days"] == 4
    assert restored.is_reunion is True
    assert restored.is_short_absence is False

    short = RestoredEmotionState(0, 0, 0, 0, 0, False, reunion_type="short_absence")
    assert short.is_short_absence is True


def test_persistence_dir_and_state_file_helpers_handle_edge_cases(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    broken_dir = tmp_path / "broken"
    original_mkdir = Path.mkdir

    def _boom(self: Path, *args, **kwargs):  # noqa: ANN003
        if self == broken_dir:
            raise OSError("mkdir blocked")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", _boom)
    persistence = EmotionPersistence(persistence_dir=broken_dir)
    assert persistence._state_file("u:/bad name").name == "u__bad_name.json"


def test_save_and_load_file_error_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    persistence = EmotionPersistence(persistence_dir=tmp_path)
    state = PersistedEmotionState(user_id="u1", valence=0.2, last_session_end_ts_ms=1)

    original_write_text = Path.write_text

    def _fail_write(self: Path, *args, **kwargs):  # noqa: ANN003
        if self.suffix == ".tmp":
            raise OSError("write blocked")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _fail_write)
    persistence._save_to_file(state)  # should not raise

    bad_file = persistence._state_file("u1")
    bad_file.write_text("{broken", encoding="utf-8")
    assert persistence._load_from_file("u1") is None

    bad_file.write_text(json.dumps({"user_id": "u1", "valence": "x"}), encoding="utf-8")
    assert persistence._load_from_file("u1") is None


def test_persist_updates_momentum_direction_and_cap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    persistence = EmotionPersistence(persistence_dir=tmp_path)
    monkeypatch.setattr(ep, "_now_ms", lambda: 123456)

    first = persistence.persist(user_id="u1", valence=0.5)
    assert first.momentum_days == 1
    assert first.momentum_direction == "positive"

    persistence._states["u1"] = PersistedEmotionState(
        user_id="u1",
        valence=0.6,
        last_session_end_ts_ms=1,
        mood_baseline=0.1,
        momentum_days=ep._MOMENTUM_MAX_DAYS,
        momentum_direction="positive",
    )
    capped = persistence.persist(user_id="u1", valence=0.7)
    assert capped.momentum_days == ep._MOMENTUM_MAX_DAYS
    assert capped.mood_baseline == 0.1

    flipped = persistence.persist(user_id="u1", valence=-0.5)
    assert flipped.momentum_days == 1
    assert flipped.momentum_direction == "negative"

    neutral = persistence.persist(user_id="u1", valence=0.0)
    assert neutral.momentum_days == 0
    assert neutral.momentum_direction == ""


def test_restore_from_file_baseline_and_reunion_variants(tmp_path: Path) -> None:
    persistence = EmotionPersistence(persistence_dir=tmp_path)
    base_ts = 10 * 24 * 3600 * 1000

    restored = persistence.restore(user_id="missing", current_ts_ms=base_ts)
    assert restored.from_baseline is True

    file_state = PersistedEmotionState(
        user_id="u1",
        valence=0.7,
        arousal=0.4,
        dominance=0.2,
        last_session_end_ts_ms=base_ts - 8 * 24 * 3600 * 1000,
        mood_baseline=0.1,
    )
    persistence._state_file("u1").write_text(json.dumps(file_state.as_dict()), encoding="utf-8")
    restored_file = persistence.restore(user_id="u1", current_ts_ms=base_ts)
    assert restored_file.from_baseline is False
    assert restored_file.reunion_type == "reunion"
    assert restored_file.is_reunion is True
    assert persistence._states["u1"].user_id == "u1"

    persistence.persist(user_id="u2", valence=0.4, arousal=0.5, dominance=0.1, ts_ms=base_ts - 4 * 24 * 3600 * 1000)
    short = persistence.restore(user_id="u2", current_ts_ms=base_ts)
    assert short.reunion_type == "short_absence"
    assert short.is_short_absence is True
    assert short.arousal > ep.DEFAULT_BASELINE_AROUSAL


def test_restore_anchor_and_momentum_branches(tmp_path: Path) -> None:
    persistence = EmotionPersistence(persistence_dir=tmp_path)
    base_ts = 20 * 24 * 3600 * 1000

    persistence._states["u1"] = PersistedEmotionState(
        user_id="u1",
        valence=-0.7,
        arousal=0.8,
        dominance=-0.3,
        last_session_end_ts_ms=base_ts - 10 * 3600 * 1000,
        mood_baseline=-0.1,
        momentum_days=5,
        momentum_direction="negative",
    )
    restored = persistence.restore(user_id="u1", current_ts_ms=base_ts)
    assert restored.anchor_active is True
    assert restored.momentum_active is True
    assert restored.momentum_days == 5

    persistence._states["u2"] = PersistedEmotionState(
        user_id="u2",
        valence=0.2,
        arousal=0.4,
        dominance=0.1,
        last_session_end_ts_ms=base_ts - 2 * 3600 * 1000,
        mood_baseline=0.0,
        momentum_days=1,
        momentum_direction="positive",
    )
    restored_no_momentum = persistence.restore(user_id="u2", current_ts_ms=base_ts)
    assert restored_no_momentum.anchor_active is False
    assert restored_no_momentum.momentum_active is False


def test_get_persisted_state_has_state_and_clear_error_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    persistence = EmotionPersistence(persistence_dir=tmp_path)
    file_state = PersistedEmotionState(user_id="u1", valence=0.3, last_session_end_ts_ms=1)
    persistence._state_file("u1").write_text(json.dumps(file_state.as_dict()), encoding="utf-8")

    restored = persistence.get_persisted_state("u1")
    assert restored is not None
    assert persistence.has_persisted_state("u1") is True

    original_unlink = Path.unlink

    def _fail_unlink(self: Path, *args, **kwargs):  # noqa: ANN003
        if self == persistence._state_file("u1"):
            raise OSError("unlink blocked")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _fail_unlink)
    persistence.clear("u1")  # should not raise


def test_direction_and_momentum_multiplier_helpers_cover_thresholds() -> None:
    assert _classify_valence_direction(0.2) == "positive"
    assert _classify_valence_direction(-0.2) == "negative"
    assert _classify_valence_direction(0.05) == ""

    assert compute_momentum_decay_multiplier(1) == 1.0
    assert compute_momentum_decay_multiplier(2) == 0.85
    assert compute_momentum_decay_multiplier(3) == 0.70
    assert compute_momentum_decay_multiplier(5) == 0.50
