"""记忆操作 Skill 包（P1 优先级）。

提供对 Episodic Memory / Semantic Memory 的读写操作能力，
支撑 PRD S1/S2 场景（记忆检索 + 记忆写入 + 记忆遗忘）。
与 host_brain/memory/ 模块深度集成。

风险分级说明：
  risk_level=1  只读操作（memory_search）
  risk_level=2  写操作（memory_store）
  risk_level=3  破坏性操作（memory_forget），require_confirm=True

所有 handler 均为轻量归一化层，返回标准化 payload，
由 impl/memory_executor.py 路由到 memory/ 模块执行。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.registry import SkillRegistry, skill

# 支持的记忆类型
_MEMORY_TYPES = frozenset({"episodic", "semantic", "working", "all"})
# 支持的记忆标签
_VALID_MEMORY_TAGS = frozenset({
    "conversation", "emotion", "event", "fact", "preference",
    "dream", "diary", "task", "reminder", "bond",
})


class MemorySkillProvider:
    """记忆操作 Skill 集合。"""

    @skill(
        skill_name="memory_search",
        clearance="LOW",
        risk_level=1,
        scope="memory",
        reversible=True,
        description="语义检索记忆库（Episodic / Semantic），返回相关记忆片段列表。",
    )
    def memory_search(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """语义检索记忆。

        Args:
            query (str): 检索查询文本（必填）
            memory_type (str): 记忆类型，episodic / semantic / working / all（默认 all）
            top_k (int): 返回最相关记忆条数（默认 5，最大 20）
            threshold (float): 相似度阈值 0.0~1.0（默认 0.6）
            tags (list[str]): 按标签过滤（可选）
            session_filter (bool): 是否只检索当前 session 的记忆（默认 False）
            date_from (str): 时间范围起始 ISO 8601（可选）
            date_to (str): 时间范围结束 ISO 8601（可选）
        """
        query = str((args or {}).get("query") or "").strip()
        memory_type = str((args or {}).get("memory_type") or "all").strip().lower()
        if memory_type not in _MEMORY_TYPES:
            memory_type = "all"
        try:
            top_k = max(1, min(20, int((args or {}).get("top_k") or 5)))
        except (TypeError, ValueError):
            top_k = 5
        try:
            threshold = max(0.0, min(1.0, float((args or {}).get("threshold") or 0.6)))
        except (TypeError, ValueError):
            threshold = 0.6
        tags_raw = (args or {}).get("tags")
        if isinstance(tags_raw, list):
            tags = [str(t).strip().lower() for t in tags_raw if t]
        else:
            tags = []
        session_filter_raw = (args or {}).get("session_filter")
        session_filter = bool(session_filter_raw) if session_filter_raw is not None else False
        date_from = str((args or {}).get("date_from") or "").strip() or None
        date_to = str((args or {}).get("date_to") or "").strip() or None

        return {
            "status": "OK",
            "tool": "memory",
            "action": "search",
            "query": query,
            "memory_type": memory_type,
            "top_k": top_k,
            "threshold": threshold,
            "tags": tags,
            "session_filter": session_filter,
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
            "date_from": date_from,
            "date_to": date_to,
            "manifest_ref": f"memory_search::{query[:60]}",
            "_context": ctx or {},
        }

    @skill(
        skill_name="memory_store",
        clearance="MEDIUM",
        risk_level=2,
        scope="memory",
        reversible=True,
        description="将指定内容写入记忆库（Episodic 或 Semantic），支持标签和重要性标注。",
    )
    def memory_store(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """写入记忆。

        Args:
            content (str): 要存储的记忆内容（必填）
            memory_type (str): 记忆类型，episodic / semantic（默认 episodic）
            tags (list[str]): 记忆标签列表（可选）
            importance (float): 重要性评分 0.0~1.0（默认 0.5）
            source (str): 记忆来源标识（可选，如 conversation / diary / event）
            emotion_tag (str): 关联情感标签（可选）
        """
        content = str((args or {}).get("content") or "").strip()
        memory_type = str((args or {}).get("memory_type") or "episodic").strip().lower()
        if memory_type not in {"episodic", "semantic"}:
            memory_type = "episodic"
        tags_raw = (args or {}).get("tags")
        if isinstance(tags_raw, list):
            tags = [str(t).strip().lower() for t in tags_raw if t]
        else:
            tags = []
        try:
            importance = max(0.0, min(1.0, float((args or {}).get("importance") or 0.5)))
        except (TypeError, ValueError):
            importance = 0.5
        source = str((args or {}).get("source") or "").strip() or None
        emotion_tag = str(
            (args or {}).get("emotion_tag")
            or (ctx or {}).get("emotion_tag")
            or "NEUTRAL"
        ).strip().upper()

        return {
            "status": "OK",
            "tool": "memory",
            "action": "store",
            "content": content,
            "memory_type": memory_type,
            "tags": tags,
            "importance": importance,
            "source": source,
            "emotion_tag": emotion_tag,
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
            "manifest_ref": f"memory_store::{memory_type}::{content[:40]}",
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
            "_context": ctx or {},
        }

    @skill(
        skill_name="memory_forget",
        clearance="MEDIUM",
        risk_level=3,
        scope="memory",
        reversible=False,
        require_confirm=True,
        description="从记忆库中删除指定记忆（不可逆，需用户确认；支持按 ID 或标签批量删除）。",
    )
    def memory_forget(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """删除记忆（不可逆操作，需确认）。

        Args:
            memory_id (str): 要删除的记忆 ID（与 tags 二选一）
            tags (list[str]): 按标签批量删除（与 memory_id 二选一）
            memory_type (str): 记忆类型过滤（可选，默认 all）
            dry_run (bool): 仅模拟执行，不实际删除（默认 False）
        """
        memory_id = str((args or {}).get("memory_id") or "").strip() or None
        tags_raw = (args or {}).get("tags")
        if isinstance(tags_raw, list):
            tags = [str(t).strip().lower() for t in tags_raw if t]
        else:
            tags = []
        memory_type = str((args or {}).get("memory_type") or "all").strip().lower()
        if memory_type not in _MEMORY_TYPES:
            memory_type = "all"
        dry_run_raw = (args or {}).get("dry_run")
        dry_run = bool(dry_run_raw) if dry_run_raw is not None else False

        return {
            "status": "OK",
            "tool": "memory",
            "action": "forget",
            "memory_id": memory_id,
            "tags": tags,
            "memory_type": memory_type,
            "dry_run": dry_run,
            "manifest_ref": f"memory_forget::{memory_id or '+'.join(tags)}",
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
            "_context": ctx or {},
        }


def build_memory_skill_registry(provider: MemorySkillProvider | None = None) -> SkillRegistry:
    """构建包含记忆操作 Skills 的 SkillRegistry。

    Memory Skill 列表：
    - memory_search：语义检索记忆（risk=1，只读）
    - memory_store：写入记忆（risk=2，写操作）
    - memory_forget：删除记忆（risk=3，不可逆，需确认）

    Returns:
        已注册 3 个记忆操作 Skills 的 SkillRegistry 实例
    """
    registry = SkillRegistry()
    registry.register_decorated(provider or MemorySkillProvider())
    return registry
