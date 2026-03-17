"""工具注册表：ReAct agent 可调用的工具列表（M-MA6）。

设计原则：
- 工具分为只读工具（allow_side_effects=False）和有副作用工具（allow_side_effects=True）
- 只读工具：所有场景均可调用
- 有副作用工具：仅 SCENE_EXEC_INTENT 场景允许调用（allow_side_effects=True 时）
- 工具执行结果写入 react_scratchpad.steps[n].observation，不直接修改 GraphState 控制字段
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ToolSpec：工具规格定义
# ---------------------------------------------------------------------------

@dataclass
class ToolSpec:
    """工具规格定义。"""
    tool_id: str
    display_name: str
    description: str
    parameters: dict[str, Any]          # JSON Schema 格式的参数定义
    has_side_effects: bool = False       # True = 有副作用（写操作），需要 allow_side_effects=True
    executor: Callable[..., Any] | None = field(default=None, repr=False)

    def to_llm_schema(self) -> dict[str, Any]:
        """转换为 LLM function calling 格式的 schema。"""
        return {
            "name": self.tool_id,
            "description": self.description,
            "parameters": self.parameters,
        }


# ---------------------------------------------------------------------------
# 内部工具：语义召回器（延迟获取，避免循环导入）
# ---------------------------------------------------------------------------

def _get_semantic_recaller():
    """延迟获取 SemanticRecaller 单例，避免循环导入。"""
    try:
        from host_brain.app.dependencies import get_semantic_recaller  # noqa: PLC0415
        return get_semantic_recaller()
    except Exception as exc:
        logger.warning("tool_registry: 无法获取 SemanticRecaller，降级为 mock: %s", exc)
        from host_brain.app.recall.semantic_recall import SemanticRecaller  # noqa: PLC0415
        return SemanticRecaller(use_mock=True)


def _run_async(coro):
    """在同步上下文中运行异步协程。"""
    try:
        loop = asyncio.get_running_loop()
        # 已有运行中的事件循环（如 FastAPI 环境）：用 run_coroutine_threadsafe
        import concurrent.futures  # noqa: PLC0415
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=5.0)
    except RuntimeError:
        # 没有运行中的事件循环：直接 asyncio.run
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 内置工具实现（只读工具，无副作用）
# ---------------------------------------------------------------------------

def _tool_search_knowledge(args: dict[str, Any]) -> dict[str, Any]:
    """知识库语义检索工具（只读）。接入 SemanticRecaller。"""
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "MISSING_QUERY", "result": None}

    t0 = int(time.time() * 1000)
    try:
        recaller = _get_semantic_recaller()
        recall_result = _run_async(recaller.recall(query=query))

        latency_ms = int(time.time() * 1000) - t0
        hits = getattr(recall_result, "hits", [])
        filtered_count = int(getattr(recall_result, "filtered_count", 0))
        degraded = bool(getattr(recall_result, "degraded", False))

        if filtered_count == 0:
            return {
                "result": f"未找到与 '{query}' 相关的知识库内容",
                "source": "knowledge_base",
                "confidence": 0.0,
                "items": [],
                "latency_ms": latency_ms,
                "degraded": degraded,
            }

        # 构建结果文本
        items = []
        lines = []
        for i, hit in enumerate(hits, 1):
            content = str(getattr(hit, "content", "") or "")
            score = float(getattr(hit, "score", 0.0))
            doc_id = str(getattr(hit, "doc_id", "") or "")
            items.append({"doc_id": doc_id, "content": content, "score": score})
            lines.append(f"[{i}] (相关度={score:.2f}) {content}")

        result_text = "\n".join(lines)
        avg_confidence = sum(it["score"] for it in items) / len(items) if items else 0.0

        return {
            "result": result_text,
            "source": "knowledge_base",
            "confidence": round(avg_confidence, 4),
            "items": items,
            "latency_ms": latency_ms,
            "degraded": degraded,
        }
    except Exception as exc:
        logger.warning("_tool_search_knowledge 执行失败: %s", exc)
        return {
            "result": f"知识库检索失败: {exc}",
            "source": "knowledge_base",
            "confidence": 0.0,
            "items": [],
            "error": str(exc),
        }


def _tool_search_by_keyword(args: dict[str, Any]) -> dict[str, Any]:
    """关键词检索工具（只读）。基于关键词重叠率召回，接入 SemanticRecaller mock 路径。"""
    keyword = str(args.get("keyword") or args.get("query") or "").strip()
    if not keyword:
        return {"error": "MISSING_KEYWORD", "result": None}

    top_k = int(args.get("top_k") or 5)
    top_k = max(1, min(top_k, 20))

    t0 = int(time.time() * 1000)
    try:
        recaller = _get_semantic_recaller()
        # 关键词检索：降低阈值以提高召回率
        recall_result = _run_async(recaller.recall(query=keyword, top_k=top_k, threshold=0.1))

        latency_ms = int(time.time() * 1000) - t0
        hits = getattr(recall_result, "hits", [])
        filtered_count = int(getattr(recall_result, "filtered_count", 0))

        if filtered_count == 0:
            return {
                "result": f"关键词 '{keyword}' 未命中任何文档",
                "source": "keyword_index",
                "matched_count": 0,
                "items": [],
                "latency_ms": latency_ms,
            }

        items = []
        lines = []
        for i, hit in enumerate(hits, 1):
            content = str(getattr(hit, "content", "") or "")
            score = float(getattr(hit, "score", 0.0))
            doc_id = str(getattr(hit, "doc_id", "") or "")
            items.append({"doc_id": doc_id, "content": content, "score": score})
            lines.append(f"[{i}] {content}")

        return {
            "result": "\n".join(lines),
            "source": "keyword_index",
            "matched_count": len(items),
            "items": items,
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        logger.warning("_tool_search_by_keyword 执行失败: %s", exc)
        return {
            "result": f"关键词检索失败: {exc}",
            "source": "keyword_index",
            "matched_count": 0,
            "items": [],
            "error": str(exc),
        }


def _tool_search_by_vector(args: dict[str, Any]) -> dict[str, Any]:
    """向量语义检索工具（只读）。接入 SemanticRecaller，使用较高相似度阈值。"""
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "MISSING_QUERY", "result": None}

    top_k = int(args.get("top_k") or 5)
    top_k = max(1, min(top_k, 20))
    threshold = float(args.get("threshold") or 0.70)
    threshold = max(0.0, min(threshold, 1.0))

    t0 = int(time.time() * 1000)
    try:
        recaller = _get_semantic_recaller()
        recall_result = _run_async(recaller.recall(query=query, top_k=top_k, threshold=threshold))

        latency_ms = int(time.time() * 1000) - t0
        hits = getattr(recall_result, "hits", [])
        filtered_count = int(getattr(recall_result, "filtered_count", 0))
        degraded = bool(getattr(recall_result, "degraded", False))

        if filtered_count == 0:
            return {
                "result": f"向量检索未找到与 '{query}' 相似度≥{threshold:.2f} 的内容",
                "source": "vector_store",
                "top_k": top_k,
                "items": [],
                "latency_ms": latency_ms,
                "degraded": degraded,
            }

        items = []
        lines = []
        for i, hit in enumerate(hits, 1):
            content = str(getattr(hit, "content", "") or "")
            score = float(getattr(hit, "score", 0.0))
            doc_id = str(getattr(hit, "doc_id", "") or "")
            items.append({"doc_id": doc_id, "content": content, "score": score})
            lines.append(f"[{i}] (相似度={score:.2f}) {content}")

        return {
            "result": "\n".join(lines),
            "source": "vector_store",
            "top_k": len(items),
            "items": items,
            "latency_ms": latency_ms,
            "degraded": degraded,
        }
    except Exception as exc:
        logger.warning("_tool_search_by_vector 执行失败: %s", exc)
        return {
            "result": f"向量检索失败: {exc}",
            "source": "vector_store",
            "top_k": 0,
            "items": [],
            "error": str(exc),
        }


def _tool_get_context(args: dict[str, Any]) -> dict[str, Any]:
    """获取当前对话上下文（只读）。返回真实的运行时上下文信息。"""
    ts_ms = int(time.time() * 1000)
    session_id = str(args.get("session_id") or "").strip() or None
    context_type = str(args.get("context_type") or "conversation").strip()

    context_info: dict[str, Any] = {
        "ts_ms": ts_ms,
        "context_type": context_type,
        "tool_registry_version": "P13",
    }

    if session_id:
        context_info["session_id"] = session_id

    # 尝试获取 SemanticRecaller 状态（反映知识库可用性）
    try:
        recaller = _get_semantic_recaller()
        use_mock = bool(getattr(recaller, "_use_mock", True))
        mock_store_size = len(getattr(recaller, "_mock_store", []))
        context_info["knowledge_base"] = {
            "available": True,
            "backend": "mock" if use_mock else "chromadb",
            "mock_doc_count": mock_store_size if use_mock else None,
        }
    except Exception:
        context_info["knowledge_base"] = {"available": False}

    return {
        "result": f"上下文已获取（ts={ts_ms}, type={context_type}）",
        "context_type": context_type,
        "context": context_info,
    }


def _tool_analyze_code(args: dict[str, Any]) -> dict[str, Any]:
    """代码静态分析工具（只读）。返回真实的代码结构分析结果。"""
    code = str(args.get("code") or "").strip()
    language = str(args.get("language") or "python").strip().lower()
    if not code:
        return {"error": "MISSING_CODE", "result": None}

    lines = code.splitlines()
    total_lines = len(lines)

    # 统计空行和注释行
    blank_lines = sum(1 for ln in lines if not ln.strip())
    comment_lines = 0
    if language in ("python", "py"):
        comment_lines = sum(1 for ln in lines if ln.strip().startswith("#"))
    elif language in ("javascript", "js", "typescript", "ts", "java", "c", "cpp", "go"):
        comment_lines = sum(1 for ln in lines if ln.strip().startswith("//") or ln.strip().startswith("*"))

    code_lines = total_lines - blank_lines - comment_lines

    # 统计函数/方法定义数
    func_count = 0
    class_count = 0
    if language in ("python", "py"):
        func_count = len(re.findall(r"^\s*(?:async\s+)?def\s+\w+", code, re.MULTILINE))
        class_count = len(re.findall(r"^\s*class\s+\w+", code, re.MULTILINE))
    elif language in ("javascript", "js", "typescript", "ts"):
        func_count = len(re.findall(
            r"(?:function\s+\w+|(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?(?:function|\())",
            code,
        ))
        class_count = len(re.findall(r"\bclass\s+\w+", code))
    elif language in ("java", "c", "cpp"):
        func_count = len(re.findall(r"\b\w+\s+\w+\s*\([^)]*\)\s*\{", code))
        class_count = len(re.findall(r"\bclass\s+\w+", code))

    # 检测潜在问题
    issues = []
    if total_lines > 300:
        issues.append(f"文件较长（{total_lines} 行），建议拆分")
    if func_count > 20:
        issues.append(f"函数数量较多（{func_count} 个），建议模块化")
    if comment_lines == 0 and code_lines > 30:
        issues.append("缺少注释，建议补充文档")

    comment_ratio = round(comment_lines / max(total_lines, 1), 3)

    result_summary = (
        f"语言={language}, 总行数={total_lines}, 代码行={code_lines}, "
        f"注释行={comment_lines}（{comment_ratio:.1%}）, 空行={blank_lines}, "
        f"函数={func_count}, 类={class_count}"
    )
    if issues:
        result_summary += f"; 建议: {'; '.join(issues)}"

    return {
        "result": result_summary,
        "language": language,
        "analysis_type": "static",
        "metrics": {
            "total_lines": total_lines,
            "code_lines": code_lines,
            "comment_lines": comment_lines,
            "blank_lines": blank_lines,
            "comment_ratio": comment_ratio,
            "func_count": func_count,
            "class_count": class_count,
        },
        "issues": issues,
    }


def _tool_evaluate_artifact(args: dict[str, Any]) -> dict[str, Any]:
    """工件质量评估工具（只读）。对单个 AgentArtifact 进行结构化质量评估。"""
    artifact = args.get("artifact") or {}
    agent_id = str(args.get("agent_id") or artifact.get("agent_id") or "").strip()
    if not artifact:
        return {"error": "MISSING_ARTIFACT", "result": None}

    confidence = float(artifact.get("confidence") or 0.0)
    summary = str(artifact.get("summary") or "").strip()
    risk_flags = list(artifact.get("risk_flags") or [])
    artifact_ref = str(artifact.get("artifact_ref") or "").strip()
    error = artifact.get("error")

    # 评估维度
    issues = []
    score = confidence

    if error:
        issues.append(f"工件含错误: {error}")
        score = 0.0
    if not summary:
        issues.append("摘要为空")
        score = max(0.0, score - 0.2)
    if not artifact_ref:
        issues.append("artifact_ref 缺失")
        score = max(0.0, score - 0.1)
    if confidence < 0.3:
        issues.append(f"置信度偏低（{confidence:.2f}）")
    high_risk = any(
        flag in risk_flags
        for flag in {"SAFETY_RISK:HIGH", "SAFETY_RISK:CRITICAL"}
    )
    if high_risk:
        issues.append("含高风险标记")
        score = 0.0

    quality = "HIGH" if score >= 0.7 else ("MEDIUM" if score >= 0.4 else "LOW")

    result_text = (
        f"工件评估完成（agent={agent_id}, 置信度={confidence:.2f}, "
        f"质量={quality}, 风险标记={risk_flags}）"
    )
    if issues:
        result_text += f"；问题: {'; '.join(issues)}"

    return {
        "result": result_text,
        "agent_id": agent_id,
        "quality": quality,
        "score": round(score, 4),
        "confidence": confidence,
        "risk_flags": risk_flags,
        "issues": issues,
        "has_error": bool(error),
    }


def _tool_compare_artifacts(args: dict[str, Any]) -> dict[str, Any]:
    """多工件比较工具（只读）。比较多个 AgentArtifact，推荐最优工件。"""
    artifacts = args.get("artifacts") or {}
    if not artifacts or not isinstance(artifacts, dict):
        return {"error": "MISSING_ARTIFACTS", "result": None}

    # 过滤有错误的工件
    valid: dict[str, dict] = {
        aid: art for aid, art in artifacts.items()
        if not art.get("error")
    }
    if not valid:
        return {
            "result": "所有工件均含错误，无法比较",
            "recommended": None,
            "reason": "ALL_FAILED",
            "scores": {},
        }

    # 计算每个工件的综合评分
    scores: dict[str, float] = {}
    for aid, art in valid.items():
        confidence = float(art.get("confidence") or 0.0)
        risk_flags = list(art.get("risk_flags") or [])
        summary = str(art.get("summary") or "")
        high_risk = any(
            flag in risk_flags
            for flag in {"SAFETY_RISK:HIGH", "SAFETY_RISK:CRITICAL"}
        )
        score = 0.0 if high_risk else confidence
        # 摘要非空加分
        if summary:
            score = min(1.0, score + 0.05)
        scores[aid] = round(score, 4)

    best_agent = max(scores, key=lambda k: scores[k])
    best_score = scores[best_agent]

    # 构建比较摘要
    comparison_lines = [
        f"{aid}: score={scores[aid]:.2f}" for aid in sorted(scores)
    ]
    result_text = (
        f"比较 {len(valid)} 个有效工件，推荐 {best_agent}（score={best_score:.2f}）。"
        f" 详情: {', '.join(comparison_lines)}"
    )

    return {
        "result": result_text,
        "recommended": best_agent,
        "reason": "HIGHEST_SCORE",
        "scores": scores,
        "valid_count": len(valid),
        "total_count": len(artifacts),
    }


def _tool_translate_text(args: dict[str, Any]) -> dict[str, Any]:
    """文本翻译工具（只读）。检测源语言并返回翻译元数据；实际翻译由 LLM 完成。"""
    text = str(args.get("text") or "").strip()
    target_lang = str(args.get("target_lang") or "en").strip().lower()
    if not text:
        return {"error": "MISSING_TEXT", "result": None}

    # 检测源语言
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    japanese_chars = len(re.findall(r"[\u3040-\u30ff]", text))
    korean_chars = len(re.findall(r"[\uac00-\ud7af]", text))
    ascii_words = len(re.findall(r"[a-zA-Z]{2,}", text))
    total_chars = max(len(text), 1)

    if chinese_chars / total_chars > 0.3:
        detected_lang = "zh"
    elif japanese_chars / total_chars > 0.2:
        detected_lang = "ja"
    elif korean_chars / total_chars > 0.2:
        detected_lang = "ko"
    elif ascii_words > 3:
        detected_lang = "en"
    else:
        detected_lang = "unknown"

    # 语言代码标准化
    lang_names = {
        "zh": "中文", "en": "英文", "ja": "日文",
        "ko": "韩文", "fr": "法文", "de": "德文",
        "es": "西班牙文", "ru": "俄文",
    }
    src_name = lang_names.get(detected_lang, detected_lang)
    tgt_name = lang_names.get(target_lang, target_lang)

    # 若源语言与目标语言相同，直接返回
    if detected_lang == target_lang:
        return {
            "result": text,
            "source_lang": detected_lang,
            "target_lang": target_lang,
            "translated": False,
            "note": f"源语言与目标语言相同（{src_name}），无需翻译",
        }

    # 无专用翻译后端：返回翻译请求元数据，由 ReAct 循环中的 LLM 完成翻译
    return {
        "result": f"[待翻译] {src_name}→{tgt_name}，原文长度={len(text)} 字符，请 LLM 完成翻译",
        "source_lang": detected_lang,
        "target_lang": target_lang,
        "translated": False,
        "text_length": len(text),
        "char_stats": {
            "chinese": chinese_chars,
            "japanese": japanese_chars,
            "korean": korean_chars,
            "ascii_words": ascii_words,
        },
        "note": "translate_text 工具提供语言检测和元数据，实际翻译由 LLM 在 ReAct 循环中完成",
    }


# ---------------------------------------------------------------------------
# 工具注册表
# ---------------------------------------------------------------------------

_TOOL_REGISTRY: dict[str, ToolSpec] = {
    "search_knowledge": ToolSpec(
        tool_id="search_knowledge",
        display_name="知识库语义检索",
        description="在知识库中进行语义检索，返回最相关的内容片段（接入 SemanticRecaller）",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索查询词"},
            },
            "required": ["query"],
        },
        has_side_effects=False,
        executor=_tool_search_knowledge,
    ),
    "search_by_keyword": ToolSpec(
        tool_id="search_by_keyword",
        display_name="关键词检索",
        description="基于关键词重叠率在知识库中检索，适合精确词汇匹配场景",
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "检索关键词"},
                "top_k": {"type": "integer", "description": "最大返回条数，默认 5，范围 1-20"},
            },
            "required": ["keyword"],
        },
        has_side_effects=False,
        executor=_tool_search_by_keyword,
    ),
    "search_by_vector": ToolSpec(
        tool_id="search_by_vector",
        display_name="向量语义检索",
        description="基于向量相似度在知识库中检索，适合语义相似场景，支持自定义相似度阈值",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索查询文本"},
                "top_k": {"type": "integer", "description": "最大返回条数，默认 5，范围 1-20"},
                "threshold": {"type": "number", "description": "相似度阈值，默认 0.70，范围 0.0-1.0"},
            },
            "required": ["query"],
        },
        has_side_effects=False,
        executor=_tool_search_by_vector,
    ),
    "get_context": ToolSpec(
        tool_id="get_context",
        display_name="获取对话上下文",
        description="获取当前对话的运行时上下文信息，包括时间戳、知识库状态等",
        parameters={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "会话 ID（可选）"},
                "context_type": {"type": "string", "description": "上下文类型，默认 conversation"},
            },
            "required": [],
        },
        has_side_effects=False,
        executor=_tool_get_context,
    ),
    "analyze_code": ToolSpec(
        tool_id="analyze_code",
        display_name="代码静态分析",
        description="对给定代码进行静态分析，返回行数、函数数、注释率、潜在问题等指标",
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要分析的代码"},
                "language": {"type": "string", "description": "编程语言，默认 python"},
            },
            "required": ["code"],
        },
        has_side_effects=False,
        executor=_tool_analyze_code,
    ),
    "translate_text": ToolSpec(
        tool_id="translate_text",
        display_name="文本翻译",
        description="检测文本语言并返回翻译元数据，实际翻译由 LLM 在 ReAct 循环中完成",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要翻译的文本"},
                "target_lang": {"type": "string", "description": "目标语言代码，如 en/ja/zh"},
            },
            "required": ["text"],
        },
        has_side_effects=False,
        executor=_tool_translate_text,
    ),
    "evaluate_artifact": ToolSpec(
        tool_id="evaluate_artifact",
        display_name="工件质量评估",
        description="对单个 AgentArtifact 进行结构化质量评估，返回质量等级、评分和问题列表",
        parameters={
            "type": "object",
            "properties": {
                "artifact": {"type": "object", "description": "要评估的工件对象（含 confidence、summary、risk_flags 等字段）"},
                "agent_id": {"type": "string", "description": "工件所属 agent ID（可选，用于标识）"},
            },
            "required": ["artifact"],
        },
        has_side_effects=False,
        executor=_tool_evaluate_artifact,
    ),
    "compare_artifacts": ToolSpec(
        tool_id="compare_artifacts",
        display_name="多工件比较",
        description="比较多个 AgentArtifact，基于置信度和风险标记推荐最优工件",
        parameters={
            "type": "object",
            "properties": {
                "artifacts": {"type": "object", "description": "工件字典，key 为 agent_id，value 为工件对象"},
            },
            "required": ["artifacts"],
        },
        has_side_effects=False,
        executor=_tool_compare_artifacts,
    ),
}

# 各 agent 可用的工具集合（只读工具）
_AGENT_TOOLS: dict[str, list[str]] = {
    "chat_agent":      ["search_knowledge", "get_context"],
    "code_agent":      ["analyze_code", "search_knowledge"],
    "translate_agent": ["translate_text", "search_knowledge"],
    "retrieval_agent": ["search_knowledge", "search_by_keyword", "search_by_vector", "get_context"],
    "intent_agent":    ["search_knowledge", "get_context", "analyze_code"],
    "judge_agent":     ["evaluate_artifact", "compare_artifacts"],  # P14：JudgeAgent ReAct 化，使用工件评估工具
}


def get_tools_for_agent(agent_id: str, allow_side_effects: bool = False) -> list[ToolSpec]:
    """获取指定 agent 可用的工具列表。

    Args:
        agent_id: agent ID
        allow_side_effects: 是否允许有副作用的工具（仅 EXEC_INTENT 场景为 True）

    Returns:
        可用工具列表
    """
    tool_ids = _AGENT_TOOLS.get(str(agent_id or "").strip(), [])
    tools = []
    for tool_id in tool_ids:
        spec = _TOOL_REGISTRY.get(tool_id)
        if spec is None:
            continue
        if spec.has_side_effects and not allow_side_effects:
            continue  # 有副作用工具在不允许时跳过
        tools.append(spec)
    return tools


def execute_tool(tool_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """执行工具调用。

    Args:
        tool_id: 工具 ID
        args: 工具参数

    Returns:
        工具执行结果字典
    """
    spec = _TOOL_REGISTRY.get(str(tool_id or "").strip())
    if spec is None:
        return {"error": f"TOOL_NOT_FOUND:{tool_id}", "result": None}
    if spec.executor is None:
        return {"error": f"TOOL_NO_EXECUTOR:{tool_id}", "result": None}
    try:
        return spec.executor(dict(args or {}))
    except Exception as exc:
        return {"error": f"TOOL_EXEC_ERROR:{type(exc).__name__}:{exc}", "result": None}


def is_tool_allowed(tool_id: str, agent_id: str, allow_side_effects: bool = False) -> bool:
    """检查工具是否允许被指定 agent 调用。"""
    allowed_tools = get_tools_for_agent(agent_id, allow_side_effects=allow_side_effects)
    allowed_ids = {t.tool_id for t in allowed_tools}
    return str(tool_id or "").strip() in allowed_ids
