"""obs_routes.py — Observability REST API 聚合入口（瘦身版）。

Phase XIV 拆分：
  - obs_routes_core.py: 依赖注入 + 工具函数 + Pydantic 模型 + 核心端点（health/sessions/trace/graph/metrics）
  - obs_routes_stats.py: 33 个子系统统计端点
  - obs_routes_platform.py: 平台统计（gateway/skills-hub）+ WebSocket + failure-events
  - obs_routes_sysmode.py: 系统运行模式管理（sys-mode 全套）

本文件仅负责聚合，确保 FastAPI app.include_router(router) 时所有端点自动注册。
"""
# 核心模块（定义 router + 依赖注入 + 工具函数 + 核心端点）
from host_brain.app.obs_routes_core import router, set_ledger_provider  # noqa: F401

# 子系统统计端点（import 后自动注册到 router）
import host_brain.app.obs_routes_stats  # noqa: F401
# 平台 + WebSocket 端点
from host_brain.app.obs_routes_platform import _fetch_remote_gateway_health  # noqa: F401
import host_brain.app.obs_routes_platform  # noqa: F401
# 系统运行模式
import host_brain.app.obs_routes_sysmode  # noqa: F401
