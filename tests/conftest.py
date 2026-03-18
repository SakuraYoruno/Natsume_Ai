"""全局测试配置：在每个测试后重置 host_brain 全局单例，防止测试间污染。"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_host_brain_singletons():
    """每个测试前后重置 host_brain.app.dependencies 中的全局单例。

    这解决了以下问题：
    - 某些测试修改了 _intent_engine / _ledger / _skill_registry / _policy_cache
    - 导致后续测试使用了被污染的单例
    """
    yield
    # 测试结束后重置
    try:
        import host_brain.app.dependencies as dep
        dep._intent_engine = None
        dep._skill_registry = None
        dep._policy_cache = None
        dep._ledger = None
    except Exception:
        pass
