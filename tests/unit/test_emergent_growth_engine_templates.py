from __future__ import annotations

import json
import random

from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persona.emergent_growth_engine import EmergentGrowthEngine


def _install_store(monkeypatch, template_id: str, content: dict) -> PromptTemplateStore:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        template_id,
        content=json.dumps(content, ensure_ascii=False),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)
    return store


def test_generate_growth_narrative_uses_template_override(monkeypatch) -> None:
    _install_store(
        monkeypatch,
        "emergent_growth_engine.narrative_templates",
        {
            "by_theme": {
                "情感支持": ["自定义情感支持成长"],
            },
            "default": ["自定义默认成长"],
        },
    )
    monkeypatch.setattr(random, "choice", lambda seq: seq[0])

    engine = EmergentGrowthEngine()
    assert engine._generate_growth_narrative("情感支持") == "自定义情感支持成长"
    assert engine._generate_growth_narrative("未知主题") == "自定义默认成长"


def test_generate_growth_narrative_uses_ja_template_override(monkeypatch) -> None:
    _install_store(
        monkeypatch,
        "emergent_growth_engine.narrative_templates.ja",
        {
            "by_theme": {
                "情感支持": ["支える時間の中で、私のやわらかさも少し変わってきました。"],
            },
            "default": ["最近のやり取りが、私を少し変えている気がします。"],
        },
    )
    monkeypatch.setattr(random, "choice", lambda seq: seq[0])

    engine = EmergentGrowthEngine()
    assert engine._generate_growth_narrative("情感支持", render_target_lang="ja") == "支える時間の中で、私のやわらかさも少し変わってきました。"
    assert engine._generate_growth_narrative("未知主题", render_target_lang="ja") == "最近のやり取りが、私を少し変えている気がします。"
