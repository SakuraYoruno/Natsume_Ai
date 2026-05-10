from __future__ import annotations

import asyncio

from tools.chat_ab_generate import (
    ab_eval_environment,
    build_arg_parser,
    generate_candidates,
    is_retryable_generation_error,
    parse_variants,
)


def test_parse_variants_accepts_current_baseline_and_kernel() -> None:
    assert parse_variants("current,soul_only_agent") == ["current", "soul_only_agent"]
    assert parse_variants("current,baseline_soul") == ["current", "baseline_soul"]
    assert parse_variants("current,kernel_v2") == ["current", "kernel_v2"]
    assert parse_variants("current,kernel_v2_soul_full") == ["current", "kernel_v2_soul_full"]
    assert parse_variants("kernel_v2_style_bank,kernel_v2_no_style_bank") == [
        "kernel_v2_style_bank",
        "kernel_v2_no_style_bank",
    ]
    assert parse_variants("kernel_v2_soul_only,kernel_v2_no_turn_directive,kernel_v2_no_rerank") == [
        "kernel_v2_soul_only",
        "kernel_v2_no_turn_directive",
        "kernel_v2_no_rerank",
    ]
    assert parse_variants("current,current_no_contract_prompt") == ["current", "current_no_contract_prompt"]
    assert parse_variants("current,current_legacy_contract_prompt") == ["current", "current_legacy_contract_prompt"]


def test_arg_parser_defaults_to_kernel_without_candidate_pool() -> None:
    args = build_arg_parser().parse_args(
        ["--cases", "tests/fixtures/chat_ab/cases.jsonl", "--out", "artifacts/chat_ab/test"]
    )

    assert args.variants == "current,kernel_v2"
    assert not hasattr(args, "candidate_count")


def test_generate_candidates_mock_outputs_current_and_baseline() -> None:
    rows = asyncio.run(
        generate_candidates(
            cases=[{"case_id": "case_1", "user_message": "hello", "history_turns": []}],
            variants=[
                "current",
                "soul_only_agent",
                "baseline_soul",
            ],
            mock=True,
        )
    )

    assert [row["variant"] for row in rows] == [
        "current",
        "soul_only_agent",
        "baseline_soul",
    ]
    assert {row["case_id"] for row in rows} == {"case_1"}
    assert all(row["ok"] for row in rows)
    assert all(row["envelope"]["cacheable"] is False for row in rows)
    assert rows[1]["trace"]["source"] == "soul_only_agent"


def test_generate_candidates_mock_outputs_current_and_kernel_v2() -> None:
    rows = asyncio.run(
        generate_candidates(
            cases=[{"case_id": "case_1", "user_message": "hello", "history_turns": []}],
            variants=["current", "kernel_v2"],
            mock=True,
        )
    )

    assert [row["variant"] for row in rows] == ["current", "kernel_v2"]
    assert rows[1]["trace"]["source"] == "chat_kernel_v2_mock"
    assert rows[1]["envelope"]["source"] == "kernel_v2"


def test_generate_candidates_mock_outputs_style_bank_kernel_variants() -> None:
    rows = asyncio.run(
        generate_candidates(
            cases=[{"case_id": "case_1", "user_message": "hello", "history_turns": []}],
            variants=["kernel_v2_no_style_bank", "kernel_v2_style_bank"],
            mock=True,
        )
    )

    assert [row["variant"] for row in rows] == ["kernel_v2_no_style_bank", "kernel_v2_style_bank"]
    assert rows[0]["envelope"]["source"] == "kernel_v2_no_style_bank"
    assert rows[1]["envelope"]["source"] == "kernel_v2_style_bank"


def test_generate_candidates_mock_outputs_generation_ablation_variants() -> None:
    rows = asyncio.run(
        generate_candidates(
            cases=[{"case_id": "case_1", "user_message": "hello", "history_turns": []}],
            variants=["kernel_v2_soul_only", "kernel_v2_no_turn_directive", "kernel_v2_no_rerank"],
            mock=True,
        )
    )

    assert [row["variant"] for row in rows] == [
        "kernel_v2_soul_only",
        "kernel_v2_no_turn_directive",
        "kernel_v2_no_rerank",
    ]
    assert [row["envelope"]["source"] for row in rows] == [
        "kernel_v2_soul_only",
        "kernel_v2_no_turn_directive",
        "kernel_v2_no_rerank",
    ]


def test_generate_candidates_mock_outputs_current_no_contract_variant() -> None:
    rows = asyncio.run(
        generate_candidates(
            cases=[{"case_id": "case_1", "user_message": "hello", "history_turns": []}],
            variants=["current", "current_no_contract_prompt"],
            mock=True,
        )
    )

    assert [row["variant"] for row in rows] == ["current", "current_no_contract_prompt"]


def test_generate_candidates_mock_outputs_current_legacy_contract_variant() -> None:
    rows = asyncio.run(
        generate_candidates(
            cases=[{"case_id": "case_1", "user_message": "hello", "history_turns": []}],
            variants=["current", "current_legacy_contract_prompt"],
            mock=True,
        )
    )

    assert [row["variant"] for row in rows] == ["current", "current_legacy_contract_prompt"]


def test_ab_eval_environment_can_disable_reply_contract_prompt(monkeypatch) -> None:
    monkeypatch.delenv("HOST_CHAT_DISABLE_REPLY_CONTRACT_PROMPT", raising=False)

    with ab_eval_environment(disable_reply_contract_prompt=True):
        import os

        assert os.environ["HOST_CHAT_DISABLE_REPLY_CONTRACT_PROMPT"] == "true"

    import os

    assert "HOST_CHAT_DISABLE_REPLY_CONTRACT_PROMPT" not in os.environ


def test_ab_eval_environment_can_enable_legacy_reply_contract_prompt(monkeypatch) -> None:
    monkeypatch.delenv("HOST_CHAT_LEGACY_REPLY_CONTRACT_PROMPT", raising=False)

    with ab_eval_environment(legacy_reply_contract_prompt=True):
        import os

        assert os.environ["HOST_CHAT_LEGACY_REPLY_CONTRACT_PROMPT"] == "true"

    import os

    assert "HOST_CHAT_LEGACY_REPLY_CONTRACT_PROMPT" not in os.environ


def test_generate_baseline_candidate_applies_model_override(monkeypatch) -> None:
    captured = {}

    async def _fake_complete(**kwargs):  # noqa: ANN003
        captured["model_id"] = kwargs.get("model_id")
        return type(
            "Result",
            (),
            {
                "content": "ok",
                "final_model": kwargs.get("model_id") or "",
                "final_provider": kwargs.get("provider_name") or "",
                "pool_exhausted": False,
            },
        )()

    monkeypatch.setattr("tools.chat_ab_generate.acomplete_llm_with_pool", _fake_complete)
    monkeypatch.setattr(
        "tools.chat_ab_generate.get_compiled_soul",
        lambda: type("Soul", (), {"runtime_full_text": "Soul"})(),
    )
    async def _fake_runtime():
        return {"model_id": "gemini-2.5-pro", "chat_model_id": "gemini-2.5-pro"}

    monkeypatch.setattr("tools.chat_ab_generate.resolve_chat_runtime", _fake_runtime)

    rows = asyncio.run(
        generate_candidates(
            cases=[{"case_id": "case_1", "user_message": "hello"}],
            variants=["baseline_soul"],
            mock=False,
            baseline_model_id="gemini-2.5-flash-lite",
        )
    )

    assert rows[0]["text"] == "ok"
    assert captured["model_id"] == "gemini-2.5-flash-lite"


def test_soul_only_agent_uses_raw_soul_md_without_compiled_wrapper(monkeypatch, tmp_path) -> None:
    captured = {}
    soul_path = tmp_path / "Soul.md"
    soul_path.write_text("RAW_SOUL_MARKER\n只按这份 Soul 说话。", encoding="utf-8")
    monkeypatch.setenv("SOUL_MD_PATH", str(soul_path))

    async def _fake_complete(**kwargs):  # noqa: ANN003
        captured["messages"] = kwargs.get("messages")
        captured["scene_type"] = kwargs.get("scene_type")
        return type(
            "Result",
            (),
            {
                "content": "ok",
                "final_model": "",
                "final_provider": "",
                "pool_exhausted": False,
            },
        )()

    async def _fake_runtime():
        return {}

    monkeypatch.setattr("tools.chat_ab_generate.acomplete_llm_with_pool", _fake_complete)
    monkeypatch.setattr("tools.chat_ab_generate.resolve_chat_runtime", _fake_runtime)

    rows = asyncio.run(
        generate_candidates(
            cases=[{"case_id": "case_1", "user_message": "hello"}],
            variants=["soul_only_agent"],
            mock=False,
        )
    )

    assert rows[0]["text"] == "ok"
    assert captured["scene_type"] == "CHAT_AB_SOUL_ONLY"
    assert captured["messages"][0] == {"role": "system", "content": "RAW_SOUL_MARKER\n只按这份 Soul 说话。"}


def test_generate_baseline_candidate_applies_provider_override(monkeypatch) -> None:
    captured = {}

    async def _fake_complete(**kwargs):  # noqa: ANN003
        captured["provider_name"] = kwargs.get("provider_name")
        return type(
            "Result",
            (),
            {
                "content": "ok",
                "final_model": kwargs.get("model_id") or "",
                "final_provider": kwargs.get("provider_name") or "",
                "pool_exhausted": False,
            },
        )()

    monkeypatch.setattr("tools.chat_ab_generate.acomplete_llm_with_pool", _fake_complete)
    monkeypatch.setattr(
        "tools.chat_ab_generate.get_compiled_soul",
        lambda: type("Soul", (), {"runtime_full_text": "Soul"})(),
    )

    async def _fake_runtime():
        return {"provider_name": "gemini", "chat_provider_name": "gemini"}

    monkeypatch.setattr("tools.chat_ab_generate.resolve_chat_runtime", _fake_runtime)

    rows = asyncio.run(
        generate_candidates(
            cases=[{"case_id": "case_1", "user_message": "hello"}],
            variants=["baseline_soul"],
            mock=False,
            baseline_provider="minimax",
            baseline_model_id="M2-her",
        )
    )

    assert rows[0]["text"] == "ok"
    assert captured["provider_name"] == "minimax"


def test_retryable_generation_error_detects_pool_exhaustion() -> None:
    assert is_retryable_generation_error({"error": "RuntimeError('LLM_POOL_EXHAUSTED')"})
    assert is_retryable_generation_error({"trace": {"error": "RESOURCE_EXHAUSTED: quota"}})
    assert not is_retryable_generation_error({"error": "ValueError('bad case')"})


def test_generate_candidates_retries_pool_exhausted_baseline_case(monkeypatch) -> None:
    calls = 0

    async def _fake_complete(**kwargs):  # noqa: ANN003
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("LLM_POOL_EXHAUSTED")
        return type(
            "Result",
            (),
            {
                "content": "ok",
                "final_model": "",
                "final_provider": "",
                "pool_exhausted": False,
            },
        )()

    async def _fake_runtime():
        return {}

    monkeypatch.setattr("tools.chat_ab_generate.acomplete_llm_with_pool", _fake_complete)
    monkeypatch.setattr(
        "tools.chat_ab_generate.get_compiled_soul",
        lambda: type("Soul", (), {"runtime_full_text": "Soul"})(),
    )
    monkeypatch.setattr("tools.chat_ab_generate.resolve_chat_runtime", _fake_runtime)

    rows = asyncio.run(
        generate_candidates(
            cases=[{"case_id": "case_1", "user_message": "hello"}],
            variants=["baseline_soul"],
            mock=False,
            max_case_retries=1,
            error_backoff_ms=0,
        )
    )

    assert calls == 2
    assert rows[0]["ok"] is True
    assert rows[0]["trace"]["generation_retry_attempts"] == 1
