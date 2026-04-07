from __future__ import annotations

import json

from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persona.drift_detector import PersonaDriftDetector


def test_drift_detector_correction_hints_use_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "drift_detector.correction_hints",
        content=json.dumps(
            {
                "by_dim": {"warmth_LOW": "custom-warmth"},
                "generic": ["custom-generic"],
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    detector = PersonaDriftDetector(check_interval=1)
    detector._turn_count = 7

    assert detector._build_correction_hint(["warmth_LOW"]) == "custom-warmth"
    assert detector._build_correction_hint(["unknown_dim"]) == "custom-generic"
