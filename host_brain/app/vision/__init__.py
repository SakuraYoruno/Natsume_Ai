"""
vision 包 —— Vision/UI 自动化
"""
from .ui_assertion import (
    UIAction,
    UIAssertionGuard,
    UIAssertionError,
    PreCheckResult,
    PostCheckResult,
    is_sensitive_ui,
    SENSITIVE_WINDOW_KEYWORDS,
)
from .clawdbot_protocol import (
    ActionType,
    ResponseFormat,
    ActionStatus,
    Action,
    ActionPlan,
    ActionResult,
    ActionReport,
)
from .action_planner import generate_action_plan
from .agent_loop import (
    ClawdbotClient,
    MockClawdbotClient,
    run_agent_loop,
)

__all__ = [
    # ui_assertion
    "UIAction",
    "UIAssertionGuard",
    "UIAssertionError",
    "PreCheckResult",
    "PostCheckResult",
    "is_sensitive_ui",
    "SENSITIVE_WINDOW_KEYWORDS",
    # clawdbot_protocol
    "ActionType",
    "ResponseFormat",
    "ActionStatus",
    "Action",
    "ActionPlan",
    "ActionResult",
    "ActionReport",
    # action_planner
    "generate_action_plan",
    # agent_loop
    "ClawdbotClient",
    "MockClawdbotClient",
    "run_agent_loop",
]
