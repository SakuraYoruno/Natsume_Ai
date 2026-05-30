from __future__ import annotations

import json
from pathlib import Path

from host_brain.codex_runtime.artifact_store import CodexArtifactStore
from host_brain.codex_runtime.browser_researcher import _extract_urls, _parse_html_document
from host_brain.codex_runtime.production_executor import (
    CodexProductionJob,
    _build_browser_research_gateway_message,
    _execute_worker,
    _find_lightnovel_epub_file,
    _maybe_push_lightnovel_epub_to_gateway,
    _build_lightnovel_gateway_message,
    _build_lightnovel_gateway_card,
    _coerce_recommendation_items,
    _find_lightnovel_recommendations_file,
    _lightnovel_recommendations_from_browser_items,
    _maybe_push_browser_research_to_gateway,
    _maybe_push_lightnovel_recommendations_to_gateway,
    _parse_lightnovel_count,
    _parse_lightnovel_update_age_days,
    _persist_production_job_memory,
    _persist_lightnovel_gateway_delivery_memory,
    _rank_lightnovel_recommendations,
    execute_codex_production_payload,
    execute_lightnovel_recommender_work_order,
    get_codex_production_job,
)
from host_brain.codex_runtime.production_studio import build_production_work_order
from host_brain.codex_runtime.prompt_profiles import build_codex_prompt
from host_brain.codex_runtime.work_order import CodexWorkOrder


def test_codex_production_executor_queues_without_blocking(tmp_path: Path) -> None:
    work_order = build_production_work_order(
        {
            "task": "整理最近对话里的长期记忆候选",
            "request_id": "req-prod-queue",
            "artifacts_root": str(tmp_path),
        },
        skill_id="codex_memory_curator",
    )

    result = execute_codex_production_payload(
        {
            "work_order": work_order,
            "start_background": False,
            "artifacts_root": str(tmp_path),
        }
    )

    run_dir = Path(result["artifacts_dir"])
    contract = json.loads(run_dir.joinpath("result_contract.json").read_text(encoding="utf-8"))
    job = get_codex_production_job(result["job_id"])

    assert result["status"] == "OK"
    assert result["result_code"] == "CODEX_PRODUCTION_QUEUED"
    assert result["codex_background"] is True
    assert result["surface_main_chain_required"] is True
    assert contract["status"] == "QUEUED"
    assert contract["metadata"]["persona_boundary"]["surface_owner"] == "host_main_persona"
    assert job is not None
    assert job["status"] == "QUEUED"
    assert run_dir.joinpath("work_order.json").exists()
    assert run_dir.joinpath("production_job.json").exists()


def test_codex_production_job_memory_uses_original_chat_context(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class _Ledger:
        intent_log = [
            {
                "request_id": "req-prod-memory",
                "intent_id": "intent-prod-memory",
                "session_id": "sess-prod-memory",
                "normalized_json": json.dumps(
                    {"person_id": "person-prod-memory", "session_id": "sess-prod-memory"},
                    ensure_ascii=False,
                ),
            }
        ]

    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: _Ledger())
    monkeypatch.setattr(
        "host_brain.app.observability_persist.persist_background_event_memory",
        lambda **kwargs: captured.update(kwargs) or {"status": "OK"},
    )

    order = CodexWorkOrder.from_mapping(
        build_production_work_order(
            {"task": "调用今日推荐", "request_id": "req-prod-memory", "artifacts_root": str(tmp_path)},
            skill_id="codex_lightnovel_recommender",
        )
    )
    job = CodexProductionJob(
        job_id="job-prod-memory",
        work_order_id=order.work_order_id,
        skill_id="codex_lightnovel_recommender",
        status="FAILED",
        artifacts_dir=str(tmp_path),
        result_contract_ref=str(tmp_path / "result_contract.json"),
        request_id="req-prod-memory",
        detail="Native lightnovel recommender found no usable recommendation candidates.",
        error="LIGHTNOVEL_NATIVE_NO_RECOMMENDATIONS",
        finished_ts_ms=1234,
    )

    _persist_production_job_memory(
        job=job,
        order=order,
        result={
            "status": "BLOCKED",
            "summary": "Native lightnovel recommender found no usable recommendation candidates.",
            "reason_code": "LIGHTNOVEL_NATIVE_NO_RECOMMENDATIONS",
            "result_text": "# 轻小说推荐报告\n\n- 推荐候选：0",
        },
    )

    assert captured["event_source"] == "codex_production"
    assert captured["request_id"] == "req-prod-memory"
    assert captured["intent_id"] == "intent-prod-memory"
    assert captured["session_id"] == "sess-prod-memory"
    assert captured["person_id"] == "person-prod-memory"
    assert captured["reason_code"] == "LIGHTNOVEL_NATIVE_NO_RECOMMENDATIONS"
    assert "推荐候选：0" in str(captured["detail"])


def test_codex_production_executor_rejects_missing_persona_boundary(tmp_path: Path) -> None:
    work_order = build_production_work_order(
        {"task": "scan repo", "artifacts_root": str(tmp_path)},
        skill_id="codex_repo_sentinel",
    )
    work_order["metadata"].pop("persona_boundary", None)

    result = execute_codex_production_payload(
        {"work_order": work_order, "start_background": False, "artifacts_root": str(tmp_path)}
    )

    assert result["status"] == "FAILED"
    assert result["reason_code"] == "CODEX_PRODUCTION_CONTRACT_INVALID"
    assert "persona_boundary" in result["detail"]


def test_codex_production_prompt_preserves_persona_boundary(tmp_path: Path) -> None:
    order = CodexWorkOrder.from_mapping(
        build_production_work_order(
            {"task": "生成工作简报", "artifacts_root": str(tmp_path)},
            skill_id="codex_workbench_brief",
        )
    )

    prompt = build_codex_prompt(order, workspace=tmp_path)

    assert "Production background role" in prompt
    assert "silent worker" in prompt
    assert "Host surface/main chain owns final user-facing expression" in prompt
    assert "Do not speak as Natsume" in prompt
    assert "write_long_term_memory" in prompt
    assert "persona_final_reply" in prompt


def test_codex_production_browser_artifact_contract_allows_temp_workspace_outputs(tmp_path: Path) -> None:
    work_order = build_production_work_order(
        {
            "task": "收集公开授权网页训练语料",
            "urls": ["https://example.com/public-domain"],
            "artifacts_root": str(tmp_path),
        },
        skill_id="codex_web_corpus_collector",
    )

    result = execute_codex_production_payload(
        {
            "work_order": work_order,
            "start_background": False,
            "artifacts_root": str(tmp_path),
        }
    )

    run_dir = Path(result["artifacts_dir"])
    queued_order = json.loads(run_dir.joinpath("work_order.json").read_text(encoding="utf-8"))
    contract = json.loads(run_dir.joinpath("result_contract.json").read_text(encoding="utf-8"))

    assert result["status"] == "OK"
    assert queued_order["run_mode"] == "worker"
    assert queued_order["workspace"]["strategy"] == "temp_workspace"
    assert "write_files" in queued_order["allowed_actions"]
    assert "bypass_paywall" in queued_order["forbidden_actions"]
    assert "corpus_manifest.json" in queued_order["input_pack"]["artifact_contract"]
    assert queued_order["input_pack"]["urls"] == ["https://example.com/public-domain"]
    assert "source_manifest" in contract["metadata"]["result_contract"]["allowed_sections"]
    assert "artifact_manifest" in contract["metadata"]["result_contract"]["allowed_sections"]


def test_browser_research_native_worker_builds_report_from_local_html(tmp_path: Path) -> None:
    html_path = tmp_path / "product.html"
    html_path.write_text(
        """<!doctype html><html><head><title>测试商品 A</title>
<meta name="description" content="轻量、安静、适合桌面长期使用。"></head>
<body><main><h1>测试商品 A</h1><p>价格 299 元，保修两年，适合需要静音和低功耗的用户。</p></main></body></html>""",
        encoding="utf-8",
    )
    order = CodexWorkOrder.from_mapping(
        build_production_work_order(
            {
                "task": "对这个商品页面做资料研究并给出摘要",
                "source_paths": [str(html_path)],
                "max_sources": 3,
                "artifacts_root": str(tmp_path),
            },
            skill_id="codex_browser_research",
        )
    )
    run_dir = tmp_path / "browser-research-run"

    result = _execute_worker(order, store=CodexArtifactStore(str(tmp_path)), run_dir=run_dir)

    assert result["status"] == "EXECUTED"
    assert result["metadata"]["native_browser_researcher"] is True
    assert result["metadata"]["readable_source_count"] == 1
    assert run_dir.joinpath("browser_research_report.md").exists()
    assert run_dir.joinpath("sources.jsonl").exists()
    assert run_dir.joinpath("browser_trace.json").exists()
    report = run_dir.joinpath("browser_research_report.md").read_text(encoding="utf-8")
    assert "测试商品 A" in report
    assert "价格 299 元" in report


def test_browser_research_url_extraction_preserves_query_string() -> None:
    assert _extract_urls("https://www.lightnovel.fun/cn/category/3/106?type=1。") == [
        "https://www.lightnovel.fun/cn/category/3/106?type=1"
    ]


def test_browser_research_gateway_message_uses_source_summaries() -> None:
    message = _build_browser_research_gateway_message(
        {
            "status": "EXECUTED",
            "summary": "Browser research report built from 1 readable source(s).",
            "metadata": {
                "readable_source_count": 1,
                "source_count": 1,
                "report_path": "D:/Natsume_Ai/artifacts/codex_runs/research/browser_research_report.md",
                "source_summaries": [
                    {
                        "title": "测试商品 A",
                        "source_ref": "https://example.test/product-a",
                        "description": "轻量、安静、适合桌面长期使用。",
                    }
                ],
            },
        }
    )

    assert "资料研究简报完成" in message
    assert "测试商品 A" in message
    assert "https://example.test/product-a" in message
    assert "browser_research_report.md" in message


def test_browser_research_gateway_push_dispatches_to_configured_gateway(tmp_path: Path, monkeypatch) -> None:
    order = CodexWorkOrder.from_mapping(
        build_production_work_order(
            {"task": "网页资料研究", "request_id": "req-browser-research-gw", "artifacts_root": str(tmp_path)},
            skill_id="codex_browser_research",
        )
    )
    calls: list[dict] = []

    class _ReplyResult:
        def as_dict(self) -> dict:
            return {"success": True, "status": "sent", "latency_ms": 12}

    def _fake_post_gateway_reply_sync(**kwargs):
        calls.append(dict(kwargs))
        return _ReplyResult()

    monkeypatch.setenv("HOST_BROWSER_RESEARCH_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("HOST_BROWSER_RESEARCH_GATEWAY_PLATFORM", "qq")
    monkeypatch.setenv("HOST_BROWSER_RESEARCH_GATEWAY_CONVERSATION_ID", "qq_dm_owner")
    monkeypatch.setattr(
        "host_brain.codex_runtime.production_executor._post_gateway_reply_sync",
        _fake_post_gateway_reply_sync,
    )

    result = _maybe_push_browser_research_to_gateway(
        job_id="job-browser-research-gw",
        order=order,
        result={
            "status": "EXECUTED",
            "summary": "Browser research report built from 1 readable source(s).",
            "metadata": {
                "report_path": str(tmp_path / "browser_research_report.md"),
                "sources_path": str(tmp_path / "sources.jsonl"),
                "readable_source_count": 1,
                "source_count": 1,
                "source_summaries": [{"title": "测试商品 A", "source_ref": "https://example.test/product-a"}],
            },
        },
        run_dir=tmp_path,
    )

    assert result is not None
    assert result["status"] == "SENT"
    assert result["success"] is True
    assert calls[0]["platform"] == "qq"
    assert calls[0]["conversation_id"] == "qq_dm_owner"
    assert "测试商品 A" in calls[0]["content"]
    assert calls[0]["raw_extra"]["reply_source"] == "codex_browser_research"


def test_codex_production_prompt_includes_account_purchase_gate(tmp_path: Path) -> None:
    order = CodexWorkOrder.from_mapping(
        build_production_work_order(
            {
                "task": "登录我的账号并使用积分购买这本轻小说后封装成 epub",
                "urls": ["https://www.lightnovel.fun/cn/detail/1144366"],
                "max_points": 30,
                "artifacts_root": str(tmp_path),
            },
            skill_id="codex_epub_builder",
        )
    )

    prompt = build_codex_prompt(order, workspace=tmp_path)

    assert order.approval_policy["require_confirm"] is True
    assert order.approval_policy["confirmed"] is False
    assert "Purchase gate" in prompt
    assert "approval_policy.confirmed=true" in prompt
    assert "Source policy" in prompt
    assert "Account policy" in prompt
    assert "Purchase policy" in prompt
    assert "purchase_without_user_confirmation" in prompt


def test_codex_production_executor_blocks_unconfirmed_epub_purchase_background(tmp_path: Path) -> None:
    work_order = build_production_work_order(
        {
            "task": "登录我的账号并使用积分购买这本轻小说后封装成 epub，最多花 30 点",
            "urls": ["https://www.lightnovel.fun/cn/detail/1144366"],
            "artifacts_root": str(tmp_path),
        },
        skill_id="codex_epub_builder",
    )

    result = execute_codex_production_payload(
        {
            "work_order": work_order,
            "start_background": True,
            "artifacts_root": str(tmp_path),
        }
    )

    run_dir = Path(result["artifacts_dir"])
    job = json.loads(run_dir.joinpath("production_job.json").read_text(encoding="utf-8"))

    assert result["status"] == "OK"
    assert result["start_background"] is False
    assert result["blocked_until_confirmation"] is True
    assert result["approval_policy"]["confirmed"] is False
    assert "不要把账号、密码、验证码或 Cookie 发到聊天里" in result["result_text"]
    assert "不会点击购买或消耗积分" in result["result_text"]
    assert job["status"] == "QUEUED"


def test_lightnovel_gateway_message_uses_recommendation_artifact() -> None:
    items = _coerce_recommendation_items(
        [
            {
                "title": "桌游咖 3",
                "detail_url": "https://www.lightnovel.fun/cn/detail/1143317",
                "update_time": "3 个月前",
                "view_count": "11.1w",
                "comment_count": "248",
                "rationale": "恋爱喜剧 + 桌游题材，适合一口气补。",
            }
        ]
    )

    message = _build_lightnovel_gateway_message(items)

    assert "今日轻小说推荐来了" in message
    assert "桌游咖 3" not in message
    assert "下面按书分别发详情" in message
    assert "\n" not in message


def test_lightnovel_gateway_card_omits_cover_url_from_caption() -> None:
    card = _build_lightnovel_gateway_card(
        {
            "title": "桌游咖 3",
            "detail_url": "https://www.lightnovel.fun/cn/detail/1143317",
            "cover_url": "https://res.lightnovel.fun/images/demo.jpg",
            "intro_snippet": "恋爱喜剧 + 桌游题材，适合一口气补。",
        },
        index=1,
    )

    assert "桌游咖 3" in card
    assert "详情：https://www.lightnovel.fun/cn/detail/1143317" in card
    assert "res.lightnovel.fun" not in card
    assert "\n详情：https://www.lightnovel.fun/cn/detail/1143317" in card


def test_lightnovel_signal_parsers_support_site_units() -> None:
    assert _parse_lightnovel_count("11.1w") == 111000
    assert _parse_lightnovel_count("2.5万 浏览") == 25000
    assert _parse_lightnovel_count("6.0k") == 6000
    assert _parse_lightnovel_update_age_days("16 小时前") == 16 / 24
    assert _parse_lightnovel_update_age_days("3 个月前") == 90


def test_lightnovel_candidate_normalization_filters_site_admin_links() -> None:
    recommendations = _lightnovel_recommendations_from_browser_items(
        [
            {
                "title": "置顶 翻译/录入勋章 、翻译工资申请",
                "url": "https://www.lightnovel.fun/cn/detail/1113228",
                "context": "站务申请帖",
            },
            {
                "title": "zydxn",
                "url": "https://www.lightnovel.fun/cn/profile/418610",
                "context": "作者主页",
            },
            {
                "title": "第六卷",
                "url": "https://www.lightnovel.fun/detail/1139274",
                "context": "标题缺少作品名",
            },
            {
                "title": "合集 [水城水城]雨森润奈的湿度很高 3[马粪文库J]",
                "url": "https://www.lightnovel.fun/cn/detail/1143992",
                "context": "1 小时前 22322 45 评论",
            },
            {
                "title": "《这本轻小说真厉害！2026》——《玩乐关系》作者·葵关南访谈 6.8w 62",
                "url": "https://www.lightnovel.fun/detail/1142232",
                "context": "资讯区排行访谈",
            },
            {
                "title": "仙剑金属武器模型珍藏礼盒众筹开启 已筹款47万元",
                "url": "https://www.lightnovel.fun/detail/1034327",
                "context": "模型众筹资讯",
            },
            {
                "title": "《败犬女主太多了！》【动画制片人】菊池雄一郎×【制作管理】伊藤裕太郎对谈",
                "url": "https://www.lightnovel.fun/detail/1139622",
                "context": "动画资讯对谈",
            },
        ]
    )

    assert [item["title"] for item in recommendations] == ["[水城水城]雨森润奈的湿度很高 3[马粪文库J]"]


def test_browser_researcher_does_not_use_whole_rank_section_as_item_context() -> None:
    html = """
    <html><body>
      <section class="ranking">
        <a href="/detail/1144366">1 [西条阳]不过是偶像！ ～但是果然颜值好高～[文库] 23 天前</a>
        <a href="/detail/1144301">2 [軽井広]轉移到異世界後，與美少女皇女結婚的我成為皇帝</a>
        <a href="/detail/1144464">3 [岸本和葉]只有在我当班时才会来我打工咖啡店的美少女模特小姐2</a>
        <a href="/detail/1144535">4 [白井ムク] 风纪委员的羞耻游戏 第一卷</a>
      </section>
      <ul>
        <li><a href="/detail/1145000">1 单条列表作品 30 分钟前</a><span>2.7w 60</span></li>
      </ul>
    </body></html>
    """

    _title, _description, _text, items, _prices = _parse_html_document(
        html,
        base_url="https://www.lightnovel.fun/",
    )

    assert items[0]["title"].startswith("1 [西条阳]")
    assert items[0]["context"] == ""
    assert "軽井広" not in items[0].get("context", "")
    compact_item = next(item for item in items if item["url"].endswith("/1145000"))
    assert compact_item["context"] == "1 单条列表作品 30 分钟前 2.7w 60"


def test_browser_researcher_lightnovel_skips_profiles_and_keeps_deeper_details() -> None:
    links = []
    for index in range(1, 18):
        links.append(f'<a href="/cn/profile/{index}">作者{index}</a>')
        links.append(f'<a href="/cn/detail/11450{index:02d}">[{index}] 可推荐作品 {index}</a>')
    html = "<html><body>" + "\n".join(links) + "</body></html>"

    _title, _description, _text, items, _prices = _parse_html_document(
        html,
        base_url="https://www.lightnovel.fun/cn/category/3/106?type=1",
    )

    assert len(items) > 12
    assert all("/profile/" not in item["url"] for item in items)
    assert any(item["url"].endswith("/1145017") for item in items)


def test_browser_researcher_sfacg_and_17k_keep_book_links_only() -> None:
    sfacg_html = """
    <html><body>
      <a href="/List/">所有轻小说列表</a>
      <a href="/Novel/651213/">魔族王女战败被俘啦？抱歉我演的</a>
      <a href="/vip/">VIP特权</a>
      <a href="/Novel/714059/">转生贫穷小绿茶，就要被欺负吗？</a>
    </body></html>
    """
    _title, _description, _text, sfacg_items, _prices = _parse_html_document(
        sfacg_html,
        base_url="https://book.sfacg.com/rank/",
    )

    assert [item["title"] for item in sfacg_items] == [
        "魔族王女战败被俘啦？抱歉我演的",
        "转生贫穷小绿茶，就要被欺负吗？",
    ]

    seventeen_k_html = """
    <html><body>
      <a href="/top/">排行榜</a>
      <a href="/book/3517901.html">系统体验员</a>
      <a href="/all/book/2_30_185_0_0_2_0_0_1.html">宅系小说</a>
      <a href="/book/3451799.html">转生骷髅：开局对战最强勇者</a>
    </body></html>
    """
    _title, _description, _text, k17_items, _prices = _parse_html_document(
        seventeen_k_html,
        base_url="https://www.17k.com/man/qingbook/",
    )

    assert [item["title"] for item in k17_items] == ["系统体验员", "转生骷髅：开局对战最强勇者"]


def test_lightnovel_candidate_context_drops_combined_rank_sections() -> None:
    recommendations = _lightnovel_recommendations_from_browser_items(
        [
            {
                "title": "2 [軽井広]轉移到異世界後，與美少女皇女結婚的我成為皇帝",
                "url": "https://www.lightnovel.fun/detail/1144301",
                "context": (
                    "1 [西条阳]不过是偶像！ 23 天前 "
                    "2 [軽井広]轉移到異世界後，與美少女皇女結婚的我成為皇帝 "
                    "3 [岸本和葉]只有在我当班时才会来我打工咖啡店的美少女模特小姐2 "
                    "4 [白井ムク] 风纪委员的羞耻游戏 第一卷"
                ),
            }
        ]
    )

    assert recommendations[0]["intro_snippet"] == ""
    assert recommendations[0]["update_time"] == ""
    assert "西条阳" not in recommendations[0]["selection_reason"]


def test_lightnovel_recommendations_accept_public_multi_source_book_urls() -> None:
    recommendations = _lightnovel_recommendations_from_browser_items(
        [
            {
                "title": "魔族王女战败被俘啦？抱歉我演的",
                "url": "http://book.sfacg.com/Novel/651213/",
                "context": "SF轻小说月票榜。",
                "source_ref": "https://book.sfacg.com/rank/",
            },
            {
                "title": "转生骷髅：开局对战最强勇者",
                "url": "https://www.17k.com/book/3451799.html",
                "context": "17K轻小说分类。",
                "source_ref": "https://www.17k.com/man/qingbook/",
            },
            {
                "title": "VIP特权",
                "url": "https://book.sfacg.com/vip/",
                "context": "导航链接。",
            },
        ]
    )

    assert [item["title"] for item in recommendations] == [
        "魔族王女战败被俘啦？抱歉我演的",
        "转生骷髅：开局对战最强勇者",
    ]


def test_lightnovel_candidate_filter_rejects_standalone_chapter_titles() -> None:
    recommendations = _lightnovel_recommendations_from_browser_items(
        [
            {
                "title": "第二十九话：预兆",
                "url": "https://www.lightnovel.fun/cn/detail/1144617",
                "context": "章节更新 3 小时前",
            },
            {
                "title": "1206.公爵级和女仆级①",
                "url": "https://www.lightnovel.fun/cn/detail/1144616",
                "context": "章节更新 3 小时前",
            },
            {
                "title": "[水城水城]雨森润奈的湿度很高 3[马粪文库J]",
                "url": "https://www.lightnovel.fun/cn/detail/1143992",
                "context": "2 小时前 24168 45 评论",
            },
        ]
    )

    assert [item["title"] for item in recommendations] == ["[水城水城]雨森润奈的湿度很高 3[马粪文库J]"]


def test_lightnovel_ranker_prefers_taste_match_over_raw_source_order(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_HISTORY_PATH", str(tmp_path / "history.jsonl"))
    ranked = _rank_lightnovel_recommendations(
        [
            {
                "title": "普通战斗记录",
                "detail_url": "https://www.lightnovel.fun/detail/plain",
                "update_time": "1 小时前",
                "view_count": "20w",
                "intro_snippet": "战斗与修炼。",
            },
            {
                "title": "和青梅竹马一起经营桌游咖",
                "detail_url": "https://www.lightnovel.fun/detail/romcom",
                "update_time": "16 小时前",
                "view_count": "7903",
                "comment_count": "26",
                "cover_url": "https://res.lightnovel.fun/images/romcom.jpg",
                "intro_snippet": "恋爱喜剧、校园日常、桌游题材。",
            },
        ],
        max_items=2,
        now_ts_ms=1_778_000_000_000,
    )

    assert ranked[0]["title"] == "和青梅竹马一起经营桌游咖"
    assert ranked[0]["recommendation_rank"] == 1
    assert ranked[0]["dedupe_state"] == "new"
    assert ranked[0]["ranking_visible_candidate"] is True
    assert "恋爱日常" in ranked[0]["selection_reason"]
    assert ranked[0]["score_breakdown"]["preference"] > 0


def test_lightnovel_ranker_downranks_non_novel_news(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_HISTORY_PATH", str(tmp_path / "history.jsonl"))
    ranked = _rank_lightnovel_recommendations(
        [
            {
                "title": "动画制片人访谈：败犬女主的制作现场",
                "detail_url": "https://www.lightnovel.fun/detail/interview",
                "update_time": "1 小时前",
                "view_count": "20w",
                "comment_count": "200",
                "cover_url": "https://res.lightnovel.fun/images/interview.jpg",
                "intro_snippet": "动画资讯与制片人访谈，并非轻小说资源条目。",
            },
            {
                "title": "和青梅竹马一起经营桌游咖",
                "detail_url": "https://www.lightnovel.fun/detail/romcom",
                "update_time": "21 天前",
                "view_count": "3.7k",
                "comment_count": "17",
                "cover_url": "https://res.lightnovel.fun/images/romcom.jpg",
                "intro_snippet": "恋爱喜剧、校园日常、桌游题材。",
            },
        ],
        max_items=2,
        now_ts_ms=1_778_000_000_000,
    )

    assert ranked[0]["detail_url"] == "https://www.lightnovel.fun/detail/romcom"
    interview = next(item for item in ranked if item["detail_url"].endswith("/interview"))
    assert interview["score_breakdown"]["negative_preference"] <= -36


def test_lightnovel_ranker_downranks_recent_history(monkeypatch, tmp_path: Path) -> None:
    history_path = tmp_path / "history.jsonl"
    history_path.write_text(
        json.dumps(
            {
                "ts_ms": 1_778_000_000_000,
                "key": "https://www.lightnovel.fun/detail/repeat",
                "title": "重复推荐",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_HISTORY_PATH", str(history_path))

    ranked = _rank_lightnovel_recommendations(
        [
            {
                "title": "重复推荐",
                "detail_url": "https://www.lightnovel.fun/detail/repeat",
                "update_time": "1 小时前",
                "view_count": "30w",
                "intro_snippet": "恋爱校园日常。",
            },
            {
                "title": "新的校园恋爱喜剧",
                "detail_url": "https://www.lightnovel.fun/detail/new",
                "update_time": "2 小时前",
                "view_count": "1w",
                "intro_snippet": "恋爱校园日常。",
            },
        ],
        max_items=2,
        now_ts_ms=1_778_086_400_000,
    )

    assert ranked[0]["detail_url"] == "https://www.lightnovel.fun/detail/new"
    repeat = next(item for item in ranked if item["detail_url"].endswith("/repeat"))
    assert repeat["dedupe_state"] == "recent_exact_repeat"
    assert repeat["ranking_visible_candidate"] is False
    assert repeat["score_breakdown"]["history"] <= -220


def test_lightnovel_ranker_blocks_recent_same_series_even_with_new_detail_url(monkeypatch, tmp_path: Path) -> None:
    history_path = tmp_path / "history.jsonl"
    history_path.write_text(
        json.dumps(
            {
                "ts_ms": 1_778_000_000_000,
                "key": "https://www.lightnovel.fun/cn/detail/1144622",
                "title": "[丸焦ししゃも]给被人说“培养失败”的前美少女童星（SSR）搭了把手之后，她开始只对我依赖到撒娇上瘾 第18话 都怪洋葱，所以没办法",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_HISTORY_PATH", str(history_path))

    ranked = _rank_lightnovel_recommendations(
        [
            {
                "title": "[丸焦ししゃも]给被人说“培养失败”的前美少女童星（SSR）搭了把手之后，她开始只对我依赖到撒娇上瘾 第19话 辽君是我的第一次哦",
                "detail_url": "https://www.lightnovel.fun/cn/detail/1144633",
                "update_time": "3 小时前",
                "intro_snippet": "恋爱日常，美少女，培养。",
            },
            {
                "title": "新的校园恋爱喜剧",
                "detail_url": "https://www.lightnovel.fun/cn/detail/1145000",
                "update_time": "2 小时前",
                "intro_snippet": "恋爱校园日常。",
            },
        ],
        max_items=2,
        now_ts_ms=1_778_086_400_000,
    )

    assert ranked[0]["detail_url"] == "https://www.lightnovel.fun/cn/detail/1145000"
    repeat = next(item for item in ranked if item["detail_url"].endswith("/1144633"))
    assert repeat["dedupe_state"] == "recent_series_repeat"
    assert repeat["ranking_visible_candidate"] is False
    assert repeat["score_breakdown"]["history"] <= -180


def test_lightnovel_ranker_keeps_visible_recommendations_cross_source(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_HISTORY_PATH", str(tmp_path / "history.jsonl"))
    ranked = _rank_lightnovel_recommendations(
        [
            {
                "title": "邻桌美少女与我的恋爱日常",
                "detail_url": "https://www.lightnovel.fun/cn/detail/1146001",
                "source_url": "https://www.lightnovel.fun/cn/category/3/106?type=1",
                "update_time": "1 小时前",
                "intro_snippet": "恋爱校园日常，美少女。",
            },
            {
                "title": "异世界皇女与我的慢生活",
                "detail_url": "https://www.lightnovel.fun/cn/detail/1146002",
                "source_url": "https://www.lightnovel.fun/",
                "update_time": "2 小时前",
                "intro_snippet": "異世界 皇女 皇帝 美少女。",
            },
            {
                "title": "培养失败少女的撒娇日常",
                "detail_url": "https://www.lightnovel.fun/cn/detail/1146003",
                "source_url": "https://www.lightnovel.fun/cn/category/3/106?type=1",
                "update_time": "3 小时前",
                "intro_snippet": "美少女 培养 日常。",
            },
            {
                "title": "魔族王女战败被俘啦？抱歉我演的",
                "detail_url": "http://book.sfacg.com/Novel/651213/",
                "source_url": "https://book.sfacg.com/rank/",
                "intro_snippet": "魔族 王女 反差 轻小说排行。",
            },
        ],
        max_items=3,
        now_ts_ms=1_778_086_400_000,
    )

    visible_sources = {item["source_key"] for item in ranked if item["ranking_visible_candidate"] is True}
    assert "lightnovel.fun" in visible_sources
    assert "sfacg.com" in visible_sources


def test_lightnovel_gateway_push_dispatches_to_configured_gateway(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    recommendations_path = workspace / "recommendations.json"
    recommendations_path.write_text(
        json.dumps(
            [
                {
                    "title": "身为路人祭司的我，发现世界是乙女游戏于是开始培养女主角 [第一卷]",
                    "detail_url": "https://www.lightnovel.fun/detail/1144163",
                    "update_time": "16 小时前",
                    "view_count": "7903",
                    "comment_count": "26",
                    "cover_url": "https://res.lightnovel.fun/images/demo.jpg",
                    "rationale": "更新近，题材钩子清晰。",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    order = CodexWorkOrder.from_mapping(
        build_production_work_order(
            {"task": "推荐轻小说", "request_id": "req-ln-gw", "artifacts_root": str(tmp_path)},
            skill_id="codex_lightnovel_recommender",
        )
    )
    calls: list[dict] = []

    class _ReplyResult:
        def as_dict(self) -> dict:
            return {"success": True, "status": "sent", "latency_ms": 12}

    def _fake_post_gateway_reply_sync(**kwargs):
        calls.append(dict(kwargs))
        return _ReplyResult()

    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_PLATFORM", "qq")
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_CONVERSATION_ID", "qq_dm_owner")
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_HISTORY_PATH", str(tmp_path / "recommendation_history.jsonl"))
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_DELIVERY_MEMORY_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.codex_runtime.production_executor._post_gateway_reply_sync",
        _fake_post_gateway_reply_sync,
    )

    result = _maybe_push_lightnovel_recommendations_to_gateway(
        job_id="job-ln-gw",
        order=order,
        result={
            "status": "EXECUTED",
            "metadata": {"workspace": str(workspace)},
        },
        run_dir=tmp_path / "run",
    )

    assert _find_lightnovel_recommendations_file(result={"metadata": {"workspace": str(workspace)}}, run_dir=tmp_path) == recommendations_path
    assert result is not None
    assert result["status"] == "SENT"
    assert result["success"] is True
    assert Path(result["ranked_recommendations_ref"]).exists()
    assert Path(result["history_ref"]).exists()
    ranked_payload = json.loads(Path(result["ranked_recommendations_ref"]).read_text(encoding="utf-8"))
    assert ranked_payload["items"][0]["recommendation_rank"] == 1
    assert calls[0]["platform"] == "qq"
    assert calls[0]["conversation_id"] == "qq_dm_owner"
    assert calls[0]["raw_extra"]["reply_source"] == "codex_lightnovel_recommender"
    assert calls[0]["raw_extra"]["ranked_recommendations_ref"] == result["ranked_recommendations_ref"]
    assert calls[1]["msg_type"] == "image"
    assert calls[1]["media_path"] == "https://res.lightnovel.fun/images/demo.jpg"
    assert "身为路人祭司" in calls[1]["content"]
    assert "命中偏好" in calls[1]["content"]
    assert "res.lightnovel.fun" not in calls[1]["content"]
    assert calls[1]["raw_extra"]["reply_source"] == "codex_lightnovel_recommender.card"
    assert result["card_results"][0]["success"] is True


def test_lightnovel_gateway_push_sends_text_detail_without_cover(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    recommendations_path = workspace / "recommendations.json"
    recommendations_path.write_text(
        json.dumps(
            [
                {
                    "title": "能够率直说出喜欢的女生无双4 更新第二章",
                    "detail_url": "https://www.lightnovel.fun/detail/1145001",
                    "update_time": "5/27",
                    "view_count": "1200",
                    "comment_count": "8",
                    "cover_url": "",
                    "intro_snippet": "恋爱喜剧续作更新，适合追更。",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    order = CodexWorkOrder.from_mapping(
        build_production_work_order(
            {"task": "推荐轻小说", "request_id": "req-ln-gw-text", "artifacts_root": str(tmp_path)},
            skill_id="codex_lightnovel_recommender",
        )
    )
    calls: list[dict] = []

    class _ReplyResult:
        def as_dict(self) -> dict:
            return {"success": True, "status": "sent", "latency_ms": 12}

    def _fake_post_gateway_reply_sync(**kwargs):
        calls.append(dict(kwargs))
        return _ReplyResult()

    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_PLATFORM", "qq")
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_CONVERSATION_ID", "qq_dm_owner")
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_HISTORY_PATH", str(tmp_path / "recommendation_history.jsonl"))
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_DELIVERY_MEMORY_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.codex_runtime.production_executor._post_gateway_reply_sync",
        _fake_post_gateway_reply_sync,
    )

    result = _maybe_push_lightnovel_recommendations_to_gateway(
        job_id="job-ln-gw-text",
        order=order,
        result={
            "status": "EXECUTED",
            "metadata": {"workspace": str(workspace)},
        },
        run_dir=tmp_path / "run",
    )

    assert result is not None
    assert result["status"] == "SENT"
    assert len(calls) == 2
    assert calls[0]["msg_type"] == "text"
    assert "下面按书分别发详情" in calls[0]["content"]
    assert "\n" not in calls[0]["content"]
    assert calls[1]["msg_type"] == "text"
    assert "media_path" not in calls[1]
    assert "能够率直说出喜欢的女生无双4" in calls[1]["content"]
    assert "简介：恋爱喜剧续作更新" in calls[1]["content"]
    assert "详情：https://www.lightnovel.fun/detail/1145001" in calls[1]["content"]
    assert "\n详情：https://www.lightnovel.fun/detail/1145001" in calls[1]["content"]
    assert result["card_results"][0]["success"] is True
    assert result["cover_results"][0]["success"] is True


def test_lightnovel_gateway_delivery_writes_life_memory(monkeypatch, tmp_path: Path) -> None:
    order = CodexWorkOrder.from_mapping(
        build_production_work_order(
            {
                "task": "推荐轻小说",
                "request_id": "req-ln-memory",
                "session_id": "qq_dm_owner",
                "person_id": "owner",
                "artifacts_root": str(tmp_path),
            },
            skill_id="codex_lightnovel_recommender",
        )
    )
    calls: list[dict] = []

    def _fake_persist_life_action_event_memory(**kwargs):  # noqa: ANN003
        calls.append(dict(kwargs))
        return {"status": "OK", "life_memory": {"episode_id": "ep_delivery"}}

    monkeypatch.setattr(
        "host_brain.app.observability_persist.persist_life_action_event_memory",
        _fake_persist_life_action_event_memory,
    )

    result = _persist_lightnovel_gateway_delivery_memory(
        job_id="job-ln-memory",
        order=order,
        ranked_items=[
            {
                "title": "第一本",
                "detail_url": "https://www.lightnovel.fun/detail/1",
                "selection_reason": "题材更贴偏好。",
                "intro_snippet": "校园日常。",
            },
            {
                "title": "第二本",
                "detail_url": "https://www.lightnovel.fun/detail/2",
            },
        ],
        max_items=2,
        gateway_delivery={"success": True, "platform": "qq", "conversation_id": "qq_dm_owner"},
        message="今日轻小说推荐来了，共 2 本。下面按书分别发详情。",
        cards=[{"caption": "1. 第一本"}, {"caption": "2. 第二本"}],
    )

    assert result["status"] == "OK"
    assert calls
    call = calls[0]
    assert call["event_source"] == "codex_production"
    assert call["action_kind"] == "lightnovel_recommendation_delivery"
    assert call["session_id"] == "qq_dm_owner"
    assert "我刚才通过 qq 给 qq_dm_owner 推荐了 2 本轻小说" in call["action_summary"]
    forbidden_followup = "如果" + "继续问"
    assert forbidden_followup not in call["action_summary"]
    assert "先看第 1 本" not in call["action_summary"]
    assert call["metadata"]["domain_event_kind"] == "lightnovel_recommendation_delivery"
    assert call["metadata"]["selected_recommendations"][0]["title"] == "第一本"
    assert "gateway://message/job-ln-memory/lightnovel/overview" in call["evidence_refs"]


def test_lightnovel_worker_uses_native_recommender(tmp_path: Path, monkeypatch) -> None:
    order = CodexWorkOrder.from_mapping(
        build_production_work_order(
            {
                "task": "推荐轻小说",
                "request_id": "req-ln-native",
                "artifacts_root": str(tmp_path),
            },
            skill_id="codex_lightnovel_recommender",
        )
    )

    def _fake_browser_research(executed_order, *, run_dir: Path):
        assert executed_order.work_order_id == order.work_order_id
        run_dir.mkdir(parents=True, exist_ok=True)
        sources_path = run_dir / "sources.jsonl"
        sources_path.write_text(
            json.dumps(
                {
                    "source_ref": "https://www.lightnovel.fun/cn/category/3/106?type=1",
                    "title": "轻小说分类",
                    "status": "OK",
                    "items": [
                        {
                            "title": "3 分类页完整作品 30 分钟前",
                            "url": "https://www.lightnovel.fun/cn/detail/1140003",
                            "context": "分类页完整 sources.jsonl 条目。",
                        }
                    ],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return {
            "status": "EXECUTED",
            "metadata": {
                "source_count": 1,
                "readable_source_count": 1,
                "sources_path": str(sources_path),
                "candidate_items": [
                    {
                        "title": "1 和青梅竹马一起经营桌游咖 2 小时前",
                        "url": "https://www.lightnovel.fun/detail/1140001",
                        "context": "恋爱喜剧、校园日常、桌游题材。",
                        "source_ref": "https://www.lightnovel.fun/",
                    }
                ],
                "source_summaries": [
                    {
                        "title": "轻小说分类",
                        "source_ref": "https://www.lightnovel.fun/cn/category/3/106?type=1",
                        "items": [
                            {
                                "title": "2 新的校园恋爱喜剧 1 小时前",
                                "url": "https://www.lightnovel.fun/detail/1140002",
                                "context": "校园日常。",
                            }
                        ],
                    }
                ],
            },
        }

    def _fake_detail(url: str, *, timeout_s: int):
        assert timeout_s >= 3
        return {
            "detail_status": "OK",
            "cover_url": f"https://res.lightnovel.fun/images/{url.rsplit('/', 1)[-1]}.jpg",
            "intro_snippet": "详情页公开简介。",
            "view_count": "2.7w",
            "comment_count": "60",
        }

    monkeypatch.setattr(
        "host_brain.codex_runtime.production_executor.execute_browser_research_work_order",
        _fake_browser_research,
    )
    monkeypatch.setattr(
        "host_brain.codex_runtime.production_executor._fetch_lightnovel_detail_metadata",
        _fake_detail,
    )

    result = execute_lightnovel_recommender_work_order(order, run_dir=tmp_path / "run")

    assert result["status"] == "EXECUTED"
    assert result["metadata"]["native_lightnovel_recommender"] is True
    recommendations_path = Path(result["metadata"]["recommendations_ref"])
    assert recommendations_path.exists()
    payload = json.loads(recommendations_path.read_text(encoding="utf-8"))
    assert payload["recommendations"][0]["title"] == "和青梅竹马一起经营桌游咖"
    assert payload["recommendations"][0]["detail_url"] == "https://www.lightnovel.fun/detail/1140001"
    assert payload["recommendations"][0]["cover_url"] == "https://res.lightnovel.fun/images/1140001.jpg"
    assert payload["recommendations"][0]["view_count"] == "2.7w"
    assert any(item["detail_url"].endswith("/1140002") for item in payload["recommendations"])
    assert any(item["detail_url"].endswith("/1140003") for item in payload["recommendations"])
    assert Path(result["metadata"]["cover_manifest_path"]).exists()
    result_payload = json.loads((tmp_path / "run" / "result.json").read_text(encoding="utf-8"))
    assert result_payload["summary"].startswith("Native lightnovel recommendations built")
    assert result_payload["metadata"]["native_lightnovel_recommender"] is True
    assert result_payload["metadata"]["recommendations_ref"] == str(recommendations_path)


def test_lightnovel_worker_recovers_completed_artifacts_after_codex_wrapper_failure(tmp_path: Path, monkeypatch) -> None:
    order = CodexWorkOrder.from_mapping(
        build_production_work_order(
            {"task": "推荐轻小说", "request_id": "req-ln-recover", "artifacts_root": str(tmp_path)},
            skill_id="codex_lightnovel_recommender",
        )
    )
    run_dir = tmp_path / "lightnovel_recommender" / order.work_order_id
    workspace = tmp_path / order.work_order_id / "workspace"
    workspace.mkdir(parents=True)
    recommendations_path = workspace / "recommendations.json"
    recommendations_path.write_text(
        json.dumps(
            [
                {
                    "title": "测试轻小说",
                    "detail_url": "https://www.lightnovel.fun/detail/recovered",
                    "update_time": "1 小时前",
                    "view_count": "1w",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (workspace / "result_contract.json").write_text(
        json.dumps(
            {
                "workorder_id": order.work_order_id,
                "status": "complete",
                "recommendation_count": 1,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (workspace / "recommendation_report.md").write_text("# report\n", encoding="utf-8")

    def _fake_execute(self, executed_order):
        assert executed_order.work_order_id == order.work_order_id
        return {
            "status": "FAILED",
            "reason_code": "CODEX_EXEC_FAILED",
            "detail": "Failed to write last message file",
            "risk_flags": ["codex_exec_failed"],
            "metadata": {},
        }

    monkeypatch.setattr(
        "host_brain.codex_runtime.production_executor.CodexRuntimeExecutor.execute",
        _fake_execute,
    )
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_NATIVE_ENABLED", "0")

    result = _execute_worker(order, store=CodexArtifactStore(str(tmp_path)), run_dir=run_dir)

    assert result["status"] == "EXECUTED"
    assert result["reason_code"] is None
    assert result["metadata"]["recovered_from_failed_codex"] is True
    assert result["metadata"]["workspace"] == str(workspace)
    assert result["metadata"]["recommendations_ref"] == str(recommendations_path)
    assert result["metadata"]["original_worker_reason_code"] == "CODEX_EXEC_FAILED"
    assert result["risk_flags"] == ["codex_exec_recovered"]
    assert _find_lightnovel_recommendations_file(result=result, run_dir=run_dir) == recommendations_path


def test_lightnovel_epub_gateway_push_dispatches_file_to_configured_gateway(tmp_path: Path, monkeypatch) -> None:
    epub_path = tmp_path / "book.epub"
    epub_path.write_bytes(b"fake epub bytes")
    order = CodexWorkOrder.from_mapping(
        build_production_work_order(
            {"task": "推荐的第一本封装 epub", "request_id": "req-ln-epub-gw", "artifacts_root": str(tmp_path)},
            skill_id="codex_epub_builder",
        )
    )
    calls: list[dict] = []

    class _ReplyResult:
        def as_dict(self) -> dict:
            return {"success": True, "status": "sent", "latency_ms": 12}

    def _fake_post_gateway_reply_sync(**kwargs):
        calls.append(dict(kwargs))
        return _ReplyResult()

    monkeypatch.setenv("HOST_LIGHTNOVEL_EPUB_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("HOST_LIGHTNOVEL_EPUB_GATEWAY_PLATFORM", "qq")
    monkeypatch.setenv("HOST_LIGHTNOVEL_EPUB_GATEWAY_CONVERSATION_ID", "qq_dm_owner")
    monkeypatch.delenv("HOST_LIGHTNOVEL_EPUB_GATEWAY_EDGE_SSH_TARGET", raising=False)
    monkeypatch.setattr(
        "host_brain.codex_runtime.production_executor._post_gateway_reply_sync",
        _fake_post_gateway_reply_sync,
    )

    result = _maybe_push_lightnovel_epub_to_gateway(
        job_id="job-ln-epub-gw",
        order=order,
        result={
            "status": "EXECUTED",
            "metadata": {
                "epub_path": str(epub_path),
                "title": "身为路人祭司的测试卷",
                "epub_size_bytes": epub_path.stat().st_size,
                "sha256": "a" * 64,
                "chapter_count": 4,
                "image_count": 3,
            },
        },
        run_dir=tmp_path / "run",
    )

    assert _find_lightnovel_epub_file(result={"metadata": {"epub_path": str(epub_path)}}, run_dir=tmp_path) == epub_path
    assert result is not None
    assert result["status"] == "SENT"
    assert result["success"] is True
    assert calls[0]["platform"] == "qq"
    assert calls[0]["conversation_id"] == "qq_dm_owner"
    assert calls[0]["msg_type"] == "file"
    assert calls[0]["media_path"] == str(epub_path)
    assert calls[0]["raw_extra"]["reply_source"] == "codex_epub_builder"
    assert calls[0]["raw_extra"]["file_name"] == "book.epub"
    assert calls[1]["msg_type"] == "text"
    assert "EPUB 已封装完成" in calls[1]["content"]


def test_lightnovel_epub_worker_uses_native_builder(tmp_path: Path) -> None:
    html_path = tmp_path / "article.html"
    html_path.write_text(
        """<!doctype html>
<html><body><h1>测试 EPUB</h1><div class="article-content">
<p>作者：测试作者</p>
<p>第一章 开始</p>
<p>这里是已经授权的测试正文。</p>
<p>""" + ("测试正文足够长。" * 120) + """</p>
</div></body></html>""",
        encoding="utf-8",
    )
    order = CodexWorkOrder.from_mapping(
        build_production_work_order(
            {
                "task": "把授权 HTML 封装成 epub",
                "source_paths": [str(html_path)],
                "title": "测试 EPUB",
                "artifacts_root": str(tmp_path),
            },
            skill_id="codex_epub_builder",
        )
    )
    run_dir = tmp_path / "run-native"

    result = _execute_worker(order, store=CodexArtifactStore(str(tmp_path)), run_dir=run_dir)

    assert result["status"] == "EXECUTED"
    assert result["metadata"]["native_lightnovel_epub_builder"] is True
    assert Path(result["metadata"]["epub_path"]).exists()
    assert run_dir.joinpath("result.json").exists()
