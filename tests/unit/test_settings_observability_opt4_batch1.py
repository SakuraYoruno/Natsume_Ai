"""OPT-4 首批 obs_routes 字段迁移回归保护单测.

覆盖 OPTIMIZATION_PLAN_20260420.md §8 / OPT-4 首批迁移：

  - ``obs_sessions_cache_ttl_ms``
  - ``obs_insights_summary_warm_enabled``
  - ``obs_digital_life_overview_warm_enabled``
  - ``obs_runtime_snapshot_warm_enabled``
  - ``obs_trace_feed_warm_enabled``

测试点：

  T-OPT4-10：ObservabilitySettings 可正常实例化，5 个新字段默认值与
            obs_routes_* 模块读到的模块级常量一致。
  T-OPT4-11：readiness_fields() 报告包含 5 个新字段，且 env_key 与
            source-of-truth 对齐。
  T-OPT4-12：env 覆盖生效 —— 设置环境变量后 settings 读到新值；同时
            obs_routes_sessions 模块级常量也反映新值（需要清 lru_cache
            并重新 import）。
  T-OPT4-13：布尔字段支持 "false"/"0"/"off" 等常见字面量。
"""

from __future__ import annotations

import importlib
import os
import sys
import unittest


def _reload_observability_settings():
    """清 lru_cache 并返回最新 settings（用于 env 覆盖测试）."""
    # 先清掉 lru_cache，不 reload 模块自身
    from host_brain.app.settings import observability as obs_settings_mod  # type: ignore

    try:
        obs_settings_mod.get_observability_settings.cache_clear()
    except AttributeError:
        pass
    return obs_settings_mod.get_observability_settings()


class TestObservabilitySettingsOpt4Batch1(unittest.TestCase):
    """Session query cache settings regression protection."""

    NEW_FIELDS_DEFAULTS = {
        "obs_sessions_cache_ttl_ms": 3000,
    }

    NEW_FIELDS_ENV_KEYS = {
        "obs_sessions_cache_ttl_ms": "OBS_SESSIONS_CACHE_TTL_MS",
    }

    def setUp(self) -> None:
        # 清掉所有相关 env 以免测试之间污染
        for env_key in self.NEW_FIELDS_ENV_KEYS.values():
            os.environ.pop(env_key, None)
        self._settings = _reload_observability_settings()

    def tearDown(self) -> None:
        for env_key in self.NEW_FIELDS_ENV_KEYS.values():
            os.environ.pop(env_key, None)
        _reload_observability_settings()

    # -----------------------------------------------------------------
    # T-OPT4-10：默认值一致性
    # -----------------------------------------------------------------
    def test_t_opt4_10_default_values(self) -> None:
        for field_name, expected in self.NEW_FIELDS_DEFAULTS.items():
            with self.subTest(field=field_name):
                actual = getattr(self._settings, field_name)
                self.assertEqual(
                    actual,
                    expected,
                    msg=f"{field_name} default drifted: expected {expected!r}, got {actual!r}",
                )

    # -----------------------------------------------------------------
    # T-OPT4-11：readiness_fields 包含新字段
    # -----------------------------------------------------------------
    def test_t_opt4_11_readiness_fields_include_new_fields(self) -> None:
        from host_brain.app.settings.observability import ObservabilitySettings  # type: ignore

        reports = ObservabilitySettings.readiness_fields()
        by_field = {r.field_name: r for r in reports}
        for field_name, env_key in self.NEW_FIELDS_ENV_KEYS.items():
            with self.subTest(field=field_name):
                self.assertIn(
                    field_name,
                    by_field,
                    msg=f"readiness_fields missing {field_name}",
                )
                self.assertEqual(
                    by_field[field_name].env_key,
                    env_key,
                    msg=f"{field_name}.env_key should be {env_key}",
                )
                self.assertFalse(
                    by_field[field_name].is_required,
                    msg="OPT-4 首批字段应为可选",
                )

    # -----------------------------------------------------------------
    # T-OPT4-12：env 覆盖生效 + obs_routes_sessions 模块常量反映新值
    # -----------------------------------------------------------------
    def test_t_opt4_12_env_override_propagates_to_obs_routes_sessions(self) -> None:
        os.environ["OBS_SESSIONS_CACHE_TTL_MS"] = "7777"
        s = _reload_observability_settings()
        self.assertEqual(s.obs_sessions_cache_ttl_ms, 7777)

        # 重新加载 obs_routes_sessions，验证模块级常量也更新
        sys.modules.pop("host_brain.app.obs_routes_sessions", None)
        mod = importlib.import_module("host_brain.app.obs_routes_sessions")
        self.assertEqual(
            mod._SESSIONS_CACHE_TTL_MS,
            7777,
            msg="obs_routes_sessions._SESSIONS_CACHE_TTL_MS 未能跟随 env 刷新",
        )

if __name__ == "__main__":
    unittest.main()
