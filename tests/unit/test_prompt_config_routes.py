from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.prompt_config_routes import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_prompt_config_templates_returns_versioned_prompt_spec_catalog() -> None:
    response = _client().get("/prompt-config/templates")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] > 0
    assert any(item["template_id"] == "react_loop.prompt_templates" for item in payload["items"])
    assert all(item["source"] == "prompt_spec" for item in payload["items"])
    assert "retired" not in payload


def test_prompt_config_template_detail_exposes_prompt_spec_shape() -> None:
    response = _client().get("/prompt-config/templates/multimodal_observer.model_prompt.templates")

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["template_id"] == "multimodal_observer.model_prompt.templates"
    assert item["source"] == "prompt_spec"
    assert item["version"].startswith("v")
    assert item["content_type"] == "json"
    assert "image_system_prompt" in item["resolved_content"]


def test_prompt_config_persona_first_batch_returns_empty_compatibility_catalog() -> None:
    response = _client().get("/prompt-config/catalog/persona-first-batch")

    assert response.status_code == 200
    payload = response.json()
    assert payload["bundles"] == []
    assert payload["bundle_count"] == 0
    assert payload["template_count"] == 0
    assert "retired" not in payload
