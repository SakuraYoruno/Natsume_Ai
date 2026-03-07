from __future__ import annotations


def test_embedding_retries_once_after_slow_cold_start(monkeypatch) -> None:
    from host_brain.retrieval import embedding as embmod

    call_count = 0
    perf_values = [0.0, 0.21, 0.21, 0.25]

    def _fake_perf_counter() -> float:
        return perf_values.pop(0)

    def _provider(text, route):  # noqa: ANN001
        nonlocal call_count
        call_count += 1
        return [0.1, 0.2], route.model_id

    runtime_config = embmod.EmbeddingRuntimeConfig(
        enabled=True,
        default_route="trial",
        routes={
            "trial": embmod.EmbeddingRouteConfig(
                route_id="trial",
                provider="sentence_transformers",
                model_id="local-embed",
                dim=2,
                timeout_ms=100,
                node_selector="default",
            )
        },
    )
    monkeypatch.setattr(embmod, "perf_counter", _fake_perf_counter)

    service = embmod.EmbeddingServiceContract(
        runtime_config=runtime_config,
        provider_registry={"sentence_transformers": _provider},
        available=True,
    )
    result = service.embed(request_id="req-embed-cold-start", text="hello")

    assert result.success
    assert result.output["warm_retry_used"] is True
    assert call_count == 2


def test_embedding_falls_back_after_repeated_slow_local_calls(monkeypatch) -> None:
    from host_brain.retrieval import embedding as embmod

    call_count = 0
    perf_values = [0.0, 0.21, 0.21, 0.45]

    def _fake_perf_counter() -> float:
        return perf_values.pop(0)

    def _provider(text, route):  # noqa: ANN001
        nonlocal call_count
        call_count += 1
        return [0.1, 0.2], route.model_id

    runtime_config = embmod.EmbeddingRuntimeConfig(
        enabled=True,
        default_route="trial",
        routes={
            "trial": embmod.EmbeddingRouteConfig(
                route_id="trial",
                provider="sentence_transformers",
                model_id="local-embed",
                dim=4,
                timeout_ms=100,
                node_selector="default",
            )
        },
    )
    monkeypatch.setattr(embmod, "perf_counter", _fake_perf_counter)

    service = embmod.EmbeddingServiceContract(
        runtime_config=runtime_config,
        provider_registry={"sentence_transformers": _provider},
        available=True,
    )
    result = service.embed(request_id="req-embed-fallback", text="hello")

    assert result.success
    assert result.output["warm_retry_used"] is True
    assert result.output["fallback_used"] is True
    assert result.output["effective_provider"] == embmod.DEFAULT_EMBED_PROVIDER
    assert result.output["embedding_model_id"] == "hash-embed-v1"
    assert len(result.output["embedding_vector"]) == 4
    assert call_count == 2


def test_reranker_retries_once_after_slow_cold_start(monkeypatch) -> None:
    from host_brain.retrieval import reranker as rermod

    call_count = 0
    perf_values = [0.0, 0.31, 0.31, 0.35]

    def _fake_perf_counter() -> float:
        return perf_values.pop(0)

    def _provider(query, candidates, route):  # noqa: ANN001
        nonlocal call_count
        call_count += 1
        return [0.9 for _ in candidates], route.model_id

    runtime_config = rermod.RerankRuntimeConfig(
        enabled=True,
        default_route="trial",
        routes={
            "trial": rermod.RerankRouteConfig(
                route_id="trial",
                provider="qwen3_reranker",
                model_id="local-rerank",
                top_k=2,
                timeout_ms=100,
                node_selector="default",
            )
        },
    )
    monkeypatch.setattr(rermod, "perf_counter", _fake_perf_counter)

    service = rermod.RerankerServiceContract(
        runtime_config=runtime_config,
        provider_registry={"qwen3_reranker": _provider},
        available=True,
    )
    result = service.rerank(
        request_id="req-rerank-cold-start",
        query="natsume",
        candidates=[
            {"doc_id": "m1", "text": "natsume and youkai"},
            {"doc_id": "m2", "text": "weather forecast"},
        ],
        top_k=1,
    )

    assert result.success
    assert result.output["warm_retry_used"] is True
    assert call_count == 2
