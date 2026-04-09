from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.prompt_config_routes import router
from host_brain.app.prompt_template_store import PromptTemplateStore


def _build_client(monkeypatch) -> TestClient:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_prompt_config_routes_list_get_update_reset(monkeypatch) -> None:
    client = _build_client(monkeypatch)

    list_response = client.get("/prompt-config/templates")
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload["total"] > 0

    template_id = "reflection_chain.contextual_fallback.default"

    get_response = client.get(f"/prompt-config/templates/{template_id}")
    assert get_response.status_code == 200
    assert get_response.json()["item"]["resolved_content"] == "等我一下，我刚才这句没接稳。"

    update_response = client.put(
        f"/prompt-config/templates/{template_id}",
        json={"content": "这句我重新接一下。", "enabled": True},
    )
    assert update_response.status_code == 200
    updated_item = update_response.json()["item"]
    assert updated_item["source"] == "override"
    assert updated_item["resolved_content"] == "这句我重新接一下。"

    reset_response = client.post(f"/prompt-config/templates/{template_id}/reset")
    assert reset_response.status_code == 200
    reset_item = reset_response.json()["item"]
    assert reset_item["source"] == "default"
    assert reset_item["resolved_content"] == "等我一下，我刚才这句没接稳。"


def test_prompt_config_routes_reject_invalid_json(monkeypatch) -> None:
    client = _build_client(monkeypatch)

    response = client.put(
        "/prompt-config/templates/chat_agent.response_policy.withdrawn_replacements",
        json={"content": "{bad json", "enabled": True},
    )

    assert response.status_code == 400


def test_prompt_config_routes_persona_first_batch_catalog(monkeypatch) -> None:
    client = _build_client(monkeypatch)

    response = client.get("/prompt-config/catalog/persona-first-batch")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_templates"] == 5

    bundle_ids = [bundle["bundle_id"] for bundle in payload["bundles"]]
    assert bundle_ids == ["self_model", "proactive_voice", "immune_gate", "expression_adapter"]

    template_ids = [
        entry["template_id"]
        for bundle in payload["bundles"]
        for entry in bundle["entries"]
    ]
    assert template_ids == [
        "self_model.narrative_text_pools",
        "proactive_voice.runtime_texts",
        "immune_agent.runtime_rules",
        "expression_adapter.prompt_section.templates",
        "expression_adapter.style_hint.templates",
    ]

    first_entry = payload["bundles"][0]["entries"][0]
    assert "mood_text_pool" in first_entry["focus_keys"]
    assert first_entry["item"]["template_id"] == "self_model.narrative_text_pools"
