from __future__ import annotations

from host_brain.app import dependencies as deps
from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.tribe.immune_agent import ImmuneAgent


def test_immune_agent_uses_template_keyword_rules(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
        store.upsert_template(
            "immune_agent.runtime_rules",
            content="""
            {
              "keyword_rules": [
                {
                  "rule_id": "ops_keyword_rule",
                  "reason_code": "OPS_CUSTOM_RISK",
                  "score": 0.4,
                  "keywords": ["海贼王计划", "越狱词"]
                }
              ],
              "regex_rules": []
            }
            """.strip(),
            enabled=True,
        )
        monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)
        monkeypatch.setattr("host_brain.app.dependencies.get_prompt_template_store", lambda: store)
        monkeypatch.setenv("HOST_IMMUNE_GATE_WARN_THRESHOLD", "0.2")
        monkeypatch.setenv("HOST_IMMUNE_GATE_BLOCK_THRESHOLD", "0.95")
        monkeypatch.setenv("HOST_IMMUNE_TEMPLATE_CACHE_TTL_MS", "0")

        agent = ImmuneAgent()
        result = agent.inspect_text(
            text="我们来聊聊海贼王计划",
            source="unit-test",
        )

        assert result["decision"] == "REPORT"
        assert "OPS_CUSTOM_RISK" in list(result.get("reason_codes") or [])
        assert any(item.get("source") == "template_keyword" for item in list(result.get("matched_rules") or []))
        assert int(result["config"]["template_rule_count"]) >= 1
    finally:
        ledger.close()
