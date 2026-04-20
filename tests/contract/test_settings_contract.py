"""Settings 分层包契约测试（OPT-4）。

覆盖：
1. 四个域 Settings 都能正确实例化（不抛错）
2. lru_cache 单例行为生效
3. readiness_fields 返回非空、结构正确
4. verify_readiness 聚合报告字段与期望吻合
5. 环境变量覆盖时字段来源标记为 env
6. 敏感字段（含 key/secret/token 等）在报告中被脱敏
"""

from __future__ import annotations

import pytest

from host_brain.app.settings import (
    CapabilityForgeSettings,
    CoreSettings,
    LLMSettings,
    ObservabilitySettings,
    SettingsReadinessReport,
    TribeSettings,
    get_capability_forge_settings,
    get_core_settings,
    get_llm_settings,
    get_observability_settings,
    get_tribe_settings,
    verify_readiness,
)
from host_brain.app.settings.base import SettingsFieldReport, inspect_setting


pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """每个测试前清空单例缓存，避免相互污染。"""
    get_core_settings.cache_clear()
    get_llm_settings.cache_clear()
    get_tribe_settings.cache_clear()
    get_observability_settings.cache_clear()
    get_capability_forge_settings.cache_clear()
    yield
    get_core_settings.cache_clear()
    get_llm_settings.cache_clear()
    get_tribe_settings.cache_clear()
    get_observability_settings.cache_clear()
    get_capability_forge_settings.cache_clear()


class TestCoreSettings:
    def test_defaults(self):
        s = CoreSettings()
        assert s.env in ("dev", "test", "prod")
        assert s.host_app_port == 18080
        assert s.log_level in ("DEBUG", "INFO", "WARN", "WARNING", "ERROR")

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("HOST_APP_PORT", "19999")
        monkeypatch.setenv("HOST_APP_LOG_LEVEL", "DEBUG")
        s = CoreSettings()
        assert s.host_app_port == 19999
        assert s.log_level == "DEBUG"

    def test_port_out_of_range_raises(self, monkeypatch):
        monkeypatch.setenv("HOST_APP_PORT", "99999")
        with pytest.raises(Exception):
            CoreSettings()

    # ─── OPT-4 第三批：新增字段（prefrontal_plan_enabled / skill_dag_parallel_window / sys_mode）───

    def test_opt4_batch3_defaults_match_legacy(self):
        """新增 3 个字段的默认值必须与迁移前 os.environ.get("...", "default") 一致。"""
        s = CoreSettings()
        assert s.prefrontal_plan_enabled is False  # 原：os.environ.get("PREFRONTAL_PLAN_ENABLED", "false") == "true"
        assert s.skill_dag_parallel_window == 4    # 原：_safe_int(env_value, default=4, minimum=1, maximum=16)
        assert s.sys_mode == "normal"              # 原：str(os.environ.get("HOST_SYS_MODE") or "normal")

    def test_opt4_batch3_env_override(self, monkeypatch):
        monkeypatch.setenv("PREFRONTAL_PLAN_ENABLED", "true")
        monkeypatch.setenv("HOST_SKILL_DAG_PARALLEL_WINDOW", "8")
        monkeypatch.setenv("HOST_SYS_MODE", "safe")
        s = CoreSettings()
        assert s.prefrontal_plan_enabled is True
        assert s.skill_dag_parallel_window == 8
        assert s.sys_mode == "safe"

    def test_opt4_batch3_skill_dag_parallel_window_out_of_range(self, monkeypatch):
        """skill_dag_parallel_window ≤ 16 的硬约束生效。"""
        monkeypatch.setenv("HOST_SKILL_DAG_PARALLEL_WINDOW", "999")
        with pytest.raises(Exception):
            CoreSettings()

    def test_opt4_batch3_readiness_fields_include_new_three(self):
        fields = CoreSettings.readiness_fields()
        names = {f.field_name for f in fields}
        assert {"prefrontal_plan_enabled", "skill_dag_parallel_window", "sys_mode"}.issubset(names)
        # 全部归属 core domain
        assert all(f.domain == "core" for f in fields)


class TestLLMSettings:
    def test_defaults(self):
        s = LLMSettings()
        assert s.provider in ("openai", "codex", "gemini", "deepseek", "mock", "disabled")
        assert s.timeout_ms >= 100
        assert s.max_retries >= 0

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("HOST_LLM_PROVIDER", "gemini")
        monkeypatch.setenv("HOST_LLM_TIMEOUT_MS", "22222")
        s = LLMSettings()
        assert s.provider == "gemini"
        assert s.timeout_ms == 22222

    def test_invalid_provider_rejected(self, monkeypatch):
        monkeypatch.setenv("HOST_LLM_PROVIDER", "not_a_provider")
        with pytest.raises(Exception):
            LLMSettings()


class TestTribeSettings:
    def test_defaults(self):
        s = TribeSettings()
        assert s.edge_rpc_timeout_ms == 3000
        assert s.enable_retrieval is True

    def test_bool_env_override(self, monkeypatch):
        monkeypatch.setenv("HOST_ENABLE_VISION", "false")
        monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "false")
        s = TribeSettings()
        assert s.enable_vision is False
        assert s.enable_retrieval is False


class TestObservabilitySettings:
    def test_defaults(self):
        s = ObservabilitySettings()
        assert s.service_health_heartbeat_interval_s == 30
        assert 0.0 <= s.soft_fail_alert_ratio_threshold <= 1.0

    def test_soft_fail_threshold_override(self, monkeypatch):
        monkeypatch.setenv("HOST_SOFT_FAIL_ALERT_RATIO_THRESHOLD", "0.2")
        s = ObservabilitySettings()
        assert s.soft_fail_alert_ratio_threshold == pytest.approx(0.2)


class TestSingletons:
    def test_get_core_settings_cached(self):
        a = get_core_settings()
        b = get_core_settings()
        assert a is b

    def test_get_llm_settings_cached(self):
        a = get_llm_settings()
        b = get_llm_settings()
        assert a is b

    def test_get_tribe_settings_cached(self):
        a = get_tribe_settings()
        b = get_tribe_settings()
        assert a is b

    def test_get_observability_settings_cached(self):
        a = get_observability_settings()
        b = get_observability_settings()
        assert a is b

    def test_get_capability_forge_settings_cached(self):
        a = get_capability_forge_settings()
        b = get_capability_forge_settings()
        assert a is b


class TestCapabilityForgeSettings:
    """OPT-4 第二批：CapabilityForgeSettings 迁移回归保护。

    验收点：
    - 10 个字段默认值与原原始 `_parse_*_env` 调用精确一致（防回归）
    - 字段边界约束（ge/le）有效（超范围应抛错）
    - capability_forge.py 的 `_get_auto_plan_thresholds()` 返回形对外契约保持不变
    - readiness_fields() 完整返回 10 个字段
    - /health/env-readiness 聚合后 domain=capability_forge 的字段能被搜到
    """

    def test_defaults_match_legacy(self):
        s = CapabilityForgeSettings()
        # 与原 `_get_auto_plan_thresholds()` 的 default= 参数成对比对
        assert s.auto_proposal_enabled is True
        assert s.auto_min_detected == 3
        assert s.auto_min_sessions == 2
        assert s.auto_min_failure_rate == pytest.approx(0.35)
        assert s.auto_min_failed == 2
        assert s.auto_dedupe_cooldown_ms == 1_800_000
        assert s.auto_canary_traffic_percent == 5
        assert s.auto_approve_enabled is False
        assert s.auto_approve_stage == "shadow"
        assert s.auto_approver == "auto_forge"

    def test_int_env_override(self, monkeypatch):
        monkeypatch.setenv("HOST_CAPABILITY_FORGE_AUTO_MIN_DETECTED", "7")
        monkeypatch.setenv("HOST_CAPABILITY_FORGE_AUTO_DEDUPE_COOLDOWN_MS", "120000")
        s = CapabilityForgeSettings()
        assert s.auto_min_detected == 7
        assert s.auto_dedupe_cooldown_ms == 120_000

    def test_float_env_override(self, monkeypatch):
        monkeypatch.setenv("HOST_CAPABILITY_FORGE_AUTO_MIN_FAILURE_RATE", "0.72")
        s = CapabilityForgeSettings()
        assert s.auto_min_failure_rate == pytest.approx(0.72)

    def test_bool_env_override(self, monkeypatch):
        monkeypatch.setenv("HOST_CAPABILITY_FORGE_AUTO_PROPOSAL_ENABLED", "false")
        monkeypatch.setenv("HOST_CAPABILITY_FORGE_AUTO_APPROVE_ENABLED", "true")
        s = CapabilityForgeSettings()
        assert s.auto_proposal_enabled is False
        assert s.auto_approve_enabled is True

    def test_string_env_override(self, monkeypatch):
        monkeypatch.setenv("HOST_CAPABILITY_FORGE_AUTO_APPROVE_STAGE", "canary")
        monkeypatch.setenv("HOST_CAPABILITY_FORGE_AUTO_APPROVER", "ops_bot")
        s = CapabilityForgeSettings()
        assert s.auto_approve_stage == "canary"
        assert s.auto_approver == "ops_bot"

    def test_out_of_range_rejected(self, monkeypatch):
        monkeypatch.setenv("HOST_CAPABILITY_FORGE_AUTO_MIN_DETECTED", "9999")  # le=100
        with pytest.raises(Exception):
            CapabilityForgeSettings()

    def test_failure_rate_out_of_range_rejected(self, monkeypatch):
        monkeypatch.setenv("HOST_CAPABILITY_FORGE_AUTO_MIN_FAILURE_RATE", "1.5")  # le=1.0
        with pytest.raises(Exception):
            CapabilityForgeSettings()

    def test_readiness_fields_complete(self):
        fields = CapabilityForgeSettings.readiness_fields()
        assert len(fields) == 10
        all_keys = {f.env_key for f in fields}
        assert all_keys == {
            "HOST_CAPABILITY_FORGE_AUTO_PROPOSAL_ENABLED",
            "HOST_CAPABILITY_FORGE_AUTO_MIN_DETECTED",
            "HOST_CAPABILITY_FORGE_AUTO_MIN_SESSIONS",
            "HOST_CAPABILITY_FORGE_AUTO_MIN_FAILURE_RATE",
            "HOST_CAPABILITY_FORGE_AUTO_MIN_FAILED",
            "HOST_CAPABILITY_FORGE_AUTO_DEDUPE_COOLDOWN_MS",
            "HOST_CAPABILITY_FORGE_AUTO_CANARY_TRAFFIC_PERCENT",
            "HOST_CAPABILITY_FORGE_AUTO_APPROVE_ENABLED",
            "HOST_CAPABILITY_FORGE_AUTO_APPROVE_STAGE",
            "HOST_CAPABILITY_FORGE_AUTO_APPROVER",
        }
        assert all(f.domain == "capability_forge" for f in fields)

    def test_readiness_aggregation_includes_capability_forge(self):
        report = verify_readiness(
            producers=[
                CoreSettings.readiness_fields,
                CapabilityForgeSettings.readiness_fields,
            ]
        )
        domains = {f.domain for f in report.fields}
        assert "capability_forge" in domains

    def test_service_thresholds_backward_contract(self, monkeypatch):
        """capability_forge.py 的 `_get_auto_plan_thresholds()` 返回结构契约不变。"""
        from host_brain.app.capability_forge import CapabilityForgeService
        from host_brain.app.capability_gap_telemetry import CapabilityGapTelemetry

        svc = CapabilityForgeService(telemetry=CapabilityGapTelemetry())
        t = svc._get_auto_plan_thresholds()
        # key 名称与原实现完全一致（新新增的调用点依赖这些 key）
        assert set(t.keys()) == {
            "enabled", "min_detected_count", "min_unique_sessions",
            "min_failure_rate", "min_failed_count", "dedupe_cooldown_ms",
            "canary_traffic_percent", "auto_approve_enabled",
            "auto_approve_stage", "auto_approver",
        }
        # 默认值与迁移前完全一致
        assert t["enabled"] is True
        assert t["min_detected_count"] == 3
        assert t["min_unique_sessions"] == 2
        assert t["min_failure_rate"] == pytest.approx(0.35)
        assert t["min_failed_count"] == 2
        assert t["dedupe_cooldown_ms"] == 1_800_000
        assert t["canary_traffic_percent"] == 5
        assert t["auto_approve_enabled"] is False
        assert t["auto_approve_stage"] == "SHADOW"  # _normalize_stage 大写化
        assert t["auto_approver"] == "auto_forge"


class TestInspectSetting:
    def test_env_source(self, monkeypatch):
        monkeypatch.setenv("HOST_APP_PORT", "19001")
        r = inspect_setting(
            domain="core", field_name="host_app_port",
            env_key="HOST_APP_PORT", default_value=18080,
            current_value=19001, is_required=False,
        )
        assert r.source == "env"
        assert "19001" in r.value_preview

    def test_default_source(self, monkeypatch):
        monkeypatch.delenv("HOST_APP_PORT", raising=False)
        r = inspect_setting(
            domain="core", field_name="host_app_port",
            env_key="HOST_APP_PORT", default_value=18080,
            current_value=18080, is_required=False,
        )
        assert r.source == "default"

    def test_missing_required(self, monkeypatch):
        monkeypatch.delenv("HOST_REQ_FIELD", raising=False)
        r = inspect_setting(
            domain="core", field_name="req_field",
            env_key="HOST_REQ_FIELD", default_value=None,
            current_value=None, is_required=True,
        )
        assert r.source == "missing"

    def test_redact_sensitive_fields(self, monkeypatch):
        monkeypatch.setenv("HOST_EDGE_RPC_SHARED_KEY", "super-secret-key-value")
        r = inspect_setting(
            domain="tribe", field_name="edge_rpc_shared_key",
            env_key="HOST_EDGE_RPC_SHARED_KEY", default_value=None,
            current_value="super-secret-key-value", is_required=False,
        )
        assert r.source == "env"
        assert "super-secret" not in r.value_preview
        assert r.value_preview in ("<redacted>", "<empty>")


class TestReadinessAggregation:
    def test_verify_readiness_merges_domains(self):
        report = verify_readiness(
            producers=[
                CoreSettings.readiness_fields,
                LLMSettings.readiness_fields,
                TribeSettings.readiness_fields,
                ObservabilitySettings.readiness_fields,
            ]
        )
        assert isinstance(report, SettingsReadinessReport)
        assert len(report.fields) > 10
        domains = {f.domain for f in report.fields}
        assert domains == {"core", "llm", "tribe", "observability"}

    def test_report_to_dict_shape(self):
        report = verify_readiness(
            producers=[CoreSettings.readiness_fields]
        )
        d = report.to_dict()
        assert set(d.keys()) >= {"ready", "counts", "missing_required", "fields"}
        assert set(d["counts"].keys()) == {"total", "env", "default", "missing"}
        assert d["ready"] is True  # core 字段全部无 required

    def test_env_override_shows_in_report(self, monkeypatch):
        monkeypatch.setenv("HOST_APP_PORT", "19001")
        get_core_settings.cache_clear()
        report = verify_readiness(producers=[CoreSettings.readiness_fields])
        port_field = next(f for f in report.fields if f.field_name == "host_app_port")
        assert port_field.source == "env"

    def test_producer_exception_does_not_break_aggregation(self):
        def bad_producer() -> list[SettingsFieldReport]:
            raise RuntimeError("bad domain")
        report = verify_readiness(
            producers=[
                bad_producer,
                CoreSettings.readiness_fields,
            ]
        )
        # core 的字段依然存在
        assert any(f.domain == "core" for f in report.fields)


class TestOpt4BatchThreeCallsites:
    """OPT-4 第三批：调用点迁移回归保护。

    目标：3 处散点 env 读取（PREFRONTAL_PLAN_ENABLED / HOST_SKILL_DAG_PARALLEL_WINDOW /
    HOST_SYS_MODE）已迁移到 CoreSettings，验证旧行为完全保持：
    - skill_dag_planner._resolve_parallel_window() 仍然能响应 env 变化
    - skill_dag_planner._resolve_parallel_window(value=N) 显式传值优先生效
    - chat_context_builder._build_runtime_mode_flags() 的 sys_mode 跟随 CoreSettings
    - health/env-readiness 聚合能看到新增 3 个字段
    """

    def test_skill_dag_parallel_window_follows_env(self, monkeypatch):
        monkeypatch.setenv("HOST_SKILL_DAG_PARALLEL_WINDOW", "12")
        get_core_settings.cache_clear()

        from host_brain.app.skill_dag_planner import _resolve_parallel_window
        assert _resolve_parallel_window() == 12

    def test_skill_dag_explicit_value_wins_over_env(self, monkeypatch):
        """显式传入 value 的路径不应受 env 影响。"""
        monkeypatch.setenv("HOST_SKILL_DAG_PARALLEL_WINDOW", "12")
        get_core_settings.cache_clear()

        from host_brain.app.skill_dag_planner import _resolve_parallel_window
        assert _resolve_parallel_window(value=3) == 3

    def test_sys_mode_flows_through_build_runtime_mode_flags(self, monkeypatch):
        monkeypatch.setenv("HOST_SYS_MODE", "safe")
        get_core_settings.cache_clear()

        from host_brain.app.chat_context_builder import _build_runtime_mode_flags
        flags = _build_runtime_mode_flags({})
        assert flags["sys_mode"] == "safe"

    def test_sys_mode_defaults_to_normal_without_env(self, monkeypatch):
        monkeypatch.delenv("HOST_SYS_MODE", raising=False)
        get_core_settings.cache_clear()

        from host_brain.app.chat_context_builder import _build_runtime_mode_flags
        flags = _build_runtime_mode_flags({})
        assert flags["sys_mode"] == "normal"

    def test_sys_mode_hard_snapshot_overrides_env(self, monkeypatch):
        """hard_snapshot 中已经有 sys_mode 时，应无视 env/CoreSettings。"""
        monkeypatch.setenv("HOST_SYS_MODE", "safe")
        get_core_settings.cache_clear()

        from host_brain.app.chat_context_builder import _build_runtime_mode_flags
        flags = _build_runtime_mode_flags({"hard_snapshot": {"sys_mode": "emergency"}})
        assert flags["sys_mode"] == "emergency"

    def test_prefrontal_plan_enabled_env_flow(self, monkeypatch):
        monkeypatch.setenv("PREFRONTAL_PLAN_ENABLED", "true")
        get_core_settings.cache_clear()

        # 直接读取 CoreSettings 验证（不触发 executive_runtime_core 整条链路）
        assert get_core_settings().prefrontal_plan_enabled is True

        monkeypatch.setenv("PREFRONTAL_PLAN_ENABLED", "false")
        get_core_settings.cache_clear()
        assert get_core_settings().prefrontal_plan_enabled is False

    def test_health_readiness_contains_new_three_fields(self, monkeypatch):
        monkeypatch.setenv("PREFRONTAL_PLAN_ENABLED", "true")
        get_core_settings.cache_clear()

        report = verify_readiness(producers=[CoreSettings.readiness_fields])
        names = {f.field_name for f in report.fields}
        assert {"prefrontal_plan_enabled", "skill_dag_parallel_window", "sys_mode"}.issubset(names)
        # env 覆盖要正确识别为 source=env
        pf_field = next(f for f in report.fields if f.field_name == "prefrontal_plan_enabled")
        assert pf_field.source == "env"
