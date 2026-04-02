from __future__ import annotations

import os

import pytest

from host_brain.core.emotion_timeline import EmotionTimeline, compute_emotion_echo
from host_brain.core.metabolism import EmotionVector


@pytest.fixture(autouse=True)
def _prod_env(monkeypatch):
    monkeypatch.setenv("Natsume_ENV", "prod")
    yield



def test_emotion_timeline_prod_append_query_and_limit() -> None:
    assert str(os.environ.get("Natsume_ENV") or "").lower() == "prod"
    timeline = EmotionTimeline(max_entries=3)
    timeline.append(ts_ms=1000, emotion_tag='neutral', valence=0.0, arousal=0.1, source='user_input')
    timeline.append(ts_ms=2000, emotion_tag='happy', valence=0.6, arousal=0.5, source='visual')
    timeline.append(ts_ms=3000, emotion_tag='sad', valence=-0.4, arousal=0.3, source='bond_event')
    timeline.append(ts_ms=4000, emotion_tag='loving', valence=0.8, arousal=0.7, source='proactive')

    rows = timeline.query(since_ms=1500, until_ms=4500, limit=10)

    assert timeline.entry_count == 3
    assert [row.emotion_tag for row in rows] == ['HAPPY', 'SAD', 'LOVING']



def test_emotion_timeline_prod_build_daily_diary() -> None:
    assert str(os.environ.get("Natsume_ENV") or "").lower() == "prod"
    timeline = EmotionTimeline(max_entries=10)
    base = 2_000_000_000_000
    timeline.append(ts_ms=base - 3000, emotion_tag='neutral', valence=0.0, arousal=0.2, source='user_input')
    timeline.append(ts_ms=base - 2000, emotion_tag='happy', valence=0.7, arousal=0.6, source='visual')
    timeline.append(ts_ms=base - 1000, emotion_tag='happy', valence=0.8, arousal=0.7, source='bond_event')

    import host_brain.core.ids as ids_mod
    original_now_ms = ids_mod.now_ms
    ids_mod.now_ms = lambda: base
    try:
        diary = timeline.build_daily_diary(window_ms=10_000)
    finally:
        ids_mod.now_ms = original_now_ms

    assert diary['total_entries'] == 3
    assert diary['dominant_emotion'] == 'HAPPY'
    assert diary['emotion_arc']['end'] == 'HAPPY'
    assert diary['diary_text']



def test_emotion_timeline_prod_rebuild_summary() -> None:
    assert str(os.environ.get("Natsume_ENV") or "").lower() == "prod"
    timeline = EmotionTimeline(max_entries=10)
    base = 3_000_000_000_000
    timeline.append(ts_ms=base - 2000, emotion_tag='sad', valence=-0.4, arousal=0.4, source='user_input')
    timeline.append(ts_ms=base - 1000, emotion_tag='happy', valence=0.6, arousal=0.5, source='visual')
    timeline.append(ts_ms=base - 500, emotion_tag='happy', valence=0.7, arousal=0.6, source='bond_event')

    import host_brain.core.ids as ids_mod
    original_now_ms = ids_mod.now_ms
    ids_mod.now_ms = lambda: base
    try:
        summary = timeline.rebuild_summary(window_ms=10_000)
    finally:
        ids_mod.now_ms = original_now_ms

    assert summary['dominant_emotion'] == 'HAPPY'
    assert summary['entry_count'] == 3
    assert summary['trend']
    assert 'entries' in summary
    assert len(summary['entries']) == 3



def test_compute_emotion_echo_prod_with_personality_traits() -> None:
    assert str(os.environ.get("Natsume_ENV") or "").lower() == "prod"
    current = EmotionVector(valence=-0.25, arousal=0.2, ts_ms=1234)
    result = compute_emotion_echo(
        'SAD',
        current_vector=current,
        bond_stage='FRIEND',
        personality_traits={'sensitivity': 0.9, 'warmth': 0.85, 'resilience': 0.4},
    )

    assert isinstance(result, dict)
    assert 'echo_emotion_tag' in result
    assert 'echo_valence' in result
    assert 'echo_arousal' in result
    assert 'response_hint' in result
