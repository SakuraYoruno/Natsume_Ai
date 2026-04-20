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
    CoreSettings,
    LLMSettings,
    ObservabilitySettings,
    SettingsReadinessReport,
    TribeSettings,
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
    yield
    get_core_settings.cache_clear()
    get_llm_settings.cache_clear()
    get_tribe_settings.cache_clear()
    get_observability_settings.cache_clear()


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
