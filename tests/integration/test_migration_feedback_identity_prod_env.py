from __future__ import annotations

import os
from pathlib import Path

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.bond.migration_runner import BondMigrationRunner, _find_missing_versions
from host_brain.bond.user_identity import collapse_bond_user_ids, get_canonical_bond_user_id, resolve_person_id
from host_brain.persona.growth_feedback_collector import GrowthFeedbackCollector
from host_brain.persona.interaction_growth_analyzer import InteractionGrowthAnalyzer
from host_brain.persona.personality_traits import PersonalityTraits


@pytest.fixture(autouse=True)
def _prod_env(monkeypatch):
    monkeypatch.setenv('Natsume_ENV', 'prod')
    monkeypatch.setenv('HOST_DEFAULT_USER_ID', 'prod-master')
    yield



def test_bond_migration_runner_prod_success_and_probe_failure(tmp_path) -> None:
    assert str(os.environ.get('Natsume_ENV') or '').lower() == 'prod'
    migrations_dir = tmp_path / 'bond_migrations'
    migrations_dir.mkdir(parents=True)
    (migrations_dir / '0001_init.cypher').write_text("CREATE CONSTRAINT bond_evt IF NOT EXISTS;\n", encoding='utf-8')
    (migrations_dir / '0002_topic.cypher').write_text("CREATE INDEX bond_topic IF NOT EXISTS;\n", encoding='utf-8')
    ledger = SQLiteLedger(db_path=':memory:', check_same_thread=False)
    runner = BondMigrationRunner(migrations_dir=migrations_dir, ledger=ledger)

    batch = runner.apply_migrations(request_id='req-bond-migrate-1', ts_ms=1775122000000)
    probe_fail = runner.apply_migrations_with_probe(
        probe_report={'status': 'DOWN', 'reason_code': 'NEO4J_LEADER_UNAVAILABLE', 'detail': 'leader unavailable'},
        request_id='req-bond-migrate-2',
        ts_ms=1775122001000,
    )
    records = runner.read_migration_log()

    assert batch.success is True
    assert batch.summary['applied_count'] == 2
    assert batch.summary['latest_version'] == 2
    assert probe_fail.success is False
    assert probe_fail.reason_code == 'NEO4J_LEADER_UNAVAILABLE'
    assert len(records) >= 3
    assert _find_missing_versions([1, 3, 4]) == [2]
    ledger.close()



def test_bond_user_identity_prod_collapses_to_canonical_user() -> None:
    assert str(os.environ.get('Natsume_ENV') or '').lower() == 'prod'
    canonical = get_canonical_bond_user_id()
    resolved = resolve_person_id(
        None,
        user_id='wechat_user_001',
        session_id='session_001',
        platform='wechat',
        from_id='wxid_123',
        conversation_id='conv_001',
    )
    collapsed = collapse_bond_user_ids(['wxid_123', 'qq_456'])

    assert canonical == 'prod-master'
    assert resolved == canonical
    assert collapsed == [canonical]



def test_growth_feedback_collector_prod_extracts_and_computes_adjustment() -> None:
    assert str(os.environ.get('Natsume_ENV') or '').lower() == 'prod'
    collector = GrowthFeedbackCollector(min_signals=2, signal_threshold=0.15, feedback_delta=0.02)
    user_id = 'prod-feedback-user'

    explicit = collector.extract_from_text('你好温柔，也很体贴', ts_ms=1775122100000)
    for signal in explicit:
        collector.add_feedback(user_id, signal)
    collector.add_webui_vote(user_id, trait_name='warmth', is_positive=True, ts_ms=1775122101000)
    collector.add_implicit_signal(user_id, trait_name='sensitivity', direction=0.8, confidence=0.5, text_hint='用户继续倾诉', ts_ms=1775122102000)

    result = collector.compute_adjustment(user_id, ts_ms=1775122103000)
    recent = collector.get_recent_feedbacks(user_id)

    assert result.total_signals >= 3
    assert result.delta.as_dict()['warmth'] > 0
    assert collector.get_buffer_size(user_id) >= 3
    assert recent



def test_interaction_growth_analyzer_prod_analyzes_real_interaction_mix() -> None:
    assert str(os.environ.get('Natsume_ENV') or '').lower() == 'prod'
    analyzer = InteractionGrowthAnalyzer.from_canon(None)
    traits = PersonalityTraits.from_baseline()

    result = analyzer.analyze(
        interaction_stats={'INTERACTION_DEEP': 6, 'EMOTIONAL_SUPPORT': 4, 'DAILY_CHECKIN': 2},
        current_traits=traits,
        analysis_period_days=7,
        ts_ms=1775122200000,
    )

    assert result.total_interactions == 12
    assert result.dominant_interaction == 'INTERACTION_DEEP'
    assert result.delta.as_dict()['curiosity'] >= 0
    assert result.trait_contributions['curiosity'] >= 0
