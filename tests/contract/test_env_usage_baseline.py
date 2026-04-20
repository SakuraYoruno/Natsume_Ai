"""`os.environ` / `os.getenv` 散点门禁（OPT-4）。

OPT-4 引入了分层 Settings（`host_brain.app.settings.*`）。
本测试作为 CI Gate，**防止回归**：

1. 当前基线以下的散点数量，允许通过；
2. 未来新增 `os.environ.get(` 或 `os.getenv(` 时，CI 会 FAIL，
   要求作者将新配置接入分层 Settings，而非散点读取。

当 OPT-5 / OPT-7 / OPT-10 顺带清理散点时，可以手动调低基线。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.contract]

# 当前基线（OPT-4 落地时的数值）
# 后续随 OPT-5 / OPT-7 / OPT-10 清理逐步下调，任何上升都会 FAIL
BASELINE_OS_ENVIRON = 488
BASELINE_OS_GETENV = 19

# 允许 Settings 包自身、配置中心文件使用 os.environ / os.getenv
ALLOWED_PATHS = (
    "host_brain/app/settings/",   # 分层 Settings 自身
    "host_brain/app/config.py",   # 现有配置中心（逐步收敛）
    "edge_client/config.py",      # 现有 edge 配置中心
)


def _scan_pattern(pattern: str, roots: list[str]) -> list[tuple[str, int, str]]:
    """在 roots 下按正则统计匹配行。

    返回 [(file, line_no, text), ...]，已过滤 ALLOWED_PATHS。
    """
    hits: list[tuple[str, int, str]] = []
    regex = re.compile(pattern)
    repo_root = Path(__file__).resolve().parents[2]
    for root in roots:
        root_path = repo_root / root
        if not root_path.exists():
            continue
        for py_path in root_path.rglob("*.py"):
            rel = py_path.relative_to(repo_root).as_posix()
            if any(rel.startswith(a) for a in ALLOWED_PATHS):
                continue
            try:
                text = py_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for idx, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    hits.append((rel, idx, line.strip()))
    return hits


def test_os_environ_baseline_not_exceeded():
    """确保 os.environ 散点数量不超过基线（允许减少不允许增加）。"""
    hits = _scan_pattern(r"os\.environ", ["host_brain", "edge_client"])
    count = len(hits)
    assert count <= BASELINE_OS_ENVIRON, (
        f"os.environ 散点数量从基线 {BASELINE_OS_ENVIRON} 上升到 {count}。\n"
        f"新增的配置读取请改为接入 host_brain/app/settings/ 下的分层 Settings，\n"
        f"而不是直接读 os.environ。"
    )


def test_os_getenv_baseline_not_exceeded():
    """确保 os.getenv 散点数量不超过基线。"""
    hits = _scan_pattern(r"os\.getenv\(", ["host_brain", "edge_client"])
    count = len(hits)
    assert count <= BASELINE_OS_GETENV, (
        f"os.getenv 散点数量从基线 {BASELINE_OS_GETENV} 上升到 {count}。\n"
        f"新增的配置读取请改为接入 host_brain/app/settings/ 下的分层 Settings。"
    )


def test_settings_package_importable():
    """确保分层 Settings 入口可导入（作为 OPT-4 接入路径 smoke check）。"""
    from host_brain.app.settings import (  # noqa: F401
        CoreSettings,
        LLMSettings,
        ObservabilitySettings,
        TribeSettings,
        verify_readiness,
    )


def test_env_core_example_exists():
    """确保 .env.core.example 存在（新人入门最小集合）。"""
    repo_root = Path(__file__).resolve().parents[2]
    core_example = repo_root / ".env.core.example"
    assert core_example.exists(), (
        ".env.core.example 不存在；OPT-4 要求提供新人入门最小必需集合。"
    )
    text = core_example.read_text(encoding="utf-8")
    # 必需字段必须出现
    required_keys = [
        "Natsume_ENV",
        "HOST_APP_PORT",
        "HOST_APP_LOG_LEVEL",
        "HOST_LLM_PROVIDER",
        "NATSUME_DATA_DIR",
    ]
    missing = [k for k in required_keys if k not in text]
    assert not missing, f".env.core.example 缺少必需字段：{missing}"
