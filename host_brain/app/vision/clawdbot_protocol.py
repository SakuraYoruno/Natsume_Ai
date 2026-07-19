"""Historical Visual Action proposal and result schemas.

协议版本：v1

这些类型只保留历史数据兼容和未来受治理 proposal 编译输入；`ActionPlan`
本身不携带 Grant、Attempt、Acceptance 或执行权限。旧 ClawdBot/Edge raw client
和 Agent Loop 已删除，任何正向执行都必须先进入新的 immutable TaskGraph revision。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED = (
    "VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED"
)

# ---------------------------------------------------------------------------
# 枚举：操作类型
# ---------------------------------------------------------------------------

class ActionType(str, Enum):
    """ClawdBot 支持的原子操作类型。"""
    CLICK        = "click"        # 单击指定坐标或元素
    DOUBLE_CLICK = "double_click" # 双击
    RIGHT_CLICK  = "right_click"  # 右键单击
    TYPE         = "type"         # 键盘输入文本
    PRESS        = "press"        # 按下快捷键（如 Enter、Ctrl+C）
    SCROLL       = "scroll"       # 滚动（方向 + 步数）
    DRAG         = "drag"         # 拖拽（起点 → 终点）
    SCREENSHOT   = "screenshot"   # 截图（用于 Agent Loop 中的状态感知）
    WAIT         = "wait"         # 等待指定毫秒
    ASSERT       = "assert"       # 断言当前屏幕包含某元素（用于校验）
    DONE         = "done"         # 标记目标已达成，终止 Agent Loop


class ResponseFormat(str, Enum):
    """夏目返回给调用方的格式。"""
    TEXT    = "text"    # 自然语言建议（给用户看）
    ACTIONS = "actions" # 历史兼容值；生产入口固定拒绝


class ActionStatus(str, Enum):
    """单条指令的执行状态。"""
    PENDING  = "pending"  # 待执行
    RUNNING  = "running"  # 执行中
    SUCCESS  = "success"  # 执行成功
    FAILED   = "failed"   # 执行失败
    SKIPPED  = "skipped"  # 被跳过（前置条件不满足）


# ---------------------------------------------------------------------------
# 单条操作指令
# ---------------------------------------------------------------------------

class Action(BaseModel):
    """一条原子操作指令。"""

    # 操作类型
    type: ActionType = Field(..., description="操作类型")

    # 目标描述（自然语言，用于日志和视觉校验）
    target_desc: str = Field("", description="目标元素的自然语言描述，如'登录按钮'")

    # 坐标（click / double_click / right_click / drag 使用）
    x: Optional[int] = Field(None, description="目标 X 坐标（屏幕绝对像素）")
    y: Optional[int] = Field(None, description="目标 Y 坐标（屏幕绝对像素）")

    # 拖拽终点（drag 使用）
    to_x: Optional[int] = Field(None, description="拖拽终点 X 坐标")
    to_y: Optional[int] = Field(None, description="拖拽终点 Y 坐标")

    # 文本输入（type 使用）
    text: Optional[str] = Field(None, description="要输入的文本内容")

    # 按键（press 使用，支持组合键如 'ctrl+c'）
    key: Optional[str] = Field(None, description="按键名称，如 'Enter'、'ctrl+c'")

    # 滚动（scroll 使用）
    direction: Optional[str] = Field(None, description="滚动方向：up / down / left / right")
    steps: Optional[int] = Field(None, description="滚动步数（默认 3）")

    # 等待（wait 使用）
    wait_ms: Optional[int] = Field(None, description="等待时长（毫秒）")

    # 断言（assert 使用）
    assert_text: Optional[str] = Field(None, description="断言屏幕中应包含的文本")
    assert_element: Optional[str] = Field(None, description="断言屏幕中应存在的元素描述")

    # 截图（screenshot 使用）
    screenshot_label: Optional[str] = Field(None, description="截图标签，用于 Agent Loop 状态追踪")

    # 扩展字段（预留）
    extra: Dict[str, Any] = Field(default_factory=dict, description="扩展参数")


# ---------------------------------------------------------------------------
# 操作计划（夏目 → ClawdBot）
# ---------------------------------------------------------------------------

class ActionPlan(BaseModel):
    """无执行 authority 的历史/候选操作提案结构。"""

    # 协议版本
    protocol_version: str = Field("v1", description="协议版本号")

    # 请求追踪
    request_id: str = Field(..., description="来源请求 ID，用于追踪")
    session_id: str = Field(..., description="会话 ID")

    # 目标描述（自然语言，用于 Agent Loop 的目标判断）
    goal: str = Field(..., description="本次自动化的目标，如'填写并提交登录表单'")

    # 操作序列
    actions: List[Action] = Field(default_factory=list, description="候选操作列表，不授予执行权限")

    # 执行策略
    stop_on_error: bool = Field(True, description="遇到错误时是否立即停止")
    max_retries: int = Field(1, description="单条指令失败后的最大重试次数")
    action_interval_ms: int = Field(200, description="相邻操作之间的间隔（毫秒）")

    # 是否需要截图反馈（Agent Loop 模式下为 True）
    need_screenshot_feedback: bool = Field(
        False,
        description="执行完毕后是否需要 ClawdBot 返回截图，供夏目做下一轮分析",
    )

    # 元信息
    scene_type: str = Field("SCENE_VISION_UI", description="来源场景类型")
    extra: Dict[str, Any] = Field(default_factory=dict, description="扩展元信息")


# ---------------------------------------------------------------------------
# 单条指令执行结果
# ---------------------------------------------------------------------------

class ActionResult(BaseModel):
    """ClawdBot 对单条指令的执行结果。"""

    action_index: int = Field(..., description="对应 ActionPlan.actions 中的下标")
    action_type: ActionType = Field(..., description="操作类型")
    status: ActionStatus = Field(..., description="执行状态")
    latency_ms: int = Field(0, description="执行耗时（毫秒）")
    error: Optional[str] = Field(None, description="失败原因（status=FAILED 时填写）")
    extra: Dict[str, Any] = Field(default_factory=dict, description="扩展结果信息")


# ---------------------------------------------------------------------------
# 操作计划执行报告（ClawdBot → 夏目）
# ---------------------------------------------------------------------------

class ActionReport(BaseModel):
    """ClawdBot 执行完 ActionPlan 后返回给夏目的完整报告。"""

    protocol_version: str = Field("v1", description="协议版本号")
    request_id: str = Field(..., description="来源请求 ID")
    session_id: str = Field(..., description="会话 ID")

    # 整体状态
    status: ActionStatus = Field(..., description="整体执行状态")
    goal_achieved: bool = Field(False, description="是否达成目标（由 DONE 指令或夏目判断）")

    # 各步骤结果
    results: List[ActionResult] = Field(default_factory=list, description="各步骤执行结果")

    # 统计
    total_actions: int = Field(0, description="总指令数")
    success_count: int = Field(0, description="成功指令数")
    failed_count: int = Field(0, description="失败指令数")
    total_latency_ms: int = Field(0, description="总耗时（毫秒）")

    # 截图反馈（need_screenshot_feedback=True 时填写）
    screenshot_base64: Optional[str] = Field(None, description="执行后的屏幕截图（Base64）")
    screenshot_mime: str = Field("image/png", description="截图 MIME 类型")

    # 错误信息
    error: Optional[str] = Field(None, description="整体失败原因")
    extra: Dict[str, Any] = Field(default_factory=dict, description="扩展信息")


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

__all__ = [
    "VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED",
    "ActionType",
    "ResponseFormat",
    "ActionStatus",
    "Action",
    "ActionPlan",
    "ActionResult",
    "ActionReport",
]
