from __future__ import annotations

from types import SimpleNamespace

from host_brain.app.obs_routes_stats import get_retrieval_runtime


class _FakeLedger:
    @property
    def retrieval_event_log(self):
        return [
            {
                "request_id": "req-old",
                "op_kind": "PIPELINE",
                "status": "OK",
                "reason_code": None,
                "detail": "older row",
                "payload": {
                    "candidate_count": 2,
                    "rerank_topk": 2,
                    "embedding_model_id": "embed-old",
                    "embedding_provider": "sentence_transformers",
                    "effective_embedding_provider": "hash-embed-v1",
                    "fallback_embedding_used": True,
                    "fallback_embedding_reason_code": "RETR_EMBED_UNAVAILABLE",
                    "rerank_model_id": "rerank-old",
                    "rerank_provider": "qwen3_reranker",
                    "effective_rerank_provider": "fast_path_heuristic",
                    "fallback_reranker_used": True,
                    "fallback_reranker_reason_code": "RETR_RERANK_UNAVAILABLE",
                },
                "ts_ms": 1000,
            },
            {
                "request_id": "req-new",
                "op_kind": "PIPELINE",
                "status": "OK",
                "reason_code": None,
                "detail": "newer row",
                "payload": {
                    "candidate_count": 5,
                    "rerank_topk": 5,
                    "embedding_model_id": "embed-new",
                    "embedding_provider": "sentence_transformers",
                    "effective_embedding_provider": "sentence_transformers",
                    "fallback_embedding_used": False,
                    "fallback_embedding_reason_code": None,
                    "rerank_model_id": "rerank-new",
                    "rerank_provider": "qwen3_reranker",
                    "effective_rerank_provider": "qwen3_reranker",
                    "fallback_reranker_used": False,
                    "fallback_reranker_reason_code": None,
                },
                "ts_ms": 2000,
            },
            {
                "request_id": "req-legacy",
                "op_kind": "PIPELINE",
                "status": "OK",
                "reason_code": None,
                "detail": "legacy row without runtime model fields",
                "payload": {
                    "candidate_count": 1,
                    "rerank_topk": 1
                },
                "ts_ms": 1500,
            },
        ]


async def _fake_runtime_route_loader(*_args, **_kwargs):  # pragma: no cover
    raise AssertionError


def test_get_retrieval_runtime_returns_latest_and_counts(monkeypatch) -> None:
    monkeypatch.setattr("host_brain.app.obs_routes_stats._get_ledger", lambda: _FakeLedger())
    monkeypatch.setattr(
        "host_brain.retrieval.embedding.load_embedding_runtime_config",
        lambda env=None: SimpleNamespace(config=SimpleNamespace(enabled=True, default_route="host_default", routes={"host_default": SimpleNamespace(provider="sentence_transformers", model_id="embed-config", dim=1024, timeout_ms=20000, node_selector="default")}), success=True),
    )
    monkeypatch.setattr(
        "host_brain.retrieval.reranker_config.load_reranker_runtime_config",
        lambda env=None: SimpleNamespace(config=SimpleNamespace(enabled=True, default_route="host_default", routes={"host_default": SimpleNamespace(provider="qwen3_reranker", model_id="rerank-config", top_k=5, timeout_ms=30000, node_selector="default")}), success=True),
    )

    response = __import__("asyncio").run(get_retrieval_runtime(limit=10))

    assert response.latest is not None
    assert response.suite_profile is None
    assert response.episodic_write_embedding_model_id is None
    assert response.latest.request_id == "req-new"
    assert response.recent_total == 2
    assert response.recent_embedding_fallbacks == 1
    assert response.recent_reranker_fallbacks == 1
    assert response.embedding.routes[0].model_id == "embed-config"
    assert response.reranker.routes[0].model_id == "rerank-config"


def test_get_retrieval_runtime_can_select_specific_request(monkeypatch) -> None:
    monkeypatch.setattr("host_brain.app.obs_routes_stats._get_ledger", lambda: _FakeLedger())
    monkeypatch.setattr(
        "host_brain.retrieval.embedding.load_embedding_runtime_config",
        lambda env=None: SimpleNamespace(config=SimpleNamespace(enabled=True, default_route="host_default", routes={}), success=True),
    )
    monkeypatch.setattr(
        "host_brain.retrieval.reranker_config.load_reranker_runtime_config",
        lambda env=None: SimpleNamespace(config=SimpleNamespace(enabled=True, default_route="host_default", routes={}), success=True),
    )

    response = __import__("asyncio").run(get_retrieval_runtime(request_id="req-old", limit=10))

    assert response.latest is not None
    assert response.latest.request_id == "req-old"
    assert response.latest.fallback_embedding_used is True
    assert response.latest.fallback_reranker_used is True



def test_get_retrieval_runtime_reports_suite_profile(monkeypatch) -> None:
    monkeypatch.setattr("host_brain.app.obs_routes_stats._get_ledger", lambda: _FakeLedger())
    monkeypatch.setattr("host_brain.retrieval.embedding.load_embedding_runtime_config", lambda env=None: SimpleNamespace(config=SimpleNamespace(enabled=True, default_route="host_default", routes={}), success=True))
    monkeypatch.setattr("host_brain.retrieval.reranker_config.load_reranker_runtime_config", lambda env=None: SimpleNamespace(config=SimpleNamespace(enabled=True, default_route="host_default", routes={}), success=True))
    monkeypatch.setenv("RETR_MODEL_SUITE_PROFILE", "qwen3_0_6b_local")
    monkeypatch.setenv("RETR_MODEL_SUITE_FORCE", "true")

    response = __import__("asyncio").run(get_retrieval_runtime(limit=10))

    assert response.suite_profile == "qwen3_0_6b_local"
    assert response.suite_force is True



def test_get_retrieval_runtime_reports_write_side_embedding_hints(monkeypatch) -> None:
    monkeypatch.setattr("host_brain.app.obs_routes_stats._get_ledger", lambda: _FakeLedger())
    monkeypatch.setattr("host_brain.retrieval.embedding.load_embedding_runtime_config", lambda env=None: SimpleNamespace(config=SimpleNamespace(enabled=True, default_route="host_default", routes={}), success=True))
    monkeypatch.setattr("host_brain.retrieval.reranker_config.load_reranker_runtime_config", lambda env=None: SimpleNamespace(config=SimpleNamespace(enabled=True, default_route="host_default", routes={}), success=True))

    class _FakeStore:
        embedding_model_id_hint = "models/Qwen3-Embedding-0.6B"
        embedding_provider_hint = "sentence_transformers"

    monkeypatch.setattr("host_brain.app.dependencies.get_episodic_store", lambda: _FakeStore())

    response = __import__("asyncio").run(get_retrieval_runtime(request_id="req-new", limit=10))

    assert response.episodic_write_embedding_model_id == "models/Qwen3-Embedding-0.6B"
    assert response.episodic_write_embedding_provider == "sentence_transformers"
    assert response.read_write_model_aligned is False  # latest req-new uses embed-new in fixture



def test_get_retrieval_runtime_include_legacy_keeps_incomplete_rows(monkeypatch) -> None:
    monkeypatch.setattr("host_brain.app.obs_routes_stats._get_ledger", lambda: _FakeLedger())
    monkeypatch.setattr("host_brain.retrieval.embedding.load_embedding_runtime_config", lambda env=None: SimpleNamespace(config=SimpleNamespace(enabled=True, default_route="host_default", routes={}), success=True))
    monkeypatch.setattr("host_brain.retrieval.reranker_config.load_reranker_runtime_config", lambda env=None: SimpleNamespace(config=SimpleNamespace(enabled=True, default_route="host_default", routes={}), success=True))

    response = __import__("asyncio").run(get_retrieval_runtime(limit=10, include_legacy=True))

    assert response.recent_total == 3
    assert any(item.runtime_fields_complete is False for item in response.recent)
