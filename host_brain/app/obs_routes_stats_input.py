"""obs_routes_stats_input.py — 可观测性统计端点聚合入口（P2-8 拆分后）。

子模块已拆分为：
  - obs_routes_stats_input_dispatch.py：InputDispatch 生命周期统计
  - obs_routes_stats_input_humanoid.py：Gateway 人体闭环系列统计

本文件作为向后兼容的聚合入口，导入并重新导出子模块中的所有路由和模型。
"""
from __future__ import annotations

# 导入子模块以触发路由注册（副作用导入）
import host_brain.app.obs_routes_stats_input_dispatch  # noqa: F401
import host_brain.app.obs_routes_stats_input_humanoid  # noqa: F401

# 向后兼容：重新导出子模块中的公共符号
from host_brain.app.obs_routes_stats_input_dispatch import (
    InputSourceItem,
    InputDispatchStatsResponse,
    get_input_dispatch_stats,
)
from host_brain.app.obs_routes_stats_input_humanoid import (
    InputHumanoidLoopStageItem,
    InputHumanoidLoopStatsResponse,
    InputHumanoidLoopTrendResponse,
    InputHumanoidLoopAlertItem,
    InputHumanoidLoopAlertsResponse,
    InputHumanoidLoopRemediationStepItem,
    InputHumanoidLoopRemediationRequestItem,
    InputHumanoidLoopRemediationResponse,
    InputHumanoidLoopDiagnosticItem,
    InputHumanoidLoopDiagnosticsResponse,
    InputHumanoidLoopHotspotRequestItem,
    InputHumanoidLoopHotspotItem,
    InputHumanoidLoopHotspotsResponse,
    InputHumanoidLoopHotspotTrendSeriesItem,
    InputHumanoidLoopHotspotTrendBucketItem,
    InputHumanoidLoopHotspotTrendResponse,
    InputHumanoidLoopSourceCorrelationItem,
    InputHumanoidLoopSourceCorrelationResponse,
    get_input_humanoid_loop_stats,
    get_input_humanoid_loop_trend,
    get_input_humanoid_loop_alerts,
    get_input_humanoid_loop_remediation,
    get_input_humanoid_loop_diagnostics,
    get_input_humanoid_loop_hotspots,
    get_input_humanoid_loop_hotspot_trend,
    get_input_humanoid_loop_source_correlation,
)

__all__ = [
    "InputSourceItem", "InputDispatchStatsResponse",
    "get_input_dispatch_stats",
    "InputHumanoidLoopStageItem", "InputHumanoidLoopStatsResponse",
    "InputHumanoidLoopTrendResponse", "InputHumanoidLoopAlertItem",
    "InputHumanoidLoopAlertsResponse", "InputHumanoidLoopRemediationStepItem",
    "InputHumanoidLoopRemediationRequestItem", "InputHumanoidLoopRemediationResponse",
    "InputHumanoidLoopDiagnosticItem", "InputHumanoidLoopDiagnosticsResponse",
    "InputHumanoidLoopHotspotRequestItem", "InputHumanoidLoopHotspotItem",
    "InputHumanoidLoopHotspotsResponse", "InputHumanoidLoopHotspotTrendSeriesItem",
    "InputHumanoidLoopHotspotTrendBucketItem", "InputHumanoidLoopHotspotTrendResponse",
    "InputHumanoidLoopSourceCorrelationItem", "InputHumanoidLoopSourceCorrelationResponse",
    "get_input_humanoid_loop_stats", "get_input_humanoid_loop_trend",
    "get_input_humanoid_loop_alerts", "get_input_humanoid_loop_remediation",
    "get_input_humanoid_loop_diagnostics", "get_input_humanoid_loop_hotspots",
    "get_input_humanoid_loop_hotspot_trend", "get_input_humanoid_loop_source_correlation",
]
