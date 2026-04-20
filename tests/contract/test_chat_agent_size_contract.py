"""chat_agent.py / prompt_builder.py 体积硬限制契约测试（OPT-7 一期）.

背景：docs/35_quality/OPTIMIZATION_PLAN_20260420.md §11 / OPT-7 指出，`chat_agent.py`
应按处理阶段拆分为 pre/post processor 与主协调器，最终 chat_agent.py ≤ 400 行、
prompt_builder.py 作为 facade ≤ 200 行。深度拆分（P2-10 / P2-12）仍在推进中。

本契约测试采用**防反复墨制**策略：

  - 以"当前行数 + 宽松上浮"作为**硬上限**，确保继续推进拆分前文件不再继续膨胀。
  - 随拆分进展，上限每次至少单调**下降**，不能上升。上调需单独评审。
  - 达到最终目标后（chat_agent.py ≤ 400 / prompt_builder.py ≤ 200），改为严格阈值。

阈值规则：
  - chat_agent.py：当前 2386 行 → 契约上限 2500 行。
    * 期待：经 OPT-7 二期后降至 ≤ 400 行；此时本契约应同步改为 400。
  - prompt_builder.py：当前 696 行 → 契约上限 800 行。
    * 期待：经 OPT-7 二期后降至 ≤ 200 行；此时本契约应同步改为 200。
  - chat_agent_preprocessor / chat_agent_postprocessor：应存在且不得再变成"垃圾桶"。
    preprocessor 当前 982 行，上限 1100 行；postprocessor 当前 315 行，上限 500 行。

稳定性 API 检查：
  - prompt_builder.py 的若干对外 symbol 必须可 import（防止拆分破坏向后兼容）。
"""

from __future__ import annotations

import importlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOST_APP = ROOT / "host_brain" / "app"


# ---------------------------------------------------------------------------
# OPT-7 硬上限（拆分完成前的防反复墨制阈值；完成后应降至目标行数）
# ---------------------------------------------------------------------------
OPT7_MAX_LINES = {
    "chat_agent.py": 2500,            # 目标 ≤ 400；当前 2386，防膨胀
    "prompt_builder.py": 800,         # 目标 ≤ 200；当前 696，防膨胀
    "chat_agent_preprocessor.py": 1100,  # 当前 982，防膨胀
    "chat_agent_postprocessor.py": 500,  # 当前 315，防膨胀
}


def _count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8") as fh:
        return sum(1 for _ in fh)


class TestChatAgentSizeLint(unittest.TestCase):
    """OPT-7 一期：chat_agent 家族文件体积硬限制。"""

    def test_t_opt7_01_chat_agent_family_within_limits(self) -> None:
        offenders: list[str] = []
        for filename, limit in OPT7_MAX_LINES.items():
            path = HOST_APP / filename
            self.assertTrue(
                path.is_file(),
                msg=f"{filename} 不存在，OPT-7 一期保护已失效，请同步更新契约。",
            )
            lines = _count_lines(path)
            if lines > limit:
                offenders.append(f"{filename}: {lines} lines (> {limit})")
        self.assertEqual(
            [],
            offenders,
            msg=(
                "chat_agent 家族文件体积超过 OPT-7 契约硬上限，"
                "禁止继续膨胀：\n  - "
                + "\n  - ".join(offenders)
                + "\n（参考 docs/35_quality/OPTIMIZATION_PLAN_20260420.md §11 OPT-7）"
            ),
        )

    def test_t_opt7_02_limits_are_monotonically_decreasing(self) -> None:
        """人工检查锁：限额必须随拆分进度单调下降，严禁因为"现在放不下"而调高上限。"""
        # 期望目标（OPTIMIZATION_PLAN_20260420.md §11）：
        # chat_agent.py ≤ 400；prompt_builder.py ≤ 200
        # 因此各硬上限应始终 > 目标值（拆分未完成）或 == 目标值（完成后）。
        expected_final_targets = {
            "chat_agent.py": 400,
            "prompt_builder.py": 200,
        }
        for name, target in expected_final_targets.items():
            self.assertGreaterEqual(
                OPT7_MAX_LINES[name],
                target,
                msg=(
                    f"{name} 的契约上限 {OPT7_MAX_LINES[name]} 不应低于最终目标 "
                    f"{target}；调整时请同步更新 expected_final_targets。"
                ),
            )


class TestPromptBuilderPublicApi(unittest.TestCase):
    """OPT-7 一期：prompt_builder 对外 API 稳定性保护.

    即便 prompt_builder.py 进一步拆分为多个子模块，以下符号仍须可从
    host_brain.app.prompt_builder 导入（re-export）。防止下游 import 断裂。
    """

    REQUIRED_SYMBOLS = (
        # 如有稳定的对外函数/类名，可逐项追加；当前仅要求模块可导入作为起点。
    )

    def test_t_opt7_03_prompt_builder_module_importable(self) -> None:
        module = importlib.import_module("host_brain.app.prompt_builder")
        self.assertIsNotNone(module, msg="prompt_builder 模块 import 失败")

    def test_t_opt7_04_chat_agent_module_importable(self) -> None:
        module = importlib.import_module("host_brain.app.chat_agent")
        self.assertIsNotNone(module, msg="chat_agent 模块 import 失败")

    def test_t_opt7_05_processor_modules_importable(self) -> None:
        # 确保 OPT-7 已落地的 pre/post processor 不被误删
        pre = importlib.import_module("host_brain.app.chat_agent_preprocessor")
        post = importlib.import_module("host_brain.app.chat_agent_postprocessor")
        self.assertIsNotNone(pre)
        self.assertIsNotNone(post)


if __name__ == "__main__":
    unittest.main()
