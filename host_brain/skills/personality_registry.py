"""人格/调度 Skill 注册表（P1 优先级）。

为 impl/ 下已实现的 5 个 executor 补全 Skill 注册：
  - diary_writer      每日日记生成（调度器触发）
  - dream_generator   梦境生成（Sleep/空闲时触发）
  - desktop_note      桌面留言（文件创建/更新/删除）
  - focus_mode        专注模式切换（进入/退出）
  - proactive_talk    主动对话（空闲时主动问候）

风险分级说明：
  risk_level=1  只读/无副作用（dream_generator / proactive_talk / focus_mode）
  risk_level=2  写操作但可逆（diary_writer / desktop_note create/update）
  risk_level=3  破坏性写操作（desktop_note delete，require_confirm=True）

所有 handler 均为轻量归一化层，返回标准化 payload，
由 loader._dispatch_executor 路由到对应的 impl executor。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.registry import SkillRegistry, skill


class PersonalitySkillProvider:
    """人格/调度 Skill 集合。

    所有方法通过 @skill 装饰器注册元数据，
    由 build_personality_skill_registry() 统一扫描注册。
    """

    # ------------------------------------------------------------------
    # diary_writer：每日日记生成
    # ------------------------------------------------------------------

    @skill(
        skill_name="diary_writer",
        clearance="MEDIUM",
        risk_level=2,
        scope="host",
        reversible=True,
        description="每日日记生成：汇总当日对话 → LLM 生成日记叙事 → 双写文件 + Episodic Memory。",
    )
    def diary_writer(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """触发每日日记生成。

        Args:
            base_dir (str): 日记文件存储基目录（必填）
            date (str): 目标日期 YYYY-MM-DD（默认当天）
        """
        base_dir = str((args or {}).get("base_dir") or "").strip()
        date = str((args or {}).get("date") or "").strip()

        return {
            "status": "OK",
            "tool": "diary_writer",
            "action": "generate",
            "base_dir": base_dir,
            "date": date,
            "manifest_ref": f"diary_writer::{date or 'today'}",
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
            "_context": ctx or {},
        }

    # ------------------------------------------------------------------
    # dream_generator：梦境生成
    # ------------------------------------------------------------------

    @skill(
        skill_name="dream_generator",
        clearance="LOW",
        risk_level=1,
        scope="memory",
        reversible=True,
        description="梦境生成：读取当日 Episodic Memory → LLM 生成梦境叙事 → 写入 Episodic（dream 标签）。",
    )
    def dream_generator(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """触发梦境生成（需处于 Sleep 或空闲状态）。

        Args:
            max_episodic_entries (int): 当日 Episodic 回顾最大条数（默认 20）
        """
        try:
            max_entries = max(1, min(100, int((args or {}).get("max_episodic_entries") or 20)))
        except (TypeError, ValueError):
            max_entries = 20

        return {
            "status": "OK",
            "tool": "dream_generator",
            "action": "generate",
            "max_episodic_entries": max_entries,
            "manifest_ref": "dream_generator::today",
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
            "_context": ctx or {},
        }

    # ------------------------------------------------------------------
    # desktop_note：桌面留言
    # ------------------------------------------------------------------

    @skill(
        skill_name="desktop_note",
        clearance="MEDIUM",
        risk_level=2,
        scope="system",
        reversible=True,
        description="桌面留言：在安全基目录下创建/更新/删除文本文件（路径穿越拒绝，内容经 QA 校验）。",
    )
    def desktop_note(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """创建/更新/删除桌面留言文件。

        Args:
            action (str): "create" | "update" | "delete"（必填）
            filename (str): 文件名，相对于安全基目录（必填）
            content (str): 文件内容（create/update 时必填）
            base_dir (str): 安全基目录绝对路径（必填）
        """
        action = str((args or {}).get("action") or "").strip().lower()
        filename = str((args or {}).get("filename") or "").strip()
        content = (args or {}).get("content")
        base_dir = str((args or {}).get("base_dir") or "").strip()

        return {
            "status": "OK",
            "tool": "desktop_note",
            "action": action,
            "filename": filename,
            "content": content,
            "base_dir": base_dir,
            "manifest_ref": f"desktop_note::{action}::{filename}",
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
            "_context": ctx or {},
        }

    # ------------------------------------------------------------------
    # focus_mode：专注模式切换
    # ------------------------------------------------------------------

    @skill(
        skill_name="focus_mode",
        clearance="LOW",
        risk_level=1,
        scope="system",
        reversible=True,
        description="专注模式切换：进入/退出专注模式，抑制低优先级调度任务和主动对话。",
    )
    def focus_mode(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """切换专注模式。

        Args:
            action (str): "enter" | "exit"（必填）
            duration_ms (int): 专注时长毫秒数（enter 时有效，默认 90 分钟）
            reason (str): 进入原因（可选）
        """
        action = str((args or {}).get("action") or "").strip().lower()
        try:
            duration_ms = max(0, int((args or {}).get("duration_ms") or 90 * 60 * 1000))
        except (TypeError, ValueError):
            duration_ms = 90 * 60 * 1000
        reason = str((args or {}).get("reason") or "").strip() or None

        return {
            "status": "OK",
            "tool": "focus_mode",
            "action": action,
            "duration_ms": duration_ms,
            "reason": reason,
            "manifest_ref": f"focus_mode::{action}",
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
            "_context": ctx or {},
        }

    # ------------------------------------------------------------------
    # proactive_talk：主动对话
    # ------------------------------------------------------------------

    @skill(
        skill_name="proactive_talk",
        clearance="LOW",
        risk_level=1,
        scope="host",
        reversible=False,
        description="主动对话：空闲超过阈值时由调度器触发，LLM 生成问候语并推送到 UI 队列。",
    )
    def proactive_talk(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """触发主动对话（需空闲超过阈值）。

        Args:
            idle_threshold_ms (int): 空闲阈值毫秒数（默认 4 小时）
            topic_hint (str): 话题提示（可选）
        """
        try:
            idle_threshold_ms = max(0, int((args or {}).get("idle_threshold_ms") or 4 * 60 * 60 * 1000))
        except (TypeError, ValueError):
            idle_threshold_ms = 4 * 60 * 60 * 1000
        topic_hint = str((args or {}).get("topic_hint") or "").strip() or None

        return {
            "status": "OK",
            "tool": "proactive_talk",
            "action": "send",
            "idle_threshold_ms": idle_threshold_ms,
            "topic_hint": topic_hint,
            "manifest_ref": "proactive_talk::send",
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
            "_context": ctx or {},
        }


def build_personality_skill_registry(
    provider: PersonalitySkillProvider | None = None,
) -> SkillRegistry:
    """构建包含人格/调度 Skills 的 SkillRegistry。

    Skill 列表：
    - diary_writer：每日日记生成（risk=2，MEDIUM，可逆）
    - dream_generator：梦境生成（risk=1，LOW，可逆）
    - desktop_note：桌面留言（risk=2，MEDIUM，可逆）
    - focus_mode：专注模式切换（risk=1，LOW，可逆）
    - proactive_talk：主动对话（risk=1，LOW，不可逆）

    Returns:
        已注册 5 个人格/调度 Skills 的 SkillRegistry 实例
    """
    registry = SkillRegistry()
    registry.register_decorated(provider or PersonalitySkillProvider())
    return registry
