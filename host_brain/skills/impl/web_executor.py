"""网络/搜索 Executor。

将 web_registry.py 归一化层的 payload 路由到网络层执行。

支持的 action：
  - search：网络搜索
  - http_request：HTTP 请求
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger("Natsume.skills.web_executor")


def execute_web_skill(args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    """执行网络/搜索操作。

    Args:
        args: 归一化 payload（来自 web_registry handler 的返回值）
        context: 执行上下文

    Returns:
        执行结果 dict，包含 status / manifest_ref / detail
    """
    payload = args or {}
    action = str(payload.get("action") or "").strip().lower()
    manifest_ref = str(payload.get("manifest_ref") or f"web::{action}")

    try:
        result = _dispatch_web_action(action, payload, context)
        return {
            "status": "OK",
            "manifest_ref": manifest_ref,
            "action": action,
            "detail": result,
        }
    except Exception as exc:
        _logger.warning("Web executor 执行失败（action=%s）: %s", action, exc)
        return {
            "status": "FAILED",
            "manifest_ref": manifest_ref,
            "action": action,
            "error": str(exc),
        }


def _dispatch_web_action(
    action: str,
    payload: dict[str, Any],
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    """将 action 路由到对应的网络操作。"""
    if action == "search":
        return _execute_search(payload, context)
    if action == "http_request":
        return _execute_http_request(payload, context)
    return {"note": f"unknown web action: {action}"}


def _execute_search(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """执行网络搜索。"""
    query = str(payload.get("query") or "")
    engine = str(payload.get("engine") or "bing")
    num_results = int(payload.get("num_results") or 5)
    language = str(payload.get("language") or "zh-CN")

    # 优先使用 Tavily（如果配置了 API Key）
    tavily_result = _try_tavily_search(query, num_results)
    if tavily_result is not None:
        return tavily_result

    # 降级：使用 DuckDuckGo（无需 API Key）
    ddg_result = _try_duckduckgo_search(query, num_results, language)
    if ddg_result is not None:
        return ddg_result

    # 最终降级：返回空结果
    _logger.info("网络搜索（无可用后端）: query=%s", query[:40])
    return {
        "query": query,
        "engine": engine,
        "results": [],
        "note": "no search backend available",
    }


def _try_tavily_search(query: str, num_results: int) -> dict[str, Any] | None:
    """尝试使用 Tavily 搜索（需要 TAVILY_API_KEY 环境变量）。"""
    try:
        import os
        api_key = str(os.environ.get("TAVILY_API_KEY") or "").strip()
        if not api_key:
            return None

        import urllib.request
        import json as _json

        url = "https://api.tavily.com/search"
        body = {
            "api_key": api_key,
            "query": query,
            "max_results": num_results,
            "search_depth": "basic",
        }
        data = _json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = _json.loads(resp.read())

        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
            }
            for r in raw.get("results", [])
        ]
        _logger.info("Tavily 搜索: query=%s, results=%d", query[:40], len(results))
        return {"query": query, "engine": "tavily", "results": results}
    except Exception as exc:
        _logger.debug("Tavily 搜索失败: %s", exc)
        return None


def _try_duckduckgo_search(query: str, num_results: int, language: str) -> dict[str, Any] | None:
    """尝试使用 DuckDuckGo 搜索（无需 API Key）。"""
    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            raw = list(ddgs.text(
                query,
                region=language.replace("-", "_").lower(),
                max_results=num_results,
            ))
        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            }
            for r in raw
        ]
        _logger.info("DuckDuckGo 搜索: query=%s, results=%d", query[:40], len(results))
        return {"query": query, "engine": "duckduckgo", "results": results}
    except ImportError:
        _logger.debug("duckduckgo_search 未安装")
        return None
    except Exception as exc:
        _logger.debug("DuckDuckGo 搜索失败: %s", exc)
        return None


def _execute_http_request(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """发送 HTTP 请求。"""
    url = str(payload.get("url") or "")
    method = str(payload.get("method") or "GET").upper()
    headers = payload.get("headers") or {}
    body = payload.get("body")
    params = payload.get("params") or {}
    timeout_ms = int(payload.get("timeout_ms") or 10000)
    verify_ssl = bool(payload.get("verify_ssl", True))

    timeout_s = timeout_ms / 1000.0

    # 构建带参数的 URL
    if params:
        import urllib.parse
        query_string = urllib.parse.urlencode(params)
        url = f"{url}?{query_string}" if "?" not in url else f"{url}&{query_string}"

    try:
        import urllib.request
        import json as _json
        import ssl

        # 请求体
        data = None
        if body is not None:
            if isinstance(body, dict):
                data = _json.dumps(body).encode("utf-8")
                if "Content-Type" not in headers:
                    headers["Content-Type"] = "application/json"
            elif isinstance(body, str):
                data = body.encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )

        ssl_ctx = None if verify_ssl else ssl.create_default_context()
        if ssl_ctx and not verify_ssl:
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

        with urllib.request.urlopen(req, timeout=timeout_s, context=ssl_ctx) as resp:
            status_code = resp.status
            resp_headers = dict(resp.headers)
            raw = resp.read()
            content_type = resp_headers.get("Content-Type", "")
            if "application/json" in content_type:
                resp_body = _json.loads(raw)
            else:
                resp_body = raw.decode("utf-8", errors="replace")

        _logger.info("HTTP 请求: %s %s → %d", method, url[:60], status_code)
        return {
            "url": url,
            "method": method,
            "status_code": status_code,
            "headers": resp_headers,
            "body": resp_body,
        }
    except Exception as exc:
        _logger.warning("HTTP 请求失败: %s %s → %s", method, url[:60], exc)
        return {"url": url, "method": method, "error": str(exc)}
