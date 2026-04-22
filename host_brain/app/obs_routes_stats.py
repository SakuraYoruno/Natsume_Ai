"""obs_routes_stats.py — 可观测性统计端点聚合入口（P2-1 重构）。

原 308KB 单文件已按功能域拆分为 5 个子模块：
  - obs_routes_stats_governor.py  : Governor决策、全局错误、子系统健康统计
  - obs_routes_stats_agent.py     : Agent Loop、Memory 子系统统计
  - obs_routes_stats_system.py    : VTS/蒸馏/WeChat/保留策略/QA/LLM/Checkpoint/限流/Vault 统计
  - obs_routes_stats_input.py     : Input Dispatch、Humanoid Loop 系列统计
  - obs_routes_stats_capability.py: 失败/权限/调度器/链路/语义召回/免疫门/检索/能力缺口统计

本文件保留为向后兼容的聚合入口，导入所有子模块以确保路由注册。
"""
from __future__ import annotations

# 导入所有子模块，触发路由注册（各子模块均使用同一 router 实例）
from .obs_routes_stats_governor import *    # noqa: F401, F403
from .obs_routes_stats_agent import *       # noqa: F401, F403
from .obs_routes_stats_system import *      # noqa: F401, F403
from .obs_routes_stats_input import *       # noqa: F401, F403
from .obs_routes_stats_capability import *  # noqa: F401, F403

# 顶层引用，供测试 monkeypatch 使用
from .obs_routes_core import _get_ledger  # noqa: F401
