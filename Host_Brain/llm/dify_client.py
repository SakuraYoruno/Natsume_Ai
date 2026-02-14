from __future__ import annotations

from typing import Any, Dict

import requests


class DifyChatflowClient:
    """
    调用 Dify Chatflow（Ego）：
    - 输入：Host 传入的 context_pack_json
    - 输出：要求 Dify 返回“严格 JSON 字符串”（answer 字段）
    注意：Dify 侧不得执行任何工具/HTTP 动作节点。
    """
    def __init__(self, api_url: str, api_key: str, user_id: str = "host_brain_v2") -> None:
        self.api_url = (api_url or "").strip()
        self.api_key = (api_key or "").strip()
        self.user_id = user_id or "host_brain_v2"
        if not self.api_url:
            raise ValueError("DIFY_API_URL 为空")
        if not self.api_key:
            raise ValueError("DIFY_API_KEY 为空")

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def chat(self, user_query: str, context_pack_json: str) -> Dict[str, Any]:
        payload = {
            "inputs": {
                "context_pack_json": context_pack_json,
            },
            "query": user_query,
            "response_mode": "blocking",
            "conversation_id": "",  # 无状态：上下文与记忆都归 Host 管
            "user": self.user_id,
            "files": [],
        }
        r = requests.post(self.api_url, headers=self.headers, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        # Dify 的返回结构会带很多字段；Host 全量保存用于审计。
        if "answer" not in data:
            data["answer"] = ""
        return data
