"""仓库大文件守门契约测试（OPT-1）。

拦截任何单文件体积超过 ``MAX_SINGLE_FILE_BYTES`` 的文件进入 git 版本控制，
防止 IDE 个人态文件（如 JetBrains ``CopilotChatHistory.xml``）、
二进制数据包、下载模型等被误纳入仓库。

追踪编号：OPTIMIZATION-PLAN-20260420 / OPT-1
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

# 单文件硬上限：1 MiB。
# 选 1MiB 的依据：
#   - 当前仓库内最大文件 docs/30_devspec/DevTasks.md 约 586 KB；
#   - 真实源码 / Markdown 文档几乎不应超过 1MiB；
#   - 若未来确有合法大文件（如必要的测试 fixture），需在下方 ALLOWLIST 显式登记。
MAX_SINGLE_FILE_BYTES = 1 * 1024 * 1024

# 例外清单：必要时把合法的大文件 repo 相对路径加入此处。
# 每项新增必须附上 PR 说明，避免 allowlist 失控。
ALLOWLIST: frozenset[str] = frozenset(
    {
        # 本地桌宠/Live2D 验证模型资产，当前用于桌宠与 VTS 模型联调。
        "desktop_pet/assets/live2d/edge_vts_model/宅家少女.8192/texture_00.png",
        "desktop_pet/assets/live2d/edge_vts_model/宅家少女.moc3",
        "desktop_pet/assets/live2d/shizuku/runtime/shizuku.1024/texture_02.png",
    }
)


def _list_tracked_files_with_size() -> list[tuple[int, str]]:
    """使用 ``git ls-tree`` 获取 HEAD 中所有被跟踪文件及大小。

    返回 ``[(size_in_bytes, repo_relative_path), ...]``。
    使用 git 原生命令而非 ``Path.stat``，因为我们要守护的是
    "已被 git 跟踪" 的文件，而非工作区里未入库的临时文件。
    """
    proc = subprocess.run(
        ["git", "-c", "core.quotePath=false", "ls-tree", "-r", "-l", "HEAD"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    rows: list[tuple[int, str]] = []
    for line in proc.stdout.splitlines():
        # 形如: "100644 blob <sha>\t<size>\t<path>"
        parts = line.split(maxsplit=4)
        if len(parts) < 5:
            continue
        size_token = parts[3]
        path = parts[4]
        try:
            size = int(size_token)
        except ValueError:
            continue
        rows.append((size, path))
    return rows


class TestRepoSizeGuard(unittest.TestCase):
    """OPT-1：仓库不允许存在 > 1MiB 的单文件。"""

    def test_no_tracked_file_exceeds_limit(self) -> None:
        rows = _list_tracked_files_with_size()
        offenders = [
            (size, path)
            for size, path in rows
            if size > MAX_SINGLE_FILE_BYTES and path not in ALLOWLIST
        ]
        if offenders:
            lines = [
                f"{size / 1024:.1f} KiB  {path}"
                for size, path in sorted(offenders, reverse=True)
            ]
            self.fail(
                "以下文件超过单文件大小上限 "
                f"{MAX_SINGLE_FILE_BYTES // 1024} KiB，不得入库：\n"
                + "\n".join(lines)
                + "\n\n如确有必要保留，请在 ALLOWLIST 中登记并在 PR 说明理由。"
            )

    def test_allowlist_entries_are_still_tracked(self) -> None:
        """若 allowlist 登记的文件已被移除/重命名，应及时清理。"""
        tracked = {path for _, path in _list_tracked_files_with_size()}
        stale = sorted(p for p in ALLOWLIST if p not in tracked)
        self.assertEqual(
            [],
            stale,
            msg=f"ALLOWLIST 中的以下文件已不存在于仓库，请清理：{stale}",
        )


if __name__ == "__main__":
    unittest.main()
