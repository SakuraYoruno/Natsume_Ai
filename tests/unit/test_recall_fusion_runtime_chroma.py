from __future__ import annotations

from host_brain.retrieval.recall_fusion import RecallFusionServiceContract


def test_recall_fusion_defaults_to_runtime_chroma_client(monkeypatch) -> None:
    sentinel = object()
    monkeypatch.setattr("host_brain.retrieval.recall_fusion.build_runtime_chroma_client", lambda: sentinel)

    service = RecallFusionServiceContract(chroma_client=None, use_hybrid=False)

    assert service.chroma is sentinel
