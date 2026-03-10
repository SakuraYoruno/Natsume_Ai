"""
test_permission_guard.py —— PermissionGuard 单元测试（需求 7）
"""
from __future__ import annotations

import pytest

from host_brain.app.skills.tool_meta import (
    AccessLevel,
    HotLoader,
    HotLoaderError,
    PermissionGuard,
    PermissionGuardError,
    ToolMeta,
)
from host_brain.app.schemas.memory_types import ProposerClass, RiskLevel


def make_tool_meta(**kwargs) -> ToolMeta:
    """创建测试用 ToolMeta。"""
    defaults = {
        "name": "TestTool",
        "description": "测试工具",
        "access_level": AccessLevel.PUBLIC,
        "risk_level": RiskLevel.LOW,
        "reversible": True,
        "requires_human_approval": False,
        "scheduler_allowed": True,
    }
    defaults.update(kwargs)
    return ToolMeta(**defaults)


class TestPermissionGuardScheduler:
    """SCHEDULER 来源权限测试（需求 7.1、7.2）。"""

    def test_scheduler_blocked_from_high_risk_tool(self):
        """验证 SCHEDULER 禁止访问 HIGH risk_level 工具（需求 7.2）。"""
        guard = PermissionGuard()
        tool = make_tool_meta(risk_level=RiskLevel.HIGH, scheduler_allowed=True)

        with pytest.raises(PermissionGuardError) as exc_info:
            guard.check(tool, ProposerClass.SCHEDULER)
        assert "HIGH risk_level" in str(exc_info.value)

    def test_scheduler_blocked_from_clawd_ui_action(self):
        """验证 SCHEDULER 禁止触发 ClawdUIAction（需求 7.2）。"""
        guard = PermissionGuard()
        tool = make_tool_meta(name="ClawdUIAction", scheduler_allowed=True)

        with pytest.raises(PermissionGuardError) as exc_info:
            guard.check(tool, ProposerClass.SCHEDULER)
        assert "ClawdUIAction" in str(exc_info.value)

    def test_scheduler_max_tool_calls_enforced(self):
        """验证 SCHEDULER 调用次数上限 ≤ 3（需求 7.2）。"""
        guard = PermissionGuard()
        tool = make_tool_meta()

        # 前 3 次应通过
        for _ in range(3):
            guard.check(tool, ProposerClass.SCHEDULER, session_id="s1")

        # 第 4 次应被拒绝
        with pytest.raises(PermissionGuardError) as exc_info:
            guard.check(tool, ProposerClass.SCHEDULER, session_id="s1")
        assert "上限" in str(exc_info.value)

    def test_scheduler_blocked_when_not_allowed(self):
        """验证 scheduler_allowed=False 时 SCHEDULER 被拒绝。"""
        guard = PermissionGuard()
        tool = make_tool_meta(scheduler_allowed=False)

        with pytest.raises(PermissionGuardError):
            guard.check(tool, ProposerClass.SCHEDULER)

    def test_user_can_access_high_risk_tool(self):
        """验证 USER 来源可以访问 HIGH risk_level 工具（无限制）。"""
        guard = PermissionGuard()
        tool = make_tool_meta(risk_level=RiskLevel.HIGH)
        guard.check(tool, ProposerClass.USER)  # 不应抛出异常

    def test_scheduler_count_reset(self):
        """验证重置 SCHEDULER 调用计数后可以继续调用。"""
        guard = PermissionGuard()
        tool = make_tool_meta()

        for _ in range(3):
            guard.check(tool, ProposerClass.SCHEDULER, session_id="s2")

        guard.reset_scheduler_count("s2")

        # 重置后应可以继续调用
        guard.check(tool, ProposerClass.SCHEDULER, session_id="s2")


class TestHumanApproval:
    """人工确认测试（需求 7.4）。"""

    @pytest.mark.asyncio
    async def test_approval_requested_for_sensitive_tool(self):
        """验证 requires_human_approval=True 时请求人工确认。"""
        approval_requests = []

        async def mock_requester(tool_name, action_desc):
            approval_requests.append((tool_name, action_desc))
            return True  # 模拟用户批准

        guard = PermissionGuard(human_approval_requester=mock_requester)
        tool = make_tool_meta(requires_human_approval=True)

        approved = await guard.request_human_approval(tool, "执行文件删除操作")
        assert approved is True
        assert len(approval_requests) == 1
        assert approval_requests[0][0] == "TestTool"

    @pytest.mark.asyncio
    async def test_no_approval_needed_for_normal_tool(self):
        """验证 requires_human_approval=False 时直接通过。"""
        guard = PermissionGuard()
        tool = make_tool_meta(requires_human_approval=False)

        approved = await guard.request_human_approval(tool, "普通操作")
        assert approved is True

    @pytest.mark.asyncio
    async def test_default_deny_when_no_requester_configured(self):
        """验证未配置 requester 时默认拒绝（安全兜底）。"""
        guard = PermissionGuard(human_approval_requester=None)
        tool = make_tool_meta(requires_human_approval=True)

        approved = await guard.request_human_approval(tool, "敏感操作")
        assert approved is False


class TestHotLoader:
    """HotLoader 元数据完整性校验测试（需求 7.5）。"""

    def test_valid_tool_meta_loads_successfully(self):
        """验证完整元数据的工具加载成功。"""
        loader = HotLoader()
        tool = make_tool_meta()
        result = loader.load_from_meta(tool)
        assert result.name == "TestTool"

    def test_incomplete_tool_meta_rejected(self):
        """验证缺少必填字段的工具被拒绝加载（需求 7.5）。"""
        loader = HotLoader()

        # 创建一个缺少 description 的工具（通过直接修改属性模拟）
        tool = make_tool_meta()
        tool.description = ""  # 清空必填字段

        with pytest.raises(HotLoaderError) as exc_info:
            loader.load_from_meta(tool)
        assert "description" in str(exc_info.value)

    def test_loaded_tool_retrievable(self):
        """验证加载后的工具可以被检索。"""
        loader = HotLoader()
        tool = make_tool_meta(name="MyTool")
        loader.load_from_meta(tool)

        retrieved = loader.get_tool("MyTool")
        assert retrieved is not None
        assert retrieved.name == "MyTool"

    def test_unload_tool(self):
        """验证工具可以被卸载。"""
        loader = HotLoader()
        tool = make_tool_meta(name="TempTool")
        loader.load_from_meta(tool)

        assert loader.unload_tool("TempTool") is True
        assert loader.get_tool("TempTool") is None
