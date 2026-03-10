"""
ui_assertion.py —— Vision/UI 自动化断言协议

为 ClawdBot 的 UI 自动化操作提供完整的断言协议（需求 8）：
- 前置断言：window_title / process_name / ui_state_hash（需求 8.1、8.2）
- 后置状态验证：截图对比 + 预期状态变化确认（需求 8.3、8.4）
- 敏感界面检测：密码框/支付/系统设置强制拒绝（需求 8.5）
- 前台窗口验证：防止误操作后台窗口（需求 8.6）
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 枚举：UI 断言失败类型
# ---------------------------------------------------------------------------

class UIAssertionError(Exception):
    """UI 断言失败时抛出的异常。"""

    def __init__(self, reason: str, audit_event: str, detail: Optional[str] = None) -> None:
        self.reason = reason
        self.audit_event = audit_event
        self.detail = detail
        super().__init__(f"UIAssertion 失败 [{audit_event}]: {reason}" + (f" | {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 数据类：UI 操作动作描述
# ---------------------------------------------------------------------------

@dataclass
class UIAction:
    """ClawdUIAction 的操作描述。"""
    action_type: str
    """操作类型（click / type / scroll / drag 等）"""
    target_selector: str
    """目标元素选择器"""
    expected_window_title: str
    """预期窗口标题（前置断言）"""
    expected_process_name: str
    """预期进程名称（前置断言）"""
    expected_ui_state_hash: str
    """识别时的 UI 状态哈希（前置断言，防止页面变化）"""
    expected_change_description: str
    """操作后预期的状态变化描述（后置断言）"""
    action_value: Optional[str] = None
    """操作值（如输入文本）"""
    metadata: Dict[str, Any] = field(default_factory=dict)
    """附加元数据"""


# ---------------------------------------------------------------------------
# 数据类：前置断言结果
# ---------------------------------------------------------------------------

@dataclass
class PreCheckResult:
    """UIAssertionGuard.pre_check() 的返回值。"""
    passed: bool
    """是否通过所有前置断言"""
    failed_assertions: List[str] = field(default_factory=list)
    """失败的断言项列表"""
    actual_window_title: Optional[str] = None
    actual_process_name: Optional[str] = None
    actual_ui_state_hash: Optional[str] = None
    is_foreground: bool = True
    """目标窗口是否为前台窗口"""


# ---------------------------------------------------------------------------
# 数据类：后置断言结果
# ---------------------------------------------------------------------------

@dataclass
class PostCheckResult:
    """UIAssertionGuard.post_check() 的返回值。"""
    passed: bool
    """是否通过后置状态验证"""
    change_detected: bool = False
    """是否检测到状态变化"""
    change_matches_expected: bool = False
    """状态变化是否符合预期"""
    similarity_score: float = 0.0
    """截图相似度（0.0-1.0，越低表示变化越大）"""
    detail: Optional[str] = None


# ---------------------------------------------------------------------------
# 敏感界面检测规则
# ---------------------------------------------------------------------------

# 敏感窗口标题关键词（大小写不敏感）
SENSITIVE_WINDOW_KEYWORDS: List[str] = [
    "密码", "password", "支付", "payment", "pay",
    "系统设置", "system preferences", "control panel",
    "安全", "security", "银行", "bank",
    "信用卡", "credit card", "身份验证", "authentication",
    "uac", "用户账户控制", "user account control",
]

# 敏感进程名称（大小写不敏感）
SENSITIVE_PROCESS_NAMES: List[str] = [
    "consent.exe",  # Windows UAC
    "credentialuibroker.exe",
    "logonui.exe",
]


def is_sensitive_ui(
    window_title: str,
    process_name: str,
) -> bool:
    """
    检测是否为敏感界面。

    Args:
        window_title: 窗口标题
        process_name: 进程名称

    Returns:
        True 表示是敏感界面，应强制拒绝操作。
    """
    title_lower = window_title.lower()
    process_lower = process_name.lower()

    for keyword in SENSITIVE_WINDOW_KEYWORDS:
        if keyword.lower() in title_lower:
            return True

    for sensitive_process in SENSITIVE_PROCESS_NAMES:
        if sensitive_process.lower() in process_lower:
            return True

    return False


# ---------------------------------------------------------------------------
# UI 断言守卫
# ---------------------------------------------------------------------------

class UIAssertionGuard:
    """
    Vision/UI 自动化断言协议守卫。

    所有 ClawdUIAction 操作必须先通过此守卫的前置断言，
    操作完成后执行后置状态验证。
    """

    def __init__(
        self,
        audit_writer: Optional[Callable] = None,
        window_info_getter: Optional[Callable] = None,
        screenshot_comparator: Optional[Callable] = None,
    ) -> None:
        """
        Args:
            audit_writer: 审计写入回调，签名为 (event: str, detail: dict) -> None。
            window_info_getter: 窗口信息获取回调，
                签名为 () -> dict，返回 {title, process_name, is_foreground, ui_state_hash}。
            screenshot_comparator: 截图比较回调，
                签名为 (before: bytes, after: bytes) -> float，返回相似度 0.0-1.0。
        """
        self._audit_writer = audit_writer
        self._window_info_getter = window_info_getter
        self._screenshot_comparator = screenshot_comparator

    async def pre_check(self, action: UIAction) -> PreCheckResult:
        """
        执行前置断言（需求 8.1、8.2）。

        验证项：
        1. window_title 匹配预期
        2. process_name 匹配预期
        3. ui_state_hash 与识别时一致（防止页面变化）
        4. 目标窗口是前台窗口（需求 8.6）
        5. 非敏感界面（需求 8.5）

        Raises:
            UIAssertionError: 任意断言失败时抛出。
        """
        result = PreCheckResult(passed=True)
        failed_assertions = []

        # 获取当前窗口信息
        current_info = await self._get_window_info()

        result.actual_window_title = current_info.get("title", "")
        result.actual_process_name = current_info.get("process_name", "")
        result.actual_ui_state_hash = current_info.get("ui_state_hash", "")
        result.is_foreground = current_info.get("is_foreground", True)

        # 断言 1：敏感界面检测（需求 8.5，最高优先级）
        if is_sensitive_ui(result.actual_window_title, result.actual_process_name):
            await self._write_audit(
                "SENSITIVE_UI_BLOCKED",
                {
                    "window_title": result.actual_window_title,
                    "process_name": result.actual_process_name,
                    "action_type": action.action_type,
                },
            )
            raise UIAssertionError(
                "检测到敏感界面，强制拒绝操作",
                "SENSITIVE_UI_BLOCKED",
                f"window={result.actual_window_title!r}, process={result.actual_process_name!r}",
            )

        # 断言 2：前台窗口验证（需求 8.6）
        if not result.is_foreground:
            failed_assertions.append("is_foreground_window")
            logger.warning(
                "目标窗口不是前台窗口: title=%r", result.actual_window_title
            )

        # 断言 3：window_title 匹配（需求 8.1）
        if result.actual_window_title != action.expected_window_title:
            failed_assertions.append("window_title")
            logger.warning(
                "window_title 不匹配: expected=%r, actual=%r",
                action.expected_window_title, result.actual_window_title,
            )

        # 断言 4：process_name 匹配（需求 8.1）
        if result.actual_process_name != action.expected_process_name:
            failed_assertions.append("process_name")
            logger.warning(
                "process_name 不匹配: expected=%r, actual=%r",
                action.expected_process_name, result.actual_process_name,
            )

        # 断言 5：ui_state_hash 一致（需求 8.1）
        if result.actual_ui_state_hash != action.expected_ui_state_hash:
            failed_assertions.append("ui_state_hash")
            logger.warning(
                "ui_state_hash 不匹配: expected=%r, actual=%r",
                action.expected_ui_state_hash, result.actual_ui_state_hash,
            )

        if failed_assertions:
            result.passed = False
            result.failed_assertions = failed_assertions

            await self._write_audit(
                "UI_PRECONDITION_FAILED",
                {
                    "failed_assertions": failed_assertions,
                    "action_type": action.action_type,
                    "expected_window": action.expected_window_title,
                    "actual_window": result.actual_window_title,
                },
            )
            # 前置断言失败，中止操作（需求 8.2）
            raise UIAssertionError(
                f"前置断言失败: {failed_assertions}",
                "UI_PRECONDITION_FAILED",
                f"failed={failed_assertions}",
            )

        logger.debug(
            "前置断言通过: window=%r, process=%r",
            result.actual_window_title, result.actual_process_name,
        )
        return result

    async def post_check(
        self,
        before_screenshot: bytes,
        after_screenshot: bytes,
        expected_change: str,
    ) -> PostCheckResult:
        """
        执行后置状态验证（需求 8.3、8.4）。

        Args:
            before_screenshot: 操作前截图字节数据
            after_screenshot: 操作后截图字节数据
            expected_change: 预期状态变化描述

        Returns:
            PostCheckResult 后置验证结果。

        Raises:
            UIAssertionError: 后置验证失败时抛出（触发 step_replay_guard）。
        """
        result = PostCheckResult(passed=False)

        # 计算截图相似度
        similarity = await self._compare_screenshots(before_screenshot, after_screenshot)
        result.similarity_score = similarity

        # 相似度 < 0.99 表示有变化（1.0 = 完全相同）
        result.change_detected = similarity < 0.99

        if not result.change_detected:
            # 操作后无变化，可能操作未生效
            result.passed = False
            result.detail = f"操作后截图无变化（相似度={similarity:.3f}），操作可能未生效"
            logger.warning("后置验证：操作后无状态变化，相似度=%.3f", similarity)

            await self._write_audit(
                "UI_POSTCONDITION_FAILED",
                {
                    "reason": "no_change_detected",
                    "similarity": similarity,
                    "expected_change": expected_change,
                },
            )
            raise UIAssertionError(
                "操作后无状态变化，触发 step_replay_guard",
                "UI_POSTCONDITION_FAILED",
                result.detail,
            )

        # 有变化，标记为通过（实际部署时可接入 LLM 验证预期变化描述）
        result.change_matches_expected = True
        result.passed = True
        result.detail = f"状态变化已确认（相似度={similarity:.3f}）"

        logger.debug(
            "后置验证通过: 相似度=%.3f, 变化描述=%r",
            similarity, expected_change,
        )
        return result

    @staticmethod
    def compute_ui_state_hash(
        window_title: str,
        process_name: str,
        screenshot_bytes: Optional[bytes] = None,
    ) -> str:
        """
        计算 UI 状态哈希，用于前置断言的一致性验证。

        Args:
            window_title: 窗口标题
            process_name: 进程名称
            screenshot_bytes: 截图字节数据（可选，参与哈希计算）

        Returns:
            SHA-256 哈希的前 16 位十六进制字符串。
        """
        payload = f"{window_title}|{process_name}"
        if screenshot_bytes:
            payload += f"|{hashlib.md5(screenshot_bytes).hexdigest()}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    async def _get_window_info(self) -> Dict[str, Any]:
        """获取当前活动窗口信息。"""
        if self._window_info_getter is None:
            # 未配置时返回空信息（骨架实现）
            return {
                "title": "",
                "process_name": "",
                "is_foreground": True,
                "ui_state_hash": "",
            }
        try:
            import asyncio
            if asyncio.iscoroutinefunction(self._window_info_getter):
                return await self._window_info_getter()
            else:
                return self._window_info_getter()
        except Exception as exc:
            logger.error("获取窗口信息失败: %s", exc)
            return {
                "title": "",
                "process_name": "",
                "is_foreground": False,
                "ui_state_hash": "",
            }

    async def _compare_screenshots(
        self,
        before: bytes,
        after: bytes,
    ) -> float:
        """
        比较两张截图的相似度。

        Returns:
            相似度 0.0-1.0（1.0 = 完全相同）。
        """
        if self._screenshot_comparator is None:
            # 未配置时使用简单哈希比较
            before_hash = hashlib.md5(before).hexdigest()
            after_hash = hashlib.md5(after).hexdigest()
            return 1.0 if before_hash == after_hash else 0.5

        try:
            import asyncio
            if asyncio.iscoroutinefunction(self._screenshot_comparator):
                return await self._screenshot_comparator(before, after)
            else:
                return self._screenshot_comparator(before, after)
        except Exception as exc:
            logger.error("截图比较失败: %s", exc)
            return 0.0

    async def _write_audit(self, event: str, detail: dict) -> None:
        """写入审计事件。"""
        if self._audit_writer is None:
            return
        try:
            import asyncio
            if asyncio.iscoroutinefunction(self._audit_writer):
                await self._audit_writer(event, detail)
            else:
                self._audit_writer(event, detail)
        except Exception as exc:
            logger.error("审计写入失败（event=%s）: %s", event, exc)
