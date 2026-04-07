from __future__ import annotations

import json

from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persona.personality_traits import PersonalityTraits, TraitsDelta
from host_brain.persona.trait_drift_guard import TraitDriftGuard


def test_trend_lock_templates_override_runtime_texts(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "trait_drift_guard.trend_lock_templates",
        content=json.dumps(
            {
                "trend_lock_awareness": {
                    "humor": {
                        "UP": ["自定义趋势锁觉察"],
                    },
                },
                "trait_names_cn": {
                    "humor": "笑感",
                },
                "direction_names_cn": {
                    "UP": "抬升",
                },
                "reason_template": "【{trait_cn}{direction_cn}{count}】",
                "proposed_action_template": "动作 {trait_cn} {direction_cn} {cumulative:+.4f}",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    guard = TraitDriftGuard()
    result = guard.check(
        delta=TraitsDelta(humor=0.03),
        current_traits=PersonalityTraits(),
        change_history=[{"humor": 0.03}] * 4,
    )

    proposal = result.evolution_proposal
    assert proposal is not None
    assert proposal["awareness_hint"] == "自定义趋势锁觉察"
    assert proposal["reason"] == "【笑感抬升4】"
    assert proposal["proposed_action"] == "动作 笑感 抬升 +0.1200"
