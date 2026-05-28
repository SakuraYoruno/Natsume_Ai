from __future__ import annotations

from host_brain.skills.codex_runtime_registry import build_codex_runtime_skill_registry


def test_codex_runtime_skill_registry_contains_new_skill_ids() -> None:
    registry = build_codex_runtime_skill_registry()

    assert {
        "codex_inspect",
        "codex_worker",
        "codex_self_heal",
        "codex_self_evolve",
        "codex_sql_audit",
        "codex_translate_project",
        "codex_skill_forge",
        "codex_research",
        "codex_organ_scan",
        "codex_persona_drift_audit",
        "codex_dream_compile",
        "codex_workbench_brief",
        "codex_repo_sentinel",
        "codex_memory_curator",
        "codex_document_digest",
        "codex_frontend_review",
        "codex_browser_research",
        "codex_frontend_acceptance",
        "codex_web_corpus_collector",
        "codex_lightnovel_recommender",
        "codex_epub_builder",
    }.issubset(set(registry.list_skill_names()))


def test_codex_worker_skill_emits_work_order_payload() -> None:
    registry = build_codex_runtime_skill_registry()
    payload = registry.require("codex_worker").handler(
        {"task": "fix the route", "confirmed": True},
        {"request_id": "req-worker"},
    )

    order = payload["work_order"]
    assert payload["tool"] == "codex_runtime"
    assert order["run_mode"] == "worker"
    assert order["allowed_actions"] == ["read_files", "generate_report", "write_files", "run_tests"]
    assert order["approval_policy"]["confirmed"] is True
    assert order["review_policy"]["review_required"] is True


def test_codex_self_heal_skill_targets_immune_domain() -> None:
    registry = build_codex_runtime_skill_registry()
    payload = registry.require("codex_self_heal").handler(
        {"task": "repair degraded health", "confirmed": True},
        {"request_id": "req-heal"},
    )

    order = payload["work_order"]
    assert order["domain"] == "immune"
    assert order["run_mode"] == "self_heal"


def test_codex_production_skill_emits_async_persona_bounded_work_order() -> None:
    registry = build_codex_runtime_skill_registry()
    payload = registry.require("codex_memory_curator").handler(
        {"task": "整理最近对话里的长期记忆候选", "start_background": False},
        {"request_id": "req-production-memory"},
    )

    order = payload["work_order"]
    metadata = order["metadata"]
    boundary = metadata["persona_boundary"]

    assert payload["tool"] == "codex_production"
    assert payload["action"] == "enqueue"
    assert payload["start_background"] is False
    assert order["domain"] == "memory"
    assert order["run_mode"] == "inspect"
    assert metadata["codex_production"] is True
    assert metadata["codex_background"] is True
    assert metadata["surface_main_chain_required"] is True
    assert boundary["surface_owner"] == "host_main_persona"
    assert "write_long_term_memory" in order["forbidden_actions"]
    assert "send_external_message" in order["forbidden_actions"]


def test_codex_frontend_acceptance_skill_uses_browser_readonly_contract() -> None:
    registry = build_codex_runtime_skill_registry()
    payload = registry.require("codex_frontend_acceptance").handler(
        {
            "task": "使用无头浏览器验证前端效果",
            "urls": ["http://127.0.0.1:5178/monitor/#/digital-life"],
            "start_background": False,
        },
        {"request_id": "req-production-frontend-acceptance"},
    )

    order = payload["work_order"]

    assert payload["tool"] == "codex_production"
    assert order["run_mode"] == "inspect"
    assert order["workspace"]["strategy"] == "runtime_repo"
    assert "use_agent_browser" in order["allowed_actions"]
    assert "capture_screenshots" in order["allowed_actions"]
    assert "write_files" not in order["allowed_actions"]
    assert "screenshots/" in order["input_pack"]["artifact_contract"]
    assert order["input_pack"]["urls"] == ["http://127.0.0.1:5178/monitor/#/digital-life"]


def test_codex_browser_research_skill_carries_source_limits() -> None:
    registry = build_codex_runtime_skill_registry()
    payload = registry.require("codex_browser_research").handler(
        {
            "task": "查阅这几个商品页面并整理证据",
            "urls": ["https://example.test/a", "https://example.test/b"],
            "max_sources": 2,
            "timeout_seconds": 9,
            "start_background": False,
        },
        {"request_id": "req-browser-research"},
    )

    order = payload["work_order"]

    assert payload["tool"] == "codex_production"
    assert order["run_mode"] == "research"
    assert order["workspace"]["strategy"] == "temp_workspace"
    assert "use_agent_browser" in order["allowed_actions"]
    assert "read_public_web_pages" in order["allowed_actions"]
    assert "screenshots/" in order["input_pack"]["artifact_contract"]
    assert order["input_pack"]["urls"] == ["https://example.test/a", "https://example.test/b"]
    assert order["input_pack"]["max_sources"] == 2
    assert order["input_pack"]["timeout_seconds"] == 9


def test_codex_epub_builder_skill_is_rights_gated_temp_workspace_worker() -> None:
    registry = build_codex_runtime_skill_registry()
    payload = registry.require("codex_epub_builder").handler(
        {
            "task": "把我有授权的轻小说文本和图片封装成 epub",
            "source_paths": ["D:/authorized/book"],
            "start_background": False,
        },
        {"request_id": "req-production-epub"},
    )

    order = payload["work_order"]

    assert payload["tool"] == "codex_production"
    assert order["run_mode"] == "worker"
    assert order["workspace"]["strategy"] == "temp_workspace"
    assert "write_files" in order["allowed_actions"]
    assert "build_epub" in order["allowed_actions"]
    assert "use_authenticated_browser_session" in order["allowed_actions"]
    assert "inspect_account_owned_content" in order["allowed_actions"]
    assert "bypass_paywall" in order["forbidden_actions"]
    assert "bypass_drm" in order["forbidden_actions"]
    assert "package_unauthorized_copyrighted_content" in order["forbidden_actions"]
    assert "spend_points_without_confirmation" in order["forbidden_actions"]
    assert "book.epub" in order["input_pack"]["artifact_contract"]
    assert order["input_pack"]["source_paths"] == ["D:/authorized/book"]
    assert order["approval_policy"]["require_confirm"] is False


def test_codex_epub_builder_carries_recommendation_selection_context() -> None:
    registry = build_codex_runtime_skill_registry()
    payload = registry.require("codex_epub_builder").handler(
        {
            "task": "推荐的第一本",
            "recommendations_path": "D:/Natsume_Ai/artifacts/lightnovel/recommendations.json",
            "selected_recommendation": {
                "title": "身为路人祭司的我，发现世界是乙女游戏于是开始培养女主角 [第一卷]",
                "detail_url": "https://www.lightnovel.fun/detail/1144163",
            },
            "start_background": False,
        },
        {"request_id": "req-production-epub-selection"},
    )

    order = payload["work_order"]

    assert order["input_pack"]["recommendations_path"].endswith("recommendations.json")
    assert order["input_pack"]["recommendation_index"] == 1
    assert order["input_pack"]["selected_recommendation"]["detail_url"] == "https://www.lightnovel.fun/detail/1144163"
    assert order["input_pack"]["site_adapter"] == ""


def test_codex_epub_builder_blocks_background_purchase_until_confirmation() -> None:
    registry = build_codex_runtime_skill_registry()
    payload = registry.require("codex_epub_builder").handler(
        {
            "task": "登录我的账号并使用积分购买这本轻小说后封装成 epub",
            "urls": ["https://www.lightnovel.fun/cn/detail/1144366"],
            "max_points": 30,
        },
        {"request_id": "req-production-epub-purchase"},
    )

    order = payload["work_order"]

    assert payload["tool"] == "codex_production"
    assert payload["start_background"] is False
    assert payload["blocked_until_confirmation"] is True
    assert order["approval_policy"]["require_confirm"] is True
    assert order["approval_policy"]["confirmed"] is False
    assert order["approval_policy"]["confirm_scope"] == "spend_account_points"
    assert order["approval_policy"]["max_points"] == 30
    assert order["input_pack"]["purchase_requested"] is True
    assert order["input_pack"]["purchase_confirmed"] is False
    assert "purchase_with_confirmed_points" in order["allowed_actions"]
    assert "purchase_without_user_confirmation" in order["forbidden_actions"]


def test_codex_epub_builder_does_not_treat_generic_approval_as_purchase_confirmation() -> None:
    registry = build_codex_runtime_skill_registry()
    payload = registry.require("codex_epub_builder").handler(
        {
            "task": "登录我的账号并使用积分购买这本轻小说后封装成 epub，最多花 30 点",
            "approval_confirmed": True,
            "start_background": True,
        },
        {"request_id": "req-production-epub-generic-approval"},
    )

    order = payload["work_order"]

    assert payload["start_background"] is False
    assert payload["blocked_until_confirmation"] is True
    assert order["approval_policy"]["require_confirm"] is True
    assert order["approval_policy"]["confirmed"] is False
    assert order["input_pack"]["purchase_confirmed"] is False


def test_codex_epub_builder_extracts_max_points_from_purchase_text() -> None:
    registry = build_codex_runtime_skill_registry()
    payload = registry.require("codex_epub_builder").handler(
        {"task": "登录我的账号买这本轻小说并封装 epub，最多花 30 点"},
        {"request_id": "req-production-epub-points"},
    )

    order = payload["work_order"]

    assert payload["start_background"] is False
    assert order["approval_policy"]["require_confirm"] is True
    assert order["approval_policy"]["max_points"] == 30
    assert order["input_pack"]["max_points"] == 30


def test_codex_lightnovel_recommender_uses_public_metadata_contract() -> None:
    registry = build_codex_runtime_skill_registry()
    explicit_urls = ["https://www.lightnovel.fun/cn/category/3/106?type=1"]
    payload = registry.require("codex_lightnovel_recommender").handler(
        {
            "task": "从每日榜单和最新资源抓取封面和简介给我推荐轻小说",
            "urls": explicit_urls,
            "start_background": False,
        },
        {"request_id": "req-lightnovel-recommender"},
    )

    order = payload["work_order"]

    assert payload["tool"] == "codex_production"
    assert order["run_mode"] == "worker"
    assert order["workspace"]["strategy"] == "temp_workspace"
    assert "read_public_web_pages" in order["allowed_actions"]
    assert "write_files" in order["allowed_actions"]
    assert "write_files" not in order["forbidden_actions"]
    assert "read_paid_content" in order["forbidden_actions"]
    assert "bulk_scrape_article_bodies" in order["forbidden_actions"]
    assert "recommendations.json" in order["input_pack"]["artifact_contract"]
    assert "cover_manifest.json" in order["input_pack"]["artifact_contract"]
    assert order["input_pack"]["urls"] == explicit_urls


def test_codex_lightnovel_recommender_uses_declared_defaults_without_urls() -> None:
    registry = build_codex_runtime_skill_registry()
    payload = registry.require("codex_lightnovel_recommender").handler(
        {
            "task": "调用今日推荐：从每日榜单或者最新资源里抓封面和简介推荐轻小说",
            "start_background": False,
        },
        {
            "request_id": "req-lightnovel-recommender-defaults",
            "session_id": "sess-lightnovel-defaults",
            "person_id": "owner",
        },
    )

    order = payload["work_order"]

    assert order["metadata"]["session_id"] == "sess-lightnovel-defaults"
    assert order["metadata"]["person_id"] == "owner"
    assert order["input_pack"]["site_adapter"] == "lightnovel.fun"
    assert order["input_pack"]["urls"] == [
        "https://www.lightnovel.fun/",
        "https://www.lightnovel.fun/cn/category/3/106?type=1",
    ]
    assert order["input_pack"]["driver_kind"] == "natsume_daily_recommendation"
    assert payload["start_background"] is False
