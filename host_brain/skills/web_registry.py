"""网络/搜索 Skill 包（P2 优先级）。

提供网络搜索和 HTTP 请求能力，支撑 PRD S2 场景（知识获取 + 外部 API 集成）。

风险分级说明：
  risk_level=1  只读/查询操作（web_search）
  risk_level=2  写操作（http_request POST/PUT/DELETE）
  risk_level=3  高风险写操作（http_request 带 auth_token）

所有 handler 均为轻量归一化层，返回标准化 payload，
由 impl/web_executor.py 路由到网络层执行。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.registry import SkillRegistry, skill

# 支持的搜索引擎
_VALID_SEARCH_ENGINES = frozenset({
    "bing", "google", "duckduckgo", "baidu", "searxng", "tavily",
})

# 支持的 HTTP 方法
_VALID_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"})


class WebSkillProvider:
    """网络/搜索 Skill 集合。"""

    @skill(
        skill_name="web_search",
        clearance="LOW",
        risk_level=1,
        scope="system",
        reversible=True,
        description="执行网络搜索，返回结构化搜索结果（标题 / URL / 摘要）。",
    )
    def web_search(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """执行网络搜索。

        Args:
            query (str): 搜索查询文本（必填）
            engine (str): 搜索引擎，bing / google / duckduckgo / baidu / searxng / tavily（默认 bing）
            num_results (int): 返回结果数量（默认 5，最大 20）
            language (str): 搜索语言（默认 zh-CN）
            safe_search (bool): 是否开启安全搜索（默认 True）
            time_range (str): 时间范围，day / week / month / year / all（默认 all）
        """
        query = str((args or {}).get("query") or "").strip()
        engine = str((args or {}).get("engine") or "bing").strip().lower()
        if engine not in _VALID_SEARCH_ENGINES:
            engine = "bing"
        try:
            num_results = max(1, min(20, int((args or {}).get("num_results") or 5)))
        except (TypeError, ValueError):
            num_results = 5
        language = str((args or {}).get("language") or "zh-CN").strip()
        safe_search_raw = (args or {}).get("safe_search")
        safe_search = bool(safe_search_raw) if safe_search_raw is not None else True
        time_range = str((args or {}).get("time_range") or "all").strip().lower()
        if time_range not in {"day", "week", "month", "year", "all"}:
            time_range = "all"

        return {
            "status": "OK",
            "tool": "web",
            "action": "search",
            "query": query,
            "engine": engine,
            "num_results": num_results,
            "language": language,
            "safe_search": safe_search,
            "time_range": time_range,
            "manifest_ref": f"web_search::{engine}::{query[:60]}",
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
            "_context": ctx or {},
        }

    @skill(
        skill_name="http_request",
        clearance="MEDIUM",
        risk_level=2,
        scope="system",
        reversible=False,
        description="发送 HTTP 请求到指定 URL（支持 GET/POST/PUT/PATCH/DELETE，含请求头和 Body）。",
    )
    def http_request(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """发送 HTTP 请求。

        Args:
            url (str): 目标 URL（必填）
            method (str): HTTP 方法，GET / POST / PUT / PATCH / DELETE / HEAD（默认 GET）
            headers (dict): 请求头（可选）
            body (dict | str): 请求体（POST/PUT/PATCH 时使用，可选）
            params (dict): URL 查询参数（可选）
            timeout_ms (int): 超时毫秒数（默认 10000）
            follow_redirects (bool): 是否跟随重定向（默认 True）
            verify_ssl (bool): 是否验证 SSL 证书（默认 True）
        """
        url = str((args or {}).get("url") or "").strip()
        method = str((args or {}).get("method") or "GET").strip().upper()
        if method not in _VALID_HTTP_METHODS:
            method = "GET"
        headers = (args or {}).get("headers")
        if not isinstance(headers, dict):
            headers = {}
        body = (args or {}).get("body")
        params = (args or {}).get("params")
        if not isinstance(params, dict):
            params = {}
        try:
            timeout_ms = max(500, min(120000, int((args or {}).get("timeout_ms") or 10000)))
        except (TypeError, ValueError):
            timeout_ms = 10000
        follow_redirects_raw = (args or {}).get("follow_redirects")
        follow_redirects = bool(follow_redirects_raw) if follow_redirects_raw is not None else True
        verify_ssl_raw = (args or {}).get("verify_ssl")
        verify_ssl = bool(verify_ssl_raw) if verify_ssl_raw is not None else True

        return {
            "status": "OK",
            "tool": "web",
            "action": "http_request",
            "url": url,
            "method": method,
            "headers": headers,
            "body": body,
            "params": params,
            "timeout_ms": timeout_ms,
            "follow_redirects": follow_redirects,
            "verify_ssl": verify_ssl,
            "manifest_ref": f"http_request::{method}::{url[:80]}",
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
            "_context": ctx or {},
        }


def build_web_skill_registry(provider: WebSkillProvider | None = None) -> SkillRegistry:
    """构建包含网络/搜索 Skills 的 SkillRegistry。

    Web Skill 列表：
    - web_search：网络搜索（risk=1，只读）
    - http_request：HTTP 请求（risk=2，写操作）

    Returns:
        已注册 2 个网络/搜索 Skills 的 SkillRegistry 实例
    """
    registry = SkillRegistry()
    registry.register_decorated(provider or WebSkillProvider())
    return registry
