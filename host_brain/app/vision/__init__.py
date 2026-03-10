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

__all__ = [
    "UIAction",
    "UIAssertionGuard",
    "UIAssertionError",
    "PreCheckResult",
    "PostCheckResult",
    "is_sensitive_ui",
    "SENSITIVE_WINDOW_KEYWORDS",
]
