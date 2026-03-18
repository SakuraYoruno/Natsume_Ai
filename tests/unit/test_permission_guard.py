"""
test_permission_guard.py —— PermissionGuard 单元测试（需求 7）
"""
from __future__ import annotations

import pytest

from host_brain.skills.registry import (
    AccessLevel,
    PermissionGuard,
    PermissionGuardError,
    SkillMeta,
)
from host_brain.app.schemas.memory_types import ProposerClass


def _handler(ctx, state):
    return {}


def make_skill_meta(**kwargs) -> SkillMeta:
    """创建测试用 SkillMeta（默认低风险、可调度）。"""
    defaults = dict(
        skill_name="TestTool",
        clearance="LOW",
        risk_level=1,
        scope="host",
        reversible=True,
        handler=_handler,
        require_confirm=False,
        description="测试工具",
        scheduler_allowed=True,
        access_level=AccessLevel.PUBLIC,
    )
    defaults.update(kwargs)
    return SkillMeta(**defaults)


class TestPermissionGuardScheduler:
    """SCHEDULER 来源权限测试（需求 7.1、7.2）。"""

    def test_scheduler_blocked_from_high_risk_tool(self):
        """验证 SCHEDULER 禁止访问 risk_level >= 4 的工具（需求 7.2）。"""
        guard = PermissionGuard()
        # risk_level=4 属于 HIGH，且 require_confirm=True（validate 要求）
        skill = make_skill_meta(risk_level=4, require_confirm=True)

        with pytest.raises(PermissionGuardError) as exc_info:
            guard.check(skill, ProposerClass.SCHEDULER)
        assert "HIGH risk_level" in str(exc_info.value)

    def test_scheduler_blocked_from_clawd_ui_action(self):
        """验证 SCHEDULER 禁止触发 ClawdUIAction（需求 7.2）。"""
        guard = PermissionGuard()
        skill = make_skill_meta(skill_name="ClawdUIAction")

        with pytest.raises(PermissionGuardError) as exc_info:
            guard.check(skill, ProposerClass.SCHEDULER)
        assert "ClawdUIAction" in str(exc_info.value)

    def test_scheduler_max_tool_calls_enforced(self):
        """验证 SCHEDULER 调用次数上限 ≤ 3（需求 7.2）。"""
        guard = PermissionGuard()
        skill = make_skill_meta()

        # 前 3 次应通过
        for _ in range(3):
            guard.check(skill, ProposerClass.SCHEDULER, session_id="s1")

        # 第 4 次应被拒绝
        with pytest.raises(PermissionGuardError) as exc_info:
            guard.check(skill, ProposerClass.SCHEDULER, session_id="s1")
        assert "上限" in str(exc_info.value)

    def test_scheduler_blocked_when_not_allowed(self):
        """验证 scheduler_allowed=False 时 SCHEDULER 被拒绝。"""
        guard = PermissionGuard()
        skill = make_skill_meta(scheduler_allowed=False)

        with pytest.raises(PermissionGuardError):
            guard.check(skill, ProposerClass.SCHEDULER)

    def test_user_can_access_high_risk_tool(self):
        """验证 USER 来源可以访问 risk_level >= 4 的工具（无限制）。"""
        guard = PermissionGuard()
        skill = make_skill_meta(risk_level=4, require_confirm=True)
        guard.check(skill, ProposerClass.USER)  # 不应抛出异常

    def test_scheduler_count_reset(self):
        """验证重置 SCHEDULER 调用计数后可以继续调用。"""
        guard = PermissionGuard()
        skill = make_skill_meta()

        for _ in range(3):
            guard.check(skill, ProposerClass.SCHEDULER, session_id="s2")

        guard.reset_scheduler_count("s2")

        # 重置后应可以继续调用
        guard.check(skill, ProposerClass.SCHEDULER, session_id="s2")

    def test_scheduler_low_risk_passes(self):
        """验证 SCHEDULER 可以正常调用低风险工具。"""
        guard = PermissionGuard()
        skill = make_skill_meta(risk_level=1)
        guard.check(skill, ProposerClass.SCHEDULER, session_id="s3")  # 不应抛出

    def test_scheduler_medium_risk_passes(self):
        """验证 SCHEDULER 可以调用 risk_level=3（MEDIUM）的工具。"""
        guard = PermissionGuard()
        skill = make_skill_meta(risk_level=3)
        guard.check(skill, ProposerClass.SCHEDULER, session_id="s4")  # 不应抛出

    def test_scheduler_risk_level_5_blocked(self):
        """验证 SCHEDULER 禁止访问 risk_level=5 的工具。"""
        guard = PermissionGuard()
        skill = make_skill_meta(risk_level=5, require_confirm=True)

        with pytest.raises(PermissionGuardError):
            guard.check(skill, ProposerClass.SCHEDULER)


class TestPermissionGuardError:
    """PermissionGuardError 异常结构测试。"""

    def test_error_carries_tool_name(self):
        """验证 PermissionGuardError 携带 tool_name 属性。"""
        err = PermissionGuardError("测试原因", "MyTool")
        assert err.tool_name == "MyTool"
        assert err.reason == "测试原因"
        assert "MyTool" in str(err)

    def test_error_default_tool_name(self):
        """验证 PermissionGuardError 默认 tool_name 为空字符串。"""
        err = PermissionGuardError("原因")
        assert err.tool_name == ""


class TestAccessLevel:
    """AccessLevel 枚举测试。"""

    def test_access_level_values(self):
        """验证 AccessLevel 枚举值正确。"""
        assert AccessLevel.PUBLIC.value == "PUBLIC"
        assert AccessLevel.INTERNAL.value == "INTERNAL"
        assert AccessLevel.RESTRICTED.value == "RESTRICTED"

    def test_skill_meta_default_access_level(self):
        """验证 SkillMeta 默认 access_level 为 PUBLIC。"""
        skill = make_skill_meta()
        assert skill.access_level == AccessLevel.PUBLIC

    def test_skill_meta_restricted_access_level(self):
        """验证 SkillMeta 可以设置 RESTRICTED 访问级别。"""
        skill = make_skill_meta(access_level=AccessLevel.RESTRICTED)
        assert skill.access_level == AccessLevel.RESTRICTED
