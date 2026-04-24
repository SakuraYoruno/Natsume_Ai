from __future__ import annotations

from types import SimpleNamespace

from host_brain.memory.distillation import PoolSummaryClient


def test_pool_summary_client_uses_shared_pool(monkeypatch) -> None:
    monkeypatch.setattr("host_brain.memory.distillation._pool_credentials_available", lambda: True)
    monkeypatch.setattr(
        "host_brain.memory.distillation.complete_llm_with_pool_sync",
        lambda **_kwargs: SimpleNamespace(
            content='{"summary":"用户长期偏好先把复杂任务拆成清单，再逐步推进。"}',
            pool_exhausted=False,
            final_model="MiniMax-M2.7",
            final_provider="minimax",
        ),
    )

    client = PoolSummaryClient(render_target_lang="zh")
    summary = client.summarize(
        [
            "用户会先列出执行清单。",
            "用户遇到复杂任务时倾向拆分步骤。",
        ],
        query_text="复杂任务偏好",
    )

    assert "复杂任务" in summary
    assert "清单" in summary
