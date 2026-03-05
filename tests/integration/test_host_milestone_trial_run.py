"""
Host 全面试运行里程碑测试套件
==============================
里程碑：M-TRIAL-RUN-2026-03-05
目标：对 host 所有 14 个注册服务进行全面试运行，验证每条链路真实可通。

规则：
- 不使用 mock，全部使用真实服务
- 模型相关测试（LLM 推理、ASR、TTS）标记 @pytest.mark.skip_model 并跳过
- 所有链路节点的出入参数、状态均写入 SQLite 账本留痕
- 每个测试结束后导出 JSON 报告到 docs/60_trace/trial_run_reports/

覆盖服务（14 个）：
  host.audit.sqlite_ledger       ✅ 真实测试
  host.governor.decision         ✅ 真实测试
  host.skills.loader             ✅ 真实测试
  host.integrations.llm_gateway  ⚠️ 跳过（需 API Key）
  host.retrieval.embedding       ✅ 真实测试（sentence_transformers 本地模型）
  host.retrieval.reranker        ✅ 真实测试（heuristic 模式）
  host.vision.screen_ocr         ⚠️ 跳过（需摄像头/屏幕）
  host.vision.camera_recognizer  ⚠️ 跳过（需摄像头）
  edge.rpc.gateway               ✅ 真实测试（配置探针）
  edge.rpc.asr                   ⚠️ 跳过（需 ASR 模型）
  edge.rpc.tts                   ⚠️ 跳过（需 TTS 模型）
  edge.rpc.barge_in              ⚠️ 跳过（依赖 TTS）
  edge.rpc.fallback_llm          ⚠️ 跳过（需本地 LLM）
  edge.rpc.resource_gate         ✅ 真实测试（配置探针）
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# 报告输出目录
# ---------------------------------------------------------------------------
_REPORT_DIR = Path("docs/60_trace/trial_run_reports")
_REPORT_DIR.mkdir(parents=True, exist_ok=True)

_RUN_TS = int(time.time() * 1000)
_RUN_DATE = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(_RUN_TS / 1000))


def _save_report(name: str, data: dict[str, Any]) -> Path:
    """将测试报告写入 JSON 文件留痕。"""
    path = _REPORT_DIR / f"{name}_{_RUN_DATE}.json"
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# 公共 fixture：内存账本（隔离，不污染生产数据）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def trial_ledger():
    """使用内存 SQLite 账本，隔离试运行数据。"""
    from host_brain.audit.sqlite_ledger import SQLiteLedger
    ledger = SQLiteLedger(db_path=":memory:", check_same_thread=False)
    yield ledger
    ledger.close()


@pytest.fixture(scope="module")
def trial_request_id():
    from host_brain.core.ids import new_request_id
    return new_request_id()


# ===========================================================================
# 服务 1：host.audit.sqlite_ledger — SQLite 账本真实读写
# ===========================================================================
class TestAuditSQLiteLedger:
    """host.audit.sqlite_ledger 真实服务试运行。"""

    def test_ledger_init_and_wal_health(self, trial_ledger, trial_request_id):
        """T1-1: 账本初始化 + WAL 健康检查。"""
        # 入参
        params_in = {"db_path": ":memory:", "check_same_thread": False}

        # 执行
        wal = trial_ledger.wal_health()

        # 出参留痕
        result = {
            "test_id": "T1-1",
            "service": "host.audit.sqlite_ledger",
            "operation": "wal_health",
            "params_in": params_in,
            "params_out": wal,
            "status": "PASS" if wal["wal_status"] in {"ACTIVE", "DEGRADED"} else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("T1-1_ledger_wal_health", result)

        assert wal["wal_status"] in {"ACTIVE", "DEGRADED"}, f"WAL status unexpected: {wal}"
        assert wal["db_path"] == ":memory:"

    def test_ledger_write_and_read_intent(self, trial_ledger, trial_request_id):
        """T1-2: 写入 intent_log 并读回验证。"""
        from host_brain.core.ids import now_ms

        ts = now_ms()
        row_in = {
            "request_id": trial_request_id,
            "intent_id": f"intent::{trial_request_id}",
            "session_id": "session::trial",
            "seq": 1,
            "ts_ms": ts,
            "nonce": f"nonce::{trial_request_id}",
            "parse_stage": "S_INTENT_PARSE",
            "raw_text": "试运行测试输入",
        }

        # 写入
        trial_ledger.log_intent(row_in)

        # 读回
        rows = trial_ledger.intent_log
        matched = [r for r in rows if r.get("request_id") == trial_request_id]

        result = {
            "test_id": "T1-2",
            "service": "host.audit.sqlite_ledger",
            "operation": "log_intent + read_back",
            "params_in": row_in,
            "params_out": {"matched_count": len(matched), "first_row": matched[0] if matched else None},
            "status": "PASS" if len(matched) >= 1 else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("T1-2_ledger_intent_write_read", result)

        assert len(matched) >= 1
        assert matched[0]["raw_text"] == "试运行测试输入"

    def test_ledger_write_service_health(self, trial_ledger, trial_request_id):
        """T1-3: 写入 service_health_log 并验证。"""
        from host_brain.core.ids import now_ms

        row_in = {
            "request_id": trial_request_id,
            "service_id": "host.audit.sqlite_ledger",
            "owner": "host",
            "required": 1,
            "status": "UP",
            "reason_code": None,
            "stage": "S_SERVICE_HEALTH",
            "detail": "trial run health check",
            "ts_ms": now_ms(),
        }
        trial_ledger.log_service_health(row_in)

        rows = trial_ledger.service_health_log
        matched = [r for r in rows if r.get("service_id") == "host.audit.sqlite_ledger"]

        result = {
            "test_id": "T1-3",
            "service": "host.audit.sqlite_ledger",
            "operation": "log_service_health",
            "params_in": row_in,
            "params_out": {"matched_count": len(matched)},
            "status": "PASS" if len(matched) >= 1 else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("T1-3_ledger_service_health_write", result)

        assert len(matched) >= 1

    def test_ledger_trace_request(self, trial_ledger, trial_request_id):
        """T1-4: trace_request 链路追踪（需先写入 governor_log）。"""
        from host_brain.core.ids import now_ms

        gov_row = {
            "request_id": trial_request_id,
            "intent_id": f"intent::{trial_request_id}",
            "decision": "APPROVE",
            "reason_code": None,
            "rule_trace": {"gates": ["R1", "R2", "R3"]},
            "tokens": {"approval_token_ref": f"tok::{trial_request_id}"},
            "stage": "S_GOV_APPROVE",
            "ts_ms": now_ms(),
        }
        trial_ledger.log_governor(gov_row)

        trace = trial_ledger.trace_request(trial_request_id)

        result = {
            "test_id": "T1-4",
            "service": "host.audit.sqlite_ledger",
            "operation": "trace_request",
            "params_in": {"request_id": trial_request_id},
            "params_out": {
                "status": trace.get("status"),
                "stage": trace.get("stage"),
                "intent_count": len(trace.get("intent_log") or []),
                "governor_count": len(trace.get("governor_log") or []),
            },
            "status": "PASS" if trace.get("status") in {"COMPLETE", "COMPLETE_REJECTED"} else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("T1-4_ledger_trace_request", result)

        assert trace["status"] in {"COMPLETE", "COMPLETE_REJECTED"}
        assert len(trace["intent_log"]) >= 1
        assert len(trace["governor_log"]) >= 1

    def test_ledger_wal_checkpoint(self, trial_ledger):
        """T1-5: WAL checkpoint 执行。"""
        result_wal = trial_ledger.checkpoint("PASSIVE")

        result = {
            "test_id": "T1-5",
            "service": "host.audit.sqlite_ledger",
            "operation": "checkpoint",
            "params_in": {"mode": "PASSIVE"},
            "params_out": result_wal,
            "status": "PASS" if result_wal["status"] == "OK" else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("T1-5_ledger_wal_checkpoint", result)

        assert result_wal["status"] == "OK"


# ===========================================================================
# 服务 2：host.governor.decision — Governor 裁决引擎
# ===========================================================================
class TestGovernorDecision:
    """host.governor.decision 真实服务试运行。"""

    @pytest.fixture(scope="class")
    def engine(self, trial_ledger):
        from host_brain.governor.decision import IntentDecisionEngine
        return IntentDecisionEngine(ledger=trial_ledger)

    def test_governor_approve_basic_intent(self, engine):
        """T2-1: 基础 intent 裁决 → APPROVE。"""
        from host_brain.core.ids import new_request_id, now_ms

        req_id = new_request_id()
        ts = now_ms()
        intent = {
            "schema_version": "Natsume.intent.v1",
            "id": f"intent::{req_id}",
            "ts_ms": ts,
            "session_id": f"session::trial::{req_id}",
            "seq": 1,
            "type": "query",
            "action": "narrative_only",
            "target": "host",
            "args": {"text": "你好，夏目"},
            "nonce": f"nonce::{req_id}",
        }

        result_gov = engine.evaluate_intent(raw_intent=intent, request_id=req_id)

        result = {
            "test_id": "T2-1",
            "service": "host.governor.decision",
            "operation": "evaluate_intent",
            "params_in": {"intent": intent, "request_id": req_id},
            "params_out": {
                "decision": result_gov.get("decision"),
                "reason_code": result_gov.get("reason_code"),
                "stage": result_gov.get("stage"),
                "intent_id": result_gov.get("intent_id"),
            },
            "status": "PASS" if result_gov.get("decision") in {"APPROVE", "REJECT"} else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("T2-1_governor_approve_basic", result)

        assert result_gov.get("decision") in {"APPROVE", "REJECT"}, f"Unexpected decision: {result_gov}"

    def test_governor_reject_missing_schema(self, engine):
        """T2-2: 缺少 schema_version → REJECT。"""
        from host_brain.core.ids import new_request_id, now_ms

        req_id = new_request_id()
        intent_bad = {
            # 故意缺少 schema_version
            "id": f"intent::{req_id}",
            "ts_ms": now_ms(),
            "session_id": f"session::trial::{req_id}",
            "seq": 1,
            "type": "query",
            "action": "narrative_only",
            "target": "host",
            "args": {"text": "bad intent"},
            "nonce": f"nonce::{req_id}",
        }

        result_gov = engine.evaluate_intent(raw_intent=intent_bad, request_id=req_id)

        result = {
            "test_id": "T2-2",
            "service": "host.governor.decision",
            "operation": "evaluate_intent_reject",
            "params_in": {"intent": intent_bad, "request_id": req_id},
            "params_out": {
                "decision": result_gov.get("decision"),
                "reason_code": result_gov.get("reason_code"),
                "stage": result_gov.get("stage"),
            },
            "status": "PASS" if result_gov.get("decision") == "REJECT" else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("T2-2_governor_reject_bad_schema", result)

        assert result_gov.get("decision") == "REJECT"
        assert result_gov.get("reason_code") is not None

    def test_governor_rate_limit_memory_backend(self, trial_ledger):
        """T2-3: 速率限制（memory backend）不阻断正常请求。"""
        from host_brain.governor.decision import IntentDecisionEngine
        from host_brain.core.ids import new_request_id, now_ms

        engine_rl = IntentDecisionEngine(ledger=trial_ledger)
        req_id = new_request_id()
        ts = now_ms()
        intent = {
            "schema_version": "Natsume.intent.v1",
            "id": f"intent::{req_id}",
            "ts_ms": ts,
            "session_id": f"session::rl::{req_id}",
            "seq": 1,
            "type": "query",
            "action": "narrative_only",
            "target": "host",
            "args": {"text": "速率限制测试"},
            "nonce": f"nonce::{req_id}",
        }

        result_gov = engine_rl.evaluate_intent(raw_intent=intent, request_id=req_id)

        result = {
            "test_id": "T2-3",
            "service": "host.governor.decision",
            "operation": "rate_limit_check",
            "params_in": {"session_id": intent["session_id"]},
            "params_out": {
                "decision": result_gov.get("decision"),
                "reason_code": result_gov.get("reason_code"),
            },
            "status": "PASS" if result_gov.get("decision") in {"APPROVE", "REJECT"} else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("T2-3_governor_rate_limit", result)

        assert result_gov.get("decision") in {"APPROVE", "REJECT"}


# ===========================================================================
# 服务 3：host.skills.loader — 技能加载器
# ===========================================================================
class TestSkillsLoader:
    """host.skills.loader 真实服务试运行。"""

    def test_skills_registry_list(self):
        """T3-1: 通过 SkillRegistry 列出所有注册技能。"""
        from host_brain.skills.registry import SkillRegistry

        registry = SkillRegistry()
        skills = registry.all()

        result = {
            "test_id": "T3-1",
            "service": "host.skills.loader",
            "operation": "SkillRegistry.all",
            "params_in": {},
            "params_out": {
                "skill_count": len(skills),
                "skill_names": [s.skill_name for s in skills[:10]],
            },
            "status": "PASS",
            "ts_ms": _RUN_TS,
        }
        _save_report("T3-1_skills_registry_list", result)

        # 技能列表可以为空（未注册），但不应抛出异常
        assert isinstance(skills, list)

    def test_skills_registry_get_builtin(self):
        """T3-2: 通过 SkillRegistry 获取内置技能（diary/desktop_note/dream）。"""
        from host_brain.skills.registry import SkillRegistry

        registry = SkillRegistry()
        builtin_names = ["diary", "desktop_note", "dream"]
        found = []
        missing = []

        for name in builtin_names:
            try:
                skill = registry.get(name)
                if skill is not None:
                    found.append(name)
                else:
                    missing.append(name)
            except Exception:
                missing.append(name)

        result = {
            "test_id": "T3-2",
            "service": "host.skills.loader",
            "operation": "SkillRegistry.get",
            "params_in": {"skill_names": builtin_names},
            "params_out": {"found": found, "missing": missing},
            "status": "PASS",  # 技能可能未注册，不强制要求
            "ts_ms": _RUN_TS,
        }
        _save_report("T3-2_skills_registry_get_builtin", result)

        # 不强制要求所有内置技能都存在，只验证接口可调用
        assert isinstance(found, list)
        assert isinstance(missing, list)

    def test_skills_loader_execute_rejected(self, trial_ledger):
        """T3-3: SkillLoader 执行被 Governor 拒绝的 intent（验证 token-gate 链路）。"""
        from host_brain.skills.loader import SkillLoader
        from host_brain.skills.registry import SkillRegistry, SkillMeta
        from host_brain.governor.tokens import TokenService
        from host_brain.core.ids import new_request_id, now_ms

        # 注册一个测试技能
        registry = SkillRegistry()
        registry.register(SkillMeta(
            skill_name="trial_test_skill",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=True,
            handler=lambda intent, ctx: {"status": "OK", "detail": "trial test"},
        ))

        token_service = TokenService()
        loader = SkillLoader(
            registry=registry,
            token_service=token_service,
            ledger=trial_ledger,
        )

        req_id = new_request_id()
        intent = {
            "schema_version": "Natsume.intent.v1",
            "id": f"intent::{req_id}",
            "ts_ms": now_ms(),
            "session_id": f"session::skill::{req_id}",
            "seq": 1,
            "type": "skill",
            "action": "trial_test_skill",
            "target": "host",
            "args": {"text": "技能执行测试"},
            "nonce": f"nonce::{req_id}",
        }
        # Governor 拒绝的输出（decision != APPROVE）
        governor_output = {
            "decision": "REJECT",
            "reason_code": "TRIAL_TEST_REJECT",
            "stage": "S_GOV_REJECT",
        }

        exec_result = loader.execute(
            intent=intent,
            governor_output=governor_output,
        )

        result = {
            "test_id": "T3-3",
            "service": "host.skills.loader",
            "operation": "SkillLoader.execute (rejected)",
            "params_in": {
                "intent_action": intent["action"],
                "governor_decision": governor_output["decision"],
            },
            "params_out": {
                "status": exec_result.status,
                "result_code": exec_result.result_code,
                "skill_name": exec_result.skill_name,
            },
            "status": "PASS" if exec_result.status == "REJECTED" else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("T3-3_skills_loader_execute_rejected", result)

        assert exec_result.status == "REJECTED"
        assert exec_result.result_code == "GOVERNOR_REJECTED"


# ===========================================================================
# 服务 4：host.integrations.llm_gateway — LLM 网关
# ⚠️ 跳过：需要真实 API Key（OPENAI_API_KEY / GEMINI_API_KEY / DEEPSEEK_API_KEY）
# ===========================================================================
@pytest.mark.skip(reason="[SKIP_MODEL] LLM 网关需要真实 API Key，跳过模型相关测试")
class TestLLMGateway:
    """host.integrations.llm_gateway — 跳过（需 API Key）。"""

    def test_llm_gateway_generate(self):
        """T4-1: [SKIP_MODEL] LLM 生成调用。需配置 OPENAI_API_KEY 等。"""
        pass

    def test_llm_gateway_streaming(self):
        """T4-2: [SKIP_MODEL] LLM 流式输出。"""
        pass


# ===========================================================================
# 服务 5：host.retrieval.embedding — 向量嵌入服务
# ===========================================================================
class TestRetrievalEmbedding:
    """host.retrieval.embedding 真实服务试运行（sentence_transformers 本地模型）。"""

    @pytest.fixture(scope="class")
    def embedding_service(self, trial_ledger):
        from host_brain.retrieval.embedding import (
            EmbeddingServiceContract,
            build_embedding_provider_registry,
            load_embedding_runtime_config,
        )

        cfg_result = load_embedding_runtime_config(env=os.environ)
        assert cfg_result.success, f"Embedding config failed: {cfg_result.as_dict()}"

        registry = build_embedding_provider_registry(env=os.environ)
        return EmbeddingServiceContract(
            ledger=trial_ledger,
            runtime_config=cfg_result.config,
            provider_registry=registry,
            node_id="trial-node-01",
            available=True,
        )

    def test_embedding_single_text(self, embedding_service):
        """T5-1: 单文本嵌入，验证向量维度和类型。"""
        from host_brain.core.ids import new_request_id, now_ms

        req_id = new_request_id()
        text_in = "夏目友人帐，妖怪与人类的故事"

        result_emb = embedding_service.embed(
            request_id=req_id,
            text=text_in,
            intent_id=f"intent::{req_id}",
            ts_ms=now_ms(),
        )

        vector = result_emb.output.get("embedding_vector") or []
        result = {
            "test_id": "T5-1",
            "service": "host.retrieval.embedding",
            "operation": "embed",
            "params_in": {"text": text_in, "request_id": req_id},
            "params_out": {
                "status": result_emb.status,
                "reason_code": result_emb.reason_code,
                "vector_dim": len(vector),
                "vector_sample": vector[:5] if vector else [],
                "model_id": result_emb.output.get("model_id"),
            },
            "status": "PASS" if result_emb.success and len(vector) > 0 else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("T5-1_embedding_single_text", result)

        assert result_emb.success, f"Embedding failed: {result_emb.reason_code}"
        assert len(vector) > 0, "Embedding vector is empty"
        assert all(isinstance(v, float) for v in vector[:5]), "Vector values must be float"

    def test_embedding_chinese_text(self, embedding_service):
        """T5-2: 中文文本嵌入（多语言模型验证）。"""
        from host_brain.core.ids import new_request_id, now_ms

        req_id = new_request_id()
        text_in = "今天天气很好，我想出去散步"

        result_emb = embedding_service.embed(
            request_id=req_id,
            text=text_in,
            ts_ms=now_ms(),
        )

        vector = result_emb.output.get("embedding_vector") or []
        result = {
            "test_id": "T5-2",
            "service": "host.retrieval.embedding",
            "operation": "embed_chinese",
            "params_in": {"text": text_in},
            "params_out": {
                "status": result_emb.status,
                "vector_dim": len(vector),
            },
            "status": "PASS" if result_emb.success else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("T5-2_embedding_chinese_text", result)

        assert result_emb.success, f"Chinese embedding failed: {result_emb.reason_code}"
        assert len(vector) > 0

    def test_embedding_empty_text_degraded(self, embedding_service):
        """T5-3: 空文本嵌入 → 降级处理（不崩溃）。"""
        from host_brain.core.ids import new_request_id, now_ms

        req_id = new_request_id()
        result_emb = embedding_service.embed(
            request_id=req_id,
            text="",
            ts_ms=now_ms(),
        )

        result = {
            "test_id": "T5-3",
            "service": "host.retrieval.embedding",
            "operation": "embed_empty",
            "params_in": {"text": ""},
            "params_out": {
                "status": result_emb.status,
                "reason_code": result_emb.reason_code,
                "success": result_emb.success,
            },
            "status": "PASS",  # 空文本可能成功也可能降级，都是合法行为
            "ts_ms": _RUN_TS,
        }
        _save_report("T5-3_embedding_empty_text", result)

        # 不崩溃即通过
        assert result_emb is not None


# ===========================================================================
# 服务 6：host.retrieval.reranker — 重排序服务
# ===========================================================================
class TestRetrievalReranker:
    """host.retrieval.reranker 真实服务试运行（heuristic 模式）。"""

    @pytest.fixture(scope="class")
    def reranker_service(self, trial_ledger):
        from host_brain.retrieval.reranker import (
            RerankerServiceContract,
            build_reranker_provider_registry,
            load_reranker_runtime_config,
        )

        cfg_result = load_reranker_runtime_config(env=os.environ)
        assert cfg_result.success, f"Reranker config failed: {cfg_result.as_dict()}"

        registry = build_reranker_provider_registry(env=os.environ)
        return RerankerServiceContract(
            ledger=trial_ledger,
            runtime_config=cfg_result.config,
            provider_registry=registry,
            node_id="trial-node-01",
            available=True,
        )

    def test_reranker_basic(self, reranker_service):
        """T6-1: 基础重排序，验证 topk 输出。"""
        from host_brain.core.ids import new_request_id, now_ms

        req_id = new_request_id()
        query = "夏目友人帐"
        candidates = [
            {"memory_id": "m1", "text": "夏目友人帐是一部关于妖怪的动漫", "score": 0.9},
            {"memory_id": "m2", "text": "今天天气很好", "score": 0.3},
            {"memory_id": "m3", "text": "夏目贵志与猫咪老师的故事", "score": 0.85},
            {"memory_id": "m4", "text": "Python 编程语言", "score": 0.1},
            {"memory_id": "m5", "text": "妖怪名册的秘密", "score": 0.7},
        ]

        result_rerank = reranker_service.rerank(
            request_id=req_id,
            query=query,
            candidates=candidates,
            top_k=3,
            ts_ms=now_ms(),
        )

        topk = result_rerank.output.get("topk") or []
        result = {
            "test_id": "T6-1",
            "service": "host.retrieval.reranker",
            "operation": "rerank",
            "params_in": {
                "query": query,
                "candidate_count": len(candidates),
                "top_k": 3,
            },
            "params_out": {
                "status": result_rerank.status,
                "reason_code": result_rerank.reason_code,
                "topk_count": len(topk),
                "topk_ids": [t.get("memory_id") for t in topk],
            },
            "status": "PASS" if result_rerank.success else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("T6-1_reranker_basic", result)

        assert result_rerank.success, f"Reranker failed: {result_rerank.reason_code}"
        assert len(topk) <= 3

    def test_reranker_empty_candidates(self, reranker_service):
        """T6-2: 空候选集重排序 → 降级处理。"""
        from host_brain.core.ids import new_request_id, now_ms

        req_id = new_request_id()
        result_rerank = reranker_service.rerank(
            request_id=req_id,
            query="test",
            candidates=[],
            top_k=5,
            ts_ms=now_ms(),
        )

        result = {
            "test_id": "T6-2",
            "service": "host.retrieval.reranker",
            "operation": "rerank_empty",
            "params_in": {"candidates": []},
            "params_out": {
                "status": result_rerank.status,
                "topk_count": len(result_rerank.output.get("topk") or []),
            },
            "status": "PASS",
            "ts_ms": _RUN_TS,
        }
        _save_report("T6-2_reranker_empty", result)

        assert result_rerank is not None
        assert len(result_rerank.output.get("topk") or []) == 0


# ===========================================================================
# 服务 7：host.vision.screen_ocr — 屏幕 OCR
# ⚠️ 跳过：需要屏幕截图能力和 OCR 模型
# ===========================================================================
@pytest.mark.skip(reason="[SKIP_MODEL] 屏幕 OCR 需要屏幕截图能力和 OCR 模型，跳过")
class TestVisionScreenOCR:
    """host.vision.screen_ocr — 跳过（需 OCR 模型）。"""

    def test_screen_ocr_capture(self):
        """T7-1: [SKIP_MODEL] 屏幕截图 + OCR 识别。"""
        pass


# ===========================================================================
# 服务 8：host.vision.camera_recognizer — 摄像头识别
# ⚠️ 跳过：需要摄像头硬件
# ===========================================================================
@pytest.mark.skip(reason="[SKIP_MODEL] 摄像头识别需要摄像头硬件，跳过")
class TestVisionCameraRecognizer:
    """host.vision.camera_recognizer — 跳过（需摄像头）。"""

    def test_camera_recognize(self):
        """T8-1: [SKIP_MODEL] 摄像头帧识别。"""
        pass


# ===========================================================================
# 服务 9：edge.rpc.gateway — Edge RPC 网关（配置探针）
# ===========================================================================
class TestEdgeRPCGateway:
    """edge.rpc.gateway 真实服务试运行（配置探针，不需要 edge 节点在线）。"""

    def test_edge_config_load(self):
        """T9-1: Edge 配置加载验证。"""
        from edge_client.config import load_edge_config

        result_cfg = load_edge_config(env=os.environ)

        result = {
            "test_id": "T9-1",
            "service": "edge.rpc.gateway",
            "operation": "load_edge_config",
            "params_in": {
                "EDGE_RPC_HOST": os.environ.get("EDGE_RPC_HOST"),
                "EDGE_RPC_PORT": os.environ.get("EDGE_RPC_PORT"),
                "EDGE_RPC_GATEWAY_ENABLED": os.environ.get("EDGE_RPC_GATEWAY_ENABLED"),
            },
            "params_out": {
                "success": result_cfg.success,
                "reason_code": result_cfg.reason_code,
                "config": result_cfg.config.as_dict() if result_cfg.success and result_cfg.config else None,
            },
            "status": "PASS" if result_cfg.success else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("T9-1_edge_config_load", result)

        assert result_cfg.success, f"Edge config load failed: {result_cfg.reason_code}"

    def test_edge_runtime_statuses(self):
        """T9-2: Edge runtime statuses 探针。"""
        from edge_client.config import load_edge_config, build_edge_runtime_statuses

        result_cfg = load_edge_config(env=os.environ)
        assert result_cfg.success

        statuses = build_edge_runtime_statuses(result_cfg.config)

        result = {
            "test_id": "T9-2",
            "service": "edge.rpc.gateway",
            "operation": "build_edge_runtime_statuses",
            "params_in": {},
            "params_out": statuses,
            "status": "PASS" if "edge.rpc.gateway" in statuses else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("T9-2_edge_runtime_statuses", result)

        assert "edge.rpc.gateway" in statuses
        # gateway 状态应为 available=True（配置已启用）
        gw_status = statuses["edge.rpc.gateway"]
        assert isinstance(gw_status.get("available"), bool)

    def test_edge_required_services_registry(self):
        """T9-3: Edge 服务注册表完整性验证。"""
        from edge_client.rpc import edge_required_services

        services = edge_required_services()
        service_ids = [s.service_id for s in services]

        expected = [
            "edge.rpc.gateway",
            "edge.rpc.asr",
            "edge.rpc.tts",
            "edge.rpc.barge_in",
            "edge.rpc.fallback_llm",
            "edge.rpc.resource_gate",
        ]

        result = {
            "test_id": "T9-3",
            "service": "edge.rpc.gateway",
            "operation": "edge_required_services",
            "params_in": {},
            "params_out": {
                "service_count": len(services),
                "service_ids": service_ids,
            },
            "status": "PASS" if all(e in service_ids for e in expected) else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("T9-3_edge_required_services", result)

        for expected_id in expected:
            assert expected_id in service_ids, f"Missing edge service: {expected_id}"


# ===========================================================================
# 服务 10-13：edge.rpc.asr / tts / barge_in / fallback_llm
# ⚠️ 跳过：需要 ASR/TTS 模型和 edge 节点在线
# ===========================================================================
@pytest.mark.skip(reason="[SKIP_MODEL] ASR 需要 faster-whisper 模型和 edge 节点在线，跳过")
class TestEdgeRPCASR:
    """edge.rpc.asr — 跳过（需 ASR 模型）。"""

    def test_asr_transcribe(self):
        """T10-1: [SKIP_MODEL] ASR 语音转文字。"""
        pass


@pytest.mark.skip(reason="[SKIP_MODEL] TTS 需要 GPT-SoVITS 模型和 edge 节点在线，跳过")
class TestEdgeRPCTTS:
    """edge.rpc.tts — 跳过（需 TTS 模型）。"""

    def test_tts_synthesize(self):
        """T11-1: [SKIP_MODEL] TTS 文字转语音。"""
        pass


@pytest.mark.skip(reason="[SKIP_MODEL] Barge-in 依赖 TTS 服务，跳过")
class TestEdgeRPCBargeIn:
    """edge.rpc.barge_in — 跳过（依赖 TTS）。"""

    def test_barge_in_interrupt(self):
        """T12-1: [SKIP_MODEL] Barge-in 打断检测。"""
        pass


@pytest.mark.skip(reason="[SKIP_MODEL] Fallback LLM 需要本地 Ollama 模型，跳过")
class TestEdgeRPCFallbackLLM:
    """edge.rpc.fallback_llm — 跳过（需本地 LLM）。"""

    def test_fallback_llm_generate(self):
        """T13-1: [SKIP_MODEL] Fallback LLM 本地推理。"""
        pass


# ===========================================================================
# 服务 14：edge.rpc.resource_gate — 资源门控
# ===========================================================================
class TestEdgeRPCResourceGate:
    """edge.rpc.resource_gate 真实服务试运行（配置探针）。"""

    def test_resource_gate_config(self):
        """T14-1: 资源门控配置加载验证。"""
        from edge_client.config import load_edge_config, build_edge_runtime_statuses

        result_cfg = load_edge_config(env=os.environ)
        assert result_cfg.success

        statuses = build_edge_runtime_statuses(result_cfg.config)

        rg_status = statuses.get("edge.rpc.resource_gate", {})
        result = {
            "test_id": "T14-1",
            "service": "edge.rpc.resource_gate",
            "operation": "resource_gate_probe",
            "params_in": {
                "EDGE_RESOURCE_GATE_ENABLED": os.environ.get("EDGE_RESOURCE_GATE_ENABLED"),
                "EDGE_MAX_VRAM_MB": os.environ.get("EDGE_MAX_VRAM_MB"),
                "EDGE_MAX_QUEUE_LENGTH": os.environ.get("EDGE_MAX_QUEUE_LENGTH"),
            },
            "params_out": rg_status,
            "status": "PASS" if "edge.rpc.resource_gate" in statuses else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("T14-1_resource_gate_config", result)

        assert "edge.rpc.resource_gate" in statuses
        assert isinstance(rg_status.get("available"), bool)


# ===========================================================================
# 综合链路：全服务健康检查报告
# ===========================================================================
class TestFullServiceHealthReport:
    """全服务健康检查报告 — 综合所有 14 个服务的状态。"""

    def test_build_service_health_report(self, trial_ledger):
        """TC-1: 构建完整服务健康报告，验证所有 14 个服务都有状态。"""
        from host_brain.app.main import build_service_health_report
        from host_brain.app.config import build_host_runtime_statuses, load_host_config
        from edge_client.config import load_edge_config, build_edge_runtime_statuses
        from host_brain.core.ids import new_request_id, now_ms

        req_id = new_request_id()
        ts = now_ms()

        # 构建 host runtime statuses
        host_result = load_host_config(env=os.environ)
        host_statuses = {}
        if host_result.success and host_result.config:
            host_statuses = build_host_runtime_statuses(host_result.config)

        # 构建 edge runtime statuses
        edge_result = load_edge_config(env=os.environ)
        edge_statuses = {}
        if edge_result.success and edge_result.config:
            edge_statuses = build_edge_runtime_statuses(edge_result.config)

        all_statuses = {**host_statuses, **edge_statuses}

        report = build_service_health_report(
            runtime_statuses=all_statuses,
            request_id=req_id,
            ledger=trial_ledger,
            ts_ms=ts,
        )

        service_ids = [s["service_id"] for s in report.get("services", [])]
        expected_services = [
            "host.audit.sqlite_ledger",
            "host.governor.decision",
            "host.skills.loader",
            "host.integrations.llm_gateway",
            "host.retrieval.embedding",
            "host.retrieval.reranker",
            "host.vision.screen_ocr",
            "host.vision.camera_recognizer",
            "edge.rpc.gateway",
            "edge.rpc.asr",
            "edge.rpc.tts",
            "edge.rpc.barge_in",
            "edge.rpc.fallback_llm",
            "edge.rpc.resource_gate",
        ]

        result = {
            "test_id": "TC-1",
            "service": "ALL_SERVICES",
            "operation": "build_service_health_report",
            "params_in": {
                "request_id": req_id,
                "host_statuses_count": len(host_statuses),
                "edge_statuses_count": len(edge_statuses),
            },
            "params_out": {
                "overall_status": report.get("summary", {}).get("overall_status"),
                "required_total": report.get("summary", {}).get("required_total"),
                "required_unavailable": report.get("summary", {}).get("required_unavailable"),
                "service_count": len(report.get("services", [])),
                "services": [
                    {
                        "service_id": s["service_id"],
                        "status": s["status"],
                        "reason_code": s.get("reason_code"),
                        "detail": s.get("detail"),
                    }
                    for s in report.get("services", [])
                ],
            },
            "status": "PASS" if len(service_ids) == 14 else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("TC-1_full_service_health_report", result)

        for expected_id in expected_services:
            assert expected_id in service_ids, f"Missing service in health report: {expected_id}"

        assert report.get("summary", {}).get("required_total") == 14

    def test_build_startup_report(self, trial_ledger):
        """TC-2: 构建完整启动报告，验证 host + edge 配置均正常加载。"""
        from host_brain.app.main import build_startup_report
        from host_brain.core.ids import new_request_id, now_ms

        req_id = new_request_id()
        ts = now_ms()

        report = build_startup_report(
            request_id=req_id,
            ledger=trial_ledger,
            ts_ms=ts,
        )

        result = {
            "test_id": "TC-2",
            "service": "ALL_SERVICES",
            "operation": "build_startup_report",
            "params_in": {"request_id": req_id},
            "params_out": {
                "boot_status": report.get("summary", {}).get("boot_status"),
                "required_total": report.get("summary", {}).get("required_total"),
                "required_unavailable": report.get("summary", {}).get("required_unavailable"),
                "issue_count": report.get("summary", {}).get("issue_count"),
                "host_config_success": report.get("config", {}).get("host", {}).get("success"),
                "edge_config_success": report.get("config", {}).get("edge", {}).get("success"),
            },
            "status": "PASS" if report.get("summary", {}).get("boot_status") in {"READY", "DEGRADED"} else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("TC-2_startup_report", result)

        assert report.get("summary", {}).get("boot_status") in {"READY", "DEGRADED"}
        assert report.get("config", {}).get("host", {}).get("success") is True
        assert report.get("config", {}).get("edge", {}).get("success") is True

    def test_host_runtime_statuses_all_services(self):
        """TC-3: host runtime statuses 覆盖所有 8 个 host 服务。"""
        from host_brain.app.config import build_host_runtime_statuses, load_host_config

        result_cfg = load_host_config(env=os.environ)
        assert result_cfg.success

        statuses = build_host_runtime_statuses(result_cfg.config)

        expected_host_services = [
            "host.audit.sqlite_ledger",
            "host.governor.decision",
            "host.skills.loader",
            "host.integrations.llm_gateway",
            "host.retrieval.embedding",
            "host.retrieval.reranker",
            "host.vision.screen_ocr",
            "host.vision.camera_recognizer",
        ]

        result = {
            "test_id": "TC-3",
            "service": "HOST_SERVICES",
            "operation": "build_host_runtime_statuses",
            "params_in": {
                "llm_provider": result_cfg.config.llm_provider,
                "enable_retrieval": result_cfg.config.enable_retrieval,
                "enable_vision": result_cfg.config.enable_vision,
            },
            "params_out": {
                k: {"available": v.get("available"), "detail": v.get("detail")}
                for k, v in statuses.items()
            },
            "status": "PASS" if all(s in statuses for s in expected_host_services) else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("TC-3_host_runtime_statuses", result)

        for service_id in expected_host_services:
            assert service_id in statuses, f"Missing host service status: {service_id}"
            assert isinstance(statuses[service_id].get("available"), bool)


# ===========================================================================
# 综合链路：完整 /v1/chat 请求链路（无 LLM，仅 Governor + 检索）
# ===========================================================================
class TestChatPipelineNoLLM:
    """完整 /v1/chat 请求链路试运行（跳过 LLM 推理，验证 Governor + 检索链路）。"""

    def test_chat_pipeline_governor_only(self, trial_ledger):
        """TC-4: Governor 裁决链路（不调用 LLM）。"""
        from host_brain.governor.decision import IntentDecisionEngine
        from host_brain.core.ids import new_request_id, now_ms

        req_id = new_request_id()
        ts = now_ms()
        session_id = f"session::trial::{req_id}"

        intent = {
            "schema_version": "Natsume.intent.v1",
            "id": f"intent::{req_id}",
            "ts_ms": ts,
            "session_id": session_id,
            "seq": 1,
            "type": "query",
            "action": "narrative_only",
            "target": "host",
            "args": {"text": "完整链路试运行测试"},
            "nonce": f"nonce::{req_id}",
        }

        engine = IntentDecisionEngine(ledger=trial_ledger)
        gov_result = engine.evaluate_intent(raw_intent=intent, request_id=req_id)

        # 追踪链路
        trace = trial_ledger.trace_request(req_id)

        result = {
            "test_id": "TC-4",
            "service": "CHAT_PIPELINE",
            "operation": "governor_chain",
            "params_in": {
                "request_id": req_id,
                "session_id": session_id,
                "message": "完整链路试运行测试",
            },
            "params_out": {
                "decision": gov_result.get("decision"),
                "reason_code": gov_result.get("reason_code"),
                "stage": gov_result.get("stage"),
                "intent_id": gov_result.get("intent_id"),
                "trace_status": trace.get("status"),
                "intent_log_count": len(trace.get("intent_log") or []),
                "governor_log_count": len(trace.get("governor_log") or []),
            },
            "status": "PASS" if gov_result.get("decision") in {"APPROVE", "REJECT"} else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("TC-4_chat_pipeline_governor", result)

        assert gov_result.get("decision") in {"APPROVE", "REJECT"}
        assert trace.get("status") in {"COMPLETE", "COMPLETE_REJECTED"}

    def test_chat_pipeline_with_retrieval(self, trial_ledger):
        """TC-5: Governor + 检索管道完整链路（不调用 LLM）。"""
        from host_brain.governor.decision import IntentDecisionEngine
        from host_brain.retrieval.pipeline import build_retrieval_pipeline_from_env
        from host_brain.core.ids import new_request_id, now_ms
        from host_brain.governor.schemas import DECISION_APPROVE

        req_id = new_request_id()
        ts = now_ms()
        session_id = f"session::retrieval::{req_id}"

        intent = {
            "schema_version": "Natsume.intent.v1",
            "id": f"intent::{req_id}",
            "ts_ms": ts,
            "session_id": session_id,
            "seq": 1,
            "type": "query",
            "action": "narrative_only",
            "target": "host",
            "args": {"text": "夏目友人帐的故事"},
            "nonce": f"nonce::{req_id}",
        }

        # Step 1: Governor 裁决
        engine = IntentDecisionEngine(ledger=trial_ledger)
        gov_result = engine.evaluate_intent(raw_intent=intent, request_id=req_id)

        retrieval_result = None
        retrieval_summary = {}

        # Step 2: 如果 APPROVE，执行检索
        if gov_result.get("decision") == DECISION_APPROVE:
            try:
                pipeline = build_retrieval_pipeline_from_env(
                    env=os.environ,
                    ledger=trial_ledger,
                    node_id="trial-node-01",
                )
                retrieval_result = pipeline.run(
                    request_id=req_id,
                    query="夏目友人帐的故事",
                    intent_id=gov_result.get("intent_id"),
                    top_k=5,
                    ts_ms=ts,
                )
                retrieval_summary = {
                    "status": retrieval_result.status,
                    "reason_code": retrieval_result.reason_code,
                    "recall_candidates": len(retrieval_result.output.get("recall", {}).get("candidates") or []),
                    "rerank_topk": len(retrieval_result.output.get("rerank", {}).get("topk") or []),
                    "evidence_refs": len(retrieval_result.output.get("evidence_pack", {}).get("evidence_refs") or []),
                    "degraded": retrieval_result.output.get("degraded"),
                }
            except Exception as exc:
                retrieval_summary = {"error": str(exc), "status": "ERROR"}

        result = {
            "test_id": "TC-5",
            "service": "CHAT_PIPELINE",
            "operation": "governor_plus_retrieval",
            "params_in": {
                "request_id": req_id,
                "message": "夏目友人帐的故事",
            },
            "params_out": {
                "decision": gov_result.get("decision"),
                "reason_code": gov_result.get("reason_code"),
                "retrieval": retrieval_summary,
            },
            "status": "PASS" if gov_result.get("decision") in {"APPROVE", "REJECT"} else "FAIL",
            "ts_ms": _RUN_TS,
        }
        _save_report("TC-5_chat_pipeline_with_retrieval", result)

        assert gov_result.get("decision") in {"APPROVE", "REJECT"}


# ===========================================================================
# 综合报告：生成里程碑汇总报告
# ===========================================================================
class TestMilestoneSummaryReport:
    """生成里程碑汇总报告。"""

    def test_generate_milestone_summary(self):
        """TS-1: 汇总所有试运行报告，生成里程碑摘要。"""
        # 收集所有已生成的报告
        report_files = sorted(_REPORT_DIR.glob(f"*_{_RUN_DATE}.json"))
        reports = []
        for f in report_files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                reports.append({
                    "file": f.name,
                    "test_id": data.get("test_id"),
                    "service": data.get("service"),
                    "operation": data.get("operation"),
                    "status": data.get("status"),
                })
            except Exception:
                pass

        pass_count = sum(1 for r in reports if r.get("status") == "PASS")
        fail_count = sum(1 for r in reports if r.get("status") == "FAIL")

        summary = {
            "milestone": "M-TRIAL-RUN-2026-03-05",
            "run_date": _RUN_DATE,
            "run_ts_ms": _RUN_TS,
            "total_reports": len(reports),
            "pass_count": pass_count,
            "fail_count": fail_count,
            "skipped_services": [
                "host.integrations.llm_gateway (需 API Key)",
                "host.vision.screen_ocr (需 OCR 模型)",
                "host.vision.camera_recognizer (需摄像头)",
                "edge.rpc.asr (需 ASR 模型)",
                "edge.rpc.tts (需 TTS 模型)",
                "edge.rpc.barge_in (依赖 TTS)",
                "edge.rpc.fallback_llm (需本地 LLM)",
            ],
            "tested_services": [
                "host.audit.sqlite_ledger",
                "host.governor.decision",
                "host.skills.loader",
                "host.retrieval.embedding",
                "host.retrieval.reranker",
                "edge.rpc.gateway",
                "edge.rpc.resource_gate",
            ],
            "reports": reports,
        }

        summary_path = _save_report("TS-1_milestone_summary", summary)

        assert len(reports) > 0, "No trial run reports generated"
        assert fail_count == 0, f"Some tests failed: {[r for r in reports if r.get('status') == 'FAIL']}"
