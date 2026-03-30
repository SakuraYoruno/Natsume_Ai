"""
统一服务注册表 — 消除 service_health_aggregator / service_health_heartbeat / obs_routes 三处重复定义。

所有模块应从此处导入服务列表，而非各自维护独立副本。
"""
from __future__ import annotations

from typing import Dict, List

# ---------------------------------------------------------------------------
# Edge 侧服务（5 个）
# ---------------------------------------------------------------------------
EDGE_SERVICES: List[Dict[str, str]] = [
    {"service_id": "asr",            "name": "ASR (Whisper)",      "side": "edge"},
    {"service_id": "tts",            "name": "TTS (GPT-SoVITS)",   "side": "edge"},
    {"service_id": "vts",            "name": "VTS / Live2D",       "side": "edge"},
    {"service_id": "audio_playback", "name": "Audio Playback",     "side": "edge"},
    {"service_id": "edge_rpc",       "name": "Edge RPC Server",    "side": "edge"},
]

EDGE_SERVICE_IDS: List[str] = [s["service_id"] for s in EDGE_SERVICES]

EDGE_DISPLAY_NAMES: Dict[str, str] = {s["service_id"]: s["name"] for s in EDGE_SERVICES}

# 边缘服务 → voice_event_log 的 event_type 前缀映射（obs_routes 使用）
EDGE_SERVICE_EVENT_PREFIXES: Dict[str, List[str]] = {
    "asr":            ["ASR"],
    "tts":            ["TTS"],
    "vts":            ["VTS"],
    "audio_playback": ["VOICE_RESOURCE_GATE", "AUDIO", "AUDIO_PLAYBACK"],
    "edge_rpc":       ["EDGE", "RPC", "GOVERNOR", "AIRLOCK"],
}

# ---------------------------------------------------------------------------
# Host 侧服务（15 个）
# ---------------------------------------------------------------------------
HOST_SERVICES: List[Dict[str, str]] = [
    {"service_id": "llm_gateway",         "name": "LLM Gateway",            "side": "host"},
    {"service_id": "neo4j",               "name": "Neo4j Bond SSOT",        "side": "host"},
    {"service_id": "sqlite_ledger",       "name": "SQLite Ledger",          "side": "host"},
    {"service_id": "chroma",              "name": "Chroma Vector Store",    "side": "host"},
    {"service_id": "scheduler",           "name": "Scheduler",              "side": "host"},
    {"service_id": "bond_engine",         "name": "Bond Engine",            "side": "host"},
    {"service_id": "memory_distiller",    "name": "Memory Distiller",       "side": "host"},
    {"service_id": "mcp_provider",        "name": "MCP Provider",           "side": "host"},
    {"service_id": "embedding",           "name": "Embedding Service",      "side": "host"},
    {"service_id": "reranker",            "name": "Reranker Service",       "side": "host"},
    {"service_id": "main_chain",          "name": "MainChain Orchestrator", "side": "host"},
    {"service_id": "routing_matrix",      "name": "LLM Routing Matrix",     "side": "host"},
    {"service_id": "memory_guard",        "name": "Memory Guard",           "side": "host"},
    {"service_id": "state_arbiter",       "name": "State Arbiter",          "side": "host"},
    {"service_id": "recall_pack_builder", "name": "Recall Pack Builder",    "side": "host"},
]

HOST_SERVICE_IDS: List[str] = [s["service_id"] for s in HOST_SERVICES]

HOST_DISPLAY_NAMES: Dict[str, str] = {s["service_id"]: s["name"] for s in HOST_SERVICES}

# ---------------------------------------------------------------------------
# LLM 节点（云端 10 个 + 本地 1 个）
# ---------------------------------------------------------------------------
# deploy_type: "cloud" = 云端 API（Gemini/OpenAI/DeepSeek），"local" = 本地推理（Ollama）
LLM_NODE_REGISTRY: Dict[str, Dict[str, str]] = {
    # 云端节点
    "chat_direct_ja":          {"role": "聊天直出",     "provider": "gemini", "model_id": "gemini-2.5-flash",      "deploy_type": "cloud"},
    "core_reasoner_primary":   {"role": "主推理",       "provider": "gemini", "model_id": "gemini-2.5-pro-preview-03-25", "deploy_type": "cloud"},
    "core_reasoner_secondary": {"role": "备用推理",     "provider": "gemini", "model_id": "gemini-2.5-flash",      "deploy_type": "cloud"},
    "planner_constraints":     {"role": "规划约束",     "provider": "gemini", "model_id": "gemini-2.5-pro-preview-03-25", "deploy_type": "cloud"},
    "tool_planner":            {"role": "工具规划",     "provider": "gemini", "model_id": "gemini-2.5-flash",      "deploy_type": "cloud"},
    "safety_verifier":         {"role": "安全验证",     "provider": "gemini", "model_id": "gemini-2.5-flash-lite", "deploy_type": "cloud"},
    "consistency_verifier":    {"role": "一致性验证",   "provider": "gemini", "model_id": "gemini-2.5-flash-lite", "deploy_type": "cloud"},
    "renderer_ja_c_style":     {"role": "日文渲染",     "provider": "gemini", "model_id": "gemini-2.5-flash",      "deploy_type": "cloud"},
    "workbench_translate":     {"role": "翻译工作台",   "provider": "gemini", "model_id": "gemini-2.5-pro-preview-03-25", "deploy_type": "cloud"},
    "workbench_code_assist":   {"role": "代码辅助",     "provider": "gemini", "model_id": "gemini-2.5-pro-preview-03-25", "deploy_type": "cloud"},
    # 国内云端节点（DeepSeek）
    "cloud_cn_deepseek":       {"role": "国内云端 Gaming", "provider": "deepseek", "model_id": "deepseek-chat",       "deploy_type": "cloud"},
    # 本地节点（Ollama）
    "host_local_14b":          {"role": "本地推理 14B", "provider": "ollama", "model_id": "qwen3:14b",             "deploy_type": "local"},
}

LLM_NODE_IDS: List[str] = list(LLM_NODE_REGISTRY.keys())

LLM_NODE_DISPLAY_NAMES: Dict[str, str] = {nid: meta["role"] for nid, meta in LLM_NODE_REGISTRY.items()}

# 按部署类型分组
LLM_CLOUD_NODE_IDS: List[str] = [nid for nid, m in LLM_NODE_REGISTRY.items() if m["deploy_type"] == "cloud"]
LLM_LOCAL_NODE_IDS: List[str] = [nid for nid, m in LLM_NODE_REGISTRY.items() if m["deploy_type"] == "local"]

# 本地推理服务（Ollama）默认端点
OLLAMA_DEFAULT_BASE_URL = "http://127.0.0.1:11434"

# ---------------------------------------------------------------------------
# 聚合列表
# ---------------------------------------------------------------------------
ALL_SERVICES: List[Dict[str, str]] = HOST_SERVICES + EDGE_SERVICES
